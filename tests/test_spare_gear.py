"""던전 장비 예비 칸: 장착한 무기·방패와 별도로 예비 무기 1개·예비 방패 1개를 이번 던전 동안 가지고 있다가 서로 바꾼다."""
import json
import random
import unittest

import dungeon
import dungeon_service as service
from balance import Balance, entity_dict
from cogs.dungeon import DungeonView, EquipView, equip_embed, open_equip, open_sell, run_embed
from dungeon import DungeonError, Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U, OTHER = 100, 7, 8


def run_with(**kw):
    run = dungeon.new_run()
    run.phase, run.room = "empty", 3
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class RuleTests(unittest.TestCase):
    def test_a_run_has_one_spare_weapon_and_one_spare_shield_and_old_saves_have_none(self):
        run = dungeon.new_run()
        self.assertEqual((run.spare_weapon, run.spare_shield), (None, None))
        old = json.loads(run.to_json())
        old.pop("spare_weapon"), old.pop("spare_shield")                      # 예비 칸이 생기기 전에 저장된 던전
        back = Run.from_json(json.dumps(old))
        self.assertEqual((back.spare_weapon, back.spare_shield), (None, None))
        run.spare_weapon, run.spare_shield = "dagger", "shield"
        back = Run.from_json(run.to_json())
        self.assertEqual((back.spare_weapon, back.spare_shield), ("dagger", "shield"))

    def test_swapping_exchanges_the_equipped_and_the_spare(self):
        run = run_with(weapon="club", spare_weapon="longsword")
        dungeon.swap_spare(run, "weapon")
        self.assertEqual((run.weapon, run.spare_weapon), ("longsword", "club"))
        self.assertIn("🔄 무기를 교체했어요", run.events[0])
        self.assertIn("낡은 몽둥이", run.events[1])
        dungeon.swap_spare(run, "weapon")                                      # 다시 바꾸면 원래대로
        self.assertEqual((run.weapon, run.spare_weapon), ("club", "longsword"))

    def test_weapons_and_shields_swap_separately(self):
        run = run_with(weapon="club", spare_weapon="dagger", shield="wooden_shield", spare_shield="shield")
        dungeon.swap_spare(run, "shield")
        self.assertEqual((run.weapon, run.spare_weapon, run.shield, run.spare_shield), ("club", "dagger", "shield", "wooden_shield"))

    def test_swapping_with_an_empty_side_puts_in_or_takes_out(self):
        run = run_with(weapon="club")
        dungeon.swap_spare(run, "weapon")                                      # 장착한 것을 예비에 넣기(맨손이 된다)
        self.assertEqual((run.weapon, run.spare_weapon), (None, "club"))
        self.assertIn("📥", run.events[0])
        self.assertIn("맨손", run.events[0])
        dungeon.swap_spare(run, "weapon")                                      # 예비를 꺼내 장착
        self.assertEqual((run.weapon, run.spare_weapon), ("club", None))
        self.assertIn("📤", run.events[0])
        run = run_with(shield="shield")
        dungeon.swap_spare(run, "shield")
        self.assertIn("맨몸 가드", run.events[0])

    def test_nothing_to_swap_is_an_error_that_changes_nothing(self):
        run = run_with()
        with self.assertRaisesRegex(DungeonError, "바꿀 무기가 없어요"):
            dungeon.swap_spare(run, "weapon")
        with self.assertRaisesRegex(DungeonError, "바꿀 방패가 없어요"):
            dungeon.swap_spare(run, "shield")
        with self.assertRaises(DungeonError):
            dungeon.swap_spare(run, "helmet")

    def test_no_swapping_in_a_fight_or_after_the_end(self):
        for phase in ("battle", "guard"):
            run = run_with(weapon="club", spare_weapon="dagger", phase=phase)
            with self.assertRaisesRegex(DungeonError, "싸우는 중"):
                dungeon.swap_spare(run, "weapon")
            self.assertEqual((run.weapon, run.spare_weapon), ("club", "dagger"))
        for phase in ("dead", "clear", "quit"):
            with self.assertRaises(DungeonError):
                dungeon.swap_spare(run_with(weapon="club", spare_weapon="dagger", phase=phase), "weapon")

    def test_the_equipped_weapon_really_changes_the_damage(self):
        run = run_with(weapon="club", spare_weapon="longsword")
        before = dungeon.player_damage(run)
        dungeon.swap_spare(run, "weapon")
        self.assertEqual((before, dungeon.player_damage(run)), (20 + 30, 20 + 100))

    def test_a_gear_the_admin_deleted_counts_as_empty(self):
        bal = Balance.build({}, {"gear": [entity_dict(g) for g in Balance.build({}).gear if g.key != "longsword"]})
        run = run_with(weapon="club", spare_weapon="longsword")
        dungeon.swap_spare(run, "weapon", bal)                                  # 사라진 예비 대신 장착한 것을 예비로 넣는다.
        self.assertEqual((run.weapon, run.spare_weapon), (None, "club"))
        run = run_with(weapon="longsword", spare_weapon="longsword")
        with self.assertRaises(DungeonError):
            dungeon.swap_spare(run, "weapon", bal)
        self.assertEqual((run.weapon, run.spare_weapon), (None, None))


class NewGearTests(unittest.TestCase):
    def test_equipping_moves_the_old_gear_to_a_free_spare_slot(self):
        run = run_with(weapon="club", pending_gear="longsword", phase="chest_done")
        dungeon.equip_pending(run)
        self.assertEqual((run.weapon, run.spare_weapon, run.pending_gear), ("longsword", "club", None))
        self.assertIn("예비 칸으로 옮겼어요", "\n".join(run.events))

    def test_equipping_loses_the_old_gear_only_when_the_spare_slot_is_taken(self):
        run = run_with(weapon="club", spare_weapon="dagger", pending_gear="longsword", phase="chest_done")
        dungeon.equip_pending(run)
        self.assertEqual((run.weapon, run.spare_weapon), ("longsword", "dagger"))      # 예비는 그대로
        self.assertIn("사라졌어요", "\n".join(run.events))

    def test_equipping_into_an_empty_slot_has_no_old_gear_to_move(self):
        run = run_with(pending_gear="shield", phase="chest_done")
        dungeon.equip_pending(run)
        self.assertEqual((run.shield, run.spare_shield), ("shield", None))
        self.assertEqual(len(run.events), 1)

    def test_a_weapon_goes_to_the_weapon_slot_and_a_shield_to_the_shield_slot(self):
        run = run_with(weapon="club", shield="wooden_shield", pending_gear="shield", phase="chest_done")
        dungeon.equip_pending(run)
        self.assertEqual((run.weapon, run.spare_weapon, run.shield, run.spare_shield), ("club", None, "shield", "wooden_shield"))

    def test_stash_puts_the_new_gear_in_the_spare_slot_and_keeps_the_equipped_one(self):
        run = run_with(weapon="club", pending_gear="longsword", phase="chest_done")
        dungeon.stash_pending(run)
        self.assertEqual((run.weapon, run.spare_weapon, run.pending_gear), ("club", "longsword", None))
        self.assertTrue(run.can_advance)                                       # 정했으니 앞으로 갈 수 있다.
        self.assertIn("📥", run.events[0])

    def test_stash_is_refused_when_that_spare_slot_is_full_and_nothing_changes(self):
        run = run_with(weapon="club", spare_weapon="dagger", pending_gear="longsword", phase="chest_done")
        with self.assertRaisesRegex(DungeonError, "예비 무기 칸이 이미 차 있어요"):
            dungeon.stash_pending(run)
        self.assertEqual((run.spare_weapon, run.pending_gear), ("dagger", "longsword"))
        run = run_with(pending_gear="shield", phase="chest_done", spare_weapon="dagger")      # 방패 칸은 비어 있어서 된다.
        dungeon.stash_pending(run)
        self.assertEqual((run.spare_shield, run.spare_weapon), ("shield", "dagger"))

    def test_stash_needs_a_pending_gear(self):
        with self.assertRaisesRegex(DungeonError, "넣을 장비가 없어요"):
            dungeon.stash_pending(run_with())

    def test_spare_gear_can_be_sold_and_is_listed_after_the_equipped_ones(self):
        run = run_with(weapon="dagger", spare_weapon="longsword", spare_shield="shield", items={"candy": 1})
        entries = dungeon.sellable(run)
        self.assertEqual([e["id"] for e in entries], ["weapon", "spare_weapon", "spare_shield", "item:candy"])
        self.assertEqual([e["tag"] for e in entries[:3]], ["장착 중", "예비", "예비"])
        total, sold = dungeon.sell(run, ["spare_weapon", "spare_shield"], "all")
        self.assertEqual(total, 15 + 15)
        self.assertEqual((run.spare_weapon, run.spare_shield, run.weapon), (None, None, "dagger"))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.ensure_defaults(G)

    def tearDown(self):
        self.store.close()

    def start(self, **kw):
        run = run_with(**kw)
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO dungeons VALUES(?,?,?)", (G, U, run.to_json()))
        return run

    def saved(self):
        return json.loads(self.store.dungeon_run(G, U))

    async def test_swap_and_stash_are_saved_and_move_to_a_new_screen(self):
        run = self.start(weapon="club", spare_weapon="longsword")
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "swap_weapon")
        self.assertTrue(out.ok)
        saved = self.saved()
        self.assertEqual((saved["weapon"], saved["spare_weapon"], saved["rev"]), ("longsword", "club", run.rev + 1))
        run = self.start(weapon="club", pending_gear="longsword", phase="chest_done")
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "stash")
        self.assertEqual((self.saved()["spare_weapon"], self.saved()["pending_gear"]), ("longsword", None))

    async def test_refusals_come_back_as_messages_and_leave_the_saved_run_alone(self):
        run = self.start(weapon="club", spare_weapon="dagger", pending_gear="longsword", phase="chest_done")
        before = self.store.dungeon_run(G, U)
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "stash")
        self.assertFalse(out.ok)
        self.assertIn("이미 차 있어요", out.text)
        run = self.start(weapon="club", spare_weapon="dagger", phase="battle", enemy={"name": "x"})
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "swap_weapon")
        self.assertFalse(out.ok)
        self.assertIn("싸우는 중", out.text)
        self.assertEqual(self.saved()["weapon"], "club")
        del before

    async def test_an_old_screen_cannot_swap(self):
        run = self.start(weapon="club", spare_weapon="dagger")
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev + 3, "swap_weapon")
        self.assertEqual((out.ok, out.stale), (False, True))
        self.assertEqual(self.saved()["weapon"], "club")

    async def test_spare_gear_is_lost_with_the_run(self):
        run = self.start(weapon="club", spare_weapon="dagger")
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "quit")
        self.assertEqual(out.ended, "quit")
        self.assertIsNone(self.store.dungeon_run(G, U))


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World(random.Random(2))
        self.store, self.bot = self.w.store, self.w.bot
        self.w.channel(400)
        self.w.new_member(OTHER)
        self.store.ensure_defaults(G)

    async def asyncTearDown(self):
        await self.w.close()

    def start(self, **kw):
        run = run_with(**kw)
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO dungeons VALUES(?,?,?)", (G, U, run.to_json()))
        return run

    def labels(self, view):
        return [getattr(c, "label", None) or type(c).__name__ for c in view.children]

    def edited(self, interaction):
        call = interaction.response.edit_message.await_args
        return call.kwargs["embed"], call.kwargs["view"]

    def button(self, view, label):
        return next(c for c in view.children if getattr(c, "label", "") == label)

    def test_the_equipment_button_shows_only_when_there_is_gear_and_not_in_a_fight(self):
        bal = self.store.balance(G)
        self.assertNotIn("장비", self.labels(DungeonView(self.bot, U, run_with(), bal)))
        for kw in (dict(weapon="club"), dict(shield="shield"), dict(spare_weapon="dagger"), dict(spare_shield="shield")):
            self.assertIn("장비", self.labels(DungeonView(self.bot, U, run_with(**kw), bal)), kw)
        fight = run_with(weapon="club", phase="battle", enemy={"name": "x", "emoji": "x", "hp": 1, "max_hp": 1, "boss": False})
        self.assertNotIn("장비", self.labels(DungeonView(self.bot, U, fight, bal)))

    def test_the_stash_button_appears_only_while_that_spare_slot_is_free(self):
        bal = self.store.balance(G)
        free = DungeonView(self.bot, U, run_with(pending_gear="longsword", phase="chest_done"), bal)
        self.assertIn("예비에 넣기", self.labels(free))
        full = DungeonView(self.bot, U, run_with(pending_gear="longsword", spare_weapon="dagger", phase="chest_done"), bal)
        self.assertNotIn("예비에 넣기", self.labels(full))
        shield = DungeonView(self.bot, U, run_with(pending_gear="shield", spare_weapon="dagger", phase="chest_done"), bal)
        self.assertIn("예비에 넣기", self.labels(shield))                           # 무기 예비가 차 있어도 방패 칸은 비어 있다.

    def test_every_screen_stays_within_discords_component_rules(self):
        bal = self.store.balance(G)
        enemy = {"name": "x", "emoji": "x", "hp": 1, "max_hp": 1, "boss": False}
        cases = [run_with(weapon="club", spare_weapon="dagger", shield="shield", spare_shield="wooden_shield", items={"candy": 2, "cookie": 1}),
                 run_with(pending_gear="longsword", phase="chest_done", weapon="club", spare_weapon="dagger", items={"candy": 1}),
                 run_with(pending_gear="shield", phase="chest_done", shield="wooden_shield", items={"candy": 1}),
                 run_with(phase="chest", weapon="club", spare_shield="shield", items={"candy": 1}),
                 run_with(phase="battle", weapon="club", spare_weapon="dagger", items={"candy": 1}, enemy=enemy)]
        for run in cases:
            DungeonView(self.bot, U, run, bal).to_components()
            EquipView(self.bot, U, run, bal).to_components()

    async def test_the_run_embed_shows_the_spare_slot_only_when_something_is_in_it(self):
        bal = self.store.balance(G)
        names = lambda run: [f.name for f in run_embed(run, None, bal).fields]
        self.assertNotIn("📦 예비", names(run_with(weapon="club")))
        embed = run_embed(run_with(weapon="club", spare_weapon="dagger"), None, bal)
        self.assertEqual({f.name: f.value for f in embed.fields}["📦 예비"], "🗡️ 단검 · 🛡️ — · 🧥 —")

    async def test_the_equipment_screen_lists_equipped_and_spare_gear(self):
        run = run_with(weapon="club", spare_weapon="longsword", shield="wooden_shield")
        embed = equip_embed(run, self.store.balance(G))
        fields = {f.name: f.value for f in embed.fields}
        self.assertIn("**장착**: 🏏 **낡은 몽둥이**", fields["⚔️ 무기"])
        self.assertIn("**예비**: ⚔️ **롱소드**", fields["⚔️ 무기"])
        self.assertIn("**장착**: 🪵 **나무 방패**", fields["🛡️ 방패"])
        self.assertIn("**예비**: 비어 있어요", fields["🛡️ 방패"])
        self.assertIn("무기·방패·갑옷을 1개씩", embed.description)
        bare = {f.name: f.value for f in equip_embed(run_with(), self.store.balance(G)).fields}
        self.assertIn("**장착**: 👊 맨손", bare["⚔️ 무기"])

    def test_the_swap_button_labels_say_what_will_happen(self):
        bal = self.store.balance(G)
        label = lambda **kw: self.labels(EquipView(self.bot, U, run_with(**kw), bal))[0]
        self.assertEqual(label(weapon="club", spare_weapon="dagger"), "무기 교체")
        self.assertEqual(label(weapon="club"), "무기 예비에 넣기")
        self.assertEqual(label(spare_weapon="dagger"), "무기 꺼내 장착")
        view = EquipView(self.bot, U, run_with(weapon="club"), bal)
        self.assertEqual([c.disabled for c in view.children], [False, True, True, False])        # 방패·갑옷은 바꿀 게 없다.

    async def test_open_the_screen_swap_through_it_and_come_back(self):
        run = self.start(weapon="club", spare_weapon="longsword")
        i = self.w.interaction()
        await open_equip(i, self.bot, run.rev)
        embed, view = self.edited(i)
        self.assertIsInstance(view, EquipView)
        self.assertEqual(embed.title, "🎒 장비")
        press = self.w.interaction()
        await self.button(view, "무기 교체").callback(press)
        embed, view = self.edited(press)
        self.assertIn("🔄 무기를 교체했어요", embed.description)
        self.assertIn("**장착**: ⚔️ **롱소드**", {f.name: f.value for f in embed.fields}["⚔️ 무기"])
        saved = json.loads(self.store.dungeon_run(G, U))
        self.assertEqual((saved["weapon"], saved["spare_weapon"]), ("longsword", "club"))
        press = self.w.interaction()
        await self.button(view, "무기 교체").callback(press)                              # 새 화면에서 다시 바꾸면 원래대로
        self.assertEqual(json.loads(self.store.dungeon_run(G, U))["weapon"], "club")
        back = self.w.interaction()
        await self.button(self.edited(press)[1], "돌아가기").callback(back)
        embed, view = self.edited(back)
        self.assertIsInstance(view, DungeonView)
        self.assertIn("1/10층", embed.title)

    async def test_it_does_not_open_in_a_fight_or_from_an_old_screen(self):
        run = self.start(weapon="club", spare_weapon="dagger", phase="battle",
                         enemy={"name": "x", "emoji": "x", "hp": 5, "max_hp": 5, "boss": False, "damage": 1, "dodge": 0, "stunned": False})
        i = self.w.interaction()
        await open_equip(i, self.bot, run.rev)
        self.assertIn("싸우는 중", sent_embed(i)[0].description)
        i.response.edit_message.assert_not_awaited()
        run = self.start(weapon="club", spare_weapon="dagger")
        i = self.w.interaction()
        await open_equip(i, self.bot, run.rev + 5)
        self.assertIn("지난 화면", sent_embed(i)[0].description)

    async def test_only_the_player_can_use_the_equipment_screen(self):
        run = self.start(weapon="club", spare_weapon="dagger")
        i = self.w.interaction()
        await open_equip(i, self.bot, run.rev)
        view = self.edited(i)[1]
        stranger = self.w.interaction(uid=OTHER)
        self.assertFalse(await view.interaction_check(stranger))
        self.assertIn("연 사람만", sent_embed(stranger)[0].description)
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)))

    async def test_stash_through_the_main_screen_buttons(self):
        run = self.start(weapon="club", pending_gear="longsword", phase="chest_done")
        view = DungeonView(self.bot, U, run, self.store.balance(G))
        press = self.w.interaction()
        await self.button(view, "예비에 넣기").callback(press)
        embed, new_view = self.edited(press)
        saved = json.loads(self.store.dungeon_run(G, U))
        self.assertEqual((saved["weapon"], saved["spare_weapon"], saved["pending_gear"]), ("club", "longsword", None))
        self.assertIn("앞으로", self.labels(new_view))
        self.assertIn("📦 예비", [f.name for f in embed.fields])

    async def test_selling_the_spare_gear_from_the_sell_screen(self):
        run = self.start(weapon="club", spare_weapon="longsword", items={"candy": 1})
        i = self.w.interaction()
        await open_sell(i, self.bot, run.rev)
        view = self.edited(i)[1]
        select = next(c for c in view.children if hasattr(c, "options"))
        self.assertIn("spare_weapon", [o.value for o in select.options])
        pick = self.w.interaction(data={"values": ["spare_weapon"]})
        await select.callback(pick)
        sell = self.w.interaction()
        await self.button(view, "선택한 것 판매").callback(sell)
        self.assertEqual(self.store.wallet(G, U), 15)
        saved = json.loads(self.store.dungeon_run(G, U))
        self.assertEqual((saved["weapon"], saved["spare_weapon"]), ("club", None))


class FullRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_whole_dungeon_with_swaps_still_reaches_the_end(self):
        """상자에서 나온 장비를 예비에 넣고 중간중간 장비를 바꾸면서 10층까지 끝까지 해도 오류 없이 클리어된다."""
        from cogs import dungeon as dungeon_cog
        w = World(random.Random(5))
        old_delay, dungeon_cog.SECRET_FRAME_DELAY = dungeon_cog.SECRET_FRAME_DELAY, 0                  # 시크릿 방 연출을 기다리지 않는다
        try:
            store, bot = w.store, w.bot
            store.ensure_defaults(G)
            store.update_settings(G, dungeon={"일반.맨손공격력": 500, "일반.최대체력": 5000, "상점.간격": 0})
            store.add_item(G, U, store.ensure_system_items(G)["key"], 1)
            from cogs.dungeon import Dungeon
            i = w.interaction()
            await Dungeon.enter.callback(Dungeon(bot), i)
            view = i.response.send_message.await_args.kwargs["view"]
            swaps = stashes = 0
            for step in range(3000):
                view.to_components()
                run = Run.from_json(store.dungeon_run(G, U))
                btn = lambda label: next((c for c in view.children if getattr(c, "label", None) == label), None)
                if run.pending_gear and btn("예비에 넣기") and step % 2 == 0:
                    pick, stashes = btn("예비에 넣기"), stashes + 1
                elif run.phase == "battle":
                    pick = btn("공격")
                elif run.phase == "chest":
                    pick = btn("상자 열기")
                elif run.pending_gear:
                    pick = btn("장착") or btn("버리기")
                else:
                    if btn("장비") and step % 7 == 0:                           # 가끔 장비 화면에서 바꿔 본다.
                        s = w.interaction()
                        await btn("장비").callback(s)
                        equip_view = s.response.edit_message.await_args.kwargs["view"]
                        for slot in ("무기", "방패"):                              # 한 번 바꾸면 화면이 새로 나오므로 늘 최신 화면의 버튼을 쓴다.
                            b = next(c for c in equip_view.children if getattr(c, "label", "").startswith(slot))
                            if not b.disabled:
                                press = w.interaction()
                                await b.callback(press)
                                swaps += 1
                                equip_view = press.response.edit_message.await_args.kwargs["view"]
                        back = w.interaction()
                        await next(c for c in equip_view.children if c.label == "돌아가기").callback(back)
                        view = back.response.edit_message.await_args.kwargs["view"]
                        continue
                    pick = btn("앞으로") or btn("출발") or btn("클리어하고 끝내기")
                inter = w.interaction()
                await pick.callback(inter)
                shown = inter.edit_original_response.await_args or inter.response.edit_message.await_args       # 시크릿 방 연출 뒤에는 마지막으로 고친 화면이 현재 화면이다
                kwargs = shown.kwargs
                if kwargs["view"] is None:
                    self.assertIn("클리어", kwargs["embed"].title)
                    break
                view = kwargs["view"]
            else:
                self.fail("끝나지 않았어요")
            self.assertGreater(swaps, 0)
            self.assertGreater(stashes, 0)
        finally:
            dungeon_cog.SECRET_FRAME_DELAY = old_delay
            await w.close()


if __name__ == "__main__":
    unittest.main()
