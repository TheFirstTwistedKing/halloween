"""실제 디스코드 상호작용 객체를 명령 처리 경로(tree._call)에 통과시켜서, 한글 옵션 이름과 권한 연결을 확인한다."""
import asyncio
import unittest
from unittest import mock
from unittest.mock import AsyncMock

import discord

from main import HalloweenBot
from store import Store

ADMIN, MEMBER = str(discord.Permissions(administrator=True).value), str(discord.Permissions(send_messages=True).value)


class RealPathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.bot = HalloweenBot(self.store)
        self.state = self.bot._connection
        self.state.user = discord.ClientUser(state=self.state, data={
            "id": "999", "username": "bot", "discriminator": "0", "avatar": None, "bot": True})
        self.bot._ready = asyncio.Event()
        for extension in ("cogs.economy", "cogs.admin", "cogs.shop", "cogs.dungeon_admin"):
            await self.bot.load_extension(extension)
        self.sent = {}
        for name in ("send_message", "defer", "autocomplete"):
            patcher = mock.patch.object(discord.InteractionResponse, name, new_callable=AsyncMock)
            self.sent[name] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(discord.Webhook, "send", new_callable=AsyncMock)
        self.followup = patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    def interaction(self, name, options=(), *, perms=MEMBER, kind=2, user_id=20):
        payload = {"id": "1", "application_id": "2", "type": kind, "token": "tok", "version": 1, "guild_id": "100",
                   "channel_id": "5", "locale": "ko", "guild_locale": "ko", "app_permissions": "0",
                   "attachment_size_limit": 10485760, "entitlements": [], "authorizing_integration_owners": {}, "context": 0,
                   "data": {"id": "3", "name": name, "type": 1, "options": list(options)},
                   "member": {"user": {"id": str(user_id), "username": "u", "discriminator": "0", "avatar": None},
                              "roles": [], "joined_at": "2026-01-01T00:00:00+00:00", "deaf": False, "mute": False,
                              "flags": 0, "permissions": perms}}
        return discord.Interaction(data=payload, state=self.state)

    def sent_embed(self):
        call = self.sent["send_message"].await_args
        return call.kwargs.get("embed"), call.kwargs

    async def test_register_an_item_with_korean_option_names(self):
        options = [{"name": "이름", "type": 3, "value": "호박 초"}, {"name": "구입", "type": 5, "value": True},
                   {"name": "판매", "type": 5, "value": False}, {"name": "구입가", "type": 4, "value": 30},
                   {"name": "설명", "type": 3, "value": "은은해요"}, {"name": "이모지", "type": 3, "value": "🕯️"}]
        await self.bot.tree._call(self.interaction("물품등록", options, perms=ADMIN))
        item = self.store.item_by_name(100, "호박 초")
        self.assertEqual((item["buyable"], item["sellable"], item["buy_price"], item["emoji"], item["description"]),
                         (1, 0, 30, "🕯️", "은은해요"))
        self.assertIn("등록했어요", self.sent_embed()[0].description)

    async def test_the_same_command_from_a_regular_member_does_nothing(self):
        options = [{"name": "이름", "type": 3, "value": "몰래 물품"}, {"name": "구입", "type": 5, "value": True},
                   {"name": "판매", "type": 5, "value": True}, {"name": "구입가", "type": 4, "value": 1}]
        await self.bot.tree._call(self.interaction("물품등록", options, perms=MEMBER))
        self.assertIsNone(self.store.item_by_name(100, "몰래 물품"))
        embed, kwargs = self.sent_embed()
        self.assertIn("관리자만", embed.description)
        self.assertTrue(kwargs["ephemeral"])

    async def test_settings_with_a_number_a_choice_and_a_boolean(self):
        options = [{"name": "재화확률", "type": 10, "value": 80.5}, {"name": "패키지방식", "type": 3, "value": "first"},
                   {"name": "패키지확률", "type": 10, "value": 0.25}, {"name": "반응", "type": 5, "value": False},
                   {"name": "재화쿨타임", "type": 4, "value": 0}]
        await self.bot.tree._call(self.interaction("이벤트설정", options, perms=ADMIN))
        cfg = self.store.settings(100)
        self.assertEqual((cfg["candy_chance"], cfg["package_mode"], cfg["package_chance"], cfg["reaction"], cfg["candy_cooldown"]),
                         (80.5, "first", 0.25, False, 0))

    async def test_bag_and_exchange_commands(self):
        self.store.add_exp(100, 20, 15000)
        await self.bot.tree._call(self.interaction("가방"))
        embed, kwargs = self.sent_embed()
        self.assertEqual(embed.title, "🎒 u의 가방")
        self.assertTrue(kwargs["ephemeral"])
        await self.bot.tree._call(self.interaction("경험치교환", [{"name": "횟수", "type": 4, "value": 1}]))
        self.assertEqual(self.store.wallet(100, 20), (15, 0))
        self.assertIn("🍬 15", self.sent_embed()[0].description)

    async def test_grant_candy_through_a_resolved_member(self):
        payload_extra = {"resolved": {"users": {"8": {"id": "8", "username": "손님", "discriminator": "0", "avatar": None}},
                                      "members": {"8": {"roles": [], "joined_at": "2026-01-01T00:00:00+00:00", "deaf": False,
                                                        "mute": False, "flags": 0, "permissions": MEMBER}}}}
        interaction = self.interaction("재화지급", [{"name": "대상", "type": 6, "value": "8"}, {"name": "수량", "type": 4, "value": 25}],
                                       perms=ADMIN)
        interaction.data.update(payload_extra)
        interaction.data["resolved"]["members"]["8"]["user"] = interaction.data["resolved"]["users"]["8"]
        await self.bot.tree._call(interaction)
        self.assertEqual(self.store.wallet(100, 8)[0], 25)

    async def test_autocomplete_for_item_names_is_wired(self):
        self.store.create_item(100, "호박 초", emoji="🕯️")
        options = [{"name": "물품", "type": 3, "value": "호박", "focused": True}]
        interaction = self.interaction("물품수정", options, kind=4, perms=ADMIN)
        await self.bot.tree._call(interaction)
        call = self.sent["autocomplete"].await_args
        choices = call.args[0] if call.args else call.kwargs["choices"]
        self.assertEqual([(c.name, c.value) for c in choices], [("🕯️ 호박 초", "호박 초")])

    async def test_dungeon_balance_commands_with_korean_option_names(self):
        await self.bot.tree._call(self.interaction("던전설정", [{"name": "항목", "type": 3, "value": "몬스터.슬라임.체력"},
                                                              {"name": "값", "type": 10, "value": 300}], perms=ADMIN))
        self.assertEqual(self.store.settings(100)["dungeon"], {"몬스터.슬라임.체력": 300})
        self.assertIn("270 → **300**", self.sent_embed()[0].description)
        await self.bot.tree._call(self.interaction("던전설정확인", [{"name": "분류", "type": 3, "value": "몬스터"}], perms=ADMIN))
        self.assertIn("`몬스터.슬라임.체력` **300** ✏️ 기본 270", self.sent_embed()[0].description)
        await self.bot.tree._call(self.interaction("던전설정초기화", [{"name": "항목", "type": 3, "value": "몬스터.슬라임.체력"}], perms=ADMIN))
        self.assertEqual(self.store.settings(100)["dungeon"], {})
        await self.bot.tree._call(self.interaction("던전시뮬", [{"name": "판수", "type": 4, "value": 100}], perms=ADMIN))
        self.sent["defer"].assert_awaited()
        embed = self.followup.await_args.kwargs["embed"]
        self.assertIn("100판", embed.title)

    async def test_dungeon_balance_commands_refuse_regular_members_on_the_real_path(self):
        await self.bot.tree._call(self.interaction("던전설정", [{"name": "항목", "type": 3, "value": "몬스터.슬라임.체력"},
                                                              {"name": "값", "type": 10, "value": 300}], perms=MEMBER))
        self.assertEqual(self.store.settings(100)["dungeon"], {})
        self.assertIn("관리자만", self.sent_embed()[0].description)

    async def test_autocomplete_for_dungeon_balance_keys_is_wired(self):
        self.store.update_settings(100, dungeon={"몬스터.마녀.공격": 99})
        options = [{"name": "항목", "type": 3, "value": "마녀", "focused": True}]
        for command in ("던전설정", "던전설정초기화"):
            self.sent["autocomplete"].reset_mock()
            await self.bot.tree._call(self.interaction(command, options, kind=4, perms=ADMIN))
            call = self.sent["autocomplete"].await_args
            choices = call.args[0] if call.args else call.kwargs["choices"]
            self.assertIn(("✏️ 몬스터.마녀.공격 = 99 (기본 135)", "몬스터.마녀.공격"), [(c.name, c.value) for c in choices], command)

    async def test_all_commands_serialize_for_discord(self):
        commands = self.bot.tree.get_commands()
        self.assertEqual(len(commands), 21)
        for command in commands:
            payload = command.to_dict(self.bot.tree)
            self.assertLessEqual(len(payload["description"]), 100, command.name)
            for option in payload.get("options", []):
                self.assertLessEqual(len(option["description"]), 100, f"{command.name}:{option['name']}")
                self.assertLessEqual(len(option["name"]), 32)
                self.assertEqual(option["name"], option["name"].lower())      # 디스코드는 옵션 이름의 영문 대문자를 허용하지 않는다.


if __name__ == "__main__":
    unittest.main()
