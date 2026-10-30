"""명령어 시험: 관리자 전용, 이벤트 채널·설정, 지급, 상점 등록, 물품·역할 관리, 삭제 확인."""
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock

import discord

from cogs.admin import Admin, item_choices
from cogs.economy import Economy
from cogs.shop import ConfirmDelete, Shop
from store import CANDY_MAX, DEFAULT_SETTINGS, MAX_SHOP_ITEMS
from tests.helpers import FakeRole, World, sent_embed

Choice = discord.app_commands.Choice


class CommandFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.admin, self.shop, self.economy = Admin(self.w.bot), Shop(self.w.bot), Economy(self.w.bot)
        self.channel = self.w.channel(400)
        self.target = self.w.new_member(8, name="손님")
        refresh = mock.patch("cogs.shop.refresh_panels", new=AsyncMock(return_value=1))
        self.refresh = refresh.start()
        self.addCleanup(refresh.stop)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def call(self, command, cog, interaction, *args, **kwargs):
        await command.callback(cog, interaction, *args, **kwargs)
        return interaction

    def reply(self, interaction):
        embed, kwargs = sent_embed(interaction)
        return embed.description if embed else None, kwargs


class AdminOnlyTests(CommandFixture):
    def every_admin_call(self):
        role = self.w.add_role(60, name="새 역할")
        item = self.store.create_item(100, "초", buyable=True, buy_price=5)
        return [
            (Admin.add_channel, self.admin, (self.channel,)), (Admin.remove_channel, self.admin, (self.channel,)),
            (Admin.list_channels, self.admin, ()),
            (Admin.settings, self.admin, (), dict(candy_chance=99.0)),
            (Admin.give_candy, self.admin, (self.target, 50)), (Admin.give_exp, self.admin, (self.target, 50)),
            (Admin.give_item, self.admin, (self.target, "초", 3)),
            (Shop.register_shop, self.shop, (self.channel,)), (Shop.unregister_shop, self.shop, (self.channel,)),
            (Shop.add_item, self.shop, ("나쁜 물품", True, True, 5, 5)),
            (Shop.add_role, self.shop, (role, True, True, 5, 5)),
            (Shop.edit_item, self.shop, ("초", ), dict(buy_price=1)),
            (Shop.delete_item, self.shop, ("초",)), (Shop.list_items, self.shop, ()),
        ]

    async def test_regular_members_are_refused_and_nothing_changes(self):
        calls = self.every_admin_call()
        before = (self.store.settings(100), [tuple(r) for r in self.store.items(100)], self.store.wallet(100, 8),
                  self.store.shops(100))
        for command, cog, args, *rest in calls:
            interaction = self.ask(admin=False)
            await command.callback(cog, interaction, *args, **(rest[0] if rest else {}))
            text, kwargs = self.reply(interaction)
            self.assertIn("관리자만", text, command.name)
            self.assertTrue(kwargs["ephemeral"], command.name)                  # 거절 메시지는 본인에게만 보인다.
        after = (self.store.settings(100), [tuple(r) for r in self.store.items(100)], self.store.wallet(100, 8),
                 self.store.shops(100))
        self.assertEqual(before, after)
        self.w.channels[400].send.assert_not_awaited()

    async def test_the_server_owner_counts_as_an_admin_even_without_the_permission_bit(self):
        interaction = self.ask(admin=False, uid=1)                              # 서버 주인(id 1)
        await self.call(Admin.give_candy, self.admin, interaction, self.target, 5)
        self.assertEqual(self.store.wallet(100, 8)[0], 5)

    async def test_every_admin_command_is_hidden_from_non_admins_in_discord(self):
        for cog in (self.admin, self.shop):
            for command in cog.get_app_commands():
                self.assertTrue(command.default_permissions.administrator, command.name)
                self.assertTrue(command.guild_only, command.name)
        for command in self.economy.get_app_commands():
            self.assertIsNone(command.default_permissions, command.name)           # /가방 등은 모두가 쓴다.


class EventChannelTests(CommandFixture):
    async def test_add_remove_and_list(self):
        interaction = await self.call(Admin.add_channel, self.admin, self.ask(), self.channel)
        self.assertEqual(self.store.settings(100)["channels"], [400])
        self.assertIn("<#400>에서 재화·EXP·패키지가 나와요", self.reply(interaction)[0])
        self.assertNotIn("⚠️", self.reply(interaction)[0])
        interaction = await self.call(Admin.add_channel, self.admin, self.ask(), self.channel)
        self.assertIn("이미 이벤트 채널", self.reply(interaction)[0])
        self.assertEqual(self.store.settings(100)["channels"], [400])
        interaction = await self.call(Admin.list_channels, self.admin, self.ask())
        self.assertIn("<#400>", sent_embed(interaction)[0].fields[0].value)
        interaction = await self.call(Admin.remove_channel, self.admin, self.ask(), self.channel)
        self.assertEqual(self.store.settings(100)["channels"], [])
        interaction = await self.call(Admin.remove_channel, self.admin, self.ask(), self.channel)
        self.assertIn("이벤트 채널이 아니에요", self.reply(interaction)[0])

    async def test_a_channel_the_bot_cannot_post_in_gets_a_warning(self):
        quiet = self.w.channel(401, perms=discord.Permissions(view_channel=True))
        interaction = await self.call(Admin.add_channel, self.admin, self.ask(), quiet)
        self.assertIn("⚠️", self.reply(interaction)[0])
        self.assertIn("패키지가 나오려면", self.reply(interaction)[0])
        self.assertEqual(self.store.settings(100)["channels"], [401])             # 경고만 하고 등록은 한다.

    async def test_channel_limit(self):
        self.store.update_settings(100, channels=list(range(1, 101)))
        interaction = await self.call(Admin.add_channel, self.admin, self.ask(), self.channel)
        self.assertIn("최대 100개", self.reply(interaction)[0])

    async def test_servers_are_independent(self):
        await self.call(Admin.add_channel, self.admin, self.ask(), self.channel)
        self.assertEqual(self.store.settings(200)["channels"], [])


class SettingsCommandTests(CommandFixture):
    async def test_without_changes_it_shows_the_current_settings(self):
        interaction = await self.call(Admin.settings, self.admin, self.ask())
        embed, kwargs = sent_embed(interaction)
        text = "\n".join(f"{f.name} {f.value}" for f in embed.fields)
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("**50%** 확률로 **1~5**개", text)
        self.assertIn("**0.1%** 확률 · 누구나 한 번씩 · 120초", text)
        self.assertIn("아직 없어요", text)
        self.assertEqual(self.store.settings(100), DEFAULT_SETTINGS)

    async def test_changes_apply_and_are_shown(self):
        interaction = await self.call(Admin.settings, self.admin, self.ask(), candy_chance=75.5, candy_min=2, candy_max=9,
                                     candy_cooldown=0, package_chance=1.5, package_mode=Choice(name="먼저", value="first"),
                                     package_seconds=45, reaction=False, exp_max=0, exp_min=0)
        cfg = self.store.settings(100)
        self.assertEqual((cfg["candy_chance"], cfg["candy_min"], cfg["candy_max"], cfg["candy_cooldown"]), (75.5, 2, 9, 0))
        self.assertEqual((cfg["package_chance"], cfg["package_mode"], cfg["package_seconds"], cfg["reaction"]), (1.5, "first", 45, False))
        text = "\n".join(f"{f.name} {f.value}" for f in sent_embed(interaction)[0].fields)
        self.assertIn("**75.5%** 확률로 **2~9**개", text)
        self.assertIn("먼저 뜯은 한 명만", text)
        self.assertIn("쿨타임: 없음", text)

    async def test_invalid_combinations_are_refused_and_change_nothing(self):
        interaction = await self.call(Admin.settings, self.admin, self.ask(), candy_min=9, candy_max=3)
        embed, _ = sent_embed(interaction)
        self.assertEqual(embed.title, "👻 앗!")
        self.assertIn("최소값이 최대값보다", embed.description)
        self.assertEqual(self.store.settings(100), DEFAULT_SETTINGS)


class GrantTests(CommandFixture):
    async def test_candy_and_exp_grants_report_the_real_change(self):
        interaction = await self.call(Admin.give_candy, self.admin, self.ask(), self.target, 40)
        self.assertEqual(self.reply(interaction)[0], "<@8>의 할로윈 재화이 +40만큼 바뀌었어요. (지금 🍬 40)")
        interaction = await self.call(Admin.give_candy, self.admin, self.ask(), self.target, CANDY_MAX)
        self.assertIn(f"+{CANDY_MAX - 40:,}만큼", self.reply(interaction)[0])                   # 상한까지만
        interaction = await self.call(Admin.give_candy, self.admin, self.ask(), self.target, -99999)
        self.assertEqual(self.store.wallet(100, 8)[0], 0)
        interaction = await self.call(Admin.give_exp, self.admin, self.ask(), self.target, 20000)
        self.assertIn("✨ 20,000", self.reply(interaction)[0])
        self.assertEqual(self.store.wallet(100, 8)[1], 20000)

    async def test_bots_are_refused(self):
        bot = self.w.new_member(77)
        bot.bot = True
        for command, args in ((Admin.give_candy, (bot, 5)), (Admin.give_exp, (bot, 5)), (Admin.give_item, (bot, "열쇠", 1))):
            interaction = await self.call(command, self.admin, self.ask(), *args)
            self.assertIn("봇에게는", self.reply(interaction)[0])
        self.assertEqual(self.store.wallet(100, 77), (0, 0))

    async def test_item_grants(self):
        interaction = await self.call(Admin.give_item, self.admin, self.ask(), self.target, "열쇠", 3)       # 기본 물품은 처음 쓸 때 만들어진다.
        self.assertIn("🗝️ 열쇠이(가) +3개", self.reply(interaction)[0])
        self.assertIn("(지금 ×3)", self.reply(interaction)[0])
        interaction = await self.call(Admin.give_item, self.admin, self.ask(), self.target, "열쇠", -10)
        self.assertIn("-3개", self.reply(interaction)[0])                                                 # 없는 만큼은 뺄 수 없다.
        interaction = await self.call(Admin.give_item, self.admin, self.ask(), self.target, "없는 물품", 1)
        self.assertIn("그런 물품이 없어요", self.reply(interaction)[0])

    async def test_roles_cannot_be_granted_as_items(self):
        self.w.add_role(50)
        self.store.create_item(100, "VIP", kind="role", role_id=50, buyable=True, buy_price=5)
        interaction = await self.call(Admin.give_item, self.admin, self.ask(), self.target, "VIP", 1)
        self.assertIn("역할은 지급할 수 없어요", self.reply(interaction)[0])

    async def test_autocomplete_lists_items_but_not_roles_for_grants(self):
        self.w.add_role(50)
        self.store.create_item(100, "VIP", kind="role", role_id=50, buyable=True, buy_price=5)
        self.store.create_item(100, "초", emoji="🕯️")
        choices = await self.admin.give_item_autocomplete(self.ask(), "")
        self.assertEqual([c.value for c in choices], ["초", "열쇠", "쓰레기"])             # 만들어진 순서
        self.assertEqual(choices[1].name, "🗝️ 열쇠")
        self.assertEqual([c.value for c in await self.admin.give_item_autocomplete(self.ask(), "쓰")], ["쓰레기"])
        self.assertEqual(await self.admin.give_item_autocomplete(NS(guild_id=None), "x"), [])

    async def test_item_choices_are_limited_to_25(self):
        for n in range(30):
            self.store.create_item(100, f"물품{n:02}")
        self.assertEqual(len(await item_choices(self.store, 100, "물품")), 25)


class ShopRegistrationTests(CommandFixture):
    async def test_registering_in_the_current_channel(self):
        interaction = await self.call(Shop.register_shop, self.shop, self.ask(channel=self.channel))
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.channel.sent[0]["embed"].title, "🎃 할로윈 상점")
        self.assertEqual(len(self.store.shops(100)), 1)
        text = interaction.followup.send.await_args.kwargs["embed"].description
        self.assertIn("<#400>에 상점을 열었어요", text)

    async def test_registering_in_another_channel_and_twice(self):
        other = self.w.channel(401)
        await self.call(Shop.register_shop, self.shop, self.ask(channel=self.channel), other)
        self.assertEqual(self.channel.sent, [])
        self.assertEqual(len(other.sent), 1)
        await self.call(Shop.register_shop, self.shop, self.ask(channel=self.channel), other)
        self.assertEqual(len(self.store.shops(100)), 1)                                   # 같은 채널에는 상점이 하나

    async def test_refusals(self):
        thread = NS(id=5, mention="<#5>")
        interaction = await self.call(Shop.register_shop, self.shop, self.ask(channel=thread))
        self.assertIn("일반 텍스트 채널", self.reply(interaction)[0])
        quiet = self.w.channel(402, perms=discord.Permissions(view_channel=True, send_messages=True))
        interaction = await self.call(Shop.register_shop, self.shop, self.ask(channel=quiet))
        self.assertIn("채널 권한", self.reply(interaction)[0])
        self.channel.send.side_effect = discord.Forbidden(NS(status=403, reason="x"), "no")
        interaction = await self.call(Shop.register_shop, self.shop, self.ask(channel=self.channel))
        self.assertIn("올리지 못했어요", interaction.followup.send.await_args.kwargs["embed"].description)
        self.assertEqual(self.store.shops(100), [])

    async def test_unregistering(self):
        await self.call(Shop.register_shop, self.shop, self.ask(channel=self.channel))
        message_id = self.store.shops(100)[0]["message"]
        interaction = await self.call(Shop.unregister_shop, self.shop, self.ask(channel=self.channel))
        self.assertIn("상점을 없앴어요", self.reply(interaction)[0])
        self.channel.get_partial_message(message_id).delete.assert_awaited_once()
        self.assertEqual(self.store.shops(100), [])
        interaction = await self.call(Shop.unregister_shop, self.shop, self.ask(channel=self.channel))
        self.assertIn("열려 있는 상점이 없어요", self.reply(interaction)[0])


class ItemManagementTests(CommandFixture):
    async def test_add_item_and_the_panel_refresh(self):
        interaction = await self.call(Shop.add_item, self.shop, self.ask(), "호박 초", True, False, 30, 0, "은은해요", "🕯️")
        embed, kwargs = sent_embed(interaction)
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("🕯️ **호박 초**을(를) 등록했어요.", embed.description)
        self.assertIn("구입 ✅ 🍬 30 · 판매 ❌", embed.description)
        item = self.store.item_by_name(100, "호박 초")
        self.assertEqual((item["buyable"], item["sellable"], item["buy_price"], item["description"]), (1, 0, 30, "은은해요"))
        self.refresh.assert_awaited_once()                                                   # 상점 메시지가 새 목록으로 바뀐다.

    async def test_add_item_true_false_flags_and_price_rules(self):
        interaction = await self.call(Shop.add_item, self.shop, self.ask(), "파는 물건", False, True, 0, 12)
        self.assertIn("구입 ❌ · 판매 ✅ 🍬 12", self.reply(interaction)[0])
        interaction = await self.call(Shop.add_item, self.shop, self.ask(), "숨김", False, False)
        self.assertIn("상점에는 나오지 않아요", self.reply(interaction)[0])
        for args, text in [(("공짜", True, False, 0), "구매가"), (("공짜2", False, True, 0, 0), "판매가"),
                           (("파는 물건", True, True, 5, 5), "같은 이름"), (("글자", True, False, 5, 0, "", "abc"), "이모지"),
                           (("  ", True, False, 5), "이름")]:
            before = self.refresh.await_count
            interaction = await self.call(Shop.add_item, self.shop, self.ask(), *args)
            embed, _ = sent_embed(interaction)
            self.assertEqual(embed.title, "👻 앗!", args)
            self.assertIn(text, embed.description)
            self.assertEqual(self.refresh.await_count, before)                          # 실패하면 패널을 고치지 않는다.

    async def test_the_shop_holds_25_items(self):
        for n in range(MAX_SHOP_ITEMS):
            self.store.create_item(100, f"물품{n}", buyable=True, buy_price=1)
        interaction = await self.call(Shop.add_item, self.shop, self.ask(), "26번째", True, False, 5)
        self.assertIn("최대 25개", self.reply(interaction)[0])

    async def test_add_role_checks_safety(self):
        safe = self.w.add_role(60, name="유령 손님", position=2)
        interaction = await self.call(Shop.add_role, self.shop, self.ask(), safe, True, True, 100, 40)
        self.assertIn("<@&60> 역할을 상점에 등록했어요", self.reply(interaction)[0])
        item = self.store.item_by_name(100, "유령 손님")
        self.assertEqual((item["kind"], item["role_id"], item["emoji"]), ("role", 60, "👑"))
        cases = [(FakeRole(61, permissions=discord.Permissions(administrator=True), name="관리자"), "관리 권한"),
                 (FakeRole(62, managed=True, name="봇 역할"), "관리하는"), (FakeRole(63, position=50, name="높은 역할"), "위에 있는"),
                 (FakeRole(64, default=True, name="@everyone"), "@everyone"),
                 (FakeRole(65, permissions=discord.Permissions(manage_messages=True), name="관리"), "관리 권한"),
                 (FakeRole(60, name="유령 손님", position=2), "이미 상점 물품으로 등록")]
        for role, text in cases:
            self.w.roles[role.id] = role
            interaction = await self.call(Shop.add_role, self.shop, self.ask(), role, True, False, 10)
            self.assertIn(text, self.reply(interaction)[0], role.name)
        self.assertEqual([r["name"] for r in self.store.items(100) if r["kind"] == "role"], ["유령 손님"])

    async def test_long_role_names_are_cut(self):
        role = self.w.add_role(70, name="긴" * 50, position=2)
        await self.call(Shop.add_role, self.shop, self.ask(), role, True, False, 10)
        self.assertEqual(self.store.items(100)[0]["name"], "긴" * 30)

    async def test_edit_item(self):
        self.store.create_item(100, "초", buy_price=5, buyable=True, description="옛 설명")
        interaction = await self.call(Shop.edit_item, self.shop, self.ask(), "초", new_name="긴 초", sellable=True, sell_price=2,
                                     emoji="🕯️")
        self.assertIn("🕯️ **긴 초**을(를) 고쳤어요.", self.reply(interaction)[0])
        item = self.store.item_by_name(100, "긴 초")
        self.assertEqual((item["sellable"], item["sell_price"], item["buy_price"], item["description"]), (1, 2, 5, "옛 설명"))
        await self.call(Shop.edit_item, self.shop, self.ask(), "긴 초", description="")
        self.assertEqual(self.store.item_by_name(100, "긴 초")["description"], "")                  # 빈 문자열로 설명을 지울 수 있다.
        await self.call(Shop.edit_item, self.shop, self.ask(), "긴 초", buyable=False)
        self.assertEqual(self.store.item_by_name(100, "긴 초")["buyable"], 0)
        self.assertEqual(self.refresh.await_count, 3)

    async def test_edit_item_errors(self):
        self.store.create_item(100, "초", buy_price=5, buyable=True)
        for args, kwargs, text in [(("없는 것",), dict(buy_price=1), "그런 물품이 없어요"), (("초",), {}, "바꿀 내용을"),
                                   (("초",), dict(buyable=True, buy_price=0), "구매가"), (("초",), dict(sellable=True), "판매가")]:
            interaction = await self.call(Shop.edit_item, self.shop, self.ask(), *args, **kwargs)
            self.assertIn(text, self.reply(interaction)[0])
        self.assertEqual(self.refresh.await_count, 0)

    async def test_system_items_can_be_put_on_sale_but_not_renamed(self):
        interaction = await self.call(Shop.edit_item, self.shop, self.ask(), "쓰레기", sellable=True, sell_price=2)
        self.assertIn("🗑️ **쓰레기**을(를) 고쳤어요.", self.reply(interaction)[0])
        interaction = await self.call(Shop.edit_item, self.shop, self.ask(), "열쇠", new_name="마스터 키")
        self.assertIn("기본 물품의 이름", self.reply(interaction)[0])

    async def test_list_items(self):
        self.w.add_role(50, name="유령 손님")
        self.store.create_item(100, "초", emoji="🕯️", buy_price=5, buyable=True)
        self.store.create_item(100, "VIP", kind="role", role_id=50, sell_price=9, sellable=True)
        interaction = await self.call(Shop.list_items, self.shop, self.ask())
        text = sent_embed(interaction)[0].description
        self.assertIn("🕯️ **초** · 구입 🍬 5 · 판매 ❌", text)
        self.assertIn("**유령 손님** `역할` · 구입 ❌ · 판매 🍬 9", text)
        self.assertIn("**열쇠** `기본`", text)
        self.assertIn("상점에 진열 2/25", text)

    async def test_item_autocomplete(self):
        self.store.create_item(100, "호박 초")
        choices = await self.shop.item_autocomplete(self.ask(), "호박")
        self.assertEqual([c.value for c in choices], ["호박 초"])
        self.assertEqual([c.value for c in await self.shop.item_autocomplete(self.ask(), "")], ["호박 초", "열쇠", "쓰레기"])
        self.assertEqual(await self.shop.item_autocomplete(NS(guild_id=None), ""), [])


class DeleteTests(CommandFixture):
    async def start(self, name="초"):
        interaction = await self.call(Shop.delete_item, self.shop, self.ask(uid=1000), name)
        embed, kwargs = sent_embed(interaction)
        return embed, kwargs.get("view"), interaction

    async def test_delete_asks_first_and_warns_about_bags(self):
        item = self.store.create_item(100, "초", emoji="🕯️", buy_price=5, buyable=True)
        self.store.add_item(100, 7, item["id"], 3)
        self.store.add_item(100, 8, item["id"], 4)
        embed, view, _ = await self.start()
        self.assertIn("가방에 있는 **7개**도 함께 사라져요", embed.description)
        self.assertIsNotNone(self.store.item_by_name(100, "초"))                      # 확인 전에는 지우지 않는다.
        interaction = self.ask(uid=1000)
        await view.children[0].callback(interaction)
        self.assertIn("가방에 있던 7개도 함께", interaction.response.edit_message.await_args.kwargs["embed"].description)
        self.assertIsNone(self.store.item_by_name(100, "초"))
        self.assertEqual(self.store.inventory(100, 7), [])
        self.refresh.assert_awaited_once()

    async def test_cancel_and_other_people_cannot_confirm(self):
        self.store.create_item(100, "초", buyable=True, buy_price=5)
        _, view, _ = await self.start()
        self.assertTrue(await view.interaction_check(self.ask(uid=1000)))
        self.assertFalse(await view.interaction_check(self.ask(uid=2000)))
        interaction = self.ask(uid=1000)
        await view.children[1].callback(interaction)
        self.assertIn("취소", interaction.response.edit_message.await_args.kwargs["embed"].description)
        self.assertIsNotNone(self.store.item_by_name(100, "초"))
        self.refresh.assert_not_awaited()

    async def test_unknown_and_system_items(self):
        embed, view, _ = await self.start("없는 것")
        self.assertIn("그런 물품이 없어요", embed.description)
        self.assertIsNone(view)
        self.store.ensure_system_items(100)
        embed, view, _ = await self.start("열쇠")
        self.assertIn("기본 물품", embed.description)
        self.assertIsNone(view)

    async def test_an_item_deleted_by_someone_else_in_the_meantime(self):
        item = self.store.create_item(100, "초", buyable=True, buy_price=5)
        _, view, _ = await self.start()
        self.store.delete_item(100, item["id"])
        interaction = self.ask(uid=1000)
        await view.children[0].callback(interaction)
        self.assertEqual(interaction.response.edit_message.await_args.kwargs["embed"].title, "👻 앗!")


class EconomyCommandTests(CommandFixture):
    async def test_help_shows_the_servers_own_numbers(self):
        interaction = await self.call(Economy.help, self.economy, self.ask(admin=False))
        embed, kwargs = sent_embed(interaction)
        text = "\n".join(f"{f.name} {f.value}" for f in embed.fields)
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("아직 정해지지 않았어요", text)
        self.assertIn("**50%** 확률로 **1~5**개", text)
        self.assertIn("**0.1%** 확률로 패키지", text)
        self.assertIn("누구나 한 번씩", text)
        self.assertIn("EXP 15,000 → 🍬 15", text)
        self.assertIn("꽝 60% · 🗝️ 열쇠 20% · 🍬 50개 10% · 🗑️ 쓰레기 10%", text)
        self.assertIn("최대 99,999개", text)
        self.store.update_settings(100, channels=[400, 401], candy_chance=80, package_mode="first")
        interaction = await self.call(Economy.help, self.economy, self.ask(admin=False))
        text = "\n".join(f"{f.name} {f.value}" for f in sent_embed(interaction)[0].fields)
        self.assertIn("<#400>, <#401>", text)
        self.assertIn("**80%**", text)
        self.assertIn("먼저 뜯은 한 명만", text)

    async def test_bag_command(self):
        interaction = await self.call(Economy.bag, self.economy, self.ask(admin=False))
        embed, kwargs = sent_embed(interaction)
        self.assertEqual(embed.title, "🎒 user7의 가방")
        self.assertTrue(kwargs["ephemeral"])

    async def test_exchange_command(self):
        self.store.add_exp(100, 7, 31000)
        interaction = await self.call(Economy.exchange, self.economy, self.ask(admin=False), 2)
        embed, kwargs = sent_embed(interaction)
        self.assertEqual(embed.description, "✨ EXP 30,000을 내고 🍬 30을 샀어요.")
        self.assertTrue(kwargs["ephemeral"])
        interaction = await self.call(Economy.exchange, self.economy, self.ask(admin=False), 1)
        self.assertEqual(sent_embed(interaction)[0].title, "👻 앗!")
        self.assertEqual(self.store.wallet(100, 7), (30, 1000))


if __name__ == "__main__":
    unittest.main()
