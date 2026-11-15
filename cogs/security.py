"""대테러(/대테러 …): 서버 잠금·해제, 최근 가입자 일괄 추방/차단, 도배 메시지 일괄 삭제, 화이트·블랙리스트, 알림 받을 관리자 역할, 감지 기준.

- 명령어는 서버 관리자만 쓴다. 감지는 **알림만** 한다(가입 폭주·도배·채널/역할 대량 삭제·화이트리스트에 없는 봇 가입·블랙리스트 차단).
  알림은 서버 주인과 '관리자 역할'(/대테러 관리자역할)을 가진 사람에게만 DM으로 간다.
- 가입 감지·블랙리스트 재가입 차단·블랙 역할·최근 가입자 처리·관리자 역할 알림은 디스코드의 멤버 인텐트가 필요하다(.env의 MEMBERS_INTENT).
"""
from __future__ import annotations

import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

import security
import theme
from access import require_admin
from owner import owner_only

LOG = logging.getLogger("halloween")
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
Choice = app_commands.Choice
ALERT_MAX = 25                      # 알림 DM을 보내는 사람 수 한도
REASON = "대테러 대응"
INTENT_NOTE = "⚠️ 이 기능은 멤버 인텐트가 필요해요. 개발자 포털 Bot 탭의 'Server Members Intent'를 켜고 .env에 `MEMBERS_INTENT=1`을 적은 뒤 봇을 다시 켜 주세요."


def mention_ids(ids, kind: str) -> str:
    form = {"channels": "<#{}>", "roles": "<@&{}>"}.get(kind, "<@{}>")
    return ", ".join(form.format(i) for i in ids[:40]) + (f" 외 {len(ids) - 40}개" if len(ids) > 40 else "") if ids else "없음"


class Security(commands.Cog):
    """🛡️ 대테러 대응 (관리자)."""

    group = app_commands.Group(name="대테러", description="서버 테러(도배·대량 가입 등)에 대응해요 (관리자)", **PERMS)
    white = app_commands.Group(name="화이트", description="대응·감지에서 빼 줄 채널·유저·봇·역할 (관리자)", parent=group)
    black = app_commands.Group(name="블랙", description="들어오면 차단할 유저·봇·역할 (관리자)", parent=group)

    def __init__(self, bot):
        self.bot = bot
        self.watch = security.Watch()

    # ---------------------------------------------------------------- 설정
    def cfg(self, guild_id: int) -> dict:
        """대테러 설정. 메시지마다 불리므로 설정 전체를 복사하지 않고 캐시를 읽는다(clean이 새 목록을 만들어서 캐시는 바뀌지 않는다)."""
        store = self.bot.store
        raw = getattr(store, "_settings", {}).get(guild_id) or store.settings(guild_id)
        return security.clean(raw["security"])

    def save(self, guild_id: int, cfg: dict) -> None:
        self.bot.store.update_settings(guild_id, security=cfg)

    def members_intent(self) -> bool:
        return bool(getattr(getattr(self.bot, "intents", None), "members", False))

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    # ---------------------------------------------------------------- 알림
    async def alert(self, guild, text: str) -> int:
        """서버 주인과 관리자 역할을 가진 사람(봇 제외)에게만 DM으로 알린다. 보낸 수."""
        cfg = self.cfg(guild.id)
        people = {}
        owner = guild.get_member(guild.owner_id) if guild.owner_id else None
        if owner is None and guild.owner_id:
            try:
                owner = await guild.fetch_member(guild.owner_id)
            except discord.HTTPException:
                owner = None
        if owner is not None:
            people[owner.id] = owner
        for role_id in cfg["admin_roles"]:
            role = guild.get_role(role_id)
            for member in (role.members if role else []):
                if not member.bot:
                    people.setdefault(member.id, member)
        embed = theme.make_embed("🚨 대테러 알림", text, color=theme.RED)
        embed.set_footer(text=f"{guild.name} · /대테러 로 대응할 수 있어요")
        sent = 0
        for member in list(people.values())[:ALERT_MAX]:
            try:
                await member.send(embed=embed)
                sent += 1
            except discord.HTTPException:
                pass
        LOG.warning("대테러 알림(서버 %s): %s → %d명", guild.id, text.replace("\n", " "), sent)
        return sent

    async def _ban(self, guild, user, why: str) -> bool:
        try:
            await guild.ban(user, reason=f"{REASON}: {why}", delete_message_seconds=0)
            return True
        except discord.HTTPException:
            return False

    # ---------------------------------------------------------------- 감지(알림만)
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if hasattr(self.bot, "allowed_guild") and not self.bot.allowed_guild(guild.id):
            return
        cfg = self.cfg(guild.id)
        if security.is_white_member(cfg, member.id, [r.id for r in member.roles], member.bot):
            return
        if security.is_black(cfg, member.id, member.bot) or security.black_roles(cfg, [r.id for r in member.roles]):
            done = await self._ban(guild, member, "블랙리스트")
            await self.alert(guild, f"⛔ 블랙리스트 {'봇' if member.bot else '유저'} {member.mention}({member.id})가 들어와서 "
                                    + ("**차단했어요**." if done else "차단하려 했지만 **실패했어요**(봇 권한·역할 순서를 확인해 주세요)."))
            return
        if member.bot:
            await self.alert(guild, f"🤖 화이트리스트에 없는 봇 {member.mention}({member.id})이(가) 들어왔어요. 모르는 봇이면 `/대테러 블랙 추가`로 차단하세요.")
        detect = cfg["detect"]
        if detect["on"] and self.watch.hit(("join", guild.id), detect["joins"], detect["join_seconds"]):
            await self.alert(guild, f"👥 **가입 폭주**: {detect['join_seconds']}초 안에 {detect['joins']}명 넘게 들어왔어요.\n"
                                    "`/대테러 잠금`으로 채팅을 막고 `/대테러 가입자처리`로 최근 가입자를 내보낼 수 있어요.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        gained = {r.id for r in after.roles} - {r.id for r in before.roles}
        if not gained:
            return
        cfg = self.cfg(after.guild.id)
        bad = security.black_roles(cfg, gained)
        if not bad or security.is_white_member(cfg, after.id, [r.id for r in after.roles], after.bot) or after.id == after.guild.owner_id:
            return
        done = await self._ban(after.guild, after, "블랙리스트 역할")
        await self.alert(after.guild, f"⛔ {after.mention}({after.id})이(가) 블랙리스트 역할 {mention_ids(bad, 'roles')}을(를) 받아서 "
                                      + ("**차단했어요**." if done else "차단하려 했지만 **실패했어요**."))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.webhook_id is not None or message.author.id == getattr(self.bot.user, "id", None):
            return
        cfg = self.cfg(message.guild.id)
        detect = cfg["detect"]
        if not detect["on"] or message.channel.id in cfg["white"]["channels"] or message.author.id == message.guild.owner_id:
            return
        roles = [r.id for r in getattr(message.author, "roles", [])]
        if security.is_white_member(cfg, message.author.id, roles, message.author.bot):
            return
        if self.watch.hit(("msg", message.guild.id, message.author.id), detect["messages"], detect["message_seconds"]):
            await self.alert(message.guild, f"💬 **도배 감지**: {message.author.mention}({message.author.id})이(가) {detect['message_seconds']}초 안에 "
                                            f"{detect['messages']}개 넘게 보냈어요(마지막: {message.channel.mention}).\n"
                                            f"`/대테러 도배삭제`로 지우고 `/대테러 블랙 추가`로 차단할 수 있어요.")

    async def _deleted(self, guild, what: str):
        detect = self.cfg(guild.id)["detect"]
        if detect["on"] and self.watch.hit(("del", what, guild.id), detect["deletes"], detect["delete_seconds"]):
            await self.alert(guild, f"🧨 **{what} 대량 삭제**: {detect['delete_seconds']}초 안에 {detect['deletes']}개 넘게 지워졌어요. "
                                    "서버 설정의 감사 로그에서 누가 지웠는지 확인하고, 그 계정의 권한을 거둬 주세요.")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self._deleted(channel.guild, "채널")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self._deleted(role.guild, "역할")

    # ---------------------------------------------------------------- 잠금
    @group.command(name="잠금", description="화이트 채널을 빼고 모든 채팅 채널에서 @everyone이 메시지를 못 보내게 막아요 (관리자)")
    @app_commands.rename(slowmode="슬로우모드")
    @app_commands.describe(slowmode="함께 걸 슬로우모드(초, 0~21600). 비우면 슬로우모드는 그대로")
    async def lock(self, interaction: discord.Interaction, slowmode: app_commands.Range[int, 0, 21600] | None = None):
        if not await require_admin(interaction):
            return
        guild = interaction.guild
        cfg = self.cfg(guild.id)
        if cfg["lock"]["active"]:
            await self._say(interaction, "이미 잠겨 있어요. 풀려면 `/대테러 해제`를 써 주세요.", False)
            return
        await interaction.response.defer()
        everyone, saved, failed = guild.default_role, {}, []
        for channel in guild.text_channels:
            if channel.id in cfg["white"]["channels"]:
                continue
            overwrite = channel.overwrites_for(everyone)
            before = {"send": overwrite.send_messages, "slow": channel.slowmode_delay}
            try:
                overwrite.send_messages = False
                await channel.set_permissions(everyone, overwrite=overwrite, reason=REASON)
                saved[str(channel.id)] = before                     # 바꾼 채널만 기억한다(해제 때 되돌린다)
                if slowmode is not None and slowmode != channel.slowmode_delay:
                    await channel.edit(slowmode_delay=slowmode, reason=REASON)
            except discord.HTTPException:
                failed.append(channel.id)
        cfg["lock"] = {"active": bool(saved), "channels": saved, "at": int(datetime.datetime.now().timestamp())}
        self.save(guild.id, cfg)
        LOG.warning("대테러 잠금: 관리자 %s, 서버 %s, 채널 %d개 (실패 %d)", interaction.user.id, guild.id, len(saved), len(failed))
        text = f"🔒 **서버를 잠갔어요.** 채널 {len(saved)}개에서 @everyone이 메시지를 못 보내요" + (f"(슬로우모드 {slowmode}초)" if slowmode else "") + "."
        if cfg["white"]["channels"]:
            text += f"\n화이트 채널은 그대로예요: {mention_ids(cfg['white']['channels'], 'channels')}"
        if failed:
            text += f"\n⚠️ {len(failed)}개 채널은 권한이 없어서 못 잠갔어요: {mention_ids(failed, 'channels')} (봇에 '채널 관리'·'권한 관리'가 필요해요)"
        text += "\n풀 때는 `/대테러 해제`."
        await interaction.followup.send(embed=theme.notice(text, not failed))
        await self.alert(guild, f"🔒 {interaction.user.mention}이(가) 서버를 잠갔어요(채널 {len(saved)}개).")

    @group.command(name="해제", description="잠금을 풀고 채널 권한·슬로우모드를 잠그기 전으로 되돌려요 (관리자)")
    async def unlock(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        guild = interaction.guild
        cfg = self.cfg(guild.id)
        if not cfg["lock"]["active"]:
            await self._say(interaction, "잠겨 있지 않아요.", False)
            return
        await interaction.response.defer()
        everyone, restored, failed, left = guild.default_role, 0, [], {}
        for cid, before in cfg["lock"]["channels"].items():
            channel = guild.get_channel(int(cid))
            if channel is None:                                       # 그 사이 지워진 채널
                continue
            try:
                overwrite = channel.overwrites_for(everyone)
                overwrite.send_messages = before["send"]
                await channel.set_permissions(everyone, overwrite=None if overwrite.is_empty() else overwrite, reason=REASON)
                if channel.slowmode_delay != before["slow"]:
                    await channel.edit(slowmode_delay=before["slow"], reason=REASON)
                restored += 1
            except discord.HTTPException:
                failed.append(int(cid))
                left[cid] = before
        cfg["lock"] = {"active": bool(left), "channels": left, "at": cfg["lock"]["at"]}       # 못 되돌린 채널은 남겨서 다시 해제할 수 있게
        self.save(guild.id, cfg)
        LOG.warning("대테러 해제: 관리자 %s, 서버 %s, 채널 %d개 (실패 %d)", interaction.user.id, guild.id, restored, len(failed))
        text = f"🔓 **잠금을 풀었어요.** 채널 {restored}개를 잠그기 전으로 되돌렸어요."
        if failed:
            text += f"\n⚠️ {len(failed)}개 채널은 못 되돌렸어요: {mention_ids(failed, 'channels')}. 권한을 확인하고 `/대테러 해제`를 다시 써 주세요."
        await interaction.followup.send(embed=theme.notice(text, not failed))

    # ---------------------------------------------------------------- 최근 가입자
    def recent_joiners(self, guild, minutes: int, now: datetime.datetime | None = None) -> tuple[list, int]:
        """최근 minutes분 안에 들어온 처리 대상(화이트·주인·관리자 권한·봇보다 높은 역할 제외), 빠진 수."""
        cfg = self.cfg(guild.id)
        t = now or datetime.datetime.now(datetime.timezone.utc)
        since = t - datetime.timedelta(minutes=minutes)
        me = guild.me
        targets, skipped = [], 0
        for member in guild.members:
            if member.joined_at is None or member.joined_at < since or member.id in (guild.owner_id, getattr(me, "id", None)):
                continue
            if security.is_white_member(cfg, member.id, [r.id for r in member.roles], member.bot) or member.guild_permissions.administrator \
                    or (me is not None and member.top_role >= me.top_role):
                skipped += 1
                continue
            targets.append(member)
        return targets, skipped

    @group.command(name="가입자처리", description="최근 N분 안에 들어온 계정을 한 번에 추방하거나 차단해요. 확인 버튼을 눌러야 실행돼요 (관리자)")
    @app_commands.rename(minutes="분", how="처리")
    @app_commands.describe(minutes="최근 몇 분 안에 들어온 계정(1~1440)", how="추방 또는 차단")
    @app_commands.choices(how=[Choice(name="추방(다시 들어올 수 있어요)", value="kick"), Choice(name="차단", value="ban")])
    async def joiners(self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 1440], how: Choice[str]):
        if not await require_admin(interaction):
            return
        if not self.members_intent():
            await self._say(interaction, INTENT_NOTE, False)
            return
        guild = interaction.guild
        await interaction.response.defer()
        try:
            await guild.chunk()
        except discord.HTTPException:
            pass
        targets, skipped = self.recent_joiners(guild, minutes)
        if not targets:
            await interaction.followup.send(embed=theme.notice(f"최근 {minutes}분 안에 들어온 처리 대상이 없어요." + (f" (화이트·관리자 등 {skipped}명은 뺐어요)" if skipped else ""), False))
            return
        verb = "차단" if how.value == "ban" else "추방"
        preview = ", ".join(m.mention for m in targets[:20]) + (f" 외 {len(targets) - 20}명" if len(targets) > 20 else "")
        view = JoinersConfirm(self, interaction.user.id, [m.id for m in targets], how.value, minutes)
        await interaction.followup.send(embed=theme.notice(f"⚠️ 최근 {minutes}분 안에 들어온 **{len(targets)}명**을 **{verb}**할까요?\n{preview}"
                                                           + (f"\n(화이트·관리자·봇보다 높은 역할 {skipped}명은 뺐어요)" if skipped else "")
                                                           + "\n60초 안에 확인을 눌러 주세요.", False), view=view)

    async def punish(self, guild, user_ids, how: str, admin_id: int) -> tuple[int, int]:
        """추방/차단을 실행한다. (성공 수, 실패 수)."""
        ok = bad = 0
        for uid in user_ids:
            member = guild.get_member(uid)
            try:
                if how == "ban":
                    await guild.ban(member or discord.Object(id=uid), reason=f"{REASON}: 최근 가입자 일괄 차단", delete_message_seconds=0)
                elif member is not None:
                    await member.kick(reason=f"{REASON}: 최근 가입자 일괄 추방")
                else:
                    continue
                ok += 1
            except discord.HTTPException:
                bad += 1
        LOG.warning("대테러 가입자처리(%s): 관리자 %s, 서버 %s, 성공 %d, 실패 %d", how, admin_id, guild.id, ok, bad)
        return ok, bad

    # ---------------------------------------------------------------- 도배 삭제
    @group.command(name="도배삭제", description="한 사람이 최근에 보낸 메시지를 화이트 채널을 빼고 모든 채널에서 한 번에 지워요 (관리자)")
    @app_commands.rename(user="사람", minutes="분", limit="채널당개수")
    @app_commands.describe(user="메시지를 지울 사람(나간 사람도 ID로 돼요)", minutes="최근 몇 분 안의 메시지(1~1440, 기본 60)",
                           limit="채널마다 살펴볼 최근 메시지 수(1~1000, 기본 200)")
    async def purge(self, interaction: discord.Interaction, user: discord.User, minutes: app_commands.Range[int, 1, 1440] = 60,
                    limit: app_commands.Range[int, 1, 1000] = 200):
        if not await require_admin(interaction):
            return
        guild = interaction.guild
        cfg = self.cfg(guild.id)
        roles = [r.id for r in getattr(guild.get_member(user.id), "roles", [])]
        if security.is_white_member(cfg, user.id, roles, user.bot):
            await self._say(interaction, f"{user.mention}은(는) 화이트리스트라서 지우지 않았어요.", False)
            return
        await interaction.response.defer()
        after = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
        total, failed = 0, []
        for channel in guild.text_channels:
            if channel.id in cfg["white"]["channels"]:
                continue
            try:
                gone = await channel.purge(limit=limit, after=after, check=lambda m: m.author.id == user.id, reason=REASON)
                total += len(gone)
            except discord.HTTPException:
                failed.append(channel.id)
        LOG.warning("대테러 도배삭제: 관리자 %s, 서버 %s, 대상 %s, %d개 (실패 채널 %d)", interaction.user.id, guild.id, user.id, total, len(failed))
        text = f"🧹 {user.mention}의 최근 {minutes}분 메시지 **{total}개**를 지웠어요."
        if failed:
            text += f"\n⚠️ {len(failed)}개 채널은 권한이 없어서 못 지웠어요(봇에 '메시지 관리'·'메시지 기록 보기'가 필요해요)."
        await interaction.followup.send(embed=theme.notice(text, not failed))

    # ---------------------------------------------------------------- 화이트·블랙리스트
    async def _change_list(self, interaction, side: str, add: bool, channel=None, user=None, role=None):
        if not await require_admin(interaction):
            return
        if channel is None and user is None and role is None:
            await self._say(interaction, "채널·사람·역할 중 하나 이상을 골라 주세요.", False)
            return
        guild = interaction.guild
        cfg = self.cfg(guild.id)
        lists = cfg[side]
        picks = []
        if channel is not None:
            picks.append(("channels", channel.id, channel.mention))
        if user is not None:
            picks.append(("bots" if user.bot else "users", user.id, user.mention))
        if role is not None:
            if role.is_default():
                await self._say(interaction, "@everyone 역할은 넣을 수 없어요.", False)
                return
            picks.append(("roles", role.id, role.mention))
        notes = []
        for kind, value, label in picks:
            if side == "black" and add:
                if value in (guild.owner_id, getattr(self.bot.user, "id", None), interaction.user.id):
                    await self._say(interaction, "서버 주인·이 봇·나 자신은 블랙리스트에 넣을 수 없어요.", False)
                    return
                if value in cfg["white"].get(kind, []):
                    await self._say(interaction, f"{label}은(는) 화이트리스트에 있어요. 먼저 `/대테러 화이트 삭제`를 해 주세요.", False)
                    return
            if add:
                if value not in lists[kind]:
                    if len(lists[kind]) >= security.LIST_MAX:
                        await self._say(interaction, f"목록마다 {security.LIST_MAX}개까지예요.", False)
                        return
                    lists[kind].append(value)
            elif value in lists[kind]:
                lists[kind].remove(value)
            else:
                notes.append(f"{label}은(는) 목록에 없었어요.")
                continue
            notes.append(label)
        if side == "white" and add:                                     # 화이트에 넣으면 블랙에서는 뺀다
            for kind, value, _label in picks:
                if kind in cfg["black"] and value in cfg["black"][kind]:
                    cfg["black"][kind].remove(value)
        self.save(guild.id, cfg)
        name = "화이트리스트" if side == "white" else "블랙리스트"
        text = f"{'➕' if add else '➖'} {name}을(를) 바꿨어요: " + ", ".join(notes)
        if side == "black" and add:
            banned = []
            for kind, value, label in picks:
                if kind in ("users", "bots"):
                    target = guild.get_member(value) or discord.Object(id=value)
                    banned.append(f"{label} " + ("차단 ✅" if await self._ban(guild, target, "블랙리스트") else "차단 실패 ⚠️(권한·역할 순서 확인)"))
                elif kind == "roles":
                    banned.append(f"{label}: 이제부터 이 역할을 받거나 가지고 들어오는 사람을 차단해요(지금 가진 사람은 그대로예요)")
            text += "\n" + "\n".join(banned)
            await self.alert(guild, f"⛔ {interaction.user.mention}이(가) 블랙리스트에 넣었어요: " + ", ".join(label for _k, _v, label in picks))
        await self._say(interaction, text)

    @white.command(name="추가", description="대응·감지에서 빼 줄 채널·사람(봇)·역할을 넣어요 (관리자)")
    @app_commands.rename(channel="채널", user="사람", role="역할")
    @app_commands.describe(channel="잠금·도배 감지·도배 삭제에서 뺄 채널", user="대응·감지에서 뺄 사람이나 봇", role="대응·감지에서 뺄 역할")
    async def white_add(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None, user: discord.User | None = None,
                        role: discord.Role | None = None):
        await self._change_list(interaction, "white", True, channel, user, role)

    @white.command(name="삭제", description="화이트리스트에서 빼요 (관리자)")
    @app_commands.rename(channel="채널", user="사람", role="역할")
    async def white_remove(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None, user: discord.User | None = None,
                           role: discord.Role | None = None):
        await self._change_list(interaction, "white", False, channel, user, role)

    @black.command(name="추가", description="블랙리스트에 넣어요. 사람·봇은 바로 차단하고, 다시 들어와도 차단해요 (관리자)")
    @app_commands.rename(user="사람", role="역할")
    @app_commands.describe(user="차단할 사람이나 봇(서버에 없어도 ID로 돼요)", role="받거나 가지고 들어오면 차단할 역할")
    async def black_add(self, interaction: discord.Interaction, user: discord.User | None = None, role: discord.Role | None = None):
        await self._change_list(interaction, "black", True, None, user, role)

    @black.command(name="삭제", description="블랙리스트에서 빼요. 이미 한 차단은 서버 설정에서 따로 풀어야 해요 (관리자)")
    @app_commands.rename(user="사람", role="역할")
    async def black_remove(self, interaction: discord.Interaction, user: discord.User | None = None, role: discord.Role | None = None):
        await self._change_list(interaction, "black", False, None, user, role)

    # ---------------------------------------------------------------- 관리자 역할·감지 설정·목록
    @group.command(name="관리자역할", description="테러 알림(DM)을 받을 '관리자' 역할을 넣거나 빼요. 서버 주인은 항상 받아요 (관리자)")
    @app_commands.rename(role="역할", action="동작")
    @app_commands.choices(action=[Choice(name="추가", value="add"), Choice(name="삭제", value="remove")])
    async def admin_roles(self, interaction: discord.Interaction, role: discord.Role, action: Choice[str]):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        roles = cfg["admin_roles"]
        if action.value == "add":
            if role.is_default():
                await self._say(interaction, "@everyone 역할은 넣을 수 없어요.", False)
                return
            if role.id not in roles:
                if len(roles) >= security.ADMIN_ROLES_MAX:
                    await self._say(interaction, f"관리자 역할은 {security.ADMIN_ROLES_MAX}개까지예요.", False)
                    return
                roles.append(role.id)
        elif role.id in roles:
            roles.remove(role.id)
        else:
            await self._say(interaction, f"{role.mention}은(는) 관리자 역할이 아니었어요.", False)
            return
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, f"👮 알림을 받는 관리자 역할: {mention_ids(roles, 'roles')} (+ 서버 주인)"
                        + ("" if self.members_intent() else "\n" + INTENT_NOTE.replace("이 기능은", "관리자 역할에게 보내는 알림은")))

    @group.command(name="감지설정", description="테러 감지(알림) 기준을 바꿔요. 비운 칸은 그대로 (관리자)")
    @app_commands.rename(on="켜기", joins="가입수", join_seconds="가입초", messages="메시지수", message_seconds="메시지초",
                         deletes="삭제수", delete_seconds="삭제초")
    @app_commands.describe(on="감지를 켤까요", joins="가입초 안에 몇 명 넘게 들어오면 알릴까요", join_seconds="가입을 세는 시간(초)",
                           messages="한 사람이 메시지초 안에 몇 개 넘게 보내면 알릴까요", message_seconds="메시지를 세는 시간(초)",
                           deletes="채널·역할이 삭제초 안에 몇 개 넘게 지워지면 알릴까요", delete_seconds="삭제를 세는 시간(초)")
    async def detect(self, interaction: discord.Interaction, on: bool | None = None, joins: app_commands.Range[int, 2, 500] | None = None,
                     join_seconds: app_commands.Range[int, 5, 3600] | None = None, messages: app_commands.Range[int, 3, 100] | None = None,
                     message_seconds: app_commands.Range[int, 2, 600] | None = None, deletes: app_commands.Range[int, 2, 100] | None = None,
                     delete_seconds: app_commands.Range[int, 5, 3600] | None = None):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        changes = {k: v for k, v in dict(on=on, joins=joins, join_seconds=join_seconds, messages=messages, message_seconds=message_seconds,
                                         deletes=deletes, delete_seconds=delete_seconds).items() if v is not None}
        cfg["detect"].update(changes)
        if changes:
            self.save(interaction.guild_id, cfg)
        await self._say(interaction, ("🔧 감지 기준을 바꿨어요.\n" if changes else "") + self.detect_text(cfg))

    def detect_text(self, cfg) -> str:
        d = cfg["detect"]
        return (f"감지 {'켜짐' if d['on'] else '꺼짐'} · 가입 {d['join_seconds']}초에 {d['joins']}명 · 도배 {d['message_seconds']}초에 {d['messages']}개 · "
                f"채널/역할 삭제 {d['delete_seconds']}초에 {d['deletes']}개 · 같은 알림은 {security.ALERT_COOLDOWN // 60}분에 한 번")

    @group.command(name="목록", description="화이트·블랙리스트, 관리자 역할, 감지 기준, 잠금 상태를 봐요 (관리자)")
    async def show(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        embed = theme.make_embed("🛡️ 대테러 설정", self.detect_text(cfg)
                                 + f"\n🔒 잠금: {'**잠겨 있어요**(채널 ' + str(len(cfg['lock']['channels'])) + '개)' if cfg['lock']['active'] else '풀려 있어요'}"
                                 + ("" if self.members_intent() else "\n" + INTENT_NOTE), color=theme.PURPLE)
        embed.add_field(name="👮 알림 받는 관리자 역할", value=mention_ids(cfg["admin_roles"], "roles") + " (+ 서버 주인)", inline=False)
        for kind, label in (("channels", "채널"), ("users", "유저"), ("bots", "봇"), ("roles", "역할")):
            embed.add_field(name=f"✅ 화이트 {label}", value=mention_ids(cfg["white"][kind], kind)[:1024], inline=True)
        for kind, label in (("users", "유저"), ("bots", "봇"), ("roles", "역할")):
            embed.add_field(name=f"⛔ 블랙 {label}", value=mention_ids(cfg["black"][kind], kind)[:1024], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=False)


class JoinersConfirm(discord.ui.View):
    """최근 가입자 일괄 처리 확인. 명령을 쓴 관리자만 누를 수 있다."""

    def __init__(self, cog: Security, admin_id: int, user_ids: list[int], how: str, minutes: int):
        super().__init__(timeout=60)
        self.cog, self.admin_id, self.user_ids, self.how, self.minutes = cog, admin_id, user_ids, how, minutes
        yes = discord.ui.Button(label="차단" if how == "ban" else "추방", emoji="⛔", style=discord.ButtonStyle.danger)
        no = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.confirm, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.admin_id)

    async def confirm(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("⏳ 처리하는 중이에요…"), view=None)
        ok, bad = await self.cog.punish(interaction.guild, self.user_ids, self.how, self.admin_id)
        verb = "차단" if self.how == "ban" else "추방"
        await interaction.edit_original_response(embed=theme.notice(f"⛔ 최근 {self.minutes}분 가입자 **{ok}명**을 {verb}했어요."
                                                                    + (f"\n⚠️ {bad}명은 실패했어요(권한·역할 순서 확인)." if bad else ""), not bad))
        await self.cog.alert(interaction.guild, f"⛔ {interaction.user.mention}이(가) 최근 {self.minutes}분 가입자 {ok}명을 {verb}했어요.")

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("취소했어요. 아무것도 하지 않았어요."), view=None)


async def setup(bot):
    await bot.add_cog(Security(bot))
