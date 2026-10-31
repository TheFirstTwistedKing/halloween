"""패키지 시험: 누구나 뜯기, 선착순, 결과 반영, 마무리, 재시작에도 유지되는 버튼."""
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock

import discord

import packages
from packages import PKG_OPEN_ID, PKG_SKIP_ID, PackageView, apply_reward, finalize_due, open_package
from panel import SHOP_BAG_ID, SHOP_SELECT_ID, ShopPanelView
from store import CANDY_MAX, PACKAGE_CANDY
from tests.helpers import Scripted, World

NOW = time.time()


class PackageFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.channel = self.w.channel(400)
        self.w.bot.get_channel = lambda cid: self.w.channels.get(cid)
        self.store = self.w.store

    async def asyncTearDown(self):
        await self.w.close()

    def package(self, mode="all", expires=None, message=9001):
        self.store.create_package(100, 400, message, expires or NOW + 600, mode)

    async def open(self, uid, result_roll, message=9001):
        self.w.bot.rng.randoms.append(result_roll)
        interaction = self.w.interaction(uid=uid, message_id=message)
        await open_package(interaction)
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])      # 결과는 뜯은 사람에게만 보인다.
        return embed


class OpenTests(PackageFixture):
    async def test_everyone_can_open_and_each_gets_their_own_result(self):
        self.package("all")
        ids = self.store.ensure_system_items(100)
        a = await self.open(1, 0.0)                         # 꽝
        b = await self.open(2, 0.65)                        # 열쇠
        c = await self.open(3, 0.85)                        # 재화 50
        d = await self.open(4, 0.95)                        # 쓰레기
        self.assertEqual([e.title for e in (a, b, c, d)], ["👻 꽝!", "🗝️ 열쇠!", "🍬 할로윈 재화 50!", "🗑️ 쓰레기!"])
        self.assertEqual(self.store.wallet(100, 3), PACKAGE_CANDY)
        self.assertEqual((self.store.qty(100, 2, ids["key"]), self.store.qty(100, 4, ids["trash"])), (1, 1))
        self.assertEqual((self.store.qty(100, 1, ids["key"]), self.store.wallet(100, 1)), (0, 0))     # 꽝은 아무것도 없다.
        self.assertEqual(len(self.store.package_openers(9001)), 4)

    async def test_one_opening_per_person(self):
        self.package("all")
        await self.open(1, 0.65)
        embed = await self.open(1, 0.65)
        self.assertIn("이미", embed.description)
        ids = self.store.ensure_system_items(100)
        self.assertEqual(self.store.qty(100, 1, ids["key"]), 1)               # 두 번째는 보상이 없다.
        self.assertEqual(len(self.w.bot.rng.randoms), 0)

    async def test_the_person_who_triggered_it_is_not_special(self):
        self.package("all")
        embed = await self.open(99, 0.65)                  # 패키지를 낸 채팅과 상관없는 사람도 뜯는다.
        self.assertEqual(embed.title, "🗝️ 열쇠!")

    async def test_first_mode_only_one_person_wins_and_the_message_shows_the_winner(self):
        self.package("first")
        winner = await self.open(1, 0.65)
        loser = await self.open(2, 0.65)
        self.assertEqual(winner.title, "🗝️ 열쇠!")
        self.assertIn("한발 늦었어요", loser.description)
        ids = self.store.ensure_system_items(100)
        self.assertEqual((self.store.qty(100, 1, ids["key"]), self.store.qty(100, 2, ids["key"])), (1, 0))
        edit = self.channel.partial[9001].edit
        edit.assert_awaited_once()
        shown = edit.await_args.kwargs
        self.assertIn("<@1>", shown["embed"].description)
        self.assertIn("🗝️ 열쇠", shown["embed"].description)
        self.assertIsNone(shown["view"])                   # 버튼이 사라진다.
        self.assertEqual(self.store.package(9001)["closed"], 2)      # 늦게 누른 사람에게 안내하려고 만료까지 기록은 남긴다.
        late = await self.open(3, 0.65)
        self.assertIn("한발 늦었어요", late.description)
        self.assertEqual(edit.await_count, 1)              # 메시지는 한 번만 고친다.
        self.assertEqual(await finalize_due(self.w.bot, NOW + 10), 0)         # 만료 전에는 정리 대상이 아니다.
        self.assertEqual(await finalize_due(self.w.bot, NOW + 601), 1)
        self.assertEqual(edit.await_count, 1)              # 이미 요약했으니 다시 고치지 않고 기록만 지운다.
        self.assertIsNone(self.store.package(9001))

    async def test_expired_or_unknown_packages(self):
        self.package("all", expires=NOW - 1)
        gone = await self.open(1, 0.0)
        self.assertIn("사라졌어요", gone.description)
        gone = await self.open(1, 0.0, message=5555)
        self.assertIn("사라졌어요", gone.description)

    async def test_candy_reward_respects_the_cap_and_says_so(self):
        self.package("all")
        self.store.add_candy(100, 1, CANDY_MAX - 20)
        embed = await self.open(1, 0.85)
        self.assertEqual(self.store.wallet(100, 1), CANDY_MAX)
        self.assertIn("20개만 받았어요", embed.description)

    async def test_skipping_changes_nothing(self):
        self.package("all")
        interaction = self.w.interaction(uid=1)
        await PackageView().on_skip(interaction)
        self.assertIn("그냥 두었어요", interaction.response.send_message.await_args.kwargs["embed"].title)
        self.assertEqual(self.store.package_openers(9001), [])
        embed = await self.open(1, 0.0)                    # 그냥 둔 뒤에도 뜯을 수 있다.
        self.assertEqual(embed.title, "👻 꽝!")

    async def test_apply_reward_directly(self):
        table = {e["id"]: e for e in self.store.package_table(100)}
        self.assertEqual(apply_reward(self.store, 100, 5, table["none"]), 0)
        self.assertEqual(apply_reward(self.store, 100, 5, table["candy"]), 50)
        self.assertEqual(apply_reward(self.store, 100, 5, table["key"]), 0)
        self.assertEqual(apply_reward(self.store, 100, 5, table["trash"]), 0)
        self.assertEqual({r["name"]: r["qty"] for r in self.store.inventory(100, 5)}, {"열쇠": 1, "쓰레기": 1})
        ghost = {"id": "g", "kind": "item", "item": 99999, "amount": 2, "weight": 1}          # 그 사이 지워진 물품: 오류 없이 아무것도 안 준다.
        self.assertEqual(apply_reward(self.store, 100, 5, ghost), 0)
        self.assertEqual({r["name"]: r["qty"] for r in self.store.inventory(100, 5)}, {"열쇠": 1, "쓰레기": 1})   # 아무것도 더해지지 않았다.
        self.assertEqual(self.store.holdings(100, 99999), 0)


class CustomTableTests(PackageFixture):
    """관리자가 결과표를 바꾸면 실제 뜯기에 그대로 반영된다."""

    def set_table(self, table):
        self.store.package_table(100)                          # 기본 표를 먼저 만들어 둔다(처음 한 번).
        self.store.update_settings(100, package_table=table)

    async def test_custom_entries_rewards_icons_and_ratios(self):
        gem = self.store.create_item(100, "보석", emoji="💎")
        self.set_table([{"id": "a", "kind": "item", "item": gem["id"], "amount": 3, "weight": 1, "emoji": "✨", "title": "반짝 보석"},
                        {"id": "b", "kind": "candy", "amount": 7, "weight": 3}])         # 합계 4 → 25% / 75%
        self.package("all")
        first = await self.open(1, 0.1)                       # 0~0.25 → a
        second = await self.open(2, 0.3)                      # 0.25~1 → b
        self.assertEqual((first.title, second.title), ("✨ 반짝 보석!", "🍬 할로윈 재화 7!"))
        self.assertEqual((self.store.qty(100, 1, gem["id"]), self.store.wallet(100, 2)), (3, 7))
        self.assertEqual([r["result"] for r in self.store.package_openers(9001)], ["a", "b"])

    async def test_currency_changes_show_in_the_result(self):
        self.store.set_currency(100, emoji="💰", name="골드")
        self.set_table([{"id": "c", "kind": "candy", "amount": 40, "weight": 1}])
        self.package("all")
        embed = await self.open(1, 0.5)
        self.assertEqual(embed.title, "💰 골드 40!")
        self.assertIn("💰 40개", embed.description)

    async def test_a_result_removed_while_the_package_is_up_is_shown_safely(self):
        self.package("all", expires=NOW + 10)
        await self.open(1, 0.0)                                # 꽝('none')을 뽑는다
        self.store.update_settings(100, package_table=[e for e in self.store.package_table(100) if e["id"] != "none"])
        await finalize_due(self.w.bot, NOW + 11)
        self.assertIn("사라진 결과", self.channel.partial[9001].edit.await_args.kwargs["embed"].description)

    async def test_an_empty_or_all_zero_table_falls_back_to_nothing_instead_of_crashing(self):
        self.store.package_table(100)
        self.store.update_settings(100, package_table=[])
        self.package("all")
        embed = await self.open(1, 0.5)
        self.assertEqual(embed.title, "👻 꽝!")


class FinalizeTests(PackageFixture):
    async def test_expired_package_becomes_a_summary_with_counts(self):
        self.package("all", expires=NOW + 10)
        for uid, roll in [(1, 0.0), (2, 0.0), (3, 0.65), (4, 0.85)]:
            await self.open(uid, roll)
        self.assertEqual(await finalize_due(self.w.bot, NOW + 5), 0)           # 아직 시간이 남았다.
        self.assertEqual(await finalize_due(self.w.bot, NOW + 11), 1)
        shown = self.channel.partial[9001].edit.await_args.kwargs
        self.assertEqual(shown["embed"].title, "📦 패키지가 사라졌어요")
        self.assertIn("4명이 뜯었어요", shown["embed"].description)
        self.assertIn("👻 꽝 ×2", shown["embed"].description)
        self.assertIn("🗝️ 열쇠 ×1", shown["embed"].description)
        self.assertIsNone(shown["view"])
        self.assertIsNone(self.store.package(9001))
        self.assertEqual(await finalize_due(self.w.bot, NOW + 99), 0)           # 두 번 정리하지 않는다.

    async def test_an_untouched_package_says_so(self):
        self.package("all", expires=NOW + 1)
        await finalize_due(self.w.bot, NOW + 2)
        self.assertIn("아무도 뜯지 않았어요", self.channel.partial[9001].edit.await_args.kwargs["embed"].description)

    async def test_missing_channel_or_message_still_cleans_up(self):
        self.package("all", expires=NOW + 1, message=1)
        self.store.create_package(100, 999, 2, NOW + 1, "all")                # 채널을 못 찾는 경우
        self.channel.get_partial_message(1).edit.side_effect = discord.NotFound(NS(status=404, reason="x"), "gone")
        self.assertEqual(await finalize_due(self.w.bot, NOW + 2), 2)
        self.assertEqual(self.store.finished_packages(NOW + 2), [])

    async def test_tidy_loop_survives_errors(self):
        from cogs.economy import Economy
        cog = Economy(self.w.bot)
        with mock.patch("cogs.economy.packages.finalize_due", new=AsyncMock(side_effect=RuntimeError("실패"))):
            with self.assertLogs("halloween", level="ERROR"):
                await cog.tidy.coro(cog)                                      # 예외가 밖으로 나가지 않는다.


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_are_persistent_and_register_with_a_real_bot(self):
        w = World()
        try:
            for view, ids in ((PackageView(), {PKG_OPEN_ID, PKG_SKIP_ID}), (ShopPanelView(), {SHOP_SELECT_ID, SHOP_BAG_ID})):
                self.assertIsNone(view.timeout)
                self.assertTrue(view.is_persistent())
                self.assertEqual({item.custom_id for item in view.children}, ids)
                w.bot.add_view(view)                                            # 실제 등록: 고정형이 아니면 여기서 오류
        finally:
            await w.close()

    async def test_a_package_posted_before_a_restart_can_still_be_opened(self):
        w = World()
        try:
            w.store.create_package(100, 400, 9001, NOW + 600, "all")
            restarted = World(Scripted([0.65]))                    # 봇을 다시 켠 것처럼 새 봇 객체
            restarted.store.db.close()
            restarted.store = w.store
            restarted.bot.store = w.store
            interaction = restarted.interaction(uid=5, message_id=9001)
            await PackageView().on_open(interaction)                  # 저장된 기록만으로 뜯을 수 있다.
            self.assertEqual(interaction.response.send_message.await_args.kwargs["embed"].title, "🗝️ 열쇠!")
        finally:
            await w.close()


if __name__ == "__main__":
    unittest.main()
