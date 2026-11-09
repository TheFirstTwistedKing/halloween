"""빠른진행: 던전에 들어간 뒤 입장 물품을 한 번 더 내면 컴퓨터가 알아서 라이브로 진행한다(껐다 켤 수 있다)."""
import asyncio
import json
import random
import unittest
from unittest.mock import AsyncMock

import balance
import content
import dungeon
import dungeon_auto
import dungeon_service as service
from balance import Balance, check_entity, entity_dict
from cogs import dungeon as cog
from cogs.dungeon import AutoConfirm, AutoView, DungeonView, ask_auto, auto_embed, start_auto
from dungeon import Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World

G, U = 100, 7
BAL = Balance.build({})


def run_with(**kw):
    run = dungeon.new_run(bal=BAL)
    run.phase, run.room = "cleared", 3
    for k, v in kw.items():
        setattr(run, k, v)
    return run


def fight(**kw):
    enemy = dungeon._spawn(balance.MONSTERS[1], 1, BAL)
    enemy.update(kw.pop("enemy", {}))
    return run_with(phase="battle", kind="monster", enemy=enemy, **kw)


class PolicyTests(unittest.TestCase):
    def test_chests_are_opened(self):
        self.assertEqual(dungeon_auto.choose(run_with(phase="chest", kind="chest"), BAL), ("chest", None))

    def test_only_better_gear_is_equipped(self):
        weapon = lambda key, **kw: run_with(pending_gear=key, phase="chest_done", **kw)
        self.assertEqual(dungeon_auto.choose(weapon("longsword", weapon="club"), BAL), ("equip", None))       # 플라즈마 블레이드 > 몽둥이
        self.assertEqual(dungeon_auto.choose(weapon("club", weapon="longsword"), BAL), ("discard", None))
        self.assertEqual(dungeon_auto.choose(weapon("club"), BAL), ("equip", None))                          # 맨손보다는 낫다
        self.assertEqual(dungeon_auto.choose(weapon("shield", shield="wooden_shield"), BAL), ("equip", None))
        self.assertEqual(dungeon_auto.choose(weapon("wooden_shield", shield="shield"), BAL), ("discard", None))
        self.assertEqual(dungeon_auto.choose(weapon("gone"), BAL), ("discard", None))                         # 사라진 장비

    def test_shields_are_compared_with_their_own_guard_chance(self):
        raw = [entity_dict(check_entity("gear", dict(key=k, name=k, emoji="🛡️", slot="shield", tier="일반", weight=1, guard=g, guard_chance=c))[0])
               for k, g, c in (("sturdy", 90, 10), ("steady", 60, 100))]
        bal = Balance.build({}, {"gear": raw})                                                               # 90×10% = 9 < 60×100% = 60
        run = run_with(pending_gear="steady", phase="chest_done", shield="sturdy")
        self.assertEqual(dungeon_auto.choose(run, bal), ("equip", None))
        run = run_with(pending_gear="sturdy", phase="chest_done", shield="steady")
        self.assertEqual(dungeon_auto.choose(run, bal), ("discard", None))

    def test_battle_choices(self):
        self.assertEqual(dungeon_auto.choose(fight(hp=BAL.max_hp), BAL), ("attack", None))
        self.assertEqual(dungeon_auto.choose(fight(hp=BAL.max_hp * 59 // 100), BAL), ("defend", None))
        self.assertEqual(dungeon_auto.choose(fight(hp=BAL.max_hp * 60 // 100), BAL), ("attack", None))
        self.assertEqual(dungeon_auto.choose(fight(hp=100, enemy={"stunned": True}), BAL), ("attack", None))   # 기절한 상대는 때린다

    def test_in_danger_it_heals_if_it_can_and_not_when_the_enemy_is_stunned(self):
        run = fight(hp=50, items={"candy": 1, "golden_pumpkin_soup": 1})
        action, key = dungeon_auto.choose(run, BAL)
        self.assertEqual(action, "item")
        self.assertIn(key, run.items)
        run.enemy["stunned"] = True
        self.assertEqual(dungeon_auto.choose(run, BAL), ("attack", None))
        self.assertEqual(dungeon_auto.choose(fight(hp=50), BAL), ("defend", None))                           # 아이템이 없으면 방어

    def test_outside_a_fight_it_heals_only_when_worth_it_then_moves_on(self):
        self.assertEqual(dungeon_auto.choose(run_with(hp=BAL.max_hp), BAL), ("advance", None))
        run = run_with(hp=10, items={"candy": 1})                                                            # 사탕 +50, 잃은 HP가 더 크다
        self.assertEqual(dungeon_auto.choose(run, BAL), ("item", "candy"))
        run = run_with(hp=BAL.max_hp - 10, items={"candy": 1})                                               # 조금 잃었다: 아낀다
        self.assertEqual(dungeon_auto.choose(run, BAL), ("advance", None))
        self.assertEqual(dungeon_auto.choose(run_with(phase="empty", kind="empty"), BAL), ("advance", None))

    def test_conditional_items_are_used_only_when_their_condition_holds(self):
        item = check_entity("consumables", dict(key="c", name="조건약", emoji="💊", tier="일반", weight=1, heal=900, conditions=[["hp_le", 20]]))[0]
        bal = Balance.build({}, {"consumables": [entity_dict(item)]})
        low = run_with(hp=bal.max_hp * 10 // 100, items={"c": 1})
        self.assertEqual(dungeon_auto.choose(low, bal), ("item", "c"))
        mid = run_with(hp=bal.max_hp * 40 // 100, items={"c": 1})                                            # 조건이 안 맞는 약은 쓰지 않는다
        self.assertEqual(dungeon_auto.choose(mid, bal), ("advance", None))

    def test_upgrade_items_are_used_only_if_the_weapon_gets_stronger(self):
        mat = check_entity("consumables", dict(key="m", name="숫돌", emoji="🪨", tier="희귀", weight=1, material=True))[0]
        raw = [{"id": 1, "weapon": "club", "item": "m", "result": "longsword"}, {"id": 2, "weapon": "longsword", "item": "m", "result": "club"}]
        bal = Balance.build({}, {"consumables": [entity_dict(mat)]}, raw)
        self.assertEqual(dungeon_auto.choose(run_with(weapon="club", items={"m": 1}), bal), ("item", "m"))
        self.assertEqual(dungeon_auto.choose(run_with(weapon="longsword", items={"m": 1}), bal), ("advance", None))   # 약해지는 개조는 안 한다
        fixed = check_entity("consumables", dict(key="f", name="개조약", emoji="🔧", tier="희귀", weight=1, modify="longsword"))[0]
        bal = Balance.build({}, {"consumables": [entity_dict(fixed)]})
        self.assertEqual(dungeon_auto.choose(run_with(weapon="club", items={"f": 1}), bal), ("item", "f"))
        self.assertEqual(dungeon_auto.choose(run_with(weapon="longsword", items={"f": 1}), bal), ("advance", None))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.key = self.store.ensure_system_items(G)["key"]
        self.store.add_item(G, U, self.key, 3)

    def tearDown(self):
        self.store.close()

    async def enter(self):
        out = await service.enter(self.store, self.locks, G, U)
        self.assertTrue(out.ok)
        return out.run

    def keys(self):
        return self.store.qty(G, U, self.key)

    def saved(self):
        return Run.from_json(self.store.dungeon_run(G, U))

    async def test_turning_it_on_costs_one_more_entry_item_once_per_dungeon(self):
        run = await self.enter()
        self.assertEqual(self.keys(), 2)
        out = await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        self.assertTrue(out.ok)
        self.assertEqual((self.keys(), out.run.auto, out.run.auto_paid, out.notes), (1, True, True, ["🗝️ 열쇠 1개"]))
        self.assertIn("빠른진행", "\n".join(out.run.events))
        self.assertEqual((self.saved().auto, self.saved().rev), (True, out.run.rev))
        out = await service.set_auto(self.store, self.locks, G, U, 0, False)
        self.assertEqual((out.ok, self.saved().auto, self.keys()), (True, False, 1))
        out = await service.set_auto(self.store, self.locks, G, U, self.saved().rev, True)             # 껐다 켜기: 다시 안 낸다
        self.assertEqual((out.ok, self.saved().auto, self.keys(), out.notes), (True, True, 1, [""]))

    async def test_without_enough_entry_items_nothing_changes(self):
        self.store.add_item(G, U, self.key, -2)                                                      # 입장에 쓰고 나면 0개
        run = await self.enter()
        self.assertEqual(self.keys(), 0)
        out = await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        self.assertFalse(out.ok)
        self.assertIn("필요해요", out.text)
        self.assertEqual((self.saved().auto, self.saved().auto_paid, self.saved().rev), (False, False, run.rev))

    async def test_the_charge_is_undone_if_there_is_no_dungeon_to_save_into(self):
        from store import StoreError
        with self.assertRaises(StoreError):
            self.store.start_auto(G, U, "{}", True)                                                  # 던전 기록이 없다: 낸 입장 물품도 돌려받는다
        self.assertEqual(self.keys(), 3)

    async def test_a_free_dungeon_has_a_free_auto_mode(self):
        self.store.update_settings(G, entry_item=None)
        run = await self.enter()
        out = await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        self.assertEqual((out.ok, out.notes, self.keys()), (True, [""], 3))

    async def test_a_stale_screen_cannot_turn_it_on_but_can_always_turn_it_off(self):
        run = await self.enter()
        out = await service.set_auto(self.store, self.locks, G, U, run.rev + 7, True)
        self.assertEqual((out.ok, out.stale, self.keys()), (False, True, 2))
        on = await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        off = await service.set_auto(self.store, self.locks, G, U, 0, False)                          # 아무 화면 번호로도 끌 수 있다
        self.assertTrue(off.ok)
        self.assertFalse(self.saved().auto)
        self.assertTrue((await service.set_auto(self.store, self.locks, G, U, 0, False)).ok)           # 이미 꺼져 있어도 괜찮다

    async def test_no_dungeon_means_no_auto(self):
        for on in (True, False):
            out = await service.set_auto(self.store, self.locks, G, U, 0, on)
            self.assertEqual((out.ok, out.stale), (False, True))

    async def test_people_cannot_act_while_the_computer_plays_except_quitting(self):
        run = await self.enter()
        on = await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        out = await service.act(self.store, self.locks, Scripted(), G, U, on.run.rev, "advance")
        self.assertFalse(out.ok)
        self.assertEqual(out.text, service.AUTO_REFUSE)
        self.assertEqual(self.saved().room, 0)
        sold = await service.sell(self.store, self.locks, G, U, on.run.rev, ["pending"], "all")
        self.assertEqual((sold.ok, sold.text), (False, service.AUTO_REFUSE))
        out = await service.act(self.store, self.locks, Scripted(), G, U, on.run.rev, "quit")
        self.assertEqual((out.ok, out.ended), (True, "quit"))
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_a_computer_action_is_refused_once_it_was_turned_off(self):
        run = await self.enter()
        on = await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        off = await service.set_auto(self.store, self.locks, G, U, 0, False)
        out = await service.act(self.store, self.locks, Scripted(), G, U, off.run.rev, "advance", auto=True)
        self.assertEqual((out.ok, out.stale), (False, True))
        step = await service.auto_step(self.store, self.locks, Scripted(), G, U)
        self.assertEqual((step.ok, step.stale), (False, True))
        self.assertEqual(self.saved().room, 0)

    async def test_an_auto_step_does_what_a_person_would_have_done(self):
        run = await self.enter()
        await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        step = await service.auto_step(self.store, self.locks, random.Random(1), G, U)
        self.assertTrue(step.ok)
        self.assertEqual(self.saved().room, 1)                                                       # 출발했다
        self.assertTrue(self.saved().auto)

    async def test_opening_dungeon_again_turns_a_stale_auto_off_unless_it_is_really_running(self):
        run = await self.enter()
        await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        out = await service.enter(self.store, self.locks, G, U, auto_running=True)
        self.assertTrue(out.run.auto)
        out = await service.enter(self.store, self.locks, G, U)                                      # 돌리는 작업이 없다(봇이 다시 켜졌다)
        self.assertFalse(out.run.auto)
        self.assertIn("빠른진행이 멈춰 있어요", "\n".join(out.run.events))
        self.assertTrue(self.saved().auto_paid)                                                      # 낸 비용은 기억한다
        again = await service.set_auto(self.store, self.locks, G, U, out.run.rev, True)
        self.assertEqual((again.ok, self.keys()), (True, 1))

    async def test_old_saved_dungeons_have_no_auto(self):
        run = dungeon.new_run()
        data = json.loads(run.to_json())
        data.pop("auto"), data.pop("auto_paid")
        back = Run.from_json(json.dumps(data))
        self.assertEqual((back.auto, back.auto_paid), (False, False))

    async def test_the_computer_plays_whole_dungeons_to_the_end(self):
        """여러 판을 끝까지: 한 걸음도 막히거나 오류 없이 끝나(클리어 또는 쓰러짐), 끝나면 던전 기록이 지워진다."""
        results = set()
        for seed in range(6):
            store, locks = Store(":memory:"), Locks()
            store.add_item(G, U, store.ensure_system_items(G)["key"], 2)
            run = (await service.enter(store, locks, G, U)).run
            await service.set_auto(store, locks, G, U, run.rev, True)
            rng, steps = random.Random(seed), 0
            while True:
                out = await service.auto_step(store, locks, rng, G, U)
                steps += 1
                self.assertTrue(out.ok, (seed, steps, out.text))
                self.assertLess(steps, 20000, seed)
                if out.ended:
                    results.add(out.ended)
                    break
            self.assertIsNone(store.dungeon_run(G, U))
            self.assertEqual(store.qty(G, U, store.ensure_system_items(G)["key"]), 0)                # 입장 + 빠른진행 = 열쇠 2개를 썼다
            store.close()
        self.assertTrue(results <= {"clear", "dead"})


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World(rng=random.Random(3))
        self.bot, self.store = self.w.bot, self.w.store
        self.key = self.store.ensure_system_items(G)["key"]
        self.store.add_item(G, U, self.key, 2)
        self.old = (cog.AUTO_DELAY, cog.AUTO_MAX_SECONDS, cog.SECRET_FRAME_DELAY)
        cog.AUTO_DELAY = cog.SECRET_FRAME_DELAY = 0
        out = await service.enter(self.store, self.bot.locks, G, U)
        self.rev = out.run.rev

    async def asyncTearDown(self):
        cog.AUTO_DELAY, cog.AUTO_MAX_SECONDS, cog.SECRET_FRAME_DELAY = self.old
        for task in list(self.bot.autos.values()):
            task.cancel()
        await self.w.close()

    def keys(self):
        return self.store.qty(G, U, self.key)

    def labels(self, view):
        return [c.label for c in view.children if getattr(c, "label", None)]

    async def finish(self):
        for task in list(self.bot.autos.values()):
            await task

    def saved(self):
        return Run.from_json(self.store.dungeon_run(G, U))

    def test_the_button_is_on_the_dungeon_screen_only_while_it_is_off(self):
        view = DungeonView(self.bot, U, run_with(), self.store.balance(G))
        self.assertIn("빠른진행", self.labels(view))
        view.to_components()
        self.assertNotIn("빠른진행", self.labels(DungeonView(self.bot, U, run_with(auto=True), self.store.balance(G))))
        self.assertEqual(self.labels(AutoView(self.bot, U)), ["멈추기"])

    async def test_the_first_press_asks_before_spending_anything(self):
        i = self.w.interaction()
        await ask_auto(i, self.bot, self.rev)
        call = i.response.edit_message.await_args
        self.assertIsInstance(call.kwargs["view"], AutoConfirm)
        self.assertIn("한 번 더", call.kwargs["embed"].description)
        self.assertIn("열쇠", call.kwargs["embed"].description)
        self.assertEqual((self.keys(), self.saved().auto, self.bot.autos), (1, False, {}))

    async def test_cancelling_goes_back_to_the_dungeon_screen(self):
        i = self.w.interaction()
        await ask_auto(i, self.bot, self.rev)
        confirm = i.response.edit_message.await_args.kwargs["view"]
        j = self.w.interaction()
        await confirm.cancel(j)
        self.assertIsInstance(j.response.edit_message.await_args.kwargs["view"], DungeonView)
        self.assertEqual((self.keys(), self.saved().auto), (1, False))

    async def test_confirming_spends_one_key_and_plays_live_until_the_end(self):
        i = self.w.interaction()
        await ask_auto(i, self.bot, self.rev)
        confirm = i.response.edit_message.await_args.kwargs["view"]
        j = self.w.interaction()
        await confirm.confirm(j)
        shown = j.response.edit_message.await_args.kwargs
        self.assertIsInstance(shown["view"], AutoView)
        self.assertIn("빠른진행 중", shown["embed"].footer.text)
        self.assertEqual(self.keys(), 0)
        self.assertEqual(len(self.bot.autos), 1)
        await self.finish()
        self.assertGreater(j.edit_original_response.await_count, 3)                                  # 라이브로 여러 번 화면을 고쳤다
        last = j.edit_original_response.await_args.kwargs
        self.assertIsNone(last["view"])                                                              # 끝난 화면에는 버튼이 없다
        self.assertIsNone(self.store.dungeon_run(G, U))
        self.assertEqual(self.bot.autos, {})
        self.assertEqual(self.keys(), 0)                                                             # 더 쓰지 않았다

    async def test_without_a_key_it_says_so_and_starts_nothing(self):
        self.store.add_item(G, U, self.key, -1)
        i = self.w.interaction()
        await start_auto(i, self.bot, self.rev)
        self.assertIn("필요해요", i.response.send_message.await_args.kwargs["embed"].description)
        self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"])
        self.assertEqual((self.saved().auto, self.bot.autos), (False, {}))

    async def test_when_the_cost_is_already_paid_it_starts_without_asking(self):
        await service.set_auto(self.store, self.bot.locks, G, U, self.rev, True)                     # 한 번 켜서 비용을 냈다
        await service.set_auto(self.store, self.bot.locks, G, U, 0, False)
        self.assertEqual(self.keys(), 0)
        j = self.w.interaction()
        await ask_auto(j, self.bot, self.saved().rev)
        self.assertIsInstance(j.response.edit_message.await_args.kwargs["view"], AutoView)           # 확인 화면 없이 바로
        self.assertEqual(self.keys(), 0)                                                             # 다시 안 낸다
        await self.finish()

    async def test_stopping_hands_control_back_with_the_normal_screen(self):
        cog.AUTO_DELAY = 0.05
        j = self.w.interaction()
        await start_auto(j, self.bot, self.rev)
        view = j.response.edit_message.await_args.kwargs["view"]
        k = self.w.interaction()
        await view.stop_auto(k)
        self.assertIn("멈추는 중", k.response.edit_message.await_args.kwargs["embed"].description)
        await self.finish()
        self.assertFalse(self.saved().auto)
        last = j.edit_original_response.await_args.kwargs
        self.assertIsInstance(last["view"], DungeonView)
        self.assertIn("멈췄어요", last["embed"].description)
        self.assertEqual(self.bot.autos, {})

    async def test_stop_without_a_running_task_just_restores_the_screen(self):
        await service.set_auto(self.store, self.bot.locks, G, U, self.rev, True)                      # 봇이 다시 켜져서 작업이 사라진 상태
        k = self.w.interaction()
        await AutoView(self.bot, U).stop_auto(k)
        self.assertFalse(self.saved().auto)
        self.assertIsInstance(k.response.edit_message.await_args.kwargs["view"], DungeonView)

    async def test_stopping_after_the_dungeon_ended_says_so(self):
        await service.act(self.store, self.bot.locks, Scripted(), G, U, self.rev, "quit")
        k = self.w.interaction()
        await AutoView(self.bot, U).stop_auto(k)
        self.assertIn("끝났어요", k.response.edit_message.await_args.kwargs["embed"].description)

    async def test_only_the_owner_can_press_the_buttons(self):
        view = AutoView(self.bot, U)
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=8)))
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)))

    async def test_opening_dungeon_while_it_runs_shows_a_stop_screen(self):
        from cogs.dungeon import Dungeon
        await service.set_auto(self.store, self.bot.locks, G, U, self.rev, True)
        gate = asyncio.get_running_loop().create_future()
        self.bot.autos[(G, U)] = asyncio.ensure_future(gate)                                          # 돌고 있는 작업인 척
        i = self.w.interaction()
        await Dungeon.enter.callback(Dungeon(self.bot), i)
        shown = i.response.send_message.await_args.kwargs
        self.assertIsInstance(shown["view"], AutoView)
        self.assertTrue(shown["ephemeral"])
        self.assertTrue(self.saved().auto)
        gate.set_result(None)
        await asyncio.sleep(0)

    async def test_opening_dungeon_after_a_restart_turns_the_stale_auto_off(self):
        from cogs.dungeon import Dungeon
        await service.set_auto(self.store, self.bot.locks, G, U, self.rev, True)
        i = self.w.interaction()
        await Dungeon.enter.callback(Dungeon(self.bot), i)
        shown = i.response.send_message.await_args.kwargs
        self.assertIsInstance(shown["view"], DungeonView)
        self.assertFalse(self.saved().auto)

    async def test_it_pauses_before_discords_fifteen_minute_limit(self):
        cog.AUTO_MAX_SECONDS = -1
        j = self.w.interaction()
        await start_auto(j, self.bot, self.rev)
        await self.finish()
        last = j.edit_original_response.await_args.kwargs
        self.assertIn("15분", last["embed"].fields[-1].value)
        self.assertIsInstance(last["view"], DungeonView)
        self.assertEqual((self.saved().auto, self.saved().auto_paid, self.bot.autos), (False, True, {}))

    async def test_if_the_message_cannot_be_edited_it_turns_itself_off_safely(self):
        j = self.w.interaction()
        j.edit_original_response = AsyncMock(side_effect=RuntimeError("Unknown Webhook"))
        await start_auto(j, self.bot, self.rev)
        await self.finish()
        self.assertEqual((self.saved().auto, self.bot.autos), (False, {}))                              # 던전은 그대로 남아 /던전으로 이어서 한다

    async def test_a_step_the_computer_cannot_do_stops_it_with_a_reason(self):
        original = dungeon_auto.choose
        dungeon_auto.choose = lambda run, bal: ("attack", None)                                       # 싸움이 없는데 공격하라고 하는 고장난 판단
        try:
            j = self.w.interaction()
            await start_auto(j, self.bot, self.rev)
            await self.finish()
        finally:
            dungeon_auto.choose = original
        last = j.edit_original_response.await_args.kwargs
        self.assertIn("싸울 상대가 없어요", last["embed"].fields[-1].value)
        self.assertEqual((self.saved().auto, self.bot.autos), (False, {}))

    def test_the_auto_embed_has_a_footer_only_while_playing(self):
        run = run_with()
        self.assertIn("빠른진행 중", auto_embed(run).footer.text)
        done = service.Outcome(True, run=run, ended="dead")
        self.assertNotIn("빠른진행 중", auto_embed(run, done).footer.text or "")
