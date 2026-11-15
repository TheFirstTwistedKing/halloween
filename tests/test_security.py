"""대테러: 잠금·해제, 최근 가입자 일괄 처리, 도배 삭제, 화이트·블랙리스트, 감지 알림(주인·관리자 역할에게만 DM)."""
import datetime
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

import security
from cogs.security import JoinersConfirm, Security
from store import DEFAULT_SETTINGS, Store, validate_settings

G = 100
OWNER, ADMIN, BOTID = 1, 2, 999
NOW = datetime.datetime(2026, 11, 15, 12, 0, tzinfo=datetime.timezone.utc)


def fail(*_a, **_k):
    raise discord.HTTPException(NS(status=403, reason="no"), "missing access")


class Role:
    def __init__(self, rid, position=1, default=False):
        self.id, self.position, self._default, self.members = rid, position, default, []
        self.mention = f"<@&{rid}>"

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position


class Member:
    def __init__(self, uid, roles=(), bot=False, joined=None, admin=False, everyone=None):
        self.id, self.bot, self.joined_at = uid, bot, joined
        self.roles = ([everyone] if everyone else []) + list(roles)
        self.mention = f"<@{uid}>"
        self.guild_permissions = NS(administrator=admin)
        self.send, self.kick = AsyncMock(), AsyncMock()
        for r in roles:
            r.members.append(self)

    @property
    def top_role(self):
        return max(self.roles, key=lambda r: r.position)


class Message:
    def __init__(self, author_id):
        self.author = NS(id=author_id)


class Channel:
    def __init__(self, cid, slow=0, messages=()):
        self.id, self.slowmode_delay, self.mention = cid, slow, f"<#{cid}>"
        self.perms: dict = {}
        self.messages = list(messages)
        self.edit_fail = False

    def overwrites_for(self, target):
        return discord.PermissionOverwrite(**self.perms.get(target.id, {}))

    async def set_permissions(self, target, *, overwrite=None, reason=None):
        if self.edit_fail:
            fail()
        if overwrite is None:
            self.perms.pop(target.id, None)
        else:
            self.perms[target.id] = {k: v for k, v in overwrite if v is not None}

    async def edit(self, *, slowmode_delay, reason=None):
        self.slowmode_delay = slowmode_delay

    async def purge(self, *, limit, after, check, reason=None):
        if self.edit_fail:
            fail()
        gone = [m for m in self.messages[:limit] if check(m)]
        self.messages = [m for m in self.messages if m not in gone]
        return gone


class Guild:
    def __init__(self):
        self.id, self.owner_id, self.name = G, OWNER, "테스트 서버"
        self.default_role = Role(G, 0, default=True)
        self.admin_role = Role(50, 5)
        self.me = Member(BOTID, roles=[Role(60, 10)], bot=True, everyone=self.default_role)
        self.members = [Member(OWNER, everyone=self.default_role), Member(ADMIN, roles=[self.admin_role], everyone=self.default_role), self.me]
        self.text_channels = [Channel(400), Channel(401, slow=5), Channel(402)]
        self.text_channels[1].perms[G] = {"send_messages": True}
        self.ban, self.chunk = AsyncMock(), AsyncMock()
        self.roles = [self.default_role, self.admin_role]

    def get_member(self, uid):
        return next((m for m in self.members if m.id == uid), None)

    def get_role(self, rid):
        return next((r for r in self.roles if r.id == rid), None)

    def get_channel(self, cid):
        return next((c for c in self.text_channels if c.id == cid), None)

    async def fetch_member(self, uid):
        found = self.get_member(uid)
        if found is None:
            fail()
        return found

    def join(self, uid, minutes_ago=1, **kw):
        member = Member(uid, joined=NOW - datetime.timedelta(minutes=minutes_ago), everyone=self.default_role, **kw)
        member.guild = self
        self.members.append(member)
        return member


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.guild = Guild()
        self.bot = NS(store=self.store, intents=NS(members=True), user=NS(id=BOTID), allowed_guild=lambda gid: True)
        self.cog = Security(self.bot)

    async def asyncTearDown(self):
        self.store.close()

    def interaction(self, uid=ADMIN, admin=True):
        user = self.guild.get_member(uid) or Member(uid)
        return NS(guild=self.guild, guild_id=G, user=user, channel=self.guild.text_channels[0],
                  permissions=discord.Permissions(administrator=admin),
                  response=NS(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()),
                  followup=NS(send=AsyncMock()), edit_original_response=AsyncMock())

    async def call(self, command, *args, uid=ADMIN, admin=True, **kwargs):
        i = self.interaction(uid, admin)
        await command.callback(self.cog, i, *args, **kwargs)
        sent = i.followup.send.call_args or i.response.send_message.call_args
        return i, (sent.kwargs["embed"].description if sent and "embed" in sent.kwargs else "")

    def cfg(self):
        return security.clean(self.store.settings(G)["security"])


class RuleTests(unittest.TestCase):
    def test_clean_defaults_and_bad_values(self):
        cfg = security.clean(None)
        self.assertEqual(cfg["detect"], security.DETECT_DEFAULT)
        self.assertEqual(cfg["white"], {"channels": [], "users": [], "bots": [], "roles": []})
        got = security.clean({"white": {"users": [5, 5, "x", True, -1]}, "detect": {"joins": 1, "messages": 20, "on": False},
                              "lock": {"active": True, "channels": {}}})
        self.assertEqual(got["white"]["users"], [5])
        self.assertEqual((got["detect"]["joins"], got["detect"]["messages"], got["detect"]["on"]), (10, 20, False))
        self.assertFalse(got["lock"]["active"])                                          # 되돌릴 채널이 없으면 잠금이 아니다
        validate_settings(dict(DEFAULT_SETTINGS, security={"white": {}}))
        with self.assertRaises(ValueError):
            validate_settings(dict(DEFAULT_SETTINGS, security=[1]))

    def test_watch_threshold_window_and_cooldown(self):
        w = security.Watch()
        self.assertFalse(any(w.hit("k", 3, 10, now=t) for t in (0, 1)))
        self.assertTrue(w.hit("k", 3, 10, now=2))
        self.assertFalse(w.hit("k", 3, 10, now=3))                                       # 같은 알림은 5분에 한 번
        self.assertFalse(w.hit("j", 3, 10, now=0) or w.hit("j", 3, 10, now=11) or w.hit("j", 3, 10, now=22))   # 창 밖의 일은 세지 않는다
        self.assertTrue(any(w.hit("k", 3, 10, now=400 + t) for t in range(3)))
        w.forget(now=10 ** 6)
        self.assertEqual(dict(w.events), {})

    def test_membership_helpers(self):
        cfg = security.clean({"white": {"users": [1], "bots": [2], "roles": [3]}, "black": {"users": [4], "bots": [5], "roles": [6]}})
        self.assertTrue(security.is_white_member(cfg, 1, []))
        self.assertTrue(security.is_white_member(cfg, 2, [], is_bot=True))
        self.assertFalse(security.is_white_member(cfg, 2, []))
        self.assertTrue(security.is_white_member(cfg, 9, [3]))
        self.assertTrue(security.is_black(cfg, 4) and security.is_black(cfg, 5, is_bot=True))
        self.assertEqual(security.black_roles(cfg, [6, 7]), [6])


class LockTests(Base):
    async def test_lock_skips_white_channels_and_unlock_restores_exactly(self):
        await self.call(Security.white_add, channel=self.guild.text_channels[2])
        _i, text = await self.call(Security.lock, 10)
        self.assertIn("채널 2개", text)
        c400, c401, c402 = self.guild.text_channels
        self.assertEqual((c400.perms[G], c401.perms[G], c402.perms.get(G)), ({"send_messages": False}, {"send_messages": False}, None))
        self.assertEqual((c400.slowmode_delay, c401.slowmode_delay), (10, 10))
        self.assertTrue(self.cfg()["lock"]["active"])
        _i, text = await self.call(Security.lock)
        self.assertIn("이미 잠겨", text)
        _i, text = await self.call(Security.unlock)
        self.assertIn("2개", text)
        self.assertNotIn(G, c400.perms)                                                   # 원래 덮어쓰기가 없던 채널은 지운다
        self.assertEqual(c401.perms[G], {"send_messages": True})                          # 원래 허용이던 채널은 허용으로
        self.assertEqual((c400.slowmode_delay, c401.slowmode_delay), (0, 5))
        self.assertFalse(self.cfg()["lock"]["active"])
        _i, text = await self.call(Security.unlock)
        self.assertIn("잠겨 있지 않아요", text)

    async def test_failures_are_reported_and_kept_for_retry(self):
        self.guild.text_channels[0].edit_fail = True
        _i, text = await self.call(Security.lock)
        self.assertIn("못 잠갔어요", text)
        self.assertEqual(list(self.cfg()["lock"]["channels"]), ["401", "402"])
        self.guild.text_channels[0].edit_fail = False
        self.guild.text_channels[1].edit_fail = True
        _i, text = await self.call(Security.unlock)
        self.assertIn("못 되돌렸어요", text)
        self.assertEqual(list(self.cfg()["lock"]["channels"]), ["401"])                   # 못 되돌린 채널만 남는다
        self.guild.text_channels[1].edit_fail = False
        await self.call(Security.unlock)
        self.assertFalse(self.cfg()["lock"]["active"])

    async def test_admin_only(self):
        i, _text = await self.call(Security.lock, uid=77, admin=False)
        self.assertIn("관리자만", i.response.send_message.call_args.kwargs["embed"].description)
        self.assertEqual(self.guild.text_channels[0].perms, {})


class JoinerTests(Base):
    async def test_recent_joiners_exclude_the_protected(self):
        fresh = self.guild.join(10)
        self.guild.join(11, minutes_ago=90)                                               # 오래전 가입
        self.guild.join(12, admin=True)                                                   # 관리자 권한
        self.guild.join(13, roles=[Role(70, 20)])                                         # 봇보다 높은 역할
        white_role = Role(71, 2)
        self.guild.roles.append(white_role)
        self.guild.join(14, roles=[white_role])
        self.guild.join(15)
        await self.call(Security.white_add, role=white_role)
        await self.call(Security.white_add, user=self.guild.get_member(15))
        targets, skipped = self.cog.recent_joiners(self.guild, 30, now=NOW)
        self.assertEqual([m.id for m in targets], [fresh.id])
        self.assertEqual(skipped, 4)

    async def test_confirm_then_kick_or_ban(self):
        self.guild.join(10)
        self.guild.join(11)
        Choice = discord.app_commands.Choice
        self.cog.recent_joiners = lambda guild, minutes: ([guild.get_member(10), guild.get_member(11)], 0)
        i, text = await self.call(Security.joiners, 30, Choice(name="추방", value="kick"))
        self.assertIn("2명", text)
        view = i.followup.send.call_args.kwargs["view"]
        self.assertFalse(await view.interaction_check(self.interaction(uid=77)))         # 명령을 쓴 관리자만
        press = self.interaction()
        await view.confirm(press)
        self.guild.get_member(10).kick.assert_awaited()
        self.assertIn("2명", press.edit_original_response.call_args.kwargs["embed"].description)
        i, _text = await self.call(Security.joiners, 30, Choice(name="차단", value="ban"))
        await i.followup.send.call_args.kwargs["view"].confirm(self.interaction())
        self.assertEqual(self.guild.ban.await_count, 2)
        i, _text = await self.call(Security.joiners, 30, Choice(name="차단", value="ban"))
        cancel = self.interaction()
        await i.followup.send.call_args.kwargs["view"].cancel(cancel)
        self.assertEqual(self.guild.ban.await_count, 2)                                   # 취소하면 아무것도 안 한다

    async def test_needs_members_intent(self):
        self.bot.intents.members = False
        Choice = discord.app_commands.Choice
        _i, text = await self.call(Security.joiners, 30, Choice(name="추방", value="kick"))
        self.assertIn("MEMBERS_INTENT", text)


class PurgeTests(Base):
    async def test_purge_one_person_outside_white_channels(self):
        c400, c401, c402 = self.guild.text_channels
        c400.messages = [Message(20), Message(21), Message(20)]
        c401.messages = [Message(20)]
        c402.messages = [Message(20)]
        await self.call(Security.white_add, channel=c402)
        _i, text = await self.call(Security.purge, NS(id=20, bot=False, mention="<@20>"))
        self.assertIn("3개", text)
        self.assertEqual(([m.author.id for m in c400.messages], len(c401.messages), len(c402.messages)), ([21], 0, 1))

    async def test_white_users_are_not_purged(self):
        target = self.guild.join(20)
        await self.call(Security.white_add, user=target)
        self.guild.text_channels[0].messages = [Message(20)]
        _i, text = await self.call(Security.purge, NS(id=20, bot=False, mention="<@20>"))
        self.assertIn("화이트리스트", text)
        self.assertEqual(len(self.guild.text_channels[0].messages), 1)


class ListTests(Base):
    async def test_white_and_black_lists(self):
        bot_user = NS(id=30, bot=True, mention="<@30>")
        user = NS(id=31, bot=False, mention="<@31>")
        role = Role(72, 1)
        await self.call(Security.white_add, user=bot_user, role=role)
        self.assertEqual((self.cfg()["white"]["bots"], self.cfg()["white"]["roles"]), ([30], [72]))
        _i, text = await self.call(Security.black_add, user=bot_user)
        self.assertIn("화이트리스트에 있어요", text)                                      # 화이트에 있으면 블랙에 못 넣는다
        _i, text = await self.call(Security.black_add, user=user)
        self.assertEqual(self.cfg()["black"]["users"], [31])
        self.assertIn("차단 ✅", text)
        self.guild.ban.assert_awaited()                                                  # 넣는 즉시 차단(서버에 없어도 ID로)
        await self.call(Security.white_add, user=user)                                    # 화이트에 넣으면 블랙에서 빠진다
        self.assertEqual((self.cfg()["white"]["users"], self.cfg()["black"]["users"]), ([31], []))
        _i, text = await self.call(Security.black_add, role=Role(73, 1))
        self.assertIn("지금 가진 사람은 그대로", text)
        _i, text = await self.call(Security.white_remove, user=NS(id=55, bot=False, mention="<@55>"))
        self.assertIn("목록에 없었어요", text)

    async def test_refusals(self):
        for who in (NS(id=OWNER, bot=False, mention="o"), NS(id=BOTID, bot=True, mention="b"), NS(id=ADMIN, bot=False, mention="a")):
            _i, text = await self.call(Security.black_add, user=who)
            self.assertIn("넣을 수 없어요", text)
        self.assertEqual(self.cfg()["black"]["users"], [])
        _i, text = await self.call(Security.white_add)
        self.assertIn("하나 이상", text)
        _i, text = await self.call(Security.white_add, role=self.guild.default_role)
        self.assertIn("@everyone", text)
        self.guild.ban.side_effect = fail
        _i, text = await self.call(Security.black_add, user=NS(id=40, bot=False, mention="<@40>"))
        self.assertIn("차단 실패", text)
        self.assertEqual(self.cfg()["black"]["users"], [40])                             # 실패해도 목록에는 남아 다시 들어오면 막는다


class DetectTests(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.cfg_save = lambda **detect: self.store.update_settings(G, security=dict(self.cfg(), detect=dict(self.cfg()["detect"], **detect)))

    def owner_dms(self):
        return self.guild.get_member(OWNER).send.await_count

    async def test_blacklisted_join_is_banned_and_alerted(self):
        await self.call(Security.black_add, user=NS(id=40, bot=False, mention="<@40>"))
        self.guild.ban.reset_mock()
        before = self.owner_dms()
        await self.cog.on_member_join(self.guild.join(40))
        self.guild.ban.assert_awaited()
        self.assertEqual(self.owner_dms(), before + 1)

    async def test_unknown_bot_and_join_flood(self):
        await self.cog.on_member_join(self.guild.join(41, bot=True))
        self.assertEqual(self.owner_dms(), 1)
        self.store.update_settings(G, security=dict(self.cfg(), white={"channels": [], "users": [], "bots": [42], "roles": []}))
        await self.cog.on_member_join(self.guild.join(42, bot=True))                     # 화이트 봇은 조용히
        self.assertEqual(self.owner_dms(), 1)
        self.cfg_save(joins=3, join_seconds=60)
        for uid in (43, 44, 45, 46):
            await self.cog.on_member_join(self.guild.join(uid))
        self.assertEqual(self.owner_dms(), 2)                                            # 가입 폭주 알림은 한 번

    async def test_black_role_gain_is_banned(self):
        bad = Role(80, 1)
        self.store.update_settings(G, security=dict(self.cfg(), black={"users": [], "bots": [], "roles": [80]}))
        before = Member(50, everyone=self.guild.default_role)
        after = Member(50, roles=[bad], everyone=self.guild.default_role)
        after.guild = self.guild
        await self.cog.on_member_update(before, after)
        self.guild.ban.assert_awaited()
        self.guild.ban.reset_mock()
        await self.cog.on_member_update(after, after)                                    # 새로 받은 역할이 없으면 아무것도
        self.guild.ban.assert_not_awaited()
        normal = Member(50, roles=[Role(81, 1)], everyone=self.guild.default_role)
        normal.guild = self.guild
        await self.cog.on_member_update(before, normal)                                  # 평범한 역할을 받으면 차단하지 않는다
        self.guild.ban.assert_not_awaited()

    async def test_spam_alert_and_exemptions(self):
        self.cfg_save(messages=3, message_seconds=60)

        def msg(uid, channel=400, bot=False):
            return NS(guild=self.guild, webhook_id=None, author=NS(id=uid, bot=bot, mention=f"<@{uid}>", roles=[]),
                      channel=self.guild.get_channel(channel))
        for _ in range(3):
            await self.cog.on_message(msg(60))
        self.assertEqual(self.owner_dms(), 1)
        for _ in range(5):
            await self.cog.on_message(msg(61, channel=402))
        self.store.update_settings(G, security=dict(self.cfg(), white=dict(self.cfg()["white"], channels=[402])))
        for _ in range(5):
            await self.cog.on_message(msg(62, channel=402))                              # 화이트 채널은 세지 않는다
        for _ in range(5):
            await self.cog.on_message(msg(OWNER))                                        # 서버 주인은 세지 않는다
        self.assertEqual(self.owner_dms(), 2)
        self.cfg_save(on=False)
        for _ in range(5):
            await self.cog.on_message(msg(63))
        self.assertEqual(self.owner_dms(), 2)

    async def test_mass_deletes_alert(self):
        self.cfg_save(deletes=2, delete_seconds=60)
        for _ in range(2):
            await self.cog.on_guild_channel_delete(NS(guild=self.guild))
        self.assertEqual(self.owner_dms(), 1)
        for _ in range(2):
            await self.cog.on_guild_role_delete(NS(guild=self.guild))
        self.assertEqual(self.owner_dms(), 2)


class AlertTests(Base):
    async def test_only_owner_and_admin_role_members_get_dms(self):
        stranger = self.guild.join(90)
        admin_bot = Member(91, roles=[self.guild.admin_role], bot=True)
        self.guild.members.append(admin_bot)
        self.assertEqual(await self.cog.alert(self.guild, "테스트"), 1)                   # 관리자 역할을 정하기 전: 주인만
        await self.call(Security.admin_roles, self.guild.admin_role, discord.app_commands.Choice(name="추가", value="add"))
        self.assertEqual(await self.cog.alert(self.guild, "테스트"), 2)                   # 주인 + 관리자 역할(봇 제외)
        stranger.send.assert_not_awaited()
        admin_bot.send.assert_not_awaited()
        self.guild.get_member(ADMIN).send.side_effect = fail                              # DM이 막혀 있어도 멈추지 않는다
        self.assertEqual(await self.cog.alert(self.guild, "테스트"), 1)
        await self.call(Security.admin_roles, self.guild.admin_role, discord.app_commands.Choice(name="삭제", value="remove"))
        self.assertEqual(self.cfg()["admin_roles"], [])

    async def test_detect_settings_and_list(self):
        _i, text = await self.call(Security.detect, None, 20, 30)
        self.assertIn("가입 30초에 20명", text)
        i, _text = await self.call(Security.show)
        embed = i.response.send_message.call_args.kwargs["embed"]
        self.assertIn("풀려 있어요", embed.description)
        self.assertTrue(any("화이트" in f.name for f in embed.fields))


if __name__ == "__main__":
    unittest.main()
