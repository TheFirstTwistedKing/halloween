"""대량 삭제: 모든 삭제 명령어에 '여러 개 골라 한 번에 지우기'가 있고, 확인 화면을 거치며, 못 지우는 것은 이유와 함께 건너뛴다."""
import unittest
from unittest.mock import AsyncMock

import discord

import balance
import content
from bulk import PAGE, BulkDelete, BulkEntry, run_deletions
from cogs.alchemy import Alchemy
from cogs.content_admin import ContentAdmin
from cogs.rules_admin import RuleError, RulesAdmin, remove_clear_reward, remove_package_entry
from cogs.shop import Shop
from store import StoreError
from tests.helpers import World, sent_embed

G, U = 100, 7


def embed_size(e):
    return len(e.title or "") + len(e.description or "") + sum(len(f.name) + len(f.value) for f in e.fields) + len(e.footer.text or "")


class RunTests(unittest.TestCase):
    def test_everything_deleted_in_order(self):
        seen = []
        deleted, failed = run_deletions(["a", "b", "c"], lambda v: seen.append(v) or f"메모 {v}", (ValueError,))
        self.assertEqual((deleted, failed, seen), ([("a", "메모 a"), ("b", "메모 b"), ("c", "메모 c")], [], ["a", "b", "c"]))

    def test_a_failure_is_reported_with_its_reason_and_the_rest_still_go(self):
        def delete(v):
            if v == "b":
                raise ValueError("다른 설정이 쓰고 있어요")
        deleted, failed = run_deletions(["a", "b", "c"], delete, (ValueError,))
        self.assertEqual([v for v, _ in deleted], ["a", "c"])
        self.assertEqual(failed, [("b", "다른 설정이 쓰고 있어요")])

    def test_something_blocked_by_another_pick_is_retried_after_that_pick_goes(self):
        alive = {"recipe", "gear"}

        def delete(v):
            if v == "gear" and "recipe" in alive:
                raise ValueError("레시피가 쓰고 있어요")
            alive.discard(v)
        deleted, failed = run_deletions(["gear", "recipe"], delete, (ValueError,))                   # 장비를 먼저 골라도 된다
        self.assertEqual(([v for v, _ in deleted], failed), (["recipe", "gear"], []))

    def test_it_stops_when_nothing_more_can_go(self):
        calls = []

        def delete(v):
            calls.append(v)
            raise ValueError("안 돼요")
        deleted, failed = run_deletions(["a", "b"], delete, (ValueError,))
        self.assertEqual((deleted, failed), ([], [("a", "안 돼요"), ("b", "안 돼요")]))
        self.assertEqual(calls, ["a", "b"])                                                           # 진전이 없으면 다시 시도하지 않는다

    def test_other_errors_are_not_swallowed(self):
        def delete(v):
            raise KeyError("버그")
        with self.assertRaises(KeyError):
            run_deletions(["a"], delete, (ValueError,))

    def test_empty(self):
        self.assertEqual(run_deletions([], lambda v: None, (ValueError,)), ([], []))


class ViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World()
        self.entries = [BulkEntry(str(i), f"항목 {i:02d}", "⚙️", f"설명 {i}") for i in range(60)]
        self.deleted = []

    async def asyncTearDown(self):
        await self.w.close()

    def view(self, **kw):
        def delete(v):
            if v == "7":
                raise ValueError("7번은 못 지워요")
            self.deleted.append(v)
            return "메모" if v == "1" else ""
        return BulkDelete(U, "테스트", self.entries, delete, (ValueError,), **kw)

    def select(self, view):
        return next(c for c in view.children if isinstance(c, discord.ui.Select))

    def button(self, view, label):
        return next(c for c in view.children if getattr(c, "label", None) == label)

    async def pick(self, view, *values):
        i = self.w.interaction()
        i.data = {"values": list(values)}
        await view.on_select(i)
        return i

    def test_the_menu_holds_25_per_page_and_is_valid_for_discord(self):
        view = self.view()
        view.to_components()
        self.assertEqual(len(self.select(view).options), PAGE)
        self.assertEqual(len(view.pages), 3)
        self.assertIn("1/3쪽", self.select(view).placeholder)
        self.assertTrue(self.button(view, "이전 쪽").disabled)
        self.assertTrue(self.button(view, "삭제").disabled)                                          # 아무것도 안 골랐으면 못 누른다

    async def test_picks_survive_page_turns(self):
        view = self.view()
        await self.pick(view, "0", "3")
        i = self.w.interaction()
        await view.turn(+1)(i)
        self.assertEqual(view.page, 1)
        await self.pick(view, "30")
        await view.turn(-1)(self.w.interaction())
        defaults = {o.value for o in self.select(view).options if o.default}
        self.assertEqual(defaults, {"0", "3"})                                                        # 돌아오면 고른 표시가 남는다
        self.assertEqual(set(view.picks), {"0", "3", "30"})
        await self.pick(view, "0")                                                                    # 이 쪽에서 3번을 해제
        self.assertEqual(set(view.picks), {"0", "30"})
        self.assertIn("2개", view.embed().description)

    async def test_select_all_and_clear(self):
        view = self.view()
        await view.select_all(self.w.interaction())
        self.assertEqual(len(view.picks), 60)
        await view.clear_picks(self.w.interaction())
        self.assertEqual(view.picks, [])

    async def test_asking_with_nothing_picked_is_refused_privately(self):
        view = self.view()
        i = self.w.interaction()
        await view.ask(i)
        self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"])
        self.assertFalse(view.confirming)

    async def test_the_confirm_screen_lists_what_will_go_and_back_keeps_the_picks(self):
        view = self.view()
        await self.pick(view, "5", "2")
        i = self.w.interaction()
        await view.ask(i)
        embed = i.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("2개", embed.title)
        self.assertIn("항목 02", embed.description)
        self.assertLess(embed.description.index("항목 02"), embed.description.index("항목 05"))      # 목록 순서대로
        self.assertEqual([c.label for c in view.children], ["삭제", "돌아가기", "취소"])
        await view.back(self.w.interaction())
        self.assertFalse(view.confirming)
        self.assertEqual(set(view.picks), {"2", "5"})
        self.assertEqual(self.deleted, [])                                                            # 확인 전에는 아무것도 안 지웠다

    async def test_cancel_deletes_nothing(self):
        view = self.view()
        await self.pick(view, "1")
        i = self.w.interaction()
        await view.cancel(i)
        self.assertIn("취소", i.response.edit_message.await_args.kwargs["embed"].description)
        self.assertEqual(self.deleted, [])

    async def test_running_deletes_reports_and_calls_after_once(self):
        after = AsyncMock()
        view = self.view(after=after)
        await self.pick(view, "1", "7", "9")
        await view.ask(self.w.interaction())
        i = self.w.interaction()
        await view.run(i)
        embed = i.response.edit_message.await_args.kwargs["embed"]
        self.assertEqual(self.deleted, ["1", "9"])
        self.assertIn("2개", embed.description)
        self.assertIn("못 지운 것 **1개**", embed.description)
        fields = {f.name: f.value for f in embed.fields}
        self.assertIn("항목 01** — 메모", fields["지운 것"])
        self.assertIn("항목 07** — 7번은 못 지워요", fields["못 지운 것과 이유"])
        self.assertIsNone(i.response.edit_message.await_args.kwargs["view"])
        after.assert_awaited_once()

    async def test_nothing_deleted_means_no_after_and_a_red_result(self):
        after = AsyncMock()
        view = self.view(after=after)
        await self.pick(view, "7")
        await view.ask(self.w.interaction())
        i = self.w.interaction()
        await view.run(i)
        embed = i.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("하나도 못 지웠어요", embed.title)
        after.assert_not_awaited()

    async def test_a_long_result_still_fits_discords_limits(self):
        view = self.view()
        await view.select_all(self.w.interaction())
        await view.ask(self.w.interaction())
        confirm = view.embed()
        self.assertLessEqual(embed_size(confirm), 6000)
        i = self.w.interaction()
        await view.run(i)
        embed = i.response.edit_message.await_args.kwargs["embed"]
        self.assertLessEqual(embed_size(embed), 6000)
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))

    async def test_only_the_admin_who_opened_it_can_use_it(self):
        view = self.view()
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=8)))
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)))

    async def test_warn_text_is_shown_on_the_confirm_screen(self):
        view = self.view(warn=lambda chosen: f"⚠️ 경고 {len(chosen)}")
        await self.pick(view, "1", "2")
        await view.ask(self.w.interaction())
        self.assertIn("⚠️ 경고 2", view.embed().description)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.w.channel(400)
        self.content = ContentAdmin(self.bot)
        self.rules = RulesAdmin(self.bot)
        self.alchemy = Alchemy(self.bot)
        self.shop = Shop(self.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def open(self, cog, command, admin=True):
        i = self.w.interaction(admin=admin)
        await command.callback(cog, i)
        return i

    async def delete_labels(self, cog, command, *labels):
        """대량 삭제 화면을 열어 이름에 labels가 들어간 항목을 골라 지우고 결과 임베드를 돌려준다."""
        i = await self.open(cog, command)
        kwargs = i.response.send_message.await_args.kwargs
        view = kwargs["view"]
        values = [e.value for e in view.entries if any(label in e.label for label in labels)]
        self.assertEqual(len(values), len(labels), (labels, [e.label for e in view.entries]))
        sel = self.w.interaction()
        sel.data = {"values": values}
        await view.on_select(sel)
        await view.ask(self.w.interaction())
        done = self.w.interaction()
        await view.run(done)
        return done.response.edit_message.await_args.kwargs["embed"]

    async def test_gear_are_deleted_in_bulk(self):
        embed = await self.delete_labels(self.content, ContentAdmin.gear_bulk, "낡은 스패너", "구리 방열판", "리벳 건")
        self.assertIn("3개", embed.description)
        names = {g.name for g in self.store.balance(G).gear}
        self.assertTrue({"낡은 스패너", "구리 방열판", "리벳 건"}.isdisjoint(names))
        self.assertIn("레이저 커터", names)

    async def test_monsters_and_bosses_share_one_screen_and_the_last_boss_is_protected(self):
        embed = await self.delete_labels(self.content, ContentAdmin.monster_bulk, "메인프레임 군주", "슬러지 코어", "스크랩 봇 왕", "악성 AI")
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(len(self.store.balance(G).bosses), 1)                                         # 마지막 보스 하나는 남는다
        self.assertIn("보스가 하나도 없어요", fields["못 지운 것과 이유"])
        self.assertNotIn("악성 AI", [m.name for m in self.store.balance(G).monsters])

    async def test_consumables_are_deleted_and_secret_bosses_can_all_go(self):
        await self.delete_labels(self.content, ContentAdmin.secret_bulk, "NULL 군주")
        self.assertEqual(self.store.balance(G).secrets, ())
        embed = await self.delete_labels(self.content, ContentAdmin.potion_bulk, "소형 배터리", "정밀 나노 겔")
        self.assertIn("2개", embed.description)
        self.assertTrue({"소형 배터리", "정밀 나노 겔"}.isdisjoint({c.name for c in self.store.balance(G).consumables}))

    async def test_something_a_recipe_uses_is_skipped_unless_the_recipe_goes_too(self):
        content.add(self.store, G, "consumables", dict(name="숫돌", emoji="🪨", tier="희귀", weight=1, material=True))
        content.add_mod(self.store, G, "낡은 스패너", "숫돌", "레이저 커터")
        embed = await self.delete_labels(self.content, ContentAdmin.gear_bulk, "낡은 스패너", "구리 방열판")
        self.assertIn("1개", embed.description)
        self.assertIn("개조 레시피", {f.name: f.value for f in embed.fields}["못 지운 것과 이유"])
        self.assertIn("낡은 스패너", {g.name for g in self.store.balance(G).gear})
        await self.delete_labels(self.content, ContentAdmin.mod_bulk, "낡은 스패너")                    # 레시피를 지우면
        embed = await self.delete_labels(self.content, ContentAdmin.gear_bulk, "낡은 스패너")           # 장비도 지워진다
        self.assertIn("1개", embed.description)

    async def test_package_results_and_clear_rewards(self):
        table = self.store.package_table(G)
        n = len(table)
        embed = await self.delete_labels(self.rules, RulesAdmin.package_bulk, "쓰레기", "빈 패킷")
        self.assertEqual(len(self.store.package_table(G)), n - 2)
        self.assertIn("2개", embed.description)
        gem = self.store.create_item(G, "보석", emoji="💎")
        self.store.update_settings(G, clear_rewards=[{"id": "a", "item": gem["id"], "amount": 2, "chance": 50},
                                                     {"id": "b", "item": gem["id"], "amount": 5, "chance": 10}])
        embed = await self.delete_labels(self.rules, RulesAdmin.clear_bulk, "×2", "×5")
        self.assertEqual(self.store.settings(G)["clear_rewards"], [])

    async def test_the_last_package_result_cannot_be_removed(self):
        self.store.update_settings(G, package_table=[{"id": "x", "kind": "none", "weight": 1, "amount": 1}, {"id": "y", "kind": "none", "weight": 2, "amount": 1}])
        i = await self.open(self.rules, RulesAdmin.package_bulk)
        view = i.response.send_message.await_args.kwargs["view"]
        await view.select_all(self.w.interaction())
        await view.ask(self.w.interaction())
        done = self.w.interaction()
        await view.run(done)
        embed = done.response.edit_message.await_args.kwargs["embed"]
        self.assertEqual(len(self.store.package_table(G)), 1)
        self.assertIn("하나도 없거나", {f.name: f.value for f in embed.fields}["못 지운 것과 이유"])

    async def test_the_helper_functions_behave_like_the_single_delete_commands(self):
        with self.assertRaises(RuleError):
            remove_package_entry(self.store, G, "nope")
        with self.assertRaises(RuleError):
            remove_clear_reward(self.store, G, "nope")

    async def test_alchemy_recipes_are_deleted_in_bulk_and_items_stay(self):
        a = self.store.create_item(G, "가", sell_price=100, sellable=True)
        b = self.store.create_item(G, "나", sell_price=100, sellable=True)
        result = self.store.create_item(G, "합성물")
        self.store.add_recipe(G, result["id"], [a["id"], b["id"]])
        before = {r["name"] for r in self.store.items(G)}
        embed = await self.delete_labels(self.alchemy, Alchemy.recipe_bulk, "합성물")
        self.assertIn("1개", embed.description)
        self.assertEqual(self.store.recipes(G), [])
        self.assertEqual({r["name"] for r in self.store.items(G)}, before)                              # 결과물 물품은 그대로

    async def test_items_are_deleted_with_their_bag_contents_and_protected_ones_are_skipped(self):
        key = self.store.ensure_system_items(G)["key"]                                                  # 던전 입장 물품이라 못 지운다
        gem = self.store.create_item(G, "보석", emoji="💎")
        self.store.add_item(G, U, gem["id"], 5)
        i = await self.open(self.shop, Shop.delete_items_bulk)
        view = i.response.send_message.await_args.kwargs["view"]
        values = [e.value for e in view.entries if e.label in ("보석", "열쇠")]
        sel = self.w.interaction()
        sel.data = {"values": values}
        await view.on_select(sel)
        ask = self.w.interaction()
        await view.ask(ask)
        self.assertIn("5개", ask.response.edit_message.await_args.kwargs["embed"].description)          # 가방에서 사라질 개수를 미리 알려 준다
        done = self.w.interaction()
        await view.run(done)
        embed = done.response.edit_message.await_args.kwargs["embed"]
        self.assertIsNone(self.store.item_by_name(G, "보석"))
        self.assertEqual(self.store.qty(G, U, gem["id"]), 0)
        self.assertIsNotNone(self.store.item_by_name(G, "열쇠"))
        fields = {f.name: f.value for f in embed.fields}
        self.assertIn("가방 5개도 사라짐", fields["지운 것"])
        self.assertIn("쓰는 설정", fields["못 지운 것과 이유"])
        self.assertTrue(done.response.edit_message.await_count == 1)

    async def test_the_shop_panels_are_refreshed_only_when_an_item_was_really_deleted(self):
        from unittest.mock import patch
        self.store.create_item(G, "보석", emoji="💎")
        namespace = Shop.delete_items_bulk.callback.__globals__                                           # 이 명령어가 실제로 쓰는 모듈의 이름 공간
        with patch.dict(namespace, {"refresh_panels": AsyncMock()}):
            i = await self.open(self.shop, Shop.delete_items_bulk)
            view = i.response.send_message.await_args.kwargs["view"]
            sel = self.w.interaction()
            sel.data = {"values": [next(e.value for e in view.entries if e.label == "열쇠")]}              # 입장 물품이라 못 지운다
            await view.on_select(sel)
            await view.ask(self.w.interaction())
            await view.run(self.w.interaction())
            namespace["refresh_panels"].assert_not_awaited()
            i = await self.open(self.shop, Shop.delete_items_bulk)
            view = i.response.send_message.await_args.kwargs["view"]
            sel = self.w.interaction()
            sel.data = {"values": [next(e.value for e in view.entries if e.label == "보석")]}
            await view.on_select(sel)
            await view.ask(self.w.interaction())
            await view.run(self.w.interaction())
            namespace["refresh_panels"].assert_awaited_once()

    async def test_every_bulk_command_is_admin_only_and_handles_an_empty_list(self):
        for cog, command in ((self.content, ContentAdmin.monster_bulk), (self.content, ContentAdmin.gear_bulk), (self.content, ContentAdmin.potion_bulk),
                             (self.content, ContentAdmin.secret_bulk), (self.content, ContentAdmin.mod_bulk), (self.rules, RulesAdmin.package_bulk),
                             (self.rules, RulesAdmin.clear_bulk), (self.alchemy, Alchemy.recipe_bulk), (self.shop, Shop.delete_items_bulk)):
            i = await self.open(cog, command, admin=False)
            self.assertIn("관리자만", sent_embed(i)[0].description, command.name)
        for cog, command in ((self.content, ContentAdmin.mod_bulk), (self.rules, RulesAdmin.clear_bulk), (self.alchemy, Alchemy.recipe_bulk)):
            i = await self.open(cog, command)                                                            # 비어 있으면 안내만
            embed, kwargs = sent_embed(i)
            self.assertIn("지울 수 있는", embed.description, command.name)
            self.assertNotIn("view", kwargs, command.name)
