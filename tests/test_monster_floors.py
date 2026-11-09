"""일반 몬스터·보스가 나올 층 정하기(/몬스터 층): 정한 층에서만 나오고, 층마다 나올 몬스터가 비지 않게 지킨다."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
from balance import Balance, check_entity, entity_dict, monsters_for_floor
from cogs.content_admin import ContentAdmin
from cogs.dungeon_admin import DungeonAdmin
from tests.helpers import Scripted, World, sent_embed

G = 100


def mon(key, name, **kw):
    data = dict(key=key, name=name, emoji="👾", hp=100, damage=10, dodge=5)
    data.update(kw)
    return data


def bal_with(monsters=None, bosses=None, **overrides):
    raw = {}
    if monsters is not None:
        raw["monsters"] = [entity_dict(check_entity("monsters", m)[0]) for m in monsters]
    if bosses is not None:
        raw["bosses"] = [entity_dict(check_entity("bosses", b)[0]) for b in bosses]
    return Balance.build(overrides, raw)


class EntityTests(unittest.TestCase):
    def test_floors_are_sorted_unique_and_default_to_every_floor(self):
        self.assertEqual(check_entity("monsters", mon("a", "가"))[0].floors, ())
        self.assertEqual(check_entity("monsters", mon("a", "가", floors=[5, 3, 3]))[0].floors, (3, 5))
        self.assertEqual(check_entity("bosses", mon("b", "나", floors=[10]))[0].floors, (10,))
        self.assertEqual(check_entity("monsters", mon("a", "가", floors=None))[0].floors, ())
        self.assertTrue(all(m.floors == () for m in balance.MONSTERS + balance.BOSSES))               # 기본 몬스터는 모든 층

    def test_bad_floors_are_refused(self):
        for bad in ([0], [1001], [1.5], ["3"], [True], "3", list(range(1, 502))):
            entity, problem = check_entity("monsters", mon("a", "가", floors=bad))
            self.assertIsNone(entity, bad)
            self.assertIn("층", problem)

    def test_round_trip_and_old_saves(self):
        entity, _ = check_entity("monsters", mon("a", "가", floors=[2, 4]))
        loaded, problem = check_entity("monsters", json.loads(json.dumps(entity_dict(entity))))
        self.assertIsNone(problem)
        self.assertEqual(loaded, entity)
        self.assertEqual(check_entity("monsters", mon("a", "가"))[0].floors, ())                      # 층 항목이 없던 예전 저장


class PoolTests(unittest.TestCase):
    def table(self, *specs):
        return tuple(check_entity("monsters", mon(k, k, floors=f, weight=w))[0] for k, f, w in specs)

    def test_only_monsters_for_that_floor_are_candidates(self):
        t = self.table(("a", [1], 1), ("b", [2, 3], 1), ("c", [], 1))
        self.assertEqual([m.key for m in monsters_for_floor(t, 1)], ["a", "c"])
        self.assertEqual([m.key for m in monsters_for_floor(t, 3)], ["b", "c"])
        self.assertEqual([m.key for m in monsters_for_floor(t, 9)], ["c"])

    def test_zero_weight_never_counts(self):
        t = self.table(("a", [1], 0), ("b", [], 1))
        self.assertEqual([m.key for m in monsters_for_floor(t, 1)], ["b"])

    def test_if_nothing_fits_the_restrictions_are_ignored_instead_of_breaking(self):
        t = self.table(("a", [1], 1), ("b", [2], 2))
        self.assertEqual([m.key for m in monsters_for_floor(t, 11)], ["a", "b"])                       # 무한모드의 높은 층 등
        self.assertEqual([m.key for m in monsters_for_floor(self.table(("a", [1], 0)), 5)], ["a"])      # 전부 비율 0이어도 오류 없이


class AppearTests(unittest.TestCase):
    def monster_room(self, bal, floor, pick):
        """floor층 2번째 방(잡몹)에 들어선다: 방 종류 추첨 0.99 = 잡몹, 그다음 pick으로 몬스터를 고른다."""
        run = dungeon.new_run(bal=bal)
        run.floor, run.room, run.phase = floor, 1, "cleared"
        dungeon.advance(run, Scripted(randoms=[0.99, pick]), bal)
        return run

    def test_a_monster_only_shows_up_on_its_floors(self):
        bal = bal_with(monsters=[mon("a", "가", floors=[1]), mon("b", "나", floors=[2, 3]), mon("c", "다")])
        names = lambda floor: {self.monster_room(bal, floor, d).enemy["name"] for d in (0.0, 0.3, 0.6, 0.99)}
        self.assertEqual(names(1), {"가", "다"})
        self.assertEqual(names(2), {"나", "다"})
        self.assertEqual(names(3), {"나", "다"})
        self.assertEqual(names(4), {"다"})

    def test_the_boss_room_uses_the_same_rule(self):
        bal = bal_with(bosses=[mon("x", "엑스", floors=[1, 2], dodge=0), mon("y", "와이", floors=[3], dodge=0), mon("z", "제트", dodge=0)])

        def boss(floor, draw):
            run = dungeon.new_run(bal=bal)
            run.floor, run.room, run.phase = floor, 9, "cleared"
            dungeon.advance(run, Scripted(randoms=[draw]), bal)
            return run.enemy["name"]
        self.assertEqual({boss(1, d) for d in (0.0, 0.5, 0.99)}, {"엑스", "제트"})
        self.assertEqual({boss(3, d) for d in (0.0, 0.5, 0.99)}, {"와이", "제트"})
        self.assertEqual({boss(7, d) for d in (0.0, 0.5, 0.99)}, {"제트"})

    def test_weights_apply_among_the_candidates_of_the_floor(self):
        bal = bal_with(monsters=[mon("a", "가", floors=[1], weight=1), mon("b", "나", weight=3)])
        self.assertEqual(self.monster_room(bal, 1, 0.2499).enemy["name"], "가")                         # 1 : 3 이므로 25%까지 가
        self.assertEqual(self.monster_room(bal, 1, 0.25).enemy["name"], "나")
        self.assertEqual(self.monster_room(bal, 2, 0.0).enemy["name"], "나")

    def test_default_monsters_still_appear_everywhere(self):
        bal = Balance.build({})
        seen = {self.monster_room(bal, floor, d).enemy["name"] for floor in (1, 5, 10) for d in (0.0, 0.3, 0.6, 0.99)}
        self.assertEqual(seen, {m.name for m in balance.MONSTERS})

    def test_an_infinite_floor_beyond_every_restriction_still_gets_monsters(self):
        bal = bal_with(monsters=[mon("a", "가", floors=[1, 2, 3])])
        run = self.monster_room(bal, 15, 0.5)
        self.assertEqual(run.enemy["name"], "가")

    def test_the_simulator_runs_with_restrictions_and_stays_deterministic(self):
        bal = bal_with(monsters=[mon("a", "가", floors=[1, 2, 3, 4, 5]), mon("b", "나", floors=[6, 7, 8, 9, 10])])
        self.assertEqual(balance_sim.simulate(bal, 20, seed=3), balance_sim.simulate(bal, 20, seed=3))


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store

    async def asyncTearDown(self):
        await self.w.close()

    def floors_of(self, name):
        return next(m.floors for m in self.store.balance(G).monsters + self.store.balance(G).bosses if m.name == name)

    def test_restricting_everything_is_refused_naming_the_empty_floor(self):
        for name in ("정찰 드론", "오일 슬러지", "악성 AI"):
            content.edit(self.store, G, ("monsters",), name, {"floors": [1, 2, 3]})
        with self.assertRaises(content.ContentError) as ctx:
            content.edit(self.store, G, ("monsters",), "고장 난 마우스", {"floors": [1, 2, 3]})
        self.assertIn("4층에 나올 수 있는 몬스터가 하나도 없어요", str(ctx.exception))
        self.assertEqual(self.floors_of("고장 난 마우스"), ())                                                 # 저장되지 않았다

    def test_one_unrestricted_monster_is_enough(self):
        for name in ("정찰 드론", "오일 슬러지", "악성 AI"):
            content.edit(self.store, G, ("monsters",), name, {"floors": [1]})
        self.assertEqual(self.floors_of("고장 난 마우스"), ())

    def test_bosses_need_a_boss_for_every_floor_too(self):
        content.edit(self.store, G, ("bosses",), "메인프레임 군주", {"floors": [1, 2, 3, 4, 5]})
        content.edit(self.store, G, ("bosses",), "슬러지 코어", {"floors": [6, 7, 8, 9]})
        with self.assertRaises(content.ContentError) as ctx:
            content.edit(self.store, G, ("bosses",), "스크랩 봇 왕", {"floors": [1, 2, 3]})                # 10층에 나올 보스가 없다
        self.assertIn("10층에 나올 수 있는 보스가 하나도 없어요", str(ctx.exception))
        content.edit(self.store, G, ("bosses",), "스크랩 봇 왕", {"floors": [10]})
        self.assertEqual(self.floors_of("스크랩 봇 왕"), (10,))

    def test_a_zero_weight_monster_does_not_count_as_a_candidate(self):
        content.edit(self.store, G, ("monsters",), "고장 난 마우스", {"weight": 0})
        nine = list(range(1, 10))
        content.edit(self.store, G, ("monsters",), "정찰 드론", {"floors": nine})
        content.edit(self.store, G, ("monsters",), "오일 슬러지", {"floors": nine})
        with self.assertRaises(content.ContentError) as ctx:
            content.edit(self.store, G, ("monsters",), "악성 AI", {"floors": nine})
        self.assertIn("10층", str(ctx.exception))

    def test_with_fewer_floors_the_same_restrictions_are_fine(self):
        self.store.update_settings(G, dungeon={"일반.층수": 3})
        for name in ("정찰 드론", "오일 슬러지", "악성 AI", "고장 난 마우스"):
            content.edit(self.store, G, ("monsters",), name, {"floors": [1, 2, 3]})

    async def test_raising_the_floor_count_is_also_checked_against_the_restrictions(self):
        self.store.update_settings(G, dungeon={"일반.층수": 3})
        for name in ("정찰 드론", "오일 슬러지", "악성 AI", "고장 난 마우스"):
            content.edit(self.store, G, ("monsters",), name, {"floors": [1, 2, 3]})
        admin = DungeonAdmin(self.w.bot)
        i = self.w.interaction(admin=True)
        await DungeonAdmin.set_param.callback(admin, i, "일반.층수", 5)
        self.assertIn("4층에 나올 수 있는 몬스터가 하나도 없어요", sent_embed(i)[0].description)
        self.assertEqual(self.store.balance(G).floors, 3)                                               # 바뀌지 않았다


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def floors(self, name):
        bal = self.store.balance(G)
        return next(m.floors for m in bal.monsters + bal.bosses if m.name == name)

    async def test_set_view_and_clear_floors_for_a_monster(self):
        text = await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "1-3,5")
        self.assertIn("1~3층, 5층", text)
        self.assertEqual(self.floors("오일 슬러지"), (1, 2, 3, 5))
        self.assertIn("1~3층, 5층", await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지"))
        self.assertIn("모든 층", await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "전체"))
        self.assertEqual(self.floors("오일 슬러지"), ())

    async def test_bosses_work_the_same_way(self):
        text = await self.run_cmd(ContentAdmin.monster_floors, "메인프레임 군주", "1~4")
        self.assertIn("(보스)", text)
        self.assertEqual(self.floors("메인프레임 군주"), (1, 2, 3, 4))

    async def test_bad_input_unknown_name_and_empty_floor_are_explained(self):
        self.assertIn("층 번호가 아니에요", await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "abc"))
        self.assertIn("그런 몬스터·보스가 없어요", await self.run_cmd(ContentAdmin.monster_floors, "없는몹", "3"))
        for name in ("정찰 드론", "악성 AI", "고장 난 마우스"):
            await self.run_cmd(ContentAdmin.monster_floors, name, "1-3")
        text = await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "1-3")
        self.assertIn("4층에 나올 수 있는 몬스터가 하나도 없어요", text)
        self.assertEqual(self.floors("오일 슬러지"), ())

    async def test_warnings_for_floors_nobody_can_reach(self):
        self.assertIn("무한모드를 골라야", await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "11"))
        self.store.update_settings(G, dungeon={"일반.무한모드": 0})
        self.assertIn("나올 수 없어요", await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "12"))

    async def test_the_list_shows_floors_only_when_restricted(self):
        default = content.describe("monsters", content.current(self.store, G, "monsters")[1])
        self.assertNotIn("나오는 층", default)
        self.assertTrue(default.endswith("등장 1"))                                                     # 예전 표시 그대로
        await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "2,4")
        line = content.describe("monsters", next(d for d in content.current(self.store, G, "monsters") if d["name"] == "오일 슬러지"))
        self.assertIn("나오는 층 2층, 4층", line)

    async def test_admin_only_and_autocomplete(self):
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.monster_floors, "오일 슬러지", "3", admin=False))
        self.assertEqual(self.floors("오일 슬러지"), ())
        i = self.w.interaction(admin=True)
        names = [c.value for c in await self.cog.monster_names(i, "")]
        self.assertIn("오일 슬러지", names)
        self.assertIn("메인프레임 군주", names)

    def test_options_are_text_so_many_floors_fit(self):
        kinds = {p.name: (p.type.name, p.required) for p in ContentAdmin.monster_floors.parameters}
        self.assertEqual(kinds, {"target": ("string", True), "floors": ("string", False)})
