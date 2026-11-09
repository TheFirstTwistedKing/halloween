"""장비·소모품이 나올 층 정하기(/장비 층, /소모품 층): 상자에서 정한 층에서만 나오고, 층마다 나올 것이 비지 않게 지킨다."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
from balance import Balance, candidates_for_floor, check_entity, entity_dict
from cogs.content_admin import ContentAdmin
from cogs.dungeon_admin import DungeonAdmin
from tests.helpers import Scripted, World, sent_embed

G = 100


def gear(key, name, **kw):
    data = dict(key=key, name=name, emoji="⚔️", slot="weapon", tier="일반", weight=1, attack=50)
    data.update(kw)
    return data


def item(key, name, **kw):
    data = dict(key=key, name=name, emoji="💾", tier="일반", weight=1, heal=10)
    data.update(kw)
    return data


def bal_with(gears=None, items=None, **overrides):
    raw = {}
    if gears is not None:
        raw["gear"] = [entity_dict(check_entity("gear", g)[0]) for g in gears]
    if items is not None:
        raw["consumables"] = [entity_dict(check_entity("consumables", c)[0]) for c in items]
    return Balance.build(overrides, raw)


class EntityTests(unittest.TestCase):
    def test_floors_are_sorted_unique_and_default_to_every_floor(self):
        self.assertEqual(check_entity("gear", gear("a", "가"))[0].floors, ())
        self.assertEqual(check_entity("gear", gear("a", "가", floors=[5, 3, 3]))[0].floors, (3, 5))
        self.assertEqual(check_entity("consumables", item("b", "나", floors=[10]))[0].floors, (10,))
        self.assertEqual(check_entity("consumables", item("b", "나", floors=None))[0].floors, ())
        self.assertTrue(all(g.floors == () for g in balance.GEAR + balance.CONSUMABLES))

    def test_bad_floors_are_refused(self):
        for bad in ([0], [1001], [1.5], ["3"], [True], "3"):
            for section, data in (("gear", gear("a", "가", floors=bad)), ("consumables", item("b", "나", floors=bad))):
                entity, problem = check_entity(section, data)
                self.assertIsNone(entity, (section, bad))
                self.assertIn("층", problem)

    def test_round_trip_and_old_saves(self):
        for section, data in (("gear", gear("a", "가", floors=[2, 4])), ("consumables", item("b", "나", floors=[2, 4]))):
            entity, _ = check_entity(section, data)
            loaded, problem = check_entity(section, json.loads(json.dumps(entity_dict(entity))))
            self.assertIsNone(problem)
            self.assertEqual(loaded, entity)
        self.assertEqual(check_entity("gear", gear("a", "가"))[0].floors, ())                          # 층 항목이 없던 예전 저장


class PoolTests(unittest.TestCase):
    def test_gear_and_items_share_the_same_candidate_rule(self):
        table = tuple(check_entity("gear", gear(k, k, floors=f, weight=w))[0] for k, f, w in
                      (("a", [1], 1), ("b", [2, 3], 1), ("c", [], 1), ("z", [1], 0)))
        self.assertEqual([x.key for x in candidates_for_floor(table, 1)], ["a", "c"])
        self.assertEqual([x.key for x in candidates_for_floor(table, 3)], ["b", "c"])
        self.assertEqual([x.key for x in candidates_for_floor(table, 9)], ["c"])
        only = tuple(check_entity("consumables", item(k, k, floors=f))[0] for k, f in (("a", [1]), ("b", [2])))
        self.assertEqual([x.key for x in candidates_for_floor(only, 11)], ["a", "b"])                  # 맞는 게 없으면 제한을 무시


class ChestTests(unittest.TestCase):
    def chest(self, bal, floor, gear_draw, item_draw):
        run = dungeon.new_run(bal=bal)
        run.floor = floor
        g, c, _ = dungeon.open_chest(run, Scripted(randoms=[gear_draw, item_draw]), bal)
        return g.name, c.name

    def test_gear_and_items_only_drop_on_their_floors(self):
        bal = bal_with(gears=[gear("a", "가", floors=[1]), gear("b", "나", floors=[2, 3]), gear("c", "다")],
                       items=[item("x", "엑스", floors=[1]), item("y", "와이", floors=[3]), item("z", "제트")])
        draws = (0.0, 0.3, 0.6, 0.99)
        seen = lambda floor: ({self.chest(bal, floor, d, 0.5)[0] for d in draws}, {self.chest(bal, floor, 0.5, d)[1] for d in draws})
        self.assertEqual(seen(1), ({"가", "다"}, {"엑스", "제트"}))
        self.assertEqual(seen(2), ({"나", "다"}, {"제트"}))
        self.assertEqual(seen(3), ({"나", "다"}, {"와이", "제트"}))
        self.assertEqual(seen(7), ({"다"}, {"제트"}))

    def test_weights_apply_among_the_candidates_of_the_floor(self):
        bal = bal_with(gears=[gear("a", "가", floors=[1], weight=1), gear("b", "나", weight=3)], items=[item("z", "제트")])
        self.assertEqual(self.chest(bal, 1, 0.2499, 0.5)[0], "가")                                      # 1 : 3 이므로 25%까지 가
        self.assertEqual(self.chest(bal, 1, 0.25, 0.5)[0], "나")
        self.assertEqual(self.chest(bal, 2, 0.0, 0.5)[0], "나")

    def test_default_chest_still_drops_everything_everywhere(self):
        bal = Balance.build({})
        gears = {self.chest(bal, f, d, 0.5)[0] for f in (1, 5, 10) for d in [i / 200 for i in range(200)]}
        self.assertEqual(gears, {g.name for g in balance.GEAR})

    def test_an_infinite_floor_beyond_every_restriction_still_drops(self):
        bal = bal_with(gears=[gear("a", "가", floors=[1, 2, 3])], items=[item("x", "엑스", floors=[1])])
        self.assertEqual(self.chest(bal, 15, 0.5, 0.5), ("가", "엑스"))

    def test_the_simulator_runs_with_restrictions_and_stays_deterministic(self):
        bal = bal_with(gears=[gear("a", "가", floors=[1, 2, 3, 4, 5]), gear("b", "나", floors=[6, 7, 8, 9, 10])],
                       items=[item("x", "엑스", floors=[1, 2, 3, 4, 5]), item("y", "와이")])
        self.assertEqual(balance_sim.simulate(bal, 20, seed=3), balance_sim.simulate(bal, 20, seed=3))


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.bal = self.store.balance(G)

    async def asyncTearDown(self):
        await self.w.close()

    def everything_but(self, section, keep, floors):
        for e in content.current(self.store, G, section):
            if e["name"] != keep:
                content.edit(self.store, G, (section,), e["name"], {"floors": floors})

    def test_restricting_every_gear_is_refused_naming_the_empty_floor(self):
        self.everything_but("gear", "레일건", [1, 2, 3])
        with self.assertRaises(content.ContentError) as ctx:
            content.edit(self.store, G, ("gear",), "레일건", {"floors": [1, 2, 3]})
        self.assertIn("4층 상자에서 나올 수 있는 장비가 하나도 없어요", str(ctx.exception))

    def test_restricting_every_item_is_refused_naming_the_empty_floor(self):
        self.everything_but("consumables", "소형 배터리", [1, 2, 3])
        with self.assertRaises(content.ContentError) as ctx:
            content.edit(self.store, G, ("consumables",), "소형 배터리", {"floors": [1, 2, 3]})
        self.assertIn("4층 상자에서 나올 수 있는 소모품이 하나도 없어요", str(ctx.exception))

    def test_one_unrestricted_entry_is_enough(self):
        self.everything_but("gear", "레일건", [1])
        entry = next(g for g in self.store.balance(G).gear if g.name == "레일건")
        self.assertEqual(entry.floors, ())

    def test_a_zero_weight_entry_does_not_count_as_a_candidate(self):
        for e in content.current(self.store, G, "consumables"):
            if e["name"] not in ("소형 배터리", "납땜 키트"):
                content.edit(self.store, G, ("consumables",), e["name"], {"floors": [1, 2, 3]})
        content.edit(self.store, G, ("consumables",), "납땜 키트", {"weight": 0})                      # 쿠키는 제한이 없어도 비율 0이라 후보가 아니다
        with self.assertRaises(content.ContentError) as ctx:
            content.edit(self.store, G, ("consumables",), "소형 배터리", {"floors": [1, 2, 3]})
        self.assertIn("4층 상자", str(ctx.exception))

    async def test_raising_the_floor_count_is_checked_against_gear_restrictions(self):
        self.store.update_settings(G, dungeon={"일반.층수": 3})
        self.everything_but("gear", "레일건", [1, 2, 3])
        content.edit(self.store, G, ("gear",), "레일건", {"floors": [1, 2, 3]})
        admin = DungeonAdmin(self.w.bot)
        i = self.w.interaction(admin=True)
        await DungeonAdmin.set_param.callback(admin, i, "일반.층수", 5)
        self.assertIn("4층 상자에서 나올 수 있는 장비가 하나도 없어요", sent_embed(i)[0].description)
        self.assertEqual(self.store.balance(G).floors, 3)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def floors(self, attr, name):
        return next(x.floors for x in getattr(self.store.balance(G), attr) if x.name == name)

    async def test_set_view_and_clear_for_gear_and_items(self):
        for command, attr, name in ((ContentAdmin.gear_floors, "gear", "레이저 커터"), (ContentAdmin.potion_floors, "consumables", "소형 배터리")):
            self.assertIn("1~3층, 5층", await self.run_cmd(command, name, "1-3,5"))
            self.assertEqual(self.floors(attr, name), (1, 2, 3, 5))
            self.assertIn("1~3층, 5층", await self.run_cmd(command, name))
            self.assertIn("모든 층", await self.run_cmd(command, name, "전체"))
            self.assertEqual(self.floors(attr, name), ())

    async def test_each_command_only_finds_its_own_kind(self):
        self.assertIn("그런 장비가 없어요", await self.run_cmd(ContentAdmin.gear_floors, "소형 배터리", "3"))
        self.assertIn("그런 소모품이 없어요", await self.run_cmd(ContentAdmin.potion_floors, "레이저 커터", "3"))
        self.assertIn("층 번호가 아니에요", await self.run_cmd(ContentAdmin.gear_floors, "레이저 커터", "abc"))

    async def test_warnings_for_floors_nobody_can_reach(self):
        self.assertIn("무한모드를 골라야", await self.run_cmd(ContentAdmin.gear_floors, "레이저 커터", "11"))
        self.store.update_settings(G, dungeon={"일반.무한모드": 0})
        self.assertIn("나올 수 없어요", await self.run_cmd(ContentAdmin.potion_floors, "소형 배터리", "12"))

    async def test_the_list_shows_floors_only_when_restricted(self):
        for section, name, command in (("gear", "레이저 커터", ContentAdmin.gear_floors), ("consumables", "소형 배터리", ContentAdmin.potion_floors)):
            find = lambda: next(d for d in content.current(self.store, G, section) if d["name"] == name)
            self.assertNotIn("나오는 층", content.describe(section, find()))
            await self.run_cmd(command, name, "2,4")
            self.assertIn("나오는 층 2층, 4층", content.describe(section, find()))

    async def test_admin_only_and_autocomplete(self):
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.gear_floors, "레이저 커터", "3", admin=False))
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.potion_floors, "소형 배터리", "3", admin=False))
        self.assertEqual(self.floors("gear", "레이저 커터"), ())
        i = self.w.interaction(admin=True)
        self.assertIn("레이저 커터", [c.value for c in await self.cog.gear_names(i, "")])
        self.assertIn("소형 배터리", [c.value for c in await self.cog.potion_names(i, "")])

    def test_options_are_text_so_many_floors_fit(self):
        for command in (ContentAdmin.gear_floors, ContentAdmin.potion_floors):
            kinds = {p.name: (p.type.name, p.required) for p in command.parameters}
            self.assertEqual(kinds, {"target": ("string", True), "floors": ("string", False)})
