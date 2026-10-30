"""시험용 가짜 객체: 난수, 역할, 서버·멤버, 상호작용, 채널."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord

from main import HalloweenBot
from services import Locks
from store import Store


class Scripted:
    """정해 둔 값을 차례로 돌려주는 가짜 난수 생성기. 값이 모자라면 예상 밖의 추가 추첨이 있었다는 뜻이라 시험이 실패한다."""

    def __init__(self, randoms=(), ints=()):
        self.randoms, self.ints = list(randoms), list(ints)

    def random(self):
        assert self.randoms, "예상보다 난수를 더 뽑았어요"
        return self.randoms.pop(0)

    def randint(self, low, high):
        return self.ints.pop(0) if self.ints else low


class FakeRole:
    def __init__(self, role_id, position=1, permissions=None, managed=False, default=False, name="역할"):
        self.id, self.position, self.managed, self.default, self.name = role_id, position, managed, default, name
        self.permissions = permissions or discord.Permissions(send_messages=True)
        self.mention = f"<@&{role_id}>"

    def is_default(self):
        return self.default

    def __ge__(self, other):
        return self.position >= other.position


class World:
    """가짜 서버 하나와 멤버들. add_roles/remove_roles가 멤버의 역할 목록을 실제로 바꾼다."""

    def __init__(self, rng=None, guild_ids=None):
        self.store = Store(":memory:")
        self.bot = HalloweenBot(self.store, guild_ids, rng or Scripted())
        self.locks = self.bot.locks
        self.top = FakeRole(1, position=10)
        self.roles = {}
        self.me = NS(top_role=self.top, guild_permissions=discord.Permissions(manage_roles=True))
        self.channels = {}
        self.guild = NS(id=100, name="테스트 서버", owner_id=1, me=self.me, get_role=lambda rid: self.roles.get(rid),
                        get_channel=lambda cid: self.channels.get(cid))
        self.members = {}
        self.member = self.new_member(7)

    def new_member(self, uid, name=None):
        member = NS(id=uid, guild=self.guild, roles=[], display_name=name or f"user{uid}", mention=f"<@{uid}>", bot=False)

        async def add_roles(role, reason=None):
            await asyncio.sleep(0)
            member.roles.append(role)

        async def remove_roles(role, reason=None):
            await asyncio.sleep(0)
            member.roles.remove(role)
        member.add_roles, member.remove_roles = AsyncMock(side_effect=add_roles), AsyncMock(side_effect=remove_roles)
        self.members[uid] = member
        return member

    def add_role(self, role_id=50, **kw):
        self.roles[role_id] = FakeRole(role_id, **kw)
        return self.roles[role_id]

    def channel(self, channel_id=400, *, perms=None, text=True):
        sent = []

        async def send(**kwargs):
            sent.append(kwargs)
            return NS(id=9000 + len(sent), jump_url=f"https://discord.com/channels/100/{channel_id}/{9000 + len(sent)}")

        base = discord.TextChannel if text else object
        channel = MagicMock(spec=base)
        channel.id, channel.mention, channel.guild = channel_id, f"<#{channel_id}>", self.guild
        channel.send = AsyncMock(side_effect=send)
        channel.sent = sent
        channel.partial = {}
        channel.permissions_for = lambda who: perms if perms is not None else discord.Permissions(
            view_channel=True, send_messages=True, embed_links=True)

        def get_partial_message(message_id):
            return channel.partial.setdefault(message_id, NS(id=message_id, edit=AsyncMock(), delete=AsyncMock()))
        channel.get_partial_message = get_partial_message
        self.channels[channel_id] = channel
        return channel

    def message(self, content="안녕", *, uid=7, channel=None, **kw):
        channel = channel or self.channels.get(400) or self.channel(400)
        author = self.members.get(uid) or self.new_member(uid)
        defaults = dict(guild=self.guild, author=author, channel=channel, webhook_id=None, type=discord.MessageType.default,
                        content=content, add_reaction=AsyncMock())
        defaults.update(kw)
        return NS(**defaults)

    def interaction(self, *, uid=7, admin=False, message_id=9001, data=None, channel=None, perms=None):
        member = self.members.get(uid) or self.new_member(uid)
        permissions = perms or (discord.Permissions(administrator=True) if admin else discord.Permissions(send_messages=True))
        return NS(client=self.bot, guild=self.guild, guild_id=100, user=member, permissions=permissions, data=data or {},
                  message=NS(id=message_id), channel=channel or self.channels.get(400),
                  response=NS(send_message=AsyncMock(), defer=AsyncMock(), send_modal=AsyncMock(), edit_message=AsyncMock()),
                  followup=NS(send=AsyncMock()), edit_original_response=AsyncMock())

    async def close(self):
        await self.bot.close()
        self.store.close()


def sent_embed(interaction, which="send_message"):
    """상호작용이 마지막으로 보낸 임베드와 그 옵션."""
    target = interaction.response.send_message if which == "send_message" else interaction.followup.send
    call = target.await_args
    return call.kwargs.get("embed"), call.kwargs
