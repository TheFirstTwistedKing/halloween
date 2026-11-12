"""무한모드 최종보스: 설정 검증, 등장(무한 100층 10번째 방), 2페이즈를 지나 쓰러뜨리면 완전 클리어, 보상, 화면, 명령어."""
import json
import unittest

import balance
import dungeon
import dungeon_service as service
from balance import Balance, check_final, clean_final, floor_scaled
from cogs.dungeon import has_intro, run_embed, secret_intro_frames
from cogs.final_boss import FinalBoss, final_text
from dungeon import ROOMS, Run
from services import Locks
from store import DEFAULT_SETTINGS, Store, validate_settings
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
HIT = [0.99] * 40


def bal_with(final=None, **overrides):
    return Balance.build({"일반.무한모드": 1, **overrides}, None, None, None, None, None, final)


def at_gate(b, floor=100, **kw):
    """무한모드로 floor층 9번째 방까지 와 있는 던전(다음 [앞으로]가 10번째 방)."""
    run = dungeon.new_run(bal=b)
    run.infinite, run.floor, run.room, run.phase, run.kind = True, floor, ROOMS - 1, "empty", "empty"
    for k, v in kw.items():
        setattr(run, k, v)
    return run


def facing_final(b, **kw):
    run = at_gate(b, **kw)
    dungeon.advance(run, Scripted(randoms=HIT), b)
    return run


class SettingTests(unittest.TestCase):
    def test_defaults(self):
        b = balance.DEFAULT
        self.assertTrue(b.final_on)
        self.assertEqual((b.final_floor, b.final_reward, b.final_tickets), (100, 50000, 1))
        boss = b.final_boss
        self.assertEqual((boss.name, boss.hp, boss.damage, boss.dodge, boss.boss), ("오메가 코어", 1500, 150, 0, True))
        self.assertEqual((boss.phase2_hp, boss.skill), (1500, "strike"))

    def test_check_final_validates_and_keeps_choices(self):
        data, problem = check_final({"name": "종말 엔진", "hp": 3000, "floor": 50, "reward": 7, "tickets": 0, "element": "e"})
        self.assertIsNone(problem)
        self.assertEqual((data["name"], data["hp"], data["damage"], data["floor"], data["reward"], data["tickets"], data["element"]),
                         ("종말 엔진", 3000, 150, 50, 7, 0, "e"))
        for bad in ({"hp": 0}, {"hp": "x"}, {"floor": 1001}, {"floor": True}, {"reward": -1}, {"tickets": 11}, {"skill": "fly"}, {"name": ""}):
            self.assertIsNotNone(check_final(bad)[1], bad)
        self.assertIsNone(check_final({"skill": "none"})[0]["skill"])               # 지운 스킬은 기본 스킬로 돌아가지 않는다
        self.assertEqual(check_final({"dodge": 50})[0].get("dodge", 0), 0)          # 회피는 정할 수 없다(보스는 항상 명중)

    def test_broken_settings_fall_back_to_the_default(self):
        self.assertEqual(clean_final({"hp": -5, "name": "망가짐"})["final_boss"].name, "오메가 코어")
        self.assertEqual(clean_final("junk")["final_floor"], 100)
        validate_settings(dict(DEFAULT_SETTINGS, final_boss={"hp": 10}))
        with self.assertRaises(ValueError):
            validate_settings(dict(DEFAULT_SETTINGS, final_boss=[1]))

    def test_when_it_is_off(self):
        self.assertFalse(bal_with({"floor": 0}).final_on)
        self.assertFalse(bal_with({"floor": 10}).final_on)                          # 마지막 층(10) 이하
        self.assertTrue(bal_with({"floor": 11}).final_on)
        self.assertFalse(Balance.build({"일반.무한모드": 0}).final_on)


class AppearTests(unittest.TestCase):
    def test_it_waits_in_the_tenth_room_of_floor_100(self):
        b = bal_with()
        run = facing_final(b)
        enemy = run.enemy
        self.assertEqual((run.phase, run.kind, run.room, enemy["final"], enemy["name"]), ("battle", "boss", ROOMS, True, "오메가 코어"))
        self.assertEqual((enemy["hp"], enemy["damage"]), (floor_scaled(1500, 100, b.floor_scale), floor_scaled(150, 100, b.floor_scale)))
        self.assertEqual(enemy["phase2_hp"], floor_scaled(1500, 100, b.floor_scale))
        self.assertEqual(enemy["skill"]["kind"], "strike")
        self.assertIn("final_intro", run.last)
        self.assertTrue(any("최종 관문" in e for e in run.events))

    def test_only_on_that_floor_room_and_in_infinite_mode(self):
        b = bal_with()
        for run in (at_gate(b, floor=99), at_gate(b, floor=101), at_gate(b, infinite=False)):
            dungeon.advance(run, Scripted(randoms=HIT), b)
            self.assertFalse((run.enemy or {}).get("final"), run.floor)
        run = at_gate(b, room=ROOMS - 2)
        dungeon.advance(run, Scripted(randoms=HIT), b)
        self.assertFalse((run.enemy or {}).get("final"))
        for off in (bal_with({"floor": 0}), Balance.build({"일반.무한모드": 0})):   # 꺼 두면 무한모드 중인 던전에도 나오지 않는다
            run = at_gate(off)
            dungeon.advance(run, Scripted(randoms=HIT), off)
            self.assertFalse((run.enemy or {}).get("final"))

    def test_it_replaces_whatever_the_tenth_room_was_set_to(self):
        b = bal_with(**{"방10.빈방": 100, "방10.보스": 0})
        run = facing_final(b)
        self.assertTrue(run.enemy["final"])

    def test_floor_can_be_changed(self):
        b = bal_with({"floor": 12, "name": "작은 코어", "hp": 10})
        run = facing_final(b, floor=12)
        self.assertEqual((run.enemy["name"], run.enemy["final"]), ("작은 코어", True))

    def test_the_summit_mentions_it(self):
        quiet = {"상점.간격": 0, "일반.시크릿확률": 0}                            # 상점·시크릿 방 없이 바로 꼭대기로
        b = bal_with(**quiet)
        run = dungeon.new_run(bal=b)
        run.floor, run.room, run.kind, run.phase = b.floors, ROOMS, "boss", "cleared"
        dungeon.advance(run, Scripted(randoms=[0.99] * 5), b)
        self.assertEqual(run.phase, "summit")
        self.assertTrue(any("100층" in e and "최종보스" in e for e in run.events))
        b = bal_with({"floor": 0}, **quiet)
        run = dungeon.new_run(bal=b)
        run.floor, run.room, run.kind, run.phase = b.floors, ROOMS, "boss", "cleared"
        dungeon.advance(run, Scripted(randoms=[0.99] * 5), b)
        self.assertFalse(any("최종보스" in e for e in run.events))


class DefeatTests(unittest.TestCase):
    def test_two_bars_then_a_full_clear(self):
        b = bal_with()
        run = facing_final(b)
        run.enemy["hp"] = 1
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual((run.phase, run.enemy["phase"], run.final_clear), ("battle", 2, False))
        run.enemy["hp"] = 1
        events = dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual((run.phase, run.final_clear), ("clear", True))
        self.assertTrue(run.finished)
        self.assertTrue(any("완전히 클리어" in e for e in events))

    def test_burn_cannot_take_the_clear_away(self):
        b = bal_with({"phase2_hp": 0})
        run = facing_final(b, hp=5)
        run.burn_amount, run.burn_turns, run.enemy["hp"] = 500, 3, 1
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual((run.phase, run.hp, run.burn_turns), ("clear", 5, 0))

    def test_nothing_ticks_after_the_clear(self):
        b = bal_with({"phase2_hp": 0})
        run = facing_final(b, hp=5, regen_turns=3, regen_amount=10)
        run.enemy["hp"] = 1
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual((run.phase, run.hp, run.regen_turns), ("clear", 5, 3))

    def test_an_ordinary_boss_does_not_clear(self):
        b = bal_with()
        run = at_gate(b, floor=99)
        dungeon.advance(run, Scripted(randoms=HIT), b)
        run.enemy.update(hp=1, phase2_hp=0)
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual((run.phase, run.final_clear), ("cleared", False))

    def test_old_saves_read_without_the_flag(self):
        data = json.loads(dungeon.new_run().to_json())
        data.pop("final_clear")
        self.assertFalse(Run.from_json(json.dumps(data)).final_clear)


class ScreenTests(unittest.TestCase):
    def test_battle_title_marker_and_intro(self):
        b = bal_with()
        run = facing_final(b)
        embed = run_embed(run, None, b)
        self.assertIn("최종 관문", embed.title)
        self.assertTrue(any("🌀 FINAL" in f.name for f in embed.fields))
        self.assertTrue(has_intro(run))
        self.assertIn("F I N A L", secret_intro_frames(run.enemy)[-1].title)
        self.assertIn("S E C R E T", secret_intro_frames(dict(run.enemy, final=False))[-1].title)

    def test_final_text(self):
        self.assertIn("100층", final_text(bal_with()))
        self.assertIn("나오지 않아요", final_text(bal_with({"floor": 5})))
        self.assertIn("꺼져 있어요", final_text(bal_with({"floor": 0})))
        self.assertIn("무한모드가 꺼져", final_text(Balance.build({"일반.무한모드": 0})))
        self.assertIn("스킬: 없음", final_text(bal_with({"skill": "none"})))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        self.store.update_settings(G, dungeon={"일반.무한모드": 1}, final_boss={"reward": 1234, "tickets": 2, "phase2_hp": 0})

    def tearDown(self):
        self.store.close()

    async def fight(self, final=True):
        out = await service.enter(self.store, self.locks, G, U)
        bal = self.store.balance(G)
        run = out.run
        run.infinite, run.floor, run.room, run.phase, run.kind = True, 100 if final else 99, ROOMS - 1, "empty", "empty"
        dungeon.advance(run, Scripted(randoms=HIT), bal)
        run.enemy["hp"] = 1
        self.store.save_dungeon(G, U, run.to_json())
        return await service.act(self.store, self.locks, Scripted(randoms=HIT, ints=[100]), G, U, run.rev, "attack")

    async def test_the_final_win_ends_the_run_with_the_big_reward(self):
        before = self.store.wallet(G, U)
        out = await self.fight()
        self.assertTrue(out.ok, out.text)
        self.assertEqual(out.ended, "clear")
        self.assertTrue(out.reward.final)
        self.assertEqual((out.reward.final_candy, out.reward.final_tickets), (1234, 2))
        self.assertEqual(self.store.wallet(G, U) - before, 100 + 1234)               # 클리어 보상(굴린 100) + 최종보스 보상
        self.assertEqual(self.store.tickets(G, U), 2)
        self.assertIsNone(self.store.dungeon_run(G, U))
        embed = run_embed(out.run, out, self.store.balance(G), self.store.currency(G))
        self.assertIn("완전 클리어", embed.title)
        shown = "".join(f.value for f in embed.fields)
        self.assertIn("최종보스 보상", shown)
        self.assertIn("획득권 +2", shown)

    async def test_an_ordinary_clear_gives_no_final_reward(self):
        out = await service.enter(self.store, self.locks, G, U)
        run = out.run
        run.floor, run.room, run.phase, run.kind = self.store.balance(G).floors, ROOMS, "summit", "summit"
        self.store.save_dungeon(G, U, run.to_json())
        out = await service.act(self.store, self.locks, Scripted(randoms=HIT, ints=[100]), G, U, run.rev, "finish")
        self.assertEqual(out.ended, "clear")
        self.assertFalse(out.reward.final)
        self.assertEqual((self.store.wallet(G, U), self.store.tickets(G, U)), (100, 0))

    async def test_an_ordinary_boss_win_gives_no_final_reward(self):
        out = await self.fight(final=False)
        self.assertTrue(out.ok, out.text)
        self.assertIsNone(out.ended)
        self.assertEqual(self.store.tickets(G, U), 0)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = FinalBoss(self.w.bot)
        self.Kind = type("C", (), {"__init__": lambda self, value: setattr(self, "value", value)})

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def boss(self):
        return self.store.balance(G).final_boss

    async def test_setup_view_and_partial_changes(self):
        self.assertIn("오메가 코어", await self.run_cmd(FinalBoss.setup_boss))
        text = await self.run_cmd(FinalBoss.setup_boss, name="종말 엔진", emoji="☢️", hp=4000, floor=60, reward=99999, tickets=3)
        self.assertIn("바꿨어요", text)
        b = self.store.balance(G)
        self.assertEqual((b.final_boss.name, b.final_boss.emoji, b.final_boss.hp, b.final_boss.damage, b.final_floor, b.final_reward, b.final_tickets),
                         ("종말 엔진", "☢️", 4000, 150, 60, 99999, 3))
        await self.run_cmd(FinalBoss.setup_boss, damage=300)
        self.assertEqual((self.boss().name, self.boss().damage), ("종말 엔진", 300))
        self.assertIn("종말 엔진", await self.run_cmd(FinalBoss.show))

    async def test_phase_and_skill(self):
        await self.run_cmd(FinalBoss.phase, 5000, 400)
        self.assertEqual((self.boss().phase2_hp, self.boss().phase2_damage), (5000, 400))
        await self.run_cmd(FinalBoss.phase, 0, 400)
        self.assertEqual((self.boss().phase2_hp, self.boss().phase2_damage), (0, 0))
        await self.run_cmd(FinalBoss.skill, self.Kind("burn"), 2, 60, 3)
        self.assertEqual((self.boss().skill, self.boss().skill_power, self.boss().skill_turns), ("burn", 60, 3))
        await self.run_cmd(FinalBoss.skill, self.Kind("none"))
        self.assertIsNone(self.boss().skill)
        await self.run_cmd(FinalBoss.setup_boss, hp=2000)                           # 다른 칸을 바꿔도 지운 스킬은 그대로 없다
        self.assertIsNone(self.boss().skill)
        self.assertIn("세기", await self.run_cmd(FinalBoss.skill, self.Kind("heal"), 2, 500))
        self.assertIsNone(self.boss().skill)

    async def test_element_set_and_clear(self):
        self.store.update_settings(G, elements=[{"key": "e1", "name": "전기", "emoji": "⚡"}])
        await self.run_cmd(FinalBoss.setup_boss, element="전기")
        self.assertEqual(self.boss().element, "e1")
        await self.run_cmd(FinalBoss.setup_boss, element="없음")
        self.assertIsNone(self.boss().element)
        self.assertIn("그런 속성이 없어요", await self.run_cmd(FinalBoss.setup_boss, element="물"))
        names = [c.value for c in await self.cog.element_names(self.w.interaction(admin=True), "")]
        self.assertEqual(names, ["없음", "전기"])

    async def test_bad_input_reset_and_admin_only(self):
        self.assertIn("이모지", await self.run_cmd(FinalBoss.setup_boss, emoji="abc"))
        self.assertIn("관리자만", await self.run_cmd(FinalBoss.setup_boss, name="해킹", admin=False))
        self.assertIn("관리자만", await self.run_cmd(FinalBoss.reset, admin=False))
        self.assertIn("관리자만", await self.run_cmd(FinalBoss.setup_boss, admin=False))     # 보기만 해도 관리자 전용
        self.assertIn("관리자만", await self.run_cmd(FinalBoss.show, admin=False))
        self.assertEqual(self.boss().name, "오메가 코어")
        await self.run_cmd(FinalBoss.setup_boss, name="종말 엔진", floor=0)
        self.assertFalse(self.store.balance(G).final_on)
        await self.run_cmd(FinalBoss.reset)
        self.assertEqual((self.boss().name, self.store.balance(G).final_floor), ("오메가 코어", 100))


if __name__ == "__main__":
    unittest.main()
