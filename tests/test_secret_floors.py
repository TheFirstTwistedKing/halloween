"""시크릿 보스가 나올 층 정하기: 여러 층을 고를 수 있고(3,5,8-10), 그 층의 보스를 이기고 올라갈 때만 나온다."""
import unittest

import balance
import content
import dungeon
from balance import Balance, check_entity, entity_dict, floors_text, parse_floors
from cogs.content_admin import ContentAdmin
from tests.helpers import Scripted, World, sent_embed

G = 100


def secret(**kw):
    data = dict(key="s", name="심연의 왕", emoji="🌌", hp=500, damage=100, drop_gear="gun", drop_item="golden_soup")
    data.update(kw)
    return data


def bal_with(*secrets, **overrides):
    raw = [entity_dict(check_entity("secrets", s)[0]) for s in secrets]
    return Balance.build(overrides or {}, {"secrets": raw})


def boss_cleared(bal, floor):
    run = dungeon.new_run(bal=bal)
    run.floor, run.room, run.kind, run.phase = floor, dungeon.ROOMS, "boss", "cleared"
    return run


class ParseTests(unittest.TestCase):
    def test_lists_ranges_and_mixtures(self):
        for text, expected in (("3,5,8-10", [3, 5, 8, 9, 10]), ("3", [3]), ("3 5 7", [3, 5, 7]), ("3~5", [3, 4, 5]), ("3층, 5층", [3, 5]),
                               ("5,3,3,4-5", [3, 4, 5]), (" 1 - 3 ".replace(" - ", "-"), [1, 2, 3]), ("2·4", [2, 4]), ("1000", [1000]),
                               ("10층,12층 , 15", [10, 12, 15])):
            self.assertEqual(parse_floors(text), (expected, None), text)

    def test_all_floors_keywords_mean_no_limit(self):
        for text in ("전체", "모두", "-", "all", "ALL", "*", "", "  "):
            self.assertEqual(parse_floors(text), ([], None), repr(text))

    def test_bad_input_is_explained(self):
        for text in ("abc", "3,x", "5-3", "0", "1001", "1-", "-5", "3--5", "3-4-5", "3.5", "1-501", "-3-"):
            floors, problem = parse_floors(text)
            self.assertIsNone(floors, text)
            self.assertTrue(problem, text)
        self.assertIn("예:", parse_floors("abc")[1])

    def test_the_text_groups_consecutive_floors(self):
        self.assertEqual(floors_text([1, 2, 3, 5, 8, 9]), "1~3층, 5층, 8~9층")
        self.assertEqual(floors_text([7]), "7층")
        self.assertEqual(floors_text([3, 1, 2, 2]), "1~3층")
        self.assertEqual(floors_text([]), "모든 층")
        self.assertEqual(floors_text(()), "모든 층")


class EntityTests(unittest.TestCase):
    def test_floors_are_stored_sorted_and_unique_and_default_is_all(self):
        self.assertEqual(check_entity("secrets", secret())[0].floors, ())
        self.assertEqual(check_entity("secrets", secret(floors=[5, 3, 3, 10]))[0].floors, (3, 5, 10))
        self.assertEqual(check_entity("secrets", secret(floors=None))[0].floors, ())
        self.assertEqual(balance.SECRET_BOSSES[0].floors, ())

    def test_bad_floor_values_are_refused(self):
        for bad in ([0], [1001], [-1], [1.5], ["3"], [True], "3", 3, list(range(1, 502))):
            entity, problem = check_entity("secrets", secret(floors=bad))
            self.assertIsNone(entity, bad)
            self.assertIn("층", problem)

    def test_round_trip(self):
        import json
        entity, _ = check_entity("secrets", secret(floors=[2, 4]))
        loaded, problem = check_entity("secrets", json.loads(json.dumps(entity_dict(entity))))
        self.assertIsNone(problem)
        self.assertEqual(loaded, entity)

    def test_old_saves_without_floors_mean_every_floor(self):
        old = secret()
        self.assertEqual(check_entity("secrets", old)[0].floors, ())


class AppearTests(unittest.TestCase):
    def test_a_boss_with_floors_only_appears_on_those_floors(self):
        bal = bal_with(secret(floors=[3, 5, 10]), **{"일반.시크릿확률": 100})
        for floor in range(1, 11):
            run = boss_cleared(bal, floor)
            rng = Scripted(randoms=[0.0, 0.0] if floor in (3, 5, 10) else [0.99, 0.99, 0.99])
            dungeon.advance(run, rng, bal)
            self.assertEqual(run.kind == "secret", floor in (3, 5, 10), floor)

    def test_floors_the_boss_cannot_appear_on_do_not_even_roll(self):
        bal = bal_with(secret(floors=[5]), **{"일반.시크릿확률": 100})
        run = boss_cleared(bal, 4)
        dungeon.advance(run, Scripted(), bal)                                           # 난수를 아예 뽑지 않는다(뽑으면 스크립트가 모자라 실패)
        self.assertEqual((run.floor, run.room), (5, 1))

    def test_each_floor_picks_among_the_bosses_allowed_there(self):
        bal = bal_with(secret(key="a", name="가", floors=[1]), secret(key="b", name="나", floors=[1, 2]), secret(key="c", name="다"),
                       **{"일반.시크릿확률": 100})
        names = lambda floor, draw: self.spawn(bal, floor, draw)
        self.assertEqual({names(1, d) for d in (0.0, 0.34, 0.67, 0.99)}, {"가", "나", "다"})      # 1층: 셋 다 후보
        self.assertEqual({names(2, d) for d in (0.0, 0.49, 0.5, 0.99)}, {"나", "다"})           # 2층: 가는 못 나온다
        self.assertEqual({names(7, d) for d in (0.0, 0.5, 0.99)}, {"다"})                        # 7층: 층 제한이 없는 보스만

    @staticmethod
    def spawn(bal, floor, draw):
        run = boss_cleared(bal, floor)
        dungeon.advance(run, Scripted(randoms=[0.0, draw]), bal)
        return run.enemy["name"]

    def test_a_weight_of_zero_boss_is_ignored_even_on_its_floor(self):
        bal = bal_with(secret(weight=0, floors=[2]), **{"일반.시크릿확률": 100})
        run = boss_cleared(bal, 2)
        dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.floor, 3)

    def test_infinite_floors_can_be_chosen_too(self):
        bal = bal_with(secret(floors=[12]), **{"일반.시크릿확률": 100})
        run = boss_cleared(bal, 12)
        run.infinite = True
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual((run.kind, run.floor), ("secret", 12))
        run = boss_cleared(bal, 11)
        run.infinite = True
        dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.floor, 12)

    def test_the_chance_still_applies_on_the_chosen_floors(self):
        bal = bal_with(secret(floors=[3]), **{"일반.시크릿확률": 10})
        for draw, opens in ((0.0999, True), (0.1, False)):
            run = boss_cleared(bal, 3)
            dungeon.advance(run, Scripted(randoms=[draw, 0.0] if opens else [draw]), bal)
            self.assertEqual(run.kind == "secret", opens, draw)


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

    def floors(self, name="공허의 군주"):
        return next(s.floors for s in self.store.balance(G).secrets if s.name == name)

    async def test_set_several_floors_in_one_command(self):
        text = await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "3,5,8-10")
        self.assertIn("3층, 5층, 8~10층", text)
        self.assertEqual(self.floors(), (3, 5, 8, 9, 10))
        self.assertIn("그 층의 보스를 이기고", text)

    async def test_viewing_without_a_value_changes_nothing(self):
        text = await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주")
        self.assertIn("모든 층", text)
        await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "2,4")
        self.assertIn("2층, 4층", await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주"))
        self.assertEqual(self.floors(), (2, 4))

    async def test_all_floors_clears_the_limit(self):
        await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "7")
        text = await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "전체")
        self.assertIn("모든 층", text)
        self.assertEqual(self.floors(), ())

    async def test_bad_input_and_unknown_boss_are_explained(self):
        self.assertIn("층 번호가 아니에요", await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "abc"))
        self.assertIn("작은 수부터", await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "9-3"))
        self.assertIn("그런 시크릿 보스가 없어요", await self.run_cmd(ContentAdmin.secret_floors, "없는왕", "3"))
        self.assertEqual(self.floors(), ())

    async def test_warnings_for_floors_that_cannot_be_reached(self):
        text = await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "12")
        self.assertIn("무한모드를 골라야", text)                                         # 11층부터는 무한모드로만
        self.store.update_settings(G, dungeon={"일반.무한모드": 0})
        text = await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "12")
        self.assertIn("나올 수 없어요", text)
        self.store.update_settings(G, dungeon={"일반.시크릿확률": 0})
        self.assertIn("0%라 어디서도", await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "3"))

    async def test_each_boss_has_its_own_floors_and_the_list_shows_them(self):
        await self.run_cmd(ContentAdmin.secret_add, "심연의 왕", "🌌", 3000, 250, drop_item="사탕")
        await self.run_cmd(ContentAdmin.secret_floors, "심연의 왕", "10")
        await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "1-5")
        self.assertEqual((self.floors("심연의 왕"), self.floors()), ((10,), (1, 2, 3, 4, 5)))
        i = self.w.interaction(admin=True)
        await ContentAdmin.dungeon_list.callback(self.cog, i, type("C", (), {"value": "secrets"})())
        embed, _ = sent_embed(i)
        text = "\n".join(f.value for f in embed.fields)
        self.assertIn("나오는 층 10층", text)
        self.assertIn("나오는 층 1~5층", text)

    async def test_admin_only_and_autocomplete(self):
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "3", admin=False))
        self.assertEqual(self.floors(), ())
        i = self.w.interaction(admin=True)
        self.assertEqual([c.value for c in await self.cog.secret_names(i, "")], ["공허의 군주"])

    def test_the_options_are_declared_as_text_so_many_floors_fit(self):
        kinds = {p.name: (p.type.name, p.required) for p in ContentAdmin.secret_floors.parameters}
        self.assertEqual(kinds, {"target": ("string", True), "floors": ("string", False)})

    async def test_a_floor_limit_survives_resetting_other_sections_but_not_its_own(self):
        await self.run_cmd(ContentAdmin.secret_floors, "공허의 군주", "4")
        content.reset(self.store, G, ("gear",))
        self.assertEqual(self.floors(), (4,))
        content.reset(self.store, G, ("secrets",))
        self.assertEqual(self.floors(), ())                                            # 기본 시크릿 보스로 되돌아간다
