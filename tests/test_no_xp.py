"""경험치 제외 채널(/롤플관리 경험치제외): 그 채널·그 채널의 스레드·카테고리 안에서는 채팅·음성 경험치가 오르지 않는다.
출석체크 같은 명령어는 그대로다."""
import unittest
from types import SimpleNamespace as NS

import discord

import rp
from cogs.rp import Roleplay
from store import Store
from tests.helpers import World, sent_embed

G, A = 100, 7
Choice = discord.app_commands.Choice
ADD, REMOVE, LIST = (Choice(name=n, value=v) for n, v in (("추가", "add"), ("삭제", "remove"), ("목록", "list")))


class RuleTests(unittest.TestCase):
    def cfg(self, ids):
        return rp.config({"no_xp": ids})

    def test_config_cleaning(self):
        self.assertEqual(rp.config({})["no_xp"], [])
        self.assertEqual(self.cfg([5, 5, "6", 0, True, 7])["no_xp"], [5, 7])            # 중복·잘못된 값은 뺀다
        self.assertEqual(len(self.cfg(list(range(1, 100)))["no_xp"]), rp.NO_XP_MAX)

    def test_channel_thread_and_category(self):
        cfg = self.cfg([10, 50])
        self.assertTrue(rp.no_xp(cfg, NS(id=10, category_id=None)))                       # 채널 자체
        self.assertTrue(rp.no_xp(cfg, NS(id=11, category_id=50)))                         # 제외 카테고리 안 채널
        self.assertTrue(rp.no_xp(cfg, NS(id=12, parent_id=10, parent=NS(category_id=None))))  # 제외 채널의 스레드
        self.assertTrue(rp.no_xp(cfg, NS(id=13, parent_id=20, parent=NS(category_id=50))))    # 제외 카테고리 안 채널의 스레드
        self.assertFalse(rp.no_xp(cfg, NS(id=14, category_id=60, parent_id=None)))
        self.assertFalse(rp.no_xp(cfg, None))
        self.assertFalse(rp.no_xp(self.cfg([]), NS(id=10)))

    def test_full_settings_still_save(self):
        store = Store(":memory:")
        try:
            store.update_settings(G, rp=dict(rp.config({"no_xp": [10]})))               # 모든 칸이 들어 있어도 저장된다
            self.assertEqual(rp.config(store.settings(G)["rp"])["no_xp"], [10])
        finally:
            store.close()


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Roleplay(self.bot)
        self.member = self.w.new_member(A)
        self.quiet, self.loud = self.w.channel(401), self.w.channel(402)
        self.channels = {401: self.quiet, 402: self.loud}
        self.w.guild.get_channel = lambda cid: self.channels.get(cid)
        self.store.update_settings(G, rp={"chat_xp": 10, "chat_cooldown": 0, "voice_xp": 5})

    async def asyncTearDown(self):
        await self.w.close()

    async def admin(self, action, channel=None, admin=True):
        i = self.w.interaction(uid=A, admin=admin)
        await Roleplay.no_xp_rooms.callback(self.cog, i, action, channel)
        embed, _ = sent_embed(i)
        return embed.description or ""

    def ids(self):
        return rp.config(self.store.settings(G)["rp"])["no_xp"]

    def com(self):
        return rp.row(self.store, G, A)["com"]

    async def chat(self, channel):
        await self.cog.on_message(NS(guild=self.w.guild, author=self.member, webhook_id=None, channel=channel))

    async def test_admin_add_remove_list(self):
        self.assertIn("관리자만", await self.admin(ADD, self.quiet, admin=False))
        self.assertEqual(self.ids(), [])
        self.assertIn("골라 주세요", await self.admin(ADD))
        self.assertIn("골라 주세요", await self.admin(REMOVE))
        self.assertIn("오르지 않아요", await self.admin(ADD, self.quiet))
        self.assertEqual(self.ids(), [401])
        self.assertIn("이미", await self.admin(ADD, self.quiet))
        self.assertIn("<#401>", await self.admin(LIST))
        self.assertIn("목록에 없어요", await self.admin(REMOVE, self.loud))
        self.assertIn("다시 경험치가 올라요", await self.admin(REMOVE, self.quiet))
        self.assertEqual(self.ids(), [])
        self.assertIn("없음", await self.admin(LIST))

    async def test_limit_and_deleted_channels_are_cleaned(self):
        self.store.update_settings(G, rp={**self.store.settings(G)["rp"], "no_xp": list(range(1000, 1000 + rp.NO_XP_MAX))})
        self.assertIn("목록: 없음", await self.admin(LIST))                              # 지워진 채널은 정리된다
        self.assertEqual(self.ids(), [])
        self.channels.update({c: self.w.channel(c) for c in range(1000, 1000 + rp.NO_XP_MAX)})
        self.store.update_settings(G, rp={**self.store.settings(G)["rp"], "no_xp": list(range(1000, 1000 + rp.NO_XP_MAX))})
        self.assertIn(f"{rp.NO_XP_MAX}개까지", await self.admin(ADD, self.quiet))
        self.assertNotIn(401, self.ids())

    async def test_chat_gives_no_exp_in_excluded_rooms(self):
        await self.admin(ADD, self.quiet)
        await self.chat(self.quiet)
        self.assertEqual(self.com(), 0)
        await self.chat(self.loud)
        self.assertEqual(self.com(), 10)
        await self.chat(NS(id=999, parent_id=401, parent=NS(category_id=None)))           # 제외 채널의 스레드
        self.assertEqual(self.com(), 10)

    async def test_voice_gives_no_exp_in_excluded_rooms(self):
        def person(uid):
            return NS(id=uid, bot=False, guild=self.w.guild, mention=f"<@{uid}>", voice=NS(self_mute=False, self_deaf=False))
        self.w.guild.afk_channel = None
        self.w.guild.voice_channels = [NS(id=401, category_id=None, members=[person(A)]),
                                       NS(id=403, category_id=77, members=[person(8)]),
                                       NS(id=404, category_id=None, members=[person(9)])]
        self.w.guild.stage_channels = []
        self.store.update_settings(G, rp={**self.store.settings(G)["rp"], "no_xp": [401, 77]})   # 채널 하나 + 카테고리 하나
        self.cog.bot = NS(store=self.store, guilds=[self.w.guild], allowed_guild=lambda gid: True)
        self.assertEqual(await self.cog.voice_tick(), 1)
        self.assertEqual([rp.row(self.store, G, u)["com"] for u in (A, 8, 9)], [0, 0, 5])

    async def test_commands_still_give_exp(self):
        await self.admin(ADD, self.quiet)
        i = self.w.interaction(uid=A, channel=self.quiet)
        await Roleplay.attendance.callback(self.cog, i)
        self.assertEqual(self.com(), 1000)


if __name__ == "__main__":
    unittest.main()
