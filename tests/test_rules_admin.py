"""재화·던전 입장 물품·패키지 결과표 관리 명령어 시험, 그리고 그 설정이 실제 게임에 미치는 영향."""
import unittest
from unittest import mock
from unittest.mock import AsyncMock

import dungeon_service as service
import cogs.rules_admin as rules_module
from cogs.economy import Economy
from cogs.rules_admin import RulesAdmin, package_lines
from confirm import Confirm
from josa import eul_reul, eun_neun, i_ga
from store import StoreError
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


class RulesFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = RulesAdmin(self.w.bot)
        self.w.channel(400)
        # 다른 시험이 확장을 불러오고 내리면 sys.modules의 모듈이 바뀌므로, 이 파일이 가져온 바로 그 모듈을 고친다.
        patcher = mock.patch.object(rules_module, "refresh_panels", new=AsyncMock(return_value=1))
        self.refresh = patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def run_cmd(self, command, interaction, *args, **kwargs):
        await command.callback(self.cog, interaction, *args, **kwargs)
        return interaction

    def text(self, interaction):
        embed, _ = sent_embed(interaction)
        return (embed.description or "") + "\n".join(f.value for f in embed.fields)


class AdminOnlyTests(RulesFixture):
    async def test_everything_is_admin_only_and_changes_nothing(self):
        self.store.ensure_defaults(G)
        before = self.store.settings(G)
        calls = [(RulesAdmin.currency, (), dict(name="해킹")), (RulesAdmin.entry_item, ("열쇠",)),
                 (RulesAdmin.package_list, ()), (RulesAdmin.package_add, (Choice("none"), 5.0)),
                 (RulesAdmin.package_edit, ("none",), dict(weight=1.0)), (RulesAdmin.package_delete, ("none",)),
                 (RulesAdmin.clear_list, ()), (RulesAdmin.clear_add, ("열쇠",)), (RulesAdmin.clear_edit, ("x",), dict(amount=2)),
                 (RulesAdmin.clear_delete, ("x",))]
        for command, args, *rest in calls:
            interaction = self.ask(admin=False)
            await self.run_cmd(command, interaction, *args, **(rest[0] if rest else {}))
            self.assertIn("관리자만", self.text(interaction), command.name)
        self.assertEqual(self.store.settings(G), before)


class CurrencyCommandTests(RulesFixture):
    async def test_show_then_change(self):
        i = await self.run_cmd(RulesAdmin.currency, self.ask())
        self.assertIn("🍬 **할로윈 재화** · 최대 99,999개", self.text(i))
        i = await self.run_cmd(RulesAdmin.currency, self.ask(), "💎", "젬", 5000)
        self.assertIn("💎 **젬** · 최대 5,000개", self.text(i))
        self.assertEqual(tuple(self.store.currency(G)), ("💎", "젬", 5000))
        self.refresh.assert_awaited()                                            # 상점 패널도 새 이름·이모지로 고친다.

    async def test_bad_emoji_or_name_changes_nothing(self):
        for kwargs in (dict(emoji="abc"), dict(emoji="😀😀😀😀😀😀😀😀😀"), dict(name="가" * 21), dict(name="  ")):
            i = await self.run_cmd(RulesAdmin.currency, self.ask(), **kwargs)
            self.assertIn("앗", sent_embed(i)[0].title, kwargs)
        self.assertEqual(tuple(self.store.currency(G)), ("🍬", "할로윈 재화", 99999))

    async def test_lowering_the_max_asks_first_and_names_the_count(self):
        self.store.add_candy(G, 1, 900)
        self.store.add_candy(G, 2, 90)
        i = await self.run_cmd(RulesAdmin.currency, self.ask(), maximum=100)
        embed, kwargs = sent_embed(i)
        self.assertIn("1명", embed.description)
        view = kwargs["view"]
        self.assertIsInstance(view, Confirm)
        self.assertEqual((self.store.currency(G).max, self.store.wallet(G, 1)), (99999, 900))   # 확인 전에는 그대로
        self.assertFalse(await view.interaction_check(self.ask(uid=99)))
        await view.cancel(self.ask())
        self.assertEqual((self.store.currency(G).max, self.store.wallet(G, 1)), (99999, 900))
        click = self.ask()
        await view.confirm(click)
        self.assertEqual((self.store.currency(G).max, self.store.wallet(G, 1), self.store.wallet(G, 2)), (100, 100, 90))
        self.refresh.assert_awaited()

    async def test_lowering_the_max_without_anyone_above_it_needs_no_confirmation(self):
        self.store.add_candy(G, 1, 50)
        i = await self.run_cmd(RulesAdmin.currency, self.ask(), maximum=100)
        self.assertNotIn("view", i.response.send_message.await_args.kwargs)
        self.assertEqual(self.store.currency(G).max, 100)


class EntryItemTests(RulesFixture):
    async def test_set_and_clear(self):
        ticket = self.store.create_item(G, "입장권", emoji="🎫")
        i = await self.run_cmd(RulesAdmin.entry_item, self.ask(), "입장권", 2)
        self.assertIn("🎫 **입장권** 2개", self.text(i))
        cfg = self.store.settings(G)
        self.assertEqual((cfg["entry_item"], cfg["entry_cost"]), (ticket["id"], 2))
        i = await self.run_cmd(RulesAdmin.entry_item, self.ask())
        self.assertIn("무료 입장", self.text(i))
        self.assertIsNone(self.store.settings(G)["entry_item"])

    async def test_unknown_items_and_roles_are_refused(self):
        self.w.add_role(50, name="역할")
        self.store.create_item(G, "VIP", kind="role", role_id=50, buyable=True, buy_price=5)
        for name in ("없는 물품", "VIP"):
            i = await self.run_cmd(RulesAdmin.entry_item, self.ask(), name, 1)
            self.assertIn("그런 물품이 없어요", self.text(i), name)
        self.assertEqual(self.store.settings(G)["entry_item"], self.store.ensure_system_items(G)["key"])

    async def test_the_dungeon_gate_follows_the_setting(self):
        ticket = self.store.create_item(G, "입장권", emoji="🎫")
        await self.run_cmd(RulesAdmin.entry_item, self.ask(), "입장권", 2)
        out = await service.enter(self.store, self.w.locks, G, U)
        self.assertFalse(out.ok)
        self.assertIn("🎫 입장권이 2개 필요해요", out.text)
        self.assertIn("보유 0개", out.text)
        self.store.add_item(G, U, ticket["id"], 3)
        out = await service.enter(self.store, self.w.locks, G, U)
        self.assertTrue(out.ok)
        self.assertEqual(self.store.qty(G, U, ticket["id"]), 1)                    # 2개만 냈다.
        self.assertIn("🎫 입장권 2개을(를) 내고", "\n".join(out.run.events))
        again = await service.enter(self.store, self.w.locks, G, U)                 # 진행 중이면 다시 내지 않는다.
        self.assertTrue(again.ok)
        self.assertEqual(self.store.qty(G, U, ticket["id"]), 1)
        self.assertIn("입장 물품은 쓰지 않아요", "\n".join(again.run.events))

    async def test_free_entry(self):
        await self.run_cmd(RulesAdmin.entry_item, self.ask())
        out = await service.enter(self.store, self.w.locks, G, U)
        self.assertTrue(out.ok)
        self.assertNotIn("을(를) 내고", "\n".join(out.run.events))
        self.assertIn("던전에 들어왔어요", "\n".join(out.run.events))

    async def test_the_default_key_still_works_and_a_renamed_key_is_shown_by_its_new_name(self):
        ids = self.store.ensure_system_items(G)
        out = await service.enter(self.store, self.w.locks, G, U)
        self.assertIn("🗝️ 열쇠가 1개 필요해요", out.text)
        self.store.update_item(G, ids["key"], name="황금 열쇠", emoji="🔑")
        out = await service.enter(self.store, self.w.locks, G, U)
        self.assertIn("🔑 황금 열쇠가 1개 필요해요", out.text)
        self.store.add_item(G, U, ids["key"], 1)
        out = await service.enter(self.store, self.w.locks, G, U)
        self.assertTrue(out.ok)
        self.assertEqual(self.store.qty(G, U, ids["key"]), 0)

    async def test_failed_entry_keeps_the_items(self):
        ticket = self.store.create_item(G, "입장권")
        self.store.update_settings(G, entry_item=ticket["id"], entry_cost=3, seeded=True)
        self.store.add_item(G, U, ticket["id"], 2)
        out = await service.enter(self.store, self.w.locks, G, U)
        self.assertFalse(out.ok)
        self.assertEqual((self.store.qty(G, U, ticket["id"]), self.store.dungeon_run(G, U)), (2, None))


class PackageCommandTests(RulesFixture):
    def ids(self):
        return [e["id"] for e in self.store.package_table(G)]

    async def test_list_shows_normalized_percentages(self):
        i = await self.run_cmd(RulesAdmin.package_list, self.ask())
        text = self.text(i)
        for needle in ("👻 **꽝** — 60%", "🗝️ **열쇠** — 20%", "🍬 **할로윈 재화 50** — 10%", "🗑️ **쓰레기** — 10%", "비율 합계 100"):
            self.assertIn(needle, text)

    async def test_add_each_kind(self):
        gem = self.store.create_item(G, "보석", emoji="💎")
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("item"), 10.0, "보석", 2, "✨", "반짝이")
        self.assertIn("✨ **반짝이** — 9.091%", self.text(i))                      # 합계 110 중 10
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("candy"), 5.0, None, 25)
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("none"), 1.0, None, 1, "💨", "휙")
        table = self.store.package_table(G)
        self.assertEqual([(e["kind"], e.get("item"), e["amount"], e["weight"]) for e in table[-3:]],
                         [("item", gem["id"], 2, 10.0), ("candy", None, 25, 5.0), ("none", None, 1, 1.0)])
        self.assertEqual(len({e["id"] for e in table}), len(table))

    async def test_add_validation(self):
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("item"), 5.0, "없는 물품")
        self.assertIn("물품", self.text(i))
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("item"), 5.0, None)
        self.assertIn("물품", self.text(i))
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("none"), 5.0, None, 1, "abc")
        self.assertIn("이모지", self.text(i))
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("none"), 5.0, None, 1, None, "가" * 31)
        self.assertIn("30자", self.text(i))
        self.assertEqual(len(self.store.package_table(G)), 4)

    async def test_the_table_has_a_size_limit(self):
        for n in range(16):
            await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("none"), 1.0)
        i = await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("none"), 1.0)
        self.assertIn("최대 20개", self.text(i))
        self.assertEqual(len(self.store.package_table(G)), 20)

    async def test_edit_changes_only_what_is_given_and_can_reset_icon_and_title(self):
        await self.run_cmd(RulesAdmin.package_edit, self.ask(), "candy", weight=30.0, amount=77, emoji="💰", title="돈다발")
        entry = next(e for e in self.store.package_table(G) if e["id"] == "candy")
        self.assertEqual((entry["weight"], entry["amount"], entry["emoji"], entry["title"]), (30.0, 77, "💰", "돈다발"))
        await self.run_cmd(RulesAdmin.package_edit, self.ask(), "candy", emoji="-", title="-")
        entry = next(e for e in self.store.package_table(G) if e["id"] == "candy")
        self.assertNotIn("emoji", entry)
        self.assertNotIn("title", entry)
        self.assertEqual((entry["weight"], entry["amount"]), (30.0, 77))
        new_item = self.store.create_item(G, "보석", emoji="💎")
        await self.run_cmd(RulesAdmin.package_edit, self.ask(), "key", item="보석")
        self.assertEqual(next(e for e in self.store.package_table(G) if e["id"] == "key")["item"], new_item["id"])

    async def test_edit_errors(self):
        for args, kwargs, needle in [(("nope",), dict(weight=1.0), "그런 결과"), (("none",), {}, "바꿀 내용"),
                                     (("none",), dict(item="열쇠"), "'물품' 종류"), (("key",), dict(item="없는 것"), "물품"),
                                     (("none",), dict(emoji="abc"), "이모지"), (("none",), dict(title="가" * 31), "30자")]:
            i = await self.run_cmd(RulesAdmin.package_edit, self.ask(), *args, **kwargs)
            self.assertIn(needle, self.text(i), (args, kwargs))
        self.assertEqual(self.store.package_table(G)[0]["weight"], 60)

    async def test_delete_and_the_table_can_never_be_emptied_or_zeroed(self):
        i = await self.run_cmd(RulesAdmin.package_delete, self.ask(), "trash")
        self.assertIn("지웠어요", self.text(i))
        self.assertEqual(self.ids(), ["none", "key", "candy"])
        i = await self.run_cmd(RulesAdmin.package_delete, self.ask(), "trash")
        self.assertIn("그런 결과가 없어요", self.text(i))
        await self.run_cmd(RulesAdmin.package_edit, self.ask(), "candy", weight=0.0)
        await self.run_cmd(RulesAdmin.package_edit, self.ask(), "key", weight=0.0)
        i = await self.run_cmd(RulesAdmin.package_delete, self.ask(), "none")                  # 남는 건 확률 0뿐
        self.assertIn("확률이 전부 0", self.text(i))
        self.assertEqual(self.ids(), ["none", "key", "candy"])
        i = await self.run_cmd(RulesAdmin.package_edit, self.ask(), "none", weight=0.0)
        self.assertIn("전부 0", self.text(i))
        self.assertEqual(self.store.package_table(G)[0]["weight"], 60)

    async def test_deleting_the_last_entry_is_refused(self):
        self.store.package_table(G)
        self.store.update_settings(G, package_table=[{"id": "only", "kind": "none", "weight": 1, "amount": 1}])
        i = await self.run_cmd(RulesAdmin.package_delete, self.ask(), "only")
        self.assertIn("하나도 없거나", self.text(i))

    async def test_an_item_used_by_a_result_cannot_be_deleted_until_the_result_is_removed(self):
        gem = self.store.create_item(G, "보석")
        await self.run_cmd(RulesAdmin.package_add, self.ask(), Choice("item"), 5.0, "보석")
        with self.assertRaisesRegex(StoreError, "패키지 결과표"):
            self.store.delete_item(G, gem["id"])
        rid = next(e["id"] for e in self.store.package_table(G) if e.get("item") == gem["id"])
        await self.run_cmd(RulesAdmin.package_delete, self.ask(), rid)
        self.assertEqual(self.store.delete_item(G, gem["id"]), 0)

    async def test_autocomplete(self):
        choices = await self.cog.ident_autocomplete(self.ask(), "열쇠")
        self.assertEqual([(c.name, c.value) for c in choices], [("🗝️ 열쇠 (물품, 비율 20)", "key")])
        self.assertEqual(len(await self.cog.ident_autocomplete(self.ask(), "")), 4)
        items = await self.cog.pkg_item_autocomplete(self.ask(), "열")
        self.assertEqual([c.value for c in items], ["열쇠"])
        entry = await self.cog.entry_autocomplete(self.ask(), "")
        self.assertEqual({c.value for c in entry}, {"열쇠", "쓰레기"})

    def test_package_lines_note_unnormalized_totals(self):
        lines = package_lines(self.store, G)
        self.assertEqual(len(lines), 4)


class ClearRewardTests(RulesFixture):
    def rewards(self):
        return self.store.settings(G)["clear_rewards"]

    async def finish(self, rng):
        """방 10을 넘긴 던전을 끝낸다."""
        import json
        import dungeon
        run = (await service.enter(self.store, self.w.bot.locks, G, U)).run
        data = json.loads(run.to_json())
        data.update(room=dungeon.ROOMS, phase="cleared")
        self.store.save_dungeon(G, U, json.dumps(data))
        return await service.act(self.store, self.w.bot.locks, rng, G, U, run.rev, "advance")

    def setup_key(self):
        self.store.ensure_defaults(G)
        self.store.update_settings(G, entry_item=None, dungeon={"보상.최소": 5, "보상.최대": 5, "보상.획득권확률": 0})
        return self.store.item_by_name(G, "열쇠")

    async def test_list_shows_currency_ticket_and_items(self):
        gem = self.store.create_item(G, "보석", emoji="💎")
        await self.run_cmd(RulesAdmin.clear_add, self.ask(), "보석", 3, 25.0)
        i = await self.run_cmd(RulesAdmin.clear_list, self.ask())
        text = self.text(i)
        for needle in ("히든 칭호 획득권", "💎 보석 ×3 — 25%", "/던전설정"):
            self.assertIn(needle, text)
        self.assertEqual(self.rewards()[0]["item"], gem["id"])

    async def test_add_edit_delete_and_validation(self):
        self.store.create_item(G, "보석", emoji="💎")
        i = await self.run_cmd(RulesAdmin.clear_add, self.ask(), "없는 물품")
        self.assertIn("물품", self.text(i))
        self.assertEqual(self.rewards(), [])
        await self.run_cmd(RulesAdmin.clear_add, self.ask(), "보석")
        rid = self.rewards()[0]["id"]
        self.assertEqual((self.rewards()[0]["amount"], self.rewards()[0]["chance"]), (1, 100))
        i = await self.run_cmd(RulesAdmin.clear_edit, self.ask(), rid)
        self.assertIn("하나 이상", self.text(i))
        i = await self.run_cmd(RulesAdmin.clear_edit, self.ask(), rid, "없는 물품")
        self.assertIn("물품", self.text(i))
        i = await self.run_cmd(RulesAdmin.clear_edit, self.ask(), "nope", None, 2)
        self.assertIn("그런 보상이 없어요", self.text(i))
        await self.run_cmd(RulesAdmin.clear_edit, self.ask(), rid, None, 4, 50.0)
        self.assertEqual((self.rewards()[0]["amount"], self.rewards()[0]["chance"]), (4, 50.0))
        await self.run_cmd(RulesAdmin.clear_edit, self.ask(), rid, "보석")
        i = await self.run_cmd(RulesAdmin.clear_delete, self.ask(), "nope")
        self.assertIn("그런 보상이 없어요", self.text(i))
        await self.run_cmd(RulesAdmin.clear_delete, self.ask(), rid)
        self.assertEqual(self.rewards(), [])

    async def test_size_limit(self):
        self.store.create_item(G, "보석")
        for _ in range(10):
            await self.run_cmd(RulesAdmin.clear_add, self.ask(), "보석")
        i = await self.run_cmd(RulesAdmin.clear_add, self.ask(), "보석")
        self.assertIn("최대 10개", self.text(i))
        self.assertEqual(len(self.rewards()), 10)

    async def test_default_items_can_be_rewards(self):
        self.store.ensure_defaults(G)
        i = await self.run_cmd(RulesAdmin.clear_add, self.ask(), "열쇠")
        self.assertEqual(len(self.rewards()), 1)

    async def test_item_in_use_cannot_be_deleted(self):
        gem = self.store.create_item(G, "보석")
        await self.run_cmd(RulesAdmin.clear_add, self.ask(), "보석")
        with self.assertRaisesRegex(StoreError, "던전 클리어 보상"):
            self.store.delete_item(G, gem["id"])
        await self.run_cmd(RulesAdmin.clear_delete, self.ask(), self.rewards()[0]["id"])
        self.assertEqual(self.store.delete_item(G, gem["id"]), 0)

    async def test_autocomplete(self):
        self.store.create_item(G, "보석", emoji="💎")
        await self.run_cmd(RulesAdmin.clear_add, self.ask(), "보석", 2, 30.0)
        got = await self.cog.clear_ident_autocomplete(self.ask(), "보석")
        self.assertEqual([c.name for c in got], ["💎 보석 ×2 (30%)"])
        self.assertEqual(await self.cog.clear_ident_autocomplete(self.ask(), "없음"), [])
        items = await self.cog.clear_item_autocomplete(self.ask(), "보")
        self.assertEqual([c.value for c in items], ["보석"])

    async def test_clearing_pays_items_by_independent_chance(self):
        key = self.setup_key()
        self.store.create_item(G, "보석")
        gem = self.store.item_by_name(G, "보석")
        self.store.update_settings(G, clear_rewards=[
            {"id": "a", "item": key["id"], "amount": 3, "chance": 50},
            {"id": "b", "item": gem["id"], "amount": 2, "chance": 100},
            {"id": "c", "item": gem["id"], "amount": 7, "chance": 0}])
        done = await self.finish(Scripted(randoms=[0.99, 0.49, 0.0, 0.99], ints=[5]))   # 획득권 실패 → a 통과(49<50), b 통과, c 실패(0%)
        self.assertEqual(done.reward.items, [(key["emoji"], key["name"], 3), (gem["emoji"], gem["name"], 2)])
        self.assertEqual((self.store.qty(G, U, key["id"]), self.store.qty(G, U, gem["id"])), (3, 2))

    async def test_chance_boundary_is_exclusive(self):
        key = self.setup_key()
        self.store.update_settings(G, clear_rewards=[{"id": "a", "item": key["id"], "amount": 1, "chance": 50}])
        done = await self.finish(Scripted(randoms=[0.99, 0.5], ints=[5]))
        self.assertEqual(done.reward.items, [])
        self.assertEqual(self.store.qty(G, U, key["id"]), 0)

    async def test_no_rewards_means_no_extra_random_draws(self):
        self.setup_key()
        done = await self.finish(Scripted(randoms=[0.99], ints=[5]))              # 획득권 한 번만
        self.assertEqual((done.ended, done.reward.items, done.reward.candy), ("clear", [], 5))

    async def test_a_reward_item_deleted_behind_the_scenes_is_skipped(self):
        key = self.setup_key()
        self.store.update_settings(G, clear_rewards=[{"id": "a", "item": key["id"], "amount": 1, "chance": 100}])
        self.store.db.execute("DELETE FROM items WHERE id=?", (key["id"],))     # 사용 중 검사를 우회한 직접 삭제
        done = await self.finish(Scripted(randoms=[0.99], ints=[5]))
        self.assertEqual(done.reward.items, [])
        self.assertIn("지워진 물품", "\n".join(self.cog._clear_lines(G)))

    async def test_failed_and_quit_runs_give_no_items(self):
        key = self.setup_key()
        self.store.update_settings(G, clear_rewards=[{"id": "a", "item": key["id"], "amount": 1, "chance": 100}])
        run = (await service.enter(self.store, self.w.bot.locks, G, U)).run
        out = await service.act(self.store, self.w.bot.locks, Scripted(), G, U, run.rev, "quit")
        self.assertEqual(out.ended, "quit")
        self.assertEqual(self.store.qty(G, U, key["id"]), 0)

    async def test_the_result_screen_lists_the_items(self):
        from cogs.dungeon import run_embed
        key = self.setup_key()
        self.store.update_settings(G, clear_rewards=[{"id": "a", "item": key["id"], "amount": 1234, "chance": 100}])
        done = await self.finish(Scripted(randoms=[0.99, 0.0], ints=[5]))
        embed = run_embed(done.run, done, self.store.balance(G), self.store.currency(G))
        self.assertIn(f"{key['emoji']} **{key['name']}** ×1,234", "\n".join(f.value for f in embed.fields))

    def test_validation_of_stored_rewards(self):
        ok = {"id": "a", "item": 1, "amount": 1, "chance": 100}
        self.store.update_settings(G, clear_rewards=[ok])
        bad = [dict(ok, amount=0), dict(ok, amount=100000), dict(ok, chance=-1), dict(ok, chance=101), dict(ok, item="1"),
               dict(ok, id=5), dict(ok, amount=1.5), dict(ok, chance=True), "x"]
        for entry in bad:
            with self.assertRaises(ValueError, msg=str(entry)):
                self.store.update_settings(G, clear_rewards=[entry])
        with self.assertRaises(ValueError):
            self.store.update_settings(G, clear_rewards=[ok, dict(ok)])               # 같은 id
        with self.assertRaises(ValueError):
            self.store.update_settings(G, clear_rewards=[dict(ok, id=str(n)) for n in range(11)])
        with self.assertRaises(ValueError):
            self.store.update_settings(G, clear_rewards="x")

    def test_end_dungeon_items_skip_missing_and_stack(self):
        self.store.ensure_defaults(G)
        key = self.store.item_by_name(G, "열쇠")
        self.store.update_settings(G, entry_item=None)
        self.store.db.execute("INSERT INTO dungeons(guild,user,data) VALUES(?,?,?)", (G, U, "{}"))
        self.assertEqual(self.store.end_dungeon(G, U, items=[(key["id"], 2), (key["id"], 3), (99999, 4)]), (0, 0))
        self.assertEqual(self.store.qty(G, U, key["id"]), 5)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) AS n FROM inventory WHERE guild=? AND user=?", (G, U)).fetchone()["n"], 1)


class JosaTests(unittest.TestCase):
    def test_particles_follow_the_last_syllable(self):
        self.assertEqual((i_ga("젬"), i_ga("골드"), i_ga("할로윈 재화")), ("젬이", "골드가", "할로윈 재화가"))
        self.assertEqual((eun_neun("젬"), eun_neun("재화")), ("젬은", "재화는"))
        self.assertEqual((eul_reul("젬"), eul_reul("재화")), ("젬을", "재화를"))

    def test_non_hangul_endings(self):
        self.assertEqual((i_ga("coin"), i_ga("Gem 2"), i_ga("💎"), i_ga("")), ("coin가", "Gem 2가", "💎가", "가"))
        self.assertEqual(i_ga("젬 💎"), "젬 💎이")                  # 끝의 이모지는 건너뛰고 앞의 한글 글자를 본다.


class ChatUsesCurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_reaction_uses_the_servers_emoji_and_help_uses_its_name(self):
        rng = Scripted([0.1, 0.9])
        w = World(rng)
        try:
            cog = Economy(w.bot)
            w.channel(400)
            w.store.update_settings(G, channels=[400])
            w.store.set_currency(G, emoji="💎", name="젬", max=300)
            message = w.message()
            await cog.on_message(message)
            message.add_reaction.assert_awaited_once_with("💎")
            i = w.interaction()
            await Economy.help.callback(cog, i)
            text = "\n".join(f"{f.name} {f.value}" for f in sent_embed(i)[0].fields)
            self.assertIn("💎 모으는 법", text)
            self.assertIn("젬은 **최대 300개**", text)
            self.assertIn("💎 젬 50", text)
        finally:
            await w.close()


if __name__ == "__main__":
    unittest.main()
