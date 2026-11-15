"""레벨업 알림 채널(/롤플관리 레벨업채널): 정하면 채팅·음성·명령어 레벨업을 모두 그 채널에 알린다.
비우면 원래대로(채팅은 채팅한 채널, 명령어는 답장으로만, 음성은 알리지 않음). 채널이 지워졌으면 원래대로 돌아간다."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

import rp
from cogs.rp import Roleplay
from tests.helpers import World, sent_embed

G, A = 100, 7


class ConfigTests(unittest.TestCase):
    def test_default_and_cleaning(self):
        self.assertIsNone(rp.config({})["level_channel"])
        self.assertEqual(rp.config({"level_channel": 401})["level_channel"], 401)
        for bad in ("401", 0, True, -5):
            self.assertIsNone(rp.config({"level_channel": bad})["level_channel"], bad)
        self.assertIsNone(rp.config({"shop_channel": 401})["level_channel"])           # 상점 알림 채널과는 따로


class LevelChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Roleplay(self.bot)
        self.member = self.w.new_member(A)
        self.here = self.w.channel(400)                                                 # 명령어·채팅을 쓴 채널
        self.level = self.w.channel(401)                                                # 레벨업 채널
        self.channels = {400: self.here, 401: self.level}
        self.w.guild.get_channel = lambda cid: self.channels.get(cid)
        self.store.update_settings(G, rp={"chat_xp": 100, "chat_cooldown": 0})          # 채팅 한 번에 LV 0 → 1

    async def asyncTearDown(self):
        await self.w.close()

    async def admin(self, channel=None, admin=True):
        i = self.w.interaction(uid=A, admin=admin)
        await Roleplay.level_channel.callback(self.cog, i, channel)
        embed, _ = sent_embed(i)
        return embed.description or ""

    async def chat(self):
        await self.cog.on_message(NS(guild=self.w.guild, author=self.member, webhook_id=None, channel=self.here))

    async def attend(self, channel):
        i = self.w.interaction(uid=A, channel=channel)
        await Roleplay.attendance.callback(self.cog, i)
        return sent_embed(i)[0].description

    def posted(self, channel):
        return [c.kwargs["embed"].description for c in channel.send.await_args_list]

    async def test_admin_command_sets_and_resets(self):
        self.assertIn("관리자만", await self.admin(self.level, admin=False))
        self.assertIsNone(rp.config(self.store.settings(G)["rp"])["level_channel"])
        self.assertIn("<#401>", await self.admin(self.level))
        self.assertEqual(rp.config(self.store.settings(G)["rp"])["level_channel"], 401)
        self.assertEqual(rp.config(self.store.settings(G)["rp"])["chat_xp"], 100)      # 다른 설정은 그대로
        self.assertIn("원래대로", await self.admin(None))
        self.assertIsNone(rp.config(self.store.settings(G)["rp"])["level_channel"])

    async def test_chat_level_up_goes_to_the_chosen_channel(self):
        await self.chat()                                                               # 정하지 않음: 채팅한 채널
        self.assertEqual(len(self.posted(self.here)), 1)
        self.assertIn("LV. 0 → LV. 1", self.posted(self.here)[0])
        await self.admin(self.level)
        self.store.update_settings(G, rp={**self.store.settings(G)["rp"], "chat_xp": 300})  # 100 → 400: LV 2
        await self.chat()
        self.assertEqual(len(self.posted(self.here)), 1)                                # 채팅한 채널에는 더 안 올린다
        self.assertEqual(len(self.posted(self.level)), 1)
        self.assertIn("<@7>", self.posted(self.level)[0])
        self.assertIn("LV. 1 → LV. 2", self.posted(self.level)[0])
        mentions = self.level.send.await_args.kwargs["allowed_mentions"]
        self.assertFalse(mentions.users)                                                # 알림으로 사람을 부르지 않는다

    async def test_chatting_inside_the_level_channel_still_announces(self):
        await self.admin(self.level)
        await self.cog.on_message(NS(guild=self.w.guild, author=self.member, webhook_id=None, channel=self.level))
        self.assertEqual(len(self.posted(self.level)), 1)                               # 채팅은 답장이 없으니 그 채널이어도 올린다

    async def test_deleted_channel_falls_back(self):
        await self.admin(self.level)
        del self.channels[401]
        await self.chat()
        self.assertEqual(len(self.posted(self.here)), 1)

    async def test_command_level_up(self):
        self.assertIn("LV. 0 → LV. 3", await self.attend(self.here))                    # 정하지 않음: 답장에만
        self.assertEqual((self.posted(self.here), self.posted(self.level)), ([], []))
        await self.admin(self.level)
        with self.store.db:
            self.store.db.execute("UPDATE rp SET last_attend='' WHERE guild=? AND user=?", (G, A))
        self.assertIn("LV. 3 → LV. 4", await self.attend(self.here))                    # 다른 채널에서 쓰면 레벨업 채널에도
        self.assertEqual(len(self.posted(self.level)), 1)
        self.assertIn("LV. 3 → LV. 4", self.posted(self.level)[0])
        self.assertEqual(self.posted(self.here), [])
        with self.store.db:
            self.store.db.execute("UPDATE rp SET last_attend='' WHERE guild=? AND user=?", (G, A))
        await self.attend(self.level)                                                   # 레벨업 채널에서 쓰면 두 번 올리지 않는다
        self.assertEqual(len(self.posted(self.level)), 1)

    async def test_voice_level_up_only_with_a_channel(self):
        self.store.update_settings(G, rp={"voice_xp": 100})
        talker = NS(id=A, bot=False, guild=self.w.guild, mention="<@7>", voice=NS(self_mute=False, self_deaf=False))
        self.w.guild.afk_channel = None
        self.w.guild.voice_channels = [NS(id=1, members=[talker])]
        self.w.guild.stage_channels = []
        self.cog.bot = NS(store=self.store, guilds=[self.w.guild], allowed_guild=lambda gid: True)
        self.assertEqual(await self.cog.voice_tick(), 1)                                 # 정하지 않음: 알리지 않는다
        self.assertEqual((self.posted(self.here), self.posted(self.level)), ([], []))
        self.store.update_settings(G, rp={"voice_xp": 300, "level_channel": 401})
        await self.cog.voice_tick()
        self.assertEqual(len(self.posted(self.level)), 1)
        self.assertIn("LV. 1 → LV. 2", self.posted(self.level)[0])

    async def test_send_failure_is_ignored(self):
        await self.admin(self.level)
        self.level.send = AsyncMock(side_effect=discord.HTTPException(NS(status=403, reason="x"), "no"))
        await self.chat()                                                               # 오류 없이 넘어간다
        self.assertEqual(rp.row(self.store, G, A)["com"], 100)
        self.assertIn("LV. 1 → LV. 3", await self.attend(self.here))                    # 답장은 그대로 나간다


if __name__ == "__main__":
    unittest.main()
