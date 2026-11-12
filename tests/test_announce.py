"""/공지: 관리자만, 입력창으로 제목·내용을 받아 고른 채널에 봇 이름으로 올린다. 흔적(명령어 응답 메시지)을 남기지 않는다."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

from cogs.announce import (BODY_MAX, PLAIN_MAX, Announce, AnnounceModal, build_announcement, check_image, mention_text,
                           missing_permissions)
from tests.helpers import World, sent_embed

Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})
FULL = discord.Permissions(view_channel=True, send_messages=True, embed_links=True, mention_everyone=True)


def role(mentionable=True, rid=55):
    return NS(id=rid, mention=f"<@&{rid}>", mentionable=mentionable)


class BuildTests(unittest.TestCase):
    def test_a_card_has_title_body_footer_and_time(self):
        message = build_announcement(style="embed", title="점검 안내", body="오늘 밤 10시 점검이에요.", ping="none", guild_name="테스트 서버")
        embed = message["embed"]
        self.assertEqual((embed.title, embed.description, embed.footer.text), ("점검 안내", "오늘 밤 10시 점검이에요.", "📢 테스트 서버 공지"))
        self.assertIsNotNone(embed.timestamp)
        self.assertIsNone(message["content"])
        self.assertFalse(message["allowed_mentions"].everyone)

    def test_a_card_without_title_and_with_image(self):
        embed = build_announcement(style="embed", title="", body="본문", ping="none", image="https://x.y/a.png")["embed"]
        self.assertIsNone(embed.title)
        self.assertEqual(embed.image.url, "https://x.y/a.png")

    def test_mentions_go_in_front_and_are_allowed_only_as_chosen(self):
        message = build_announcement(style="embed", title="t", body="b", ping="everyone", role=role())
        self.assertEqual(message["content"], "@everyone <@&55>")
        allowed = message["allowed_mentions"]
        self.assertTrue(allowed.everyone)
        self.assertEqual([r.id for r in allowed.roles], [55])
        self.assertFalse(allowed.users)
        here = build_announcement(style="embed", title="t", body="b", ping="here")
        self.assertEqual(here["content"], "@here")
        self.assertTrue(here["allowed_mentions"].everyone)
        self.assertEqual(mention_text("none", None), "")

    def test_plain_text_puts_everything_in_one_message(self):
        message = build_announcement(style="plain", title="이벤트", body="RAM 두 배!", ping="here", image="https://x.y/b.gif")
        self.assertEqual(message["content"], "@here\n## 이벤트\nRAM 두 배!\nhttps://x.y/b.gif")
        self.assertNotIn("embed", message)
        self.assertEqual(build_announcement(style="plain", title="", body="본문만", ping="none")["content"], "본문만")

    def test_plain_text_over_the_limit_is_refused(self):
        with self.assertRaisesRegex(ValueError, "2,000자까지"):
            build_announcement(style="plain", title="", body="가" * (PLAIN_MAX + 1), ping="none")
        build_announcement(style="embed", title="", body="가" * BODY_MAX, ping="none")       # 카드는 4,000자도 된다

    def test_image_urls_are_checked(self):
        self.assertIsNone(check_image(None))
        self.assertIsNone(check_image("https://a.b/c.png"))
        for bad in ("ftp://a", "a.png", "https://a b", "https://" + "a" * 300):
            self.assertIsNotNone(check_image(bad), bad)

    def test_missing_permissions_are_named(self):
        channel = NS(permissions_for=lambda me: discord.Permissions(view_channel=True, send_messages=True))
        self.assertEqual(missing_permissions(channel, None, style="plain", ping="none", role=None), [])
        missing = missing_permissions(channel, None, style="embed", ping="everyone", role=role(mentionable=False))
        self.assertEqual(len(missing), 3)
        self.assertTrue(any("링크 첨부" in m for m in missing) and any("@everyone" in m for m in missing) and any("<@&55>" in m for m in missing))
        mute = NS(permissions_for=lambda me: discord.Permissions())
        self.assertEqual(missing_permissions(mute, None, style="plain", ping="none", role=None), ["채널 보기", "메시지 보내기"])
        boss = NS(permissions_for=lambda me: FULL)
        self.assertEqual(missing_permissions(boss, None, style="embed", ping="here", role=role(mentionable=False)), [])   # 멘션 권한이 있으면 된다


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.cog = Announce(self.w.bot)
        self.here = self.w.channel(400)
        self.target = self.w.channel(500, perms=FULL)

    async def asyncTearDown(self):
        await self.w.close()

    async def open(self, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await Announce.announce.callback(self.cog, i, **kwargs)
        return i

    async def submit(self, modal, title="공지 제목", body="공지 내용", admin=True):
        modal.subject._value, modal.body._value = title, body
        i = self.w.interaction(admin=admin)
        await modal.on_submit(i)
        return i

    async def test_only_admins_and_the_form_opens_without_posting_anything(self):
        i = await self.open(admin=False, channel=self.target)
        self.assertIn("관리자만", sent_embed(i)[0].description)
        i.response.send_modal.assert_not_awaited()
        i = await self.open(channel=self.target)
        modal = i.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, AnnounceModal)
        self.assertEqual((modal.channel, modal.style, modal.ping), (self.target, "embed", "none"))
        self.assertEqual((modal.subject.required, modal.body.required, modal.body.style), (False, True, discord.TextStyle.paragraph))
        i.response.send_message.assert_not_awaited()
        self.target.send.assert_not_awaited()

    async def test_submitting_posts_as_the_bot_and_leaves_no_reply(self):
        i = await self.open(channel=self.target, ping=Choice("everyone"))
        done = await self.submit(i.response.send_modal.await_args.args[0], "  서버 점검  ", "  10시에 점검해요  ")
        kwargs = self.target.send.await_args.kwargs
        self.assertEqual((kwargs["embed"].title, kwargs["embed"].description, kwargs["content"]), ("서버 점검", "10시에 점검해요", "@everyone"))
        done.response.defer.assert_awaited_once()
        done.response.send_message.assert_not_awaited()                                  # 명령어 흔적 없이 봇의 공지만 보인다
        self.here.send.assert_not_awaited()

    async def test_the_current_channel_is_the_default(self):
        self.here.permissions_for = lambda me: FULL
        i = await self.open(style=Choice("plain"))
        await self.submit(i.response.send_modal.await_args.args[0], "", "짧은 공지")
        self.assertEqual(self.here.send.await_args.kwargs["content"], "짧은 공지")

    async def test_missing_permissions_or_bad_image_stop_before_the_form(self):
        i = await self.open(channel=self.here, ping=Choice("everyone"))                  # 기본 채널 권한: 멘션 권한 없음
        self.assertIn("@everyone·@here 멘션", sent_embed(i)[0].description)
        i.response.send_modal.assert_not_awaited()
        i = await self.open(channel=self.target, image="not a url")
        self.assertIn("그림은", sent_embed(i)[0].description)
        i.response.send_modal.assert_not_awaited()

    async def test_problems_at_submit_are_explained_and_nothing_is_sent(self):
        i = await self.open(channel=self.target, style=Choice("plain"))
        modal = i.response.send_modal.await_args.args[0]
        done = await self.submit(modal, "", "가" * (PLAIN_MAX + 5))
        self.assertIn("2,000자까지", sent_embed(done)[0].description)
        done = await self.submit(modal, "제목", "   ")
        self.assertIn("내용을 적어", sent_embed(done)[0].description)
        done = await self.submit(modal, "제목", "내용", admin=False)
        self.assertIn("관리자만", sent_embed(done)[0].description)
        self.target.send.assert_not_awaited()

    async def test_a_discord_error_is_reported(self):
        i = await self.open(channel=self.target)
        modal = i.response.send_modal.await_args.args[0]
        self.target.send.side_effect = discord.HTTPException(NS(status=403, reason="Forbidden"), "Missing Access")
        done = await self.submit(modal)
        self.assertIn("공지를 보내지 못했어요", sent_embed(done)[0].description)
        done.response.defer.assert_not_awaited()

    async def test_closing_the_form_failing_does_not_undo_the_post(self):
        i = await self.open(channel=self.target)
        modal = i.response.send_modal.await_args.args[0]
        modal.subject._value, modal.body._value = "t", "b"
        done = self.w.interaction(admin=True)
        done.response.defer = AsyncMock(side_effect=discord.HTTPException(NS(status=400, reason="Bad"), "x"))
        with self.assertLogs("halloween", level="WARNING"):
            await modal.on_submit(done)
        self.target.send.assert_awaited_once()

    async def test_the_post_is_logged(self):
        i = await self.open(channel=self.target, role=role())
        with self.assertLogs("halloween", level="INFO") as logs:
            await self.submit(i.response.send_modal.await_args.args[0])
        self.assertIn("공지: 관리자 7 → 채널 500", "\n".join(logs.output))
        self.assertEqual(self.target.send.await_args.kwargs["content"], "<@&55>")

    def test_options_are_declared(self):
        names = {p.display_name for p in Announce.announce.parameters}
        self.assertEqual(names, {"채널", "형식", "멘션", "역할", "그림"})
        self.assertTrue(all(not p.required for p in Announce.announce.parameters))
