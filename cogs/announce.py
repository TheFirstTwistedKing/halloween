"""관리자 공지(/공지): 봇이 직접 쓴 것처럼 채널에 공지를 올린다.

- 관리자만 쓸 수 있다. 명령어를 쓰면 제목·내용을 적는 입력창이 뜨고, 보내면 고른 채널에 **봇 이름으로 새 메시지**가 올라간다.
  입력창 응답은 아무 메시지도 남기지 않아서(defer) '누가 명령어를 썼다'는 흔적 없이 봇의 공지만 보인다.
- 형식은 카드(임베드)나 일반 글. @everyone·@here·역할 멘션을 고를 수 있고, 그림 주소를 붙일 수 있다.
- 봇이 그 채널에 글을 쓸 권한(멘션·임베드 권한 포함)이 없으면 보내지 않고 이유를 알려 준다(그때만 응답이 남는다).
"""
from __future__ import annotations

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

import theme
from access import is_admin, require_admin

LOG = logging.getLogger("halloween")
Choice = app_commands.Choice
STYLES = [Choice(name="카드(임베드)", value="embed"), Choice(name="일반 글", value="plain")]
PINGS = [Choice(name="멘션 없음", value="none"), Choice(name="@everyone (모두)", value="everyone"), Choice(name="@here (접속 중인 사람)", value="here")]
TITLE_MAX = 256                  # 디스코드 임베드 제목 한도
BODY_MAX = 4000                  # 입력창 한 칸의 한도(디스코드)
PLAIN_MAX = 2000                 # 일반 메시지 한도(디스코드)
IMAGE_MAX = 300


def check_image(url: str | None) -> str | None:
    """그림 주소가 올바르면 None, 아니면 이유."""
    if not url:
        return None
    if len(url) > IMAGE_MAX or not url.startswith(("http://", "https://")) or any(c.isspace() for c in url):
        return "그림은 공백 없는 http:// 또는 https:// 주소(300자까지)여야 해요."
    return None


def mention_text(ping: str, role) -> str:
    """메시지 맨 앞에 붙일 멘션 글자."""
    parts = {"everyone": ["@everyone"], "here": ["@here"]}.get(ping, [])
    if role is not None:
        parts.append(role.mention)
    return " ".join(parts)


def build_announcement(*, style: str, title: str, body: str, ping: str, role=None, image: str | None = None, guild_name: str = "") -> dict:
    """channel.send에 넘길 값. 일반 글이 2000자를 넘으면 ValueError(이유)."""
    head = mention_text(ping, role)
    allowed = discord.AllowedMentions(everyone=ping in ("everyone", "here"), roles=[role] if role is not None else False, users=False,
                                      replied_user=False)
    if style == "plain":
        text = "\n".join(part for part in (head, f"## {title}" if title else "", body) if part)
        if image:
            text += f"\n{image}"
        if len(text) > PLAIN_MAX:
            raise ValueError(f"일반 글은 {PLAIN_MAX:,}자까지예요(지금 {len(text):,}자). 카드(임베드) 형식으로 보내거나 내용을 줄여 주세요.")
        return {"content": text, "allowed_mentions": allowed}
    embed = discord.Embed(title=title or None, description=body, color=theme.ORANGE, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_footer(text=f"📢 {guild_name} 공지" if guild_name else "📢 공지")
    if image:
        embed.set_image(url=image)
    return {"content": head or None, "embed": embed, "allowed_mentions": allowed}


def missing_permissions(channel, me, *, style: str, ping: str, role) -> list[str]:
    """봇이 그 채널에 이 공지를 보내는 데 모자란 권한(사람이 읽는 이름). 다 있으면 빈 목록."""
    perms = channel.permissions_for(me)
    missing = []
    if not perms.view_channel:
        missing.append("채널 보기")
    if not perms.send_messages:
        missing.append("메시지 보내기")
    if style == "embed" and not perms.embed_links:
        missing.append("링크 첨부(카드 형식에 필요)")
    if ping in ("everyone", "here") and not perms.mention_everyone:
        missing.append("@everyone·@here 멘션")
    if role is not None and not getattr(role, "mentionable", False) and not perms.mention_everyone:
        missing.append(f"{role.mention} 역할 멘션(역할을 '누구나 멘션 가능'으로 하거나 봇에 @everyone 멘션 권한 필요)")
    return missing


class AnnounceModal(discord.ui.Modal):
    """공지 제목·내용을 적는 입력창. 보내면 고른 채널에 봇 이름으로 공지가 올라간다."""

    def __init__(self, cog: "Announce", channel, style: str, ping: str, role, image: str | None):
        super().__init__(title="📢 공지 작성", timeout=1800)
        self.cog, self.channel, self.style, self.ping, self.role, self.image = cog, channel, style, ping, role, image
        self.subject = discord.ui.TextInput(label="제목 (비워도 돼요)", required=False, max_length=TITLE_MAX)
        self.body = discord.ui.TextInput(label="내용", style=discord.TextStyle.paragraph, required=True, max_length=BODY_MAX)
        self.add_item(self.subject)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.publish(interaction, self.channel, style=self.style, title=self.subject.value.strip(), body=self.body.value.strip(),
                               ping=self.ping, role=self.role, image=self.image)


class Announce(commands.Cog):
    """📢 관리자 공지."""

    def __init__(self, bot):
        self.bot = bot

    async def _deny(self, interaction, text: str):
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=False)

    @app_commands.command(name="공지", description="봇 이름으로 채널에 공지를 올려요. 제목·내용은 다음 창에서 적어요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(channel="채널", style="형식", ping="멘션", role="역할", image="그림")
    @app_commands.describe(channel="공지를 올릴 채널(비우면 지금 채널)", style="카드(임베드) 또는 일반 글(기본 카드)",
                           ping="@everyone·@here로 알릴까요(기본 없음)", role="함께 멘션할 역할", image="공지에 붙일 그림 주소(http/https)")
    @app_commands.choices(style=STYLES, ping=PINGS)
    async def announce(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None, style: Choice[str] | None = None,
                       ping: Choice[str] | None = None, role: discord.Role | None = None, image: str | None = None):
        if not await require_admin(interaction):
            return
        target = channel or interaction.channel
        if target is None or not hasattr(target, "send"):
            await self._deny(interaction, "공지를 올릴 채널을 골라 주세요.")
            return
        image = image.strip() if image else None
        problem = check_image(image)
        if problem:
            await self._deny(interaction, problem)
            return
        style_value, ping_value = (style.value if style else "embed"), (ping.value if ping else "none")
        missing = missing_permissions(target, interaction.guild.me, style=style_value, ping=ping_value, role=role)
        if missing:
            await self._deny(interaction, f"봇이 {target.mention}에 이 공지를 보낼 권한이 없어요: " + ", ".join(missing))
            return
        await interaction.response.send_modal(AnnounceModal(self, target, style_value, ping_value, role, image))

    async def publish(self, interaction: discord.Interaction, channel, *, style: str, title: str, body: str, ping: str, role, image):
        """입력창을 보냈을 때: 공지를 올리고, 입력창은 메시지 없이 닫는다(흔적을 남기지 않는다)."""
        if not is_admin(interaction):                       # 입력창은 연 사람만 보내지만, 그 사이 권한을 잃었을 수도 있다.
            await self._deny(interaction, "이 명령어는 서버 관리자만 쓸 수 있어요.")
            return
        if not body:
            await self._deny(interaction, "내용을 적어 주세요.")
            return
        try:
            message = build_announcement(style=style, title=title, body=body, ping=ping, role=role, image=image,
                                         guild_name=getattr(interaction.guild, "name", ""))
        except ValueError as exc:
            await self._deny(interaction, str(exc))
            return
        try:
            sent = await channel.send(**message)
        except discord.HTTPException as exc:
            await self._deny(interaction, f"공지를 보내지 못했어요: {exc}")
            return
        LOG.info("공지: 관리자 %s → 채널 %s, 메시지 %s, 형식 %s, 멘션 %s (서버 %s)", interaction.user.id, channel.id, getattr(sent, "id", None),
                 style, ping + (f"+역할 {role.id}" if role is not None else ""), interaction.guild_id)
        try:
            await interaction.response.defer()                 # 입력창만 닫고 아무 메시지도 남기지 않는다
        except discord.HTTPException:
            LOG.warning("공지 입력창 닫기 실패(공지는 이미 올라갔어요): 서버 %s", interaction.guild_id)


async def setup(bot):
    await bot.add_cog(Announce(bot))
