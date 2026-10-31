"""상점 화면 시험: 패널 구성, 올리기·새로 고치기, 물품 상세, 수량 입력, 가방."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

from panel import ShopPanelView, build_panel, post_panel, refresh_panels
from store import CANDY_MAX
from tests.helpers import FakeRole, World, sent_embed
from views import DetailView, QuantityModal, bag_for, send_bag


class UiFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.channel = self.w.channel(400)
        self.candle = self.store.create_item(100, "호박 초", emoji="🕯️", description="은은하게 빛나요", buy_price=30, sell_price=10,
                                              buyable=True, sellable=True)
        self.w.add_role(50, name="유령 손님")
        self.vip = self.store.create_item(100, "VIP", emoji="👑", kind="role", role_id=50, buy_price=200, buyable=True)
        self.bat = self.store.create_item(100, "박쥐 날개", emoji="🦇", sell_price=7, sellable=True)

    async def asyncTearDown(self):
        await self.w.close()

    def modal(self, cls, *args, text="1"):
        modal = cls(*args)
        field = getattr(modal, "amount", None) or modal.count
        field._value = text
        return modal


class PanelContentTests(UiFixture):
    async def test_only_listed_items_are_shown_with_prices_and_flags(self):
        self.store.create_item(100, "비밀 물품")                                 # 구입·판매가 모두 꺼진 물품은 안 나온다.
        self.store.ensure_system_items(100)
        embed, view = build_panel(self.store, self.w.guild)
        text = embed.description
        self.assertIn("🕯️ **호박 초**\n┕ 구매 🍬 30 · 판매 🍬 10", text)
        self.assertIn("🦇 **박쥐 날개**\n┕ 구매 — · 판매 🍬 7", text)
        self.assertIn("👑 **유령 손님** `역할`\n┕ 구매 🍬 200 · 판매 —", text)          # 역할은 실제 역할 이름으로
        for hidden in ("비밀 물품", "열쇠", "쓰레기"):
            self.assertNotIn(hidden, text)
        self.assertIn("🍬 = 할로윈 재화", embed.fields[0].name)
        select, bag = view.children
        self.assertEqual([o.label for o in select.options], ["호박 초", "박쥐 날개", "유령 손님"])
        self.assertEqual([o.value for o in select.options], [str(self.candle["id"]), str(self.bat["id"]), str(self.vip["id"])])
        self.assertEqual(select.options[0].description, "구매 🍬 30 · 판매 🍬 10")
        self.assertEqual(select.options[1].description, "구매 불가 · 판매 🍬 7")
        self.assertEqual([str(o.emoji) for o in select.options], ["🕯️", "🦇", "👑"])
        self.assertEqual((bag.label, str(bag.emoji)), ("가방", "🎒"))

    async def test_the_shop_uses_the_servers_currency(self):
        self.store.set_currency(100, emoji="💎", name="젬")
        embed, view = build_panel(self.store, self.w.guild)
        self.assertIn("🕯️ **호박 초**\n┕ 구매 💎 30 · 판매 💎 10", embed.description)
        self.assertEqual(embed.fields[0].name, "💎 = 젬")
        self.assertEqual(view.children[0].options[0].description, "구매 💎 30 · 판매 💎 10")
        detail = DetailView(self.w.bot, self.candle["id"], self.w.member).embed()
        self.assertEqual({f.name: f.value for f in detail.fields}["구매"], "💎 30")

    async def test_a_server_emoji_currency_becomes_its_name_in_select_descriptions(self):
        """선택창 설명에는 서버 이모지가 그림으로 안 보이므로 재화 이름을 쓴다(본문 임베드에서는 그림으로 보인다)."""
        self.store.set_currency(100, emoji="<:gem:123456789012345678>", name="젬")
        embed, view = build_panel(self.store, self.w.guild)
        self.assertEqual(view.children[0].options[0].description, "구매 젬 30 · 판매 젬 10")
        self.assertIn("구매 <:gem:123456789012345678> 30", embed.description)

    async def test_an_empty_shop(self):
        w = World()
        try:
            embed, view = build_panel(w.store, w.guild)
            self.assertIn("아직 진열된 물품이 없어요", embed.description)
            self.assertTrue(view.children[0].disabled)
        finally:
            await w.close()

    async def test_the_panel_uses_halloween_colours_and_a_footer(self):
        embed, _ = build_panel(self.store, self.w.guild)
        self.assertEqual((embed.title, embed.color.value, embed.footer.text), ("🎃 할로윈 상점", 0xFF7518, "🕸️ Happy Halloween"))

    async def test_server_emoji_become_select_emoji(self):
        self.store.update_item(100, self.candle["id"], emoji="<:pumpkin:123456789012345678>")
        _, view = build_panel(self.store, self.w.guild)
        emoji = view.children[0].options[0].emoji
        self.assertEqual((emoji.name, emoji.id), ("pumpkin", 123456789012345678))

    async def test_long_names_are_cut_for_the_select(self):
        self.store.update_item(100, self.candle["id"], name="가" * 30)
        _, view = build_panel(self.store, self.w.guild)
        self.assertLessEqual(len(view.children[0].options[0].label), 100)


class PostAndRefreshTests(UiFixture):
    async def test_post_panel_sends_and_records_it(self):
        message = await post_panel(self.w.bot, self.channel)
        sent = self.channel.sent[0]
        self.assertEqual(sent["embed"].title, "🎃 할로윈 상점")
        self.assertIsInstance(sent["view"], ShopPanelView)
        self.assertEqual([(r["channel"], r["message"]) for r in self.store.shops(100)], [(400, message.id)])

    async def test_posting_again_replaces_the_old_message(self):
        first = await post_panel(self.w.bot, self.channel)
        second = await post_panel(self.w.bot, self.channel)
        self.assertNotEqual(first.id, second.id)
        self.channel.partial[first.id].delete.assert_awaited_once()
        self.assertEqual([r["message"] for r in self.store.shops(100)], [second.id])

    async def test_posting_retries_without_emoji_when_discord_rejects_them(self):
        calls = []
        original = self.channel.send.side_effect

        async def flaky(**kwargs):
            calls.append(kwargs["view"].children[0].options[0].emoji)
            if len(calls) == 1:
                raise discord.HTTPException(NS(status=400, reason="x"), "Invalid emoji")
            return await original(**kwargs)
        self.channel.send.side_effect = flaky
        await post_panel(self.w.bot, self.channel)
        self.assertIsNotNone(calls[0])
        self.assertIsNone(calls[1])
        self.assertEqual(len(self.store.shops(100)), 1)

    async def test_refresh_edits_every_registered_panel(self):
        other = self.w.channel(401)
        await post_panel(self.w.bot, self.channel)
        await post_panel(self.w.bot, other)
        self.store.update_item(100, self.candle["id"], buy_price=45)
        self.assertEqual(await refresh_panels(self.w.bot, self.w.guild), 2)
        for channel in (self.channel, other):
            edit = list(channel.partial.values())[0].edit
            self.assertIn("구매 🍬 45", edit.await_args.kwargs["embed"].description)
            self.assertIsInstance(edit.await_args.kwargs["view"], ShopPanelView)

    async def test_deleted_messages_and_channels_are_forgotten(self):
        gone_channel = self.w.channel(401)
        message = await post_panel(self.w.bot, self.channel)
        await post_panel(self.w.bot, gone_channel)
        del self.w.channels[401]                                                  # 채널이 지워짐
        self.channel.get_partial_message(message.id).edit.side_effect = discord.NotFound(NS(status=404, reason="x"), "gone")
        self.assertEqual(await refresh_panels(self.w.bot, self.w.guild), 0)
        self.assertEqual(self.store.shops(100), [])

    async def test_refresh_retries_without_emoji_then_gives_up_quietly_on_other_errors(self):
        message = await post_panel(self.w.bot, self.channel)
        edit = self.channel.get_partial_message(message.id).edit
        edit.side_effect = [discord.HTTPException(NS(status=400, reason="x"), "emoji"), None]
        self.assertEqual(await refresh_panels(self.w.bot, self.w.guild), 1)
        self.assertEqual(edit.await_count, 2)
        edit.reset_mock()
        edit.side_effect = discord.Forbidden(NS(status=403, reason="x"), "no")
        with self.assertLogs("halloween", level="WARNING"):
            self.assertEqual(await refresh_panels(self.w.bot, self.w.guild), 0)
        self.assertEqual(len(self.store.shops(100)), 1)                           # 권한 문제라면 기록은 지우지 않는다.


class SelectAndDetailTests(UiFixture):
    async def pick(self, item_id, uid=7):
        interaction = self.w.interaction(uid=uid, data={"values": [str(item_id)]})
        await ShopPanelView().on_select(interaction)
        return interaction

    async def test_picking_an_item_shows_a_private_detail_view(self):
        self.store.add_item(100, 7, self.candle["id"], 4)
        self.store.add_candy(100, 7, 25)
        interaction = await self.pick(self.candle["id"])
        embed, kwargs = sent_embed(interaction)
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(embed.title, "🕯️ 호박 초")
        self.assertEqual(embed.description, "은은하게 빛나요")
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields, {"구매": "🍬 30", "판매": "🍬 10", "보유": "×4", "내 재화": "🍬 25"})
        buy, sell = kwargs["view"].children
        self.assertEqual((buy.disabled, sell.disabled), (False, False))

    async def test_detail_for_a_role_and_for_flags(self):
        interaction = await self.pick(self.vip["id"])
        embed, kwargs = sent_embed(interaction)
        self.assertEqual({f.name: f.value for f in embed.fields}["상태"], "아직 없어요")
        self.assertEqual(embed.title, "👑 유령 손님")
        self.assertEqual([c.disabled for c in kwargs["view"].children], [False, True])        # 이 역할은 팔 수 없다.
        self.w.member.roles.append(self.w.roles[50])
        interaction = await self.pick(self.vip["id"])
        self.assertEqual({f.name: f.value for f in sent_embed(interaction)[0].fields}["상태"], "가지고 있어요 ✅")
        interaction = await self.pick(self.bat["id"])
        self.assertEqual([c.disabled for c in sent_embed(interaction)[1]["view"].children], [True, False])

    async def test_a_removed_or_hidden_or_bogus_choice_is_refused(self):
        self.store.update_item(100, self.bat["id"], sellable=False)                    # 구입·판매가 모두 꺼짐 = 진열 해제
        for value in (self.bat["id"], 99999, 0):
            interaction = await self.pick(value)
            embed, kwargs = sent_embed(interaction)
            self.assertIn("이제 상점에 없는", embed.description)
            self.assertTrue(kwargs["ephemeral"])
            self.assertNotIn("view", kwargs)
        interaction = self.w.interaction(data={"values": ["abc"]})
        await ShopPanelView().on_select(interaction)
        self.assertIn("이제 상점에 없는", sent_embed(interaction)[0].description)
        interaction = self.w.interaction(data={})
        await ShopPanelView().on_select(interaction)
        self.assertIn("이제 상점에 없는", sent_embed(interaction)[0].description)

    async def test_only_the_person_who_picked_can_use_the_buttons(self):
        view = DetailView(self.w.bot, self.candle["id"], self.w.member)
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=7)))
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=8)))

    async def test_item_buttons_ask_for_a_quantity(self):
        view = DetailView(self.w.bot, self.candle["id"], self.w.member)
        interaction = self.w.interaction()
        await view.buy_button.callback(interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, QuantityModal)
        self.assertEqual((modal.action, modal.title), ("buy", "호박 초 구매"))
        interaction = self.w.interaction()
        await view.sell_button.callback(interaction)
        self.assertEqual(interaction.response.send_modal.await_args.args[0].title, "호박 초 판매")

    async def test_role_buttons_trade_immediately_without_asking_a_quantity(self):
        self.store.add_candy(100, 7, 250)
        view = DetailView(self.w.bot, self.vip["id"], self.w.member)
        interaction = self.w.interaction()
        await view.buy_button.callback(interaction)
        interaction.response.send_modal.assert_not_awaited()
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        embed = interaction.followup.send.await_args.kwargs["embed"]
        self.assertIn("역할을 받았어요", embed.description)
        self.assertEqual([r.id for r in self.w.member.roles], [50])

    async def test_buttons_after_the_item_was_deleted(self):
        view = DetailView(self.w.bot, self.bat["id"], self.w.member)
        self.store.delete_item(100, self.bat["id"])
        interaction = self.w.interaction()
        await view.sell_button.callback(interaction)
        self.assertIn("이제 상점에 없는", sent_embed(interaction)[0].description)


class QuantityModalTests(UiFixture):
    async def submit(self, action, text, item=None, uid=7):
        item = item or self.candle
        modal = self.modal(QuantityModal, action, item["id"], "제목", text=text)
        interaction = self.w.interaction(uid=uid)
        await modal.on_submit(interaction)
        return sent_embed(interaction)

    async def test_buying_and_selling_through_the_modal(self):
        self.store.add_candy(100, 7, 100)
        embed, kwargs = await self.submit("buy", " 2 ")
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual((embed.title, embed.description), ("🎃 완료!", "🍬 60을 내고 🕯️ **호박 초** ×2을(를) 샀어요."))
        embed, _ = await self.submit("sell", "1")
        self.assertEqual(embed.description, "🕯️ **호박 초** ×1을(를) 팔고 🍬 10을 받았어요.")
        self.assertEqual((self.store.wallet(100, 7), self.store.qty(100, 7, self.candle["id"])), (50, 1))

    async def test_failures_show_a_red_notice(self):
        embed, _ = await self.submit("buy", "5")
        self.assertEqual((embed.title, embed.color.value), ("👻 앗!", 0xC0392B))
        self.assertIn("부족해요", embed.description)
        self.assertIn("모자라요", (await self.submit("sell", "1"))[0].description)

    async def test_bad_input(self):
        for text in ("", "abc", "-1", "1.5", "１", "٣", "0", "1000"):
            embed, _ = await self.submit("buy", text)
            self.assertEqual(embed.title, "👻 앗!", text)
            self.assertTrue("수량" in embed.description, text)
        self.assertEqual(self.store.wallet(100, 7), 0)

    async def test_a_deleted_item(self):
        self.store.delete_item(100, self.bat["id"])
        embed, _ = await self.submit("sell", "1", item=self.bat)
        self.assertIn("이제 상점에 없는", embed.description)


class BagTests(UiFixture):
    async def test_the_bag_shows_candy_items_and_roles(self):
        ids = self.store.ensure_system_items(100)
        self.store.add_candy(100, 7, 12345)
        self.store.add_item(100, 7, ids["key"], 2)
        self.store.add_item(100, 7, self.candle["id"], 5)
        self.w.member.roles.append(self.w.roles[50])
        embed = bag_for(self.w.bot, self.w.member)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(embed.title, "🎒 user7의 가방")
        self.assertIn("**12,345** / 99,999", fields["🍬 할로윈 재화"])
        self.assertTrue(fields["🍬 할로윈 재화"].endswith("🟧" + "⬛" * 9))          # 12% → 10칸 중 1칸
        self.assertNotIn("✨ 경험치", fields)                                      # 경험치 기능은 없다.
        self.assertEqual(fields["🎒 물품"], "🗝️ 열쇠 ×2\n🕯️ 호박 초 ×5")
        self.assertEqual(fields["👑 역할"], "👑 유령 손님")

    async def test_an_empty_bag(self):
        embed = bag_for(self.w.bot, self.w.new_member(9, name="새내기"))
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(embed.title, "🎒 새내기의 가방")
        self.assertIn("**0** / 99,999", fields["🍬 할로윈 재화"])
        self.assertEqual(fields["🎒 물품"], "비어 있어요 🕸️")
        self.assertNotIn("👑 역할", fields)
        self.assertNotIn("✨ 경험치", fields)

    async def test_candy_bar_scales_with_the_cap(self):
        from theme import candy_bar
        self.assertEqual(candy_bar(0, CANDY_MAX), "⬛" * 10)
        self.assertEqual(candy_bar(CANDY_MAX, CANDY_MAX), "🟧" * 10)
        self.assertEqual(candy_bar(CANDY_MAX // 2, CANDY_MAX), "🟧" * 5 + "⬛" * 5)
        self.assertEqual(candy_bar(50, 100), "🟧" * 5 + "⬛" * 5)
        self.assertEqual(candy_bar(500, 100), "🟧" * 10)                                # 최대를 낮춰도 칸이 넘치지 않는다.

    async def test_the_bag_is_private_and_the_panel_button_opens_it(self):
        interaction = self.w.interaction()
        await send_bag(interaction)
        embed, kwargs = sent_embed(interaction)
        self.assertTrue(kwargs["ephemeral"])
        self.assertIsNone(kwargs.get("view"))                                      # 가방에는 버튼이 없다(경험치 교환 제거).
        panel_click = self.w.interaction()
        await ShopPanelView().on_bag(panel_click)
        self.assertEqual(sent_embed(panel_click)[0].title, "🎒 user7의 가방")

    async def test_the_bag_uses_the_servers_currency(self):
        self.store.set_currency(100, emoji="💎", name="젬", max=200)
        self.store.add_candy(100, 7, 60)
        fields = {f.name: f.value for f in bag_for(self.w.bot, self.w.member).fields}
        self.assertIn("**60** / 200", fields["💎 젬"])
        self.assertTrue(fields["💎 젬"].endswith("🟧" * 3 + "⬛" * 7))                  # 30% → 3칸


if __name__ == "__main__":
    unittest.main()
