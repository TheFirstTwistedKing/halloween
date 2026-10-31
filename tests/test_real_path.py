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
        for extension in ("cogs.economy", "cogs.admin", "cogs.shop", "cogs.dungeon_admin", "cogs.rules_admin", "cogs.content_admin"):
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

    async def test_bag_command_and_the_exp_commands_are_gone(self):
        await self.bot.tree._call(self.interaction("가방"))
        embed, kwargs = self.sent_embed()
        self.assertEqual(embed.title, "🎒 u의 가방")
        self.assertTrue(kwargs["ephemeral"])
        names = {c.name for c in self.bot.tree.get_commands()}
        self.assertFalse({"경험치교환", "경험치지급"} & names)

    async def test_grant_candy_through_a_resolved_member(self):
        payload_extra = {"resolved": {"users": {"8": {"id": "8", "username": "손님", "discriminator": "0", "avatar": None}},
                                      "members": {"8": {"roles": [], "joined_at": "2026-01-01T00:00:00+00:00", "deaf": False,
                                                        "mute": False, "flags": 0, "permissions": MEMBER}}}}
        interaction = self.interaction("재화지급", [{"name": "대상", "type": 6, "value": "8"}, {"name": "수량", "type": 4, "value": 25}],
                                       perms=ADMIN)
        interaction.data.update(payload_extra)
        interaction.data["resolved"]["members"]["8"]["user"] = interaction.data["resolved"]["users"]["8"]
        await self.bot.tree._call(interaction)
        self.assertEqual(self.store.wallet(100, 8), 25)

    async def test_autocomplete_for_item_names_is_wired(self):
        self.store.create_item(100, "호박 초", emoji="🕯️")
        options = [{"name": "물품", "type": 3, "value": "호박", "focused": True}]
        interaction = self.interaction("물품수정", options, kind=4, perms=ADMIN)
        await self.bot.tree._call(interaction)
        call = self.sent["autocomplete"].await_args
        choices = call.args[0] if call.args else call.kwargs["choices"]
        self.assertEqual([(c.name, c.value) for c in choices], [("🕯️ 호박 초", "호박 초")])

    async def test_dungeon_balance_commands_with_korean_option_names(self):
        await self.bot.tree._call(self.interaction("던전설정", [{"name": "항목", "type": 3, "value": "일반.최대체력"},
                                                              {"name": "값", "type": 10, "value": 1500}], perms=ADMIN))
        self.assertEqual(self.store.settings(100)["dungeon"], {"일반.최대체력": 1500})
        self.assertIn("1,000 → **1,500**", self.sent_embed()[0].description)
        await self.bot.tree._call(self.interaction("던전설정확인", [{"name": "분류", "type": 3, "value": "일반"}], perms=ADMIN))
        self.assertIn("`일반.최대체력` **1,500** ✏️ 기본 1,000", self.sent_embed()[0].description)
        await self.bot.tree._call(self.interaction("던전설정초기화", [{"name": "항목", "type": 3, "value": "일반.최대체력"}], perms=ADMIN))
        self.assertEqual(self.store.settings(100)["dungeon"], {})
        await self.bot.tree._call(self.interaction("던전시뮬", [{"name": "판수", "type": 4, "value": 100}], perms=ADMIN))
        self.sent["defer"].assert_awaited()
        embed = self.followup.await_args.kwargs["embed"]
        self.assertIn("100판", embed.title)

    async def test_dungeon_balance_commands_refuse_regular_members_on_the_real_path(self):
        await self.bot.tree._call(self.interaction("던전설정", [{"name": "항목", "type": 3, "value": "일반.최대체력"},
                                                              {"name": "값", "type": 10, "value": 1500}], perms=MEMBER))
        self.assertEqual(self.store.settings(100)["dungeon"], {})
        self.assertIn("관리자만", self.sent_embed()[0].description)

    async def test_autocomplete_for_dungeon_balance_keys_is_wired(self):
        self.store.update_settings(100, dungeon={"방어.회복률": 99})
        options = [{"name": "항목", "type": 3, "value": "회복률", "focused": True}]
        for command in ("던전설정", "던전설정초기화"):
            self.sent["autocomplete"].reset_mock()
            await self.bot.tree._call(self.interaction(command, options, kind=4, perms=ADMIN))
            call = self.sent["autocomplete"].await_args
            choices = call.args[0] if call.args else call.kwargs["choices"]
            self.assertIn(("✏️ 방어.회복률 = 99 (기본 50)", "방어.회복률"), [(c.name, c.value) for c in choices], command)

    def sub(self, name, sub, options=(), **kw):
        """묶음 명령어(/몬스터 추가 …)의 상호작용."""
        return self.interaction(name, [{"name": sub, "type": 1, "options": list(options)}], **kw)

    async def test_currency_command_through_the_real_path(self):
        await self.bot.tree._call(self.interaction("재화설정", [{"name": "이모지", "type": 3, "value": "💎"},
                                                              {"name": "이름", "type": 3, "value": "젬"},
                                                              {"name": "최대", "type": 4, "value": 500}], perms=ADMIN))
        self.assertEqual(tuple(self.store.currency(100)), ("💎", "젬", 500))
        await self.bot.tree._call(self.interaction("재화설정", [{"name": "이모지", "type": 3, "value": "abc"}], perms=ADMIN))
        self.assertEqual(self.store.currency(100).emoji, "💎")
        await self.bot.tree._call(self.interaction("재화설정", [{"name": "이름", "type": 3, "value": "해킹"}], perms=MEMBER))
        self.assertEqual(self.store.currency(100).name, "젬")
        self.assertIn("관리자만", self.sent_embed()[0].description)

    async def test_entry_item_command_and_dungeon_gate(self):
        gem = self.store.create_item(100, "입장권", emoji="🎫")
        await self.bot.tree._call(self.interaction("던전입장물품", [{"name": "물품", "type": 3, "value": "입장권"},
                                                                 {"name": "개수", "type": 4, "value": 2}], perms=ADMIN))
        cfg = self.store.settings(100)
        self.assertEqual((cfg["entry_item"], cfg["entry_cost"]), (gem["id"], 2))
        await self.bot.tree._call(self.interaction("던전입장물품", perms=ADMIN))              # 비우면 무료
        self.assertIsNone(self.store.settings(100)["entry_item"])

    async def test_package_result_subcommands_through_the_real_path(self):
        await self.bot.tree._call(self.sub("패키지결과", "추가", [
            {"name": "종류", "type": 3, "value": "candy"}, {"name": "확률", "type": 10, "value": 5.5},
            {"name": "개수", "type": 4, "value": 9}, {"name": "이모지", "type": 3, "value": "💰"}], perms=ADMIN))
        added = self.store.settings(100)["package_table"][-1]
        self.assertEqual((added["kind"], added["weight"], added["amount"], added["emoji"]), ("candy", 5.5, 9, "💰"))
        await self.bot.tree._call(self.sub("패키지결과", "목록", perms=ADMIN))
        self.assertIn("💰", self.sent_embed()[0].description)
        await self.bot.tree._call(self.sub("패키지결과", "삭제", [{"name": "결과", "type": 3, "value": added["id"]}], perms=ADMIN))
        self.assertNotIn(added["id"], [e["id"] for e in self.store.settings(100)["package_table"]])
        await self.bot.tree._call(self.sub("패키지결과", "추가", [{"name": "종류", "type": 3, "value": "none"},
                                                              {"name": "확률", "type": 10, "value": 1}], perms=MEMBER))
        self.assertIn("관리자만", self.sent_embed()[0].description)

    async def test_dungeon_content_subcommands_through_the_real_path(self):
        await self.bot.tree._call(self.sub("몬스터", "추가", [
            {"name": "이름", "type": 3, "value": "드래곤"}, {"name": "이모지", "type": 3, "value": "🐉"},
            {"name": "체력", "type": 4, "value": 700}, {"name": "공격", "type": 4, "value": 90}, {"name": "보스", "type": 5, "value": True}],
            perms=ADMIN))
        self.assertIn("드래곤", [b.name for b in self.store.balance(100).bosses])
        await self.bot.tree._call(self.sub("몬스터", "수정", [{"name": "대상", "type": 3, "value": "드래곤"},
                                                              {"name": "체력", "type": 4, "value": 800}], perms=ADMIN))
        self.assertEqual(next(b for b in self.store.balance(100).bosses if b.name == "드래곤").hp, 800)
        await self.bot.tree._call(self.sub("장비", "추가", [
            {"name": "이름", "type": 3, "value": "도끼"}, {"name": "이모지", "type": 3, "value": "🪓"},
            {"name": "종류", "type": 3, "value": "weapon"}, {"name": "등급", "type": 3, "value": "희귀"},
            {"name": "등장", "type": 4, "value": 5}, {"name": "공격력", "type": 4, "value": 150}], perms=ADMIN))
        self.assertIn("도끼", [g.name for g in self.store.balance(100).gear])
        await self.bot.tree._call(self.sub("소모품", "추가", [
            {"name": "이름", "type": 3, "value": "만병통치약"}, {"name": "이모지", "type": 3, "value": "💊"},
            {"name": "등급", "type": 3, "value": "전설"}, {"name": "등장", "type": 4, "value": 2}, {"name": "전체회복", "type": 5, "value": True}],
            perms=ADMIN))
        self.assertTrue(next(c for c in self.store.balance(100).consumables if c.name == "만병통치약").full)
        await self.bot.tree._call(self.interaction("던전목록", [{"name": "분류", "type": 3, "value": "bosses"}], perms=ADMIN))
        self.assertIn("드래곤", self.sent_embed()[0].fields[0].value)
        await self.bot.tree._call(self.sub("몬스터", "삭제", [{"name": "대상", "type": 3, "value": "드래곤"}], perms=MEMBER))
        self.assertIn("드래곤", [b.name for b in self.store.balance(100).bosses])       # 일반 멤버는 못 지운다.

    async def test_autocomplete_for_dungeon_content_names_is_wired(self):
        options = [{"name": "대상", "type": 3, "value": "슬라임", "focused": True}]
        await self.bot.tree._call(self.interaction("몬스터", [{"name": "수정", "type": 1, "options": options}], kind=4, perms=ADMIN))
        call = self.sent["autocomplete"].await_args
        choices = call.args[0] if call.args else call.kwargs["choices"]
        self.assertEqual({c.value for c in choices}, {"슬라임", "거대 슬라임"})

    def role_grant_interaction(self, perms):
        interaction = self.interaction("역할재화지급", [{"name": "역할", "type": 8, "value": "50"},
                                                       {"name": "수량", "type": 4, "value": 25}], perms=perms)
        interaction.data["resolved"] = {"roles": {"50": {"id": "50", "name": "손님", "color": 0, "hoist": False, "position": 1,
                                                          "permissions": "0", "managed": False, "mentionable": False, "flags": 0}}}
        return interaction

    async def test_role_grant_option_names_reach_the_command_and_it_explains_the_missing_intent(self):
        from cogs.admin import NO_MEMBERS_INTENT
        await self.bot.tree._call(self.role_grant_interaction(ADMIN))          # 이 시험용 봇은 멤버 인텐트가 꺼져 있다
        self.assertEqual(self.sent_embed()[0].description, NO_MEMBERS_INTENT)
        self.assertEqual(self.store.wallet(100, 20), 0)

    async def test_role_grant_refuses_regular_members_on_the_real_path(self):
        await self.bot.tree._call(self.role_grant_interaction(MEMBER))
        self.assertIn("관리자만", self.sent_embed()[0].description)

    async def test_all_commands_serialize_for_discord(self):
        commands = self.bot.tree.get_commands()
        self.assertEqual(len(commands), 29)
        for command in commands:
            payload = command.to_dict(self.bot.tree)
            self.assertLessEqual(len(payload["description"]), 100, command.name)
            def walk(options, path):                                          # 묶음 명령어(/몬스터 추가 …)의 안쪽 옵션까지 본다.
                for option in options:
                    self.assertLessEqual(len(option["description"]), 100, f"{path}:{option['name']}")
                    self.assertLessEqual(len(option["name"]), 32)
                    self.assertEqual(option["name"], option["name"].lower())  # 디스코드는 옵션 이름의 영문 대문자를 허용하지 않는다.
                    walk(option.get("options", []), f"{path} {option['name']}")
            walk(payload.get("options", []), command.name)


if __name__ == "__main__":
    unittest.main()
