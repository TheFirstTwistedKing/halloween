"""시작 파일 시험: GUILD_ID 해석, 시작 순서(확장·고정 버튼·명령어 등록), .env 처리. 디스코드 접속만 가짜로 둔다."""
import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock

import discord

import main as entry
from main import HalloweenBot, parse_guild_ids
from store import Store


class ParseGuildIdsTests(unittest.TestCase):
    def test_separators_and_duplicates(self):
        self.assertEqual(parse_guild_ids(""), [])
        self.assertEqual(parse_guild_ids("   "), [])
        self.assertEqual(parse_guild_ids("111"), [111])
        self.assertEqual(parse_guild_ids("111,222; 333  444\n555"), [111, 222, 333, 444, 555])
        self.assertEqual(parse_guild_ids("111,111,222"), [111, 222])
        self.assertEqual(parse_guild_ids("111,,222,"), [111, 222])

    def test_bad_values_are_named(self):
        for raw in ("abc", "12a", "-5", "0", "１２３", "1.5"):
            with self.assertRaises(ValueError, msg=raw):
                parse_guild_ids(raw)
        with self.assertRaisesRegex(ValueError, "abc, 12x"):
            parse_guild_ids("111,abc,222,12x")


class SetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")

    async def asyncTearDown(self):
        self.store.close()

    async def test_startup_loads_everything_registers_persistent_buttons_and_syncs_each_server(self):
        bot = HalloweenBot(self.store, [111, 222])
        bot._ready = asyncio.Event()
        bot.tree.sync = AsyncMock(return_value=[1, 2, 3])
        with self.assertLogs("halloween", level="INFO") as logs:
            await bot.setup_hook()
        self.assertEqual({c.__class__.__name__ for c in bot.cogs.values()}, {"Economy", "Admin", "Shop", "Dungeon"})
        self.assertEqual(len(bot.tree.get_commands()), 18)
        self.assertEqual(bot.tree.sync.await_count, 2)
        self.assertEqual([call.kwargs["guild"].id for call in bot.tree.sync.await_args_list], [111, 222])
        self.assertIn("서버 ID 111: 슬래시 명령어 3개 등록 완료", "\n".join(logs.output))
        self.assertEqual(len(bot.persistent_views), 2)                       # 상점 패널 + 패키지
        await bot.close()

    async def test_without_guild_ids_commands_are_registered_globally(self):
        bot = HalloweenBot(self.store)
        bot._ready = asyncio.Event()
        bot.tree.sync = AsyncMock(return_value=[1])
        with self.assertLogs("halloween", level="INFO") as logs:
            await bot.setup_hook()
        bot.tree.sync.assert_awaited_once_with()
        self.assertIn("전체 서버에 등록했어요", "\n".join(logs.output))
        await bot.close()

    async def test_one_failing_server_does_not_block_the_others(self):
        bot = HalloweenBot(self.store, [111, 222])
        bot._ready = asyncio.Event()
        bot.tree.sync = AsyncMock(side_effect=[discord.Forbidden(NS(status=403, reason="x"), "Missing Access"), [1]])
        with self.assertLogs("halloween", level="INFO") as logs:
            await bot.setup_hook()
        text = "\n".join(logs.output)
        self.assertIn("서버 ID 111: 명령어를 등록하지 못했어요 (HTTP 403)", text)
        self.assertIn("서버 ID 222: 슬래시 명령어 1개 등록 완료", text)
        await bot.close()

    async def test_the_configured_server_list_limits_where_the_bot_works(self):
        everywhere, limited = HalloweenBot(self.store), HalloweenBot(self.store, [111])
        self.assertTrue(everywhere.allowed_guild(999))
        self.assertTrue(limited.allowed_guild(111))
        self.assertFalse(limited.allowed_guild(999))
        await everywhere.close()
        await limited.close()

    async def test_no_privileged_intents_are_needed(self):
        bot = HalloweenBot(self.store)
        intents = bot.intents
        self.assertFalse(intents.message_content or intents.members or intents.presences)
        self.assertTrue(intents.guilds and intents.guild_messages)
        await bot.close()


class MainFunctionTests(unittest.TestCase):
    def run_main(self, env, **patches):
        with mock.patch.dict(os.environ, env, clear=False), mock.patch("main.load_dotenv"):
            return entry.main()

    def test_missing_token_exits_with_a_helpful_message(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main({"DISCORD_TOKEN": "  "})
        self.assertIn("DISCORD_TOKEN", str(caught.exception))

    def test_bad_guild_id_exits_with_the_bad_value(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main({"DISCORD_TOKEN": "x", "GUILD_ID": "111,oops"})
        self.assertIn("oops", str(caught.exception))

    def test_a_wrong_token_is_reported_and_the_database_is_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp, mock.patch("main.BASE", __import__("pathlib").Path(temp)), \
                mock.patch.object(HalloweenBot, "run", side_effect=discord.LoginFailure("bad")):
            with self.assertRaises(SystemExit) as caught:
                self.run_main({"DISCORD_TOKEN": "wrong", "GUILD_ID": "", "DATABASE_PATH": "data/test.sqlite3"})
            self.assertIn("토큰이 올바르지 않아요", str(caught.exception))
            self.assertTrue((__import__("pathlib").Path(temp) / "data" / "test.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
