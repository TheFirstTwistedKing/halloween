"""무한모드: 마지막 층 보스를 이기고 앞으로 가면 '클리어하고 끝낼지, 무한모드로 계속할지' 묻는다.
무한모드를 고르면 클리어 보상을 바로 받고(쓰러져도 잃지 않음), 11층부터 층마다 보너스 재화를 받는다."""
import asyncio
import json
import random
import unittest

import balance
import dungeon
import dungeon_auto
import dungeon_service as service
from balance import Balance, check_value
from cogs.dungeon import DungeonView, respond, run_embed
from dungeon import DungeonError, Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World

G, U = 100, 7
NO_SECRET = 0.99
NO_SHOP = {"상점.간격": 0}                # 이 시험들은 층을 오르는 흐름만 본다: 상점은 test_shop.py에서 따로 시험한다


def boss_cleared(bal, floor=None, **kw):
    run = dungeon.new_run(bal=bal)
    run.floor, run.room, run.kind, run.phase = floor or bal.floors, dungeon.ROOMS, "boss", "cleared"
    for k, v in kw.items():
        setattr(run, k, v)
    return run


def at_summit(bal=None):
    bal = bal or Balance.build(NO_SHOP)
    run = boss_cleared(bal)
    dungeon.advance(run, Scripted(randoms=[NO_SECRET]), bal)
    return run, bal


class RuleTests(unittest.TestCase):
    def test_the_top_floor_boss_leads_to_a_choice_not_a_clear(self):
        run, bal = at_summit()
        self.assertEqual((run.phase, run.kind, run.enemy, run.floor), ("summit", "summit", None, bal.floors))
        self.assertFalse(run.finished)
        self.assertFalse(run.can_advance)                                                # 고르기 전에는 앞으로 못 간다
        text = "\n".join(run.events)
        self.assertIn("꼭대기", text)
        self.assertIn("무한모드", text)

    def test_lower_floors_do_not_ask(self):
        bal = Balance.build(NO_SHOP)
        for floor in range(1, bal.floors):
            run = boss_cleared(bal, floor=floor)
            dungeon.advance(run, Scripted(randoms=[NO_SECRET]), bal)
            self.assertEqual(run.floor, floor + 1, floor)
            self.assertNotEqual(run.phase, "summit")

    def test_turning_the_question_off_clears_immediately_like_before(self):
        bal = Balance.build({**NO_SHOP, "일반.무한모드": 0})
        run = boss_cleared(bal)
        dungeon.advance(run, Scripted(randoms=[NO_SECRET]), bal)
        self.assertEqual(run.phase, "clear")

    def test_take_clear_ends_the_run_and_only_works_at_the_top(self):
        run, _ = at_summit()
        dungeon.take_clear(run)
        self.assertEqual((run.phase, run.finished), ("clear", True))
        with self.assertRaisesRegex(DungeonError, "꼭대기"):
            dungeon.take_clear(boss_cleared(Balance.build(NO_SHOP), floor=3))

    def test_going_infinite_climbs_to_floor_eleven_with_a_bonus_and_a_partial_heal(self):
        run, bal = at_summit()
        run.hp = 500
        events = dungeon.go_infinite(run, Scripted(randoms=[NO_SECRET]), bal)
        self.assertEqual((run.infinite, run.floor, run.room, run.phase == "summit"), (True, bal.floors + 1, 1, False))
        self.assertEqual(run.bonus, bal.infinite_bonus)                                     # 11층 보너스(기본 30)
        self.assertEqual(run.hp, 500 + (bal.max_hp - 500) * bal.floor_heal // 100)          # 올라가며 잃은 HP를 일부 회복
        self.assertIn("무한모드", events[0])
        self.assertIn(f"{bal.floors + 1}층", "\n".join(events))

    def test_the_floor_bonus_grows_with_each_floor_and_is_adjustable(self):
        bal = Balance.build({**NO_SHOP, "무한.층보너스": 100, "무한.보너스증가": 25})
        run, _ = at_summit(bal)
        dungeon.go_infinite(run, Scripted(randoms=[NO_SECRET]), bal)
        seen = [run.bonus]
        for _ in range(2):
            run.bonus, run.room, run.kind, run.phase, run.enemy = 0, dungeon.ROOMS, "boss", "cleared", None
            dungeon.advance(run, Scripted(randoms=[NO_SECRET]), bal)
            seen.append(run.bonus)
        self.assertEqual(seen, [100, 125, 150])
        zero = Balance.build({**NO_SHOP, "무한.층보너스": 0, "무한.보너스증가": 0})
        run, _ = at_summit(zero)
        dungeon.go_infinite(run, Scripted(randoms=[NO_SECRET]), zero)
        self.assertEqual(run.bonus, 0)

    def test_infinite_never_asks_again_and_keeps_climbing_past_the_old_top(self):
        run, bal = at_summit()
        dungeon.go_infinite(run, Scripted(randoms=[NO_SECRET]), bal)
        for expected in range(bal.floors + 2, bal.floors + 6):
            run.room, run.kind, run.phase, run.enemy = dungeon.ROOMS, "boss", "cleared", None
            dungeon.advance(run, Scripted(randoms=[NO_SECRET]), bal)
            self.assertEqual((run.floor, run.phase == "summit", run.finished), (expected, False, False), expected)

    def test_monsters_keep_getting_stronger_on_infinite_floors(self):
        bal = Balance.build(NO_SHOP)
        low = dungeon._spawn(balance.MONSTERS[1], bal.floors, bal)
        high = dungeon._spawn(balance.MONSTERS[1], bal.floors + 5, bal)
        self.assertGreater(high["max_hp"], low["max_hp"])
        self.assertGreater(high["damage"], low["damage"])

    def test_the_secret_room_can_still_open_on_infinite_floors_and_the_top_is_asked_only_once(self):
        bal = Balance.build(NO_SHOP)
        run, _ = at_summit(bal)
        dungeon.go_infinite(run, Scripted(randoms=[NO_SECRET]), bal)
        run.room, run.kind, run.phase, run.enemy = dungeon.ROOMS, "boss", "cleared", None
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual((run.kind, run.floor), ("secret", bal.floors + 1))
        run.phase, run.enemy = "cleared", None
        dungeon.advance(run, Scripted(), bal)                                               # 시크릿 보스를 이긴 뒤: 다시 묻지 않고 올라간다
        self.assertEqual((run.floor, run.phase == "summit"), (bal.floors + 2, False))

    def test_the_secret_room_comes_before_the_question_on_the_top_floor(self):
        bal = Balance.build(NO_SHOP)
        run = boss_cleared(bal)
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual(run.kind, "secret")
        run.phase, run.enemy = "cleared", None
        dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.phase, "summit")

    def test_old_saves_have_no_infinite_state(self):
        data = json.loads(dungeon.new_run().to_json())
        data.pop("infinite"), data.pop("bonus")
        back = Run.from_json(json.dumps(data))
        self.assertEqual((back.infinite, back.bonus), (False, 0))

    def test_settings(self):
        self.assertEqual(check_value("일반.무한모드", 0), (0, None))
        self.assertEqual(check_value("일반.무한모드", 1), (1, None))
        self.assertIsNotNone(check_value("일반.무한모드", 2)[1])
        self.assertIsNotNone(check_value("일반.무한모드", 0.5)[1])
        self.assertEqual(check_value("무한.층보너스", 99999), (99999, None))
        self.assertIsNotNone(check_value("무한.보너스증가", -1)[1])
        bal = Balance.build(NO_SHOP)
        self.assertEqual((bal.infinite, bal.infinite_bonus, bal.infinite_step), (1, 30, 10))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.key = self.store.ensure_system_items(G)["key"]
        self.store.add_item(G, U, self.key, 2)
        self.store.update_settings(G, dungeon={**NO_SHOP, "보상.최소": 100, "보상.최대": 100, "보상.획득권확률": 0})

    def tearDown(self):
        self.store.close()

    async def at_top(self):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=dungeon.FLOORS, room=dungeon.ROOMS, phase="cleared", kind="boss")
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        top = await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET]), G, U, rev, "advance")
        self.assertEqual(top.run.phase, "summit")
        return top.run

    def saved(self):
        return Run.from_json(self.store.dungeon_run(G, U))

    async def test_choosing_infinite_pays_the_clear_reward_now_and_keeps_the_run(self):
        top = await self.at_top()
        self.assertEqual(self.store.wallet(G, U), 0)                                       # 고르기 전에는 아무 보상도 없다
        out = await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET]), G, U, top.rev, "infinite")
        self.assertTrue(out.ok)
        self.assertIsNone(out.ended)
        run = self.saved()
        self.assertEqual((run.infinite, run.floor, run.bonus), (True, 11, 0))
        self.assertEqual(self.store.wallet(G, U), 100 + 30)                                # 클리어 보상 + 11층 보너스
        text = "\n".join(out.run.events)
        self.assertIn("클리어 보상", text)
        self.assertIn("무한 **11층** 보너스", text)

    async def test_clear_reward_extras_come_with_it_and_only_once(self):
        gem = self.store.create_item(G, "보석", emoji="💎")
        self.store.update_settings(G, clear_rewards=[{"id": "g", "item": gem["id"], "amount": 3, "chance": 100}],
                                   dungeon={**NO_SHOP, "보상.최소": 100, "보상.최대": 100, "보상.획득권확률": 100})
        top = await self.at_top()
        out = await service.act(self.store, self.locks, Scripted(randoms=[0.0, NO_SECRET], ints=[100]), G, U, top.rev, "infinite")
        self.assertTrue(out.ok)
        self.assertEqual((self.store.qty(G, U, gem["id"]), self.store.tickets(G, U)), (3, 1))
        text = "\n".join(out.run.events)
        self.assertIn("히든 칭호 획득권", text)
        self.assertIn("보석", text)
        again = await service.act(self.store, self.locks, Scripted(randoms=[0.0], ints=[100]), G, U, top.rev, "infinite")   # 같은 화면을 또 눌러도
        self.assertEqual((again.ok, again.stale), (False, True))
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, gem["id"]), self.store.tickets(G, U)), (130, 3, 1))

    async def test_two_presses_at_once_pay_only_once(self):
        top = await self.at_top()
        results = await asyncio.gather(
            service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET, NO_SECRET]), G, U, top.rev, "infinite"),
            service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET, NO_SECRET]), G, U, top.rev, "infinite"))
        self.assertEqual(sorted(r.ok for r in results), [False, True])
        self.assertEqual(self.store.wallet(G, U), 130)

    async def test_each_new_infinite_floor_pays_a_growing_bonus(self):
        top = await self.at_top()
        out = await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET]), G, U, top.rev, "infinite")
        wallet = [self.store.wallet(G, U)]
        for _ in range(3):
            data = json.loads(self.store.dungeon_run(G, U))
            data.update(room=dungeon.ROOMS, kind="boss", phase="cleared", enemy=None)
            self.store.save_dungeon(G, U, json.dumps(data))
            rev = self.saved().rev
            out = await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET]), G, U, rev, "advance")
            wallet.append(self.store.wallet(G, U))
            self.assertIn("보너스", "\n".join(out.run.events))
        self.assertEqual(wallet, [130, 130 + 40, 130 + 40 + 50, 130 + 40 + 50 + 60])      # 12층 40, 13층 50, 14층 60

    async def test_nothing_is_lost_when_the_player_dies_after_going_infinite(self):
        top = await self.at_top()
        await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET]), G, U, top.rev, "infinite")
        data = json.loads(self.store.dungeon_run(G, U))
        enemy = dungeon._spawn(balance.MONSTERS[1], 11, self.store.balance(G))
        enemy["damage"] = 99999
        data.update(phase="battle", kind="monster", room=2, hp=5, enemy=enemy)
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = self.saved().rev
        out = await service.act(self.store, self.locks, Scripted(), G, U, rev, "defend", guard_ok=False)
        self.assertEqual(out.ended, "dead")
        self.assertIsNone(self.store.dungeon_run(G, U))
        self.assertEqual(self.store.wallet(G, U), 130)                                      # 이미 받은 보상은 그대로
        embed = run_embed(out.run, out, self.store.balance(G))
        self.assertIn("11층", " ".join(f.value for f in embed.fields))

    async def test_finishing_pays_exactly_the_clear_reward_and_ends(self):
        top = await self.at_top()
        done = await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET], ints=[100]), G, U, top.rev, "finish")
        self.assertEqual((done.ended, done.reward.candy), ("clear", 100))
        self.assertEqual(self.store.wallet(G, U), 100)
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_you_can_sell_equip_and_leave_while_the_question_is_open(self):
        top = await self.at_top()
        data = json.loads(self.store.dungeon_run(G, U))
        data.update(items={"candy": 2})
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = self.saved().rev
        sold = await service.sell(self.store, self.locks, G, U, rev, ["item:candy"], "all")
        self.assertTrue(sold.ok)
        self.assertEqual(sold.run.phase, "summit")                                          # 팔아도 질문은 그대로
        left = await service.act(self.store, self.locks, Scripted(), G, U, sold.run.rev, "quit")
        self.assertEqual(left.ended, "quit")

    async def test_the_reward_respects_the_wallet_maximum(self):
        self.store.update_settings(G, currency_max=120)
        top = await self.at_top()
        out = await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET]), G, U, top.rev, "infinite")
        self.assertTrue(out.ok)
        self.assertEqual(self.store.wallet(G, U), 120)                                      # 130이 아니라 최대 120까지

    async def test_a_dungeon_that_already_ended_pays_nothing(self):
        self.assertIsNone(self.store.reward_in_dungeon(G, U, "{}", candy=500, tickets=3))
        self.assertEqual((self.store.wallet(G, U), self.store.tickets(G, U)), (0, 0))

    async def test_a_bad_choice_is_refused(self):
        out = await service.enter(self.store, self.locks, G, U)
        for action in ("finish", "infinite"):
            done = await service.act(self.store, self.locks, Scripted(), G, U, self.saved().rev, action)
            self.assertFalse(done.ok, action)
            self.assertIn("꼭대기", done.text)
        self.assertEqual((self.store.wallet(G, U), self.saved().infinite), (0, False))

    async def test_quick_play_takes_the_clear_and_never_goes_infinite(self):
        top = await self.at_top()
        self.assertEqual(dungeon_auto.choose(top, self.store.balance(G)), ("finish", None))
        await service.set_auto(self.store, self.locks, G, U, self.saved().rev, True)
        step = await service.auto_step(self.store, self.locks, Scripted(randoms=[0.0], ints=[100]), G, U)
        self.assertEqual(step.ended, "clear")


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World(rng=Scripted(randoms=[NO_SECRET, NO_SECRET]))
        self.bot, self.store = self.w.bot, self.w.store
        self.store.update_settings(G, dungeon={**NO_SHOP, "보상.최소": 100, "보상.최대": 100, "보상.획득권확률": 0})

    async def asyncTearDown(self):
        await self.w.close()

    def labels(self, view):
        return [getattr(c, "label", None) for c in view.children]

    def test_the_summit_screen_has_the_two_choices_and_no_advance_button(self):
        run, bal = at_summit()
        view = DungeonView(object(), U, run, bal)
        view.to_components()
        labels = self.labels(view)
        self.assertIn("클리어하고 끝내기", labels)
        self.assertIn("무한모드 계속", labels)
        self.assertNotIn("앞으로", labels)
        embed = run_embed(run, None, bal)
        self.assertIn("꼭대기", embed.title)
        self.assertEqual(embed.color.value, 0xF1C40F)

    def test_the_infinite_screen_shows_the_floor(self):
        run, bal = at_summit()
        dungeon.go_infinite(run, Scripted(randoms=[NO_SECRET]), bal)
        self.assertIn("무한모드 — 11층", run_embed(run, None, bal).title)

    async def test_pressing_the_buttons_works_through_the_real_screen_path(self):
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        out = await service.enter(self.store, self.bot.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=dungeon.FLOORS, room=dungeon.ROOMS, phase="cleared", kind="boss")
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        i = self.w.interaction()
        await respond(i, self.bot, rev, "advance")
        shown = i.response.edit_message.await_args.kwargs
        self.assertIn("클리어하고 끝내기", self.labels(shown["view"]))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        j = self.w.interaction()
        await respond(j, self.bot, rev, "infinite")
        shown = j.response.edit_message.await_args.kwargs
        self.assertIn("클리어 보상", "\n".join(shown["embed"].description.split("\n")))
        self.assertIn("무한모드", shown["embed"].title)
        self.assertIsNotNone(shown["view"])                                                  # 계속 진행하는 화면
        self.assertEqual(self.store.wallet(G, U), 130)
