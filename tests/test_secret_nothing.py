"""시크릿 보스의 '빈 드롭 확률': 그 확률로는 무력화해도 아무것도 떨어뜨리지 않는다. 0이면 예전처럼 늘 하나는 떨어뜨린다."""
import json
import unittest

import content
import dungeon
from balance import Balance, check_entity, entity_dict
from cogs.content_admin import ContentAdmin
from tests.helpers import Scripted, World, sent_embed

G = 100


def secret(**kw):
    data = dict(key="s", name="심연의 왕", emoji="🌌", hp=500, damage=100, drop_gear="gun", drop_item="golden_soup")
    data.update(kw)
    return data


def bal_with(*secrets):
    return Balance.build({}, {"secrets": [entity_dict(check_entity("secrets", s)[0]) for s in secrets]})


def beaten(bal):
    run = dungeon.new_run(bal=bal)
    run.floor, run.room, run.kind, run.phase = 1, dungeon.ROOMS + 1, "secret", "battle"
    run.enemy = dungeon._spawn(bal.secrets[0], 1, bal)
    return run


class EntityTests(unittest.TestCase):
    def test_default_is_zero_and_old_saves_read_as_zero(self):
        boss = check_entity("secrets", secret())[0]
        self.assertEqual(boss.nothing_chance, 0)
        old = entity_dict(boss)
        old.pop("nothing_chance")
        self.assertEqual(check_entity("secrets", old)[0].nothing_chance, 0)

    def test_decimals_and_range(self):
        self.assertEqual(check_entity("secrets", secret(nothing_chance=12.5))[0].nothing_chance, 12.5)
        self.assertEqual(check_entity("secrets", secret(nothing_chance=100))[0].nothing_chance, 100)
        for bad in (-1, 100.1, "5", True):
            self.assertIsNotNone(check_entity("secrets", secret(nothing_chance=bad))[1], bad)

    def test_it_travels_with_the_spawned_boss(self):
        bal = bal_with(secret(nothing_chance=30))
        self.assertEqual(beaten(bal).enemy["drops"]["nothing"], 30)


class DropTests(unittest.TestCase):
    def test_under_the_chance_nothing_drops(self):
        bal = bal_with(secret(nothing_chance=30))
        run = beaten(bal)
        events = dungeon.secret_drops(run, Scripted(randoms=[0.2999]), bal)     # 29.99 < 30: 빈 드롭, 다른 난수는 뽑지 않는다
        self.assertEqual((run.pending_gear, run.items), (None, {}))
        self.assertEqual(len(events), 1)
        self.assertIn("아무것도 떨어뜨리지 않았어요", events[0])

    def test_over_the_chance_the_usual_drops_follow(self):
        bal = bal_with(secret(nothing_chance=30))
        run = beaten(bal)
        events = dungeon.secret_drops(run, Scripted(randoms=[0.3, 0.0, 0.0]), bal)  # 30 이상이면 장비·아이템을 굴린다
        self.assertEqual((run.pending_gear, run.items), ("gun", {"golden_soup": 1}))
        self.assertIn("시크릿 보스의 드롭!", events[0])

    def test_zero_keeps_the_old_guarantee_and_draws_no_extra_number(self):
        bal = bal_with(secret())
        run = beaten(bal)
        dungeon.secret_drops(run, Scripted(randoms=[0.99, 0.99]), bal)                # 둘 다 실패해도 하나는 반드시
        self.assertTrue(run.pending_gear or run.items)

    def test_a_hundred_means_never(self):
        bal = bal_with(secret(nothing_chance=100))
        for draw in (0.0, 0.5, 0.9999):
            run = beaten(bal)
            dungeon.secret_drops(run, Scripted(randoms=[draw]), bal)
            self.assertEqual((run.pending_gear, run.items), (None, {}))

    def test_a_fight_saved_before_this_update_still_drops(self):
        bal = bal_with(secret(nothing_chance=50))
        run = beaten(bal)
        data = json.loads(run.to_json())
        data["enemy"]["drops"].pop("nothing")                                         # 업데이트 전에 시작한 싸움
        run = dungeon.Run.from_json(json.dumps(data))
        dungeon.secret_drops(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertTrue(run.pending_gear)

    def test_the_kill_through_a_real_attack_uses_it(self):
        bal = bal_with(secret(nothing_chance=100, hp=1))
        run = beaten(bal)
        run.enemy["hp"] = 1
        events = dungeon.player_attack(run, Scripted(randoms=[0.99, 0.0]), bal)        # 적중(보스는 회피 0) → 빈 드롭 굴림
        self.assertEqual(run.phase, "cleared")
        self.assertTrue(any("아무것도 떨어뜨리지 않았어요" in e for e in events))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def boss(self, name):
        return next(s for s in self.store.balance(G).secrets if s.name == name)

    async def test_add_and_edit_with_the_option_and_the_list_says_it(self):
        text = await self.run_cmd(ContentAdmin.secret_add, "오류 덩어리", "👾", 800, 90, drop_item="소형 배터리", nothing=25)
        self.assertIn("25% 확률로 없음", text)
        self.assertEqual(self.boss("오류 덩어리").nothing_chance, 25)
        await self.run_cmd(ContentAdmin.secret_edit, "오류 덩어리", nothing=0.5)
        self.assertEqual(self.boss("오류 덩어리").nothing_chance, 0.5)
        await self.run_cmd(ContentAdmin.secret_edit, "오류 덩어리", hp=900)                # 다른 걸 고쳐도 그대로
        self.assertEqual(self.boss("오류 덩어리").nothing_chance, 0.5)
        await self.run_cmd(ContentAdmin.secret_edit, "오류 덩어리", nothing=0)
        line = content.describe("secrets", next(d for d in content.current(self.store, G, "secrets") if d["name"] == "오류 덩어리"), self.store.balance(G))
        self.assertIn("드롭(하나는 반드시)", line)

    async def test_admin_only_and_option_limits(self):
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.secret_add, "가", "👾", 1, 1, drop_item="소형 배터리", nothing=5, admin=False))
        for command in (ContentAdmin.secret_add, ContentAdmin.secret_edit):
            option = next(p for p in command.parameters if p.display_name == "빈드롭확률")
            self.assertEqual((option.min_value, option.max_value), (0, 100))
            self.assertLessEqual(len(command.parameters), 25)
