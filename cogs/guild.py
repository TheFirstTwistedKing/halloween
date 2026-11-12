"""길드(/길드 …)와 길드 설정(/길드설정, 관리자). 규칙은 guilds.py에 있다."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import guild_war
import guilds
import theme
from access import require_admin
from guilds import GuildError

LOG = logging.getLogger("halloween")
Choice = app_commands.Choice
BUFF_CHOICES = [Choice(name=f"{name} — {text}"[:100], value=key) for key, (name, text) in guilds.BUFFS.items()]
SHOW_MAX = 30


def mention_list(users, limit: int = SHOW_MAX) -> str:
    shown = ", ".join(f"<@{u}>" for u in users[:limit])
    return shown + (f" 외 {len(users) - limit}명" if len(users) > limit else "") if users else "없음"


def guild_embed(store, guild_id: int, row, viewer: int | None = None) -> discord.Embed:
    cur = store.currency(guild_id)
    users = guilds.members(store, guild_id, row["id"])
    size = store.settings(guild_id)["guild_size"]
    embed = theme.make_embed(f"🛡️ {row['name']}", f"👑 길드장 <@{row['leader']}> · 👥 {len(users)}/{size}명", color=theme.PURPLE)
    embed.add_field(name="👥 길드원", value=mention_list(users)[:1024], inline=False)
    embed.add_field(name="✨ 길드 버프", value=guilds.buff_text(row["buff"]), inline=False)
    embed.add_field(name="🏦 금고", value=cur.money(row["vault"]), inline=True)
    embed.add_field(name="⚔️ 길드전", value=f"{row['wins']}승 {row['losses']}패", inline=True)
    if viewer is not None and viewer == row["leader"]:
        waiting = guilds.requests(store, guild_id, row["id"])
        if waiting:
            embed.add_field(name="📨 가입 신청 (`/길드 수락`·`/길드 거절`)", value=mention_list(waiting)[:1024], inline=False)
    return embed


def war_embed(store, guild_id: int, war, now=None) -> discord.Embed:
    """길드전 현황(진행 중·대기 중)."""
    a, d = war["attacker_name"], war["defender_name"]
    if war["state"] == "pending":
        return theme.make_embed("⚔️ 선전포고 대기 중", f"**{a}** → **{d}**\n{d}의 길드장이 `/길드전 수락`하면 시작해요.", color=theme.RED)
    lines = [f"**{a}** {war['attacker_score']:,}점  vs  {war['defender_score']:,}점 **{d}**", f"⏳ 남은 시간 {guild_war.time_left(war, now)}",
             f"길드원이 던전을 끝낼 때마다 점수: 오른 층 수 + 클리어 +{guild_war.CLEAR_POINTS} + 최종보스 +{guild_war.FINAL_POINTS}",
             f"끝나면 진 길드원 모두의 RAM {guild_war.LOSS_PERCENT}%가 이긴 길드원들에게 똑같이 나눠 들어가요."]
    embed = theme.make_embed("⚔️ 길드전 진행 중", "\n".join(lines), color=theme.RED)
    for gid, name in ((war["attacker"], a), (war["defender"], d)):
        top = guild_war.top_scorers(store, war["id"], gid)
        embed.add_field(name=f"🏅 {name}", value="\n".join(f"<@{u}> {p:,}점" for u, p in top) or "아직 없어요", inline=True)
    return embed


def result_embed(store, guild_id: int, result: dict) -> discord.Embed:
    war, cur = result["war"], store.currency(guild_id)
    score = f"**{war['attacker_name']}** {war['attacker_score']:,}점 vs {war['defender_score']:,}점 **{war['defender_name']}**"
    if result["winner"] is None:
        return theme.make_embed("⚔️ 길드전 끝 — 무승부", score + "\n점수가 같아서 아무것도 옮기지 않았어요.", color=theme.PURPLE)
    embed = theme.make_embed(f"⚔️ 길드전 끝 — 🏆 {result['winner']['name']} 승리!", score, color=theme.GOLD)
    embed.add_field(name=f"💸 {result['loser']['name']} (RAM {guild_war.LOSS_PERCENT}%를 잃었어요)", inline=False,
                    value=(", ".join(f"<@{u}> -{cur.money(n)}" for u, n in list(result["taken"].items())[:SHOW_MAX]) or "없음")[:1024])
    embed.add_field(name=f"💰 {result['winner']['name']} (합계 {cur.money(result['total'])}을(를) 나눠 받았어요)", inline=False,
                    value=(", ".join(f"<@{u}> +{cur.money(n)}" for u, n in list(result["given"].items())[:SHOW_MAX]) or "없음")[:1024])
    return embed


class Guild(commands.Cog):
    """🛡️ 길드와 길드전."""

    group = app_commands.Group(name="길드", description="길드를 만들고 들어가고, 금고·버프·랭킹을 봐요", guild_only=True)
    war = app_commands.Group(name="길드전", description="다른 길드에 선전포고하고, 받고, 현황을 봐요", guild_only=True)

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.settle_loop.start()

    async def cog_unload(self):
        self.settle_loop.cancel()

    @tasks.loop(seconds=60)
    async def settle_loop(self):
        """끝난 길드전을 정하고, 길드전을 받은 채널에 결과를 올린다. 한 번 실패해도 계속 돈다."""
        try:
            await self.settle_now()
        except Exception:
            LOG.exception("길드전 정산 중 오류")

    @settle_loop.before_loop
    async def before_settle(self):
        await self.bot.wait_until_ready()

    async def settle_now(self, now=None) -> list:
        done = guild_war.settle_due(self.bot.store, now)
        for guild_id, result in done:
            channel = self.bot.get_channel(result["war"]["channel"]) if result["war"]["channel"] else None
            LOG.info("길드전 %s 끝: 서버 %s, 승리 %s, 이동 %s", result["war"]["id"], guild_id,
                     result["winner"]["id"] if result["winner"] else None, result["total"])
            if channel is not None and hasattr(channel, "send"):
                try:
                    await channel.send(embed=result_embed(self.bot.store, guild_id, result),
                                       allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    LOG.warning("길드전 결과를 채널 %s에 올리지 못했어요", result["war"]["channel"])
        return done

    # ---------------------------------------------------------------- 길드전
    @war.command(name="선전포고", description="다른 길드에 선전포고해요. 상대 길드장이 수락하면 시작해요 (길드장)")
    @app_commands.rename(name="길드")
    @app_commands.describe(name="상대 길드(자동완성)")
    async def declare(self, interaction: discord.Interaction, name: str):
        store, gid = self.bot.store, interaction.guild_id
        hours = store.settings(gid)["war_hours"]
        await self._run(interaction, lambda: guild_war.declare(store, gid, interaction.user.id, name),
                        lambda w: f"⚔️ **{w['attacker_name']}**이(가) **{w['defender_name']}**에 선전포고했어요!\n"
                                  f"상대 길드장이 {hours}시간 안에 `/길드전 수락`하면 {hours}시간 동안 길드전이 열려요. "
                                  f"지면 길드원 모두의 RAM {guild_war.LOSS_PERCENT}%를 잃어요.")

    @war.command(name="수락", description="우리 길드에 온 선전포고를 받아요. 지금부터 길드전이 시작돼요 (길드장)")
    @app_commands.rename(name="길드")
    @app_commands.describe(name="선전포고한 길드(여러 곳이면 고르세요)")
    async def war_accept(self, interaction: discord.Interaction, name: str | None = None):
        store, gid = self.bot.store, interaction.guild_id
        channel = getattr(interaction.channel, "id", None)
        await self._run(interaction, lambda: guild_war.accept(store, gid, interaction.user.id, name, channel),
                        lambda w: f"🔥 **길드전 시작!** **{w['attacker_name']}** vs **{w['defender_name']}** — {guild_war.time_left(w)} 동안\n"
                                  "길드원이 던전을 끝낼 때마다 점수가 쌓여요. 결과는 이 채널에 올라와요. (`/길드전 현황`)")

    @war.command(name="거절", description="우리 길드에 온 선전포고를 거절해요 (길드장)")
    @app_commands.rename(name="길드")
    async def war_decline(self, interaction: discord.Interaction, name: str | None = None):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guild_war.decline(store, gid, interaction.user.id, name),
                        lambda w: f"🕊️ **{w['attacker_name']}**의 선전포고를 거절했어요.")

    @war.command(name="취소", description="우리가 한 선전포고를 거둬요(상대가 받기 전에만) (길드장)")
    async def war_cancel(self, interaction: discord.Interaction):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guild_war.cancel(store, gid, interaction.user.id),
                        lambda w: f"🕊️ **{w['defender_name']}**에 한 선전포고를 거뒀어요.")

    @war.command(name="현황", description="우리 길드의 길드전 현황과 최근 결과를 봐요")
    async def war_status(self, interaction: discord.Interaction):
        store, gid = self.bot.store, interaction.guild_id
        await self.settle_now()                                 # 시간이 끝났는데 아직 안 정했다면 먼저 정한다
        row = guilds.of_user(store, gid, interaction.user.id)
        if row is None:
            await self._say(interaction, "길드에 들어가 있지 않아요.", False)
            return
        current = guild_war.open_war(store, gid, row["id"])
        incoming = store.db.execute("SELECT attacker_name FROM guild_wars WHERE guild=? AND state='pending' AND defender=?", (gid, row["id"])).fetchall()
        if current is not None:
            embed = war_embed(store, gid, current)
        else:
            embed = theme.make_embed("⚔️ 길드전", "지금 하는 길드전이 없어요. 길드장이 `/길드전 선전포고`로 시작해요.", color=theme.PURPLE)
        if incoming:
            embed.add_field(name="📨 우리에게 온 선전포고 (`/길드전 수락`·`거절`)", value=", ".join(f"**{r['attacker_name']}**" for r in incoming), inline=False)
        past = guild_war.history(store, gid, row["id"])
        if past:
            embed.add_field(name="📜 최근 결과", inline=False, value="\n".join(
                f"{w['attacker_name']} {w['attacker_score']:,} : {w['defender_score']:,} {w['defender_name']}"
                + (" (무승부)" if w["winner"] is None else (" 🏆 승리" if w["winner"] == row["id"] else " 패배")) for w in past))
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @declare.autocomplete("name")
    @war_accept.autocomplete("name")
    @war_decline.autocomplete("name")
    async def war_names(self, interaction: discord.Interaction, current: str):
        return await self.guild_names(interaction, current)

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    async def _run(self, interaction, work, done):
        """work()를 하고 done(결과) 문장을 보여 준다. GuildError면 이유를 보여 준다."""
        try:
            result = work()
        except GuildError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, done(result))

    def _named(self, guild_id: int, name: str):
        row = guilds.by_name(self.bot.store, guild_id, name)
        if row is None:
            raise GuildError(f"**{name}** 길드가 없어요. 이름은 자동완성에서 골라 주세요.")
        return row

    @group.command(name="만들기", description="새 길드를 만들어요(만든 사람이 길드장). 만드는 비용이 들어요")
    @app_commands.rename(name="이름")
    @app_commands.describe(name=f"길드 이름(1~{guilds.NAME_MAX}자)")
    async def create(self, interaction: discord.Interaction, name: str):
        store, gid = self.bot.store, interaction.guild_id
        cost = store.settings(gid)["guild_cost"]
        await self._run(interaction, lambda: guilds.create(store, gid, interaction.user.id, name),
                        lambda row: f"🛡️ **{row['name']}** 길드를 만들었어요!" + (f" ({store.currency(gid).money(cost)}을(를) 냈어요)" if cost else "")
                        + "\n`/길드 버프`로 길드 버프를 고르고, 길드원은 `/길드 가입`으로 신청해요.")

    @group.command(name="정보", description="길드 정보를 봐요. 비우면 내 길드")
    @app_commands.rename(name="길드")
    @app_commands.describe(name="볼 길드(자동완성)")
    async def info(self, interaction: discord.Interaction, name: str | None = None):
        store, gid, uid = self.bot.store, interaction.guild_id, interaction.user.id
        try:
            row = self._named(gid, name) if name else guilds.of_user(store, gid, uid)
        except GuildError as exc:
            await self._say(interaction, str(exc), False)
            return
        if row is None:
            await self._say(interaction, "길드에 들어가 있지 않아요. `/길드 랭킹`에서 길드를 찾아 `/길드 가입`하거나 `/길드 만들기`로 만들어요.", False)
            return
        await interaction.response.send_message(embed=guild_embed(store, gid, row, uid), ephemeral=False)

    @group.command(name="랭킹", description="길드 순위(길드전 승리 → 길드원 재화 합계 순)")
    async def ranking(self, interaction: discord.Interaction):
        store, gid = self.bot.store, interaction.guild_id
        cur = store.currency(gid)
        rows = guilds.ranking(store, gid)
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = [f"{medals.get(i, f'{i + 1}.')} **{e['row']['name']}** — {e['row']['wins']}승 {e['row']['losses']}패 · 👥 {e['members']}명 · "
                 f"길드원 {cur.money(e['wealth'])}" for i, e in enumerate(rows[:15])]
        await interaction.response.send_message(embed=theme.make_embed("🏆 길드 랭킹", "\n".join(lines) or "아직 길드가 없어요. `/길드 만들기`로 만들어요.",
                                                                       color=theme.GOLD), ephemeral=False)

    @group.command(name="가입", description="길드에 가입을 신청해요. 길드장이 수락하면 들어가요")
    @app_commands.rename(name="길드")
    @app_commands.describe(name="신청할 길드(자동완성)")
    async def join(self, interaction: discord.Interaction, name: str):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guilds.apply(store, gid, interaction.user.id, self._named(gid, name)["id"]),
                        lambda row: f"📨 **{row['name']}** 길드에 가입을 신청했어요. 길드장 <@{row['leader']}>의 수락을 기다려 주세요.")

    @group.command(name="수락", description="가입 신청을 받아 줘요 (길드장)")
    @app_commands.rename(member="사람")
    async def accept(self, interaction: discord.Interaction, member: discord.Member):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guilds.accept(store, gid, interaction.user.id, member.id),
                        lambda row: f"🎉 {member.mention}님이 **{row['name']}** 길드에 들어왔어요!")

    @group.command(name="거절", description="가입 신청을 거절해요 (길드장)")
    @app_commands.rename(member="사람")
    async def decline(self, interaction: discord.Interaction, member: discord.Member):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guilds.decline(store, gid, interaction.user.id, member.id),
                        lambda row: f"✖️ {member.mention}님의 가입 신청을 거절했어요.")

    @group.command(name="탈퇴", description="길드에서 나가요. 혼자 남은 길드장이 나가면 길드가 해체돼요")
    async def leave(self, interaction: discord.Interaction):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guilds.leave(store, gid, interaction.user.id),
                        lambda result: f"🚪 **{result[0]['name']}** 길드를 " + ("해체했어요." if result[1] else "나왔어요."))

    @group.command(name="추방", description="길드원을 내보내요 (길드장)")
    @app_commands.rename(member="사람")
    async def kick(self, interaction: discord.Interaction, member: discord.Member):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guilds.kick(store, gid, interaction.user.id, member.id),
                        lambda row: f"🚪 {member.mention}님을 **{row['name']}** 길드에서 내보냈어요.")

    @group.command(name="위임", description="길드장을 다른 길드원에게 넘겨요 (길드장)")
    @app_commands.rename(member="사람")
    async def transfer(self, interaction: discord.Interaction, member: discord.Member):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guilds.transfer(store, gid, interaction.user.id, member.id),
                        lambda row: f"👑 이제 {member.mention}님이 **{row['name']}** 길드장이에요.")

    @group.command(name="해체", description="길드를 없애요. 금고는 길드원에게 똑같이 나눠요 (길드장)")
    @app_commands.rename(sure="확인")
    @app_commands.describe(sure="정말 해체하려면 True")
    async def disband(self, interaction: discord.Interaction, sure: bool):
        store, gid = self.bot.store, interaction.guild_id
        if not sure:
            await self._say(interaction, "해체하지 않았어요. 정말 해체하려면 `확인:True`로 다시 써 주세요.", False)
            return
        row = guilds.of_user(store, gid, interaction.user.id)
        cur = store.currency(gid)
        await self._run(interaction, lambda: guilds.disband(store, gid, interaction.user.id),
                        lambda paid: f"💥 **{row['name']}** 길드를 해체했어요." + (
                            " 금고를 나눴어요: " + ", ".join(f"<@{u}> {cur.money(n)}" for u, n in paid.items() if n) if any(paid.values()) else ""))

    @group.command(name="입금", description="내 재화를 길드 금고에 넣어요")
    @app_commands.rename(amount="금액")
    async def deposit(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 10 ** 10]):
        store, gid = self.bot.store, interaction.guild_id
        cur = store.currency(gid)
        await self._run(interaction, lambda: guilds.deposit(store, gid, interaction.user.id, amount),
                        lambda vault: f"🏦 금고에 {cur.money(amount)}을(를) 넣었어요. (금고 {cur.money(vault)})")

    @group.command(name="지급", description="길드 금고에서 길드원에게 재화를 줘요 (길드장)")
    @app_commands.rename(member="사람", amount="금액")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 10 ** 10]):
        store, gid = self.bot.store, interaction.guild_id
        cur = store.currency(gid)
        await self._run(interaction, lambda: guilds.pay(store, gid, interaction.user.id, member.id, amount),
                        lambda vault: f"🏦 {member.mention}님에게 금고에서 {cur.money(amount)}을(를) 줬어요. (금고 {cur.money(vault)})")

    @group.command(name="버프", description="길드 전체에 적용할 버프를 골라요 (길드장)")
    @app_commands.rename(kind="종류")
    @app_commands.choices(kind=BUFF_CHOICES)
    async def buff(self, interaction: discord.Interaction, kind: Choice[str]):
        store, gid = self.bot.store, interaction.guild_id
        await self._run(interaction, lambda: guilds.set_buff(store, gid, interaction.user.id, kind.value),
                        lambda row: f"✨ **{row['name']}** 길드 버프: {guilds.buff_text(row['buff'])}\n다음에 던전에 들어갈 때부터 적용돼요.")

    @info.autocomplete("name")
    @join.autocomplete("name")
    async def guild_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        return [Choice(name=row["name"], value=row["name"]) for row in guilds.all_guilds(self.bot.store, interaction.guild_id)
                if needle in row["name"].lower()][:25]

    @app_commands.command(name="길드설정", description="길드를 만드는 비용·인원 한도·길드전 기간을 정해요. 비우면 지금 설정 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(cost="생성비용", size="인원", hours="길드전시간")
    @app_commands.describe(cost="길드를 만들 때 내는 재화(0이면 무료)", size="길드 인원 한도(길드장 포함, 2~100)",
                           hours="길드전 기간(시간, 1~168). 선전포고도 이 시간 안에 받아야 해요")
    async def settings(self, interaction: discord.Interaction, cost: app_commands.Range[int, 0, 1_000_000_000] | None = None,
                       size: app_commands.Range[int, 2, 100] | None = None, hours: app_commands.Range[int, 1, 168] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        changes = {k: v for k, v in dict(guild_cost=cost, guild_size=size, war_hours=hours).items() if v is not None}
        if changes:
            store.update_settings(gid, **changes)
        cfg, cur = store.settings(gid), store.currency(gid)
        await self._say(interaction, ("🛡️ 길드 설정을 바꿨어요.\n" if changes else "🛡️ 지금 길드 설정\n")
                        + f"만드는 비용 {cur.money(cfg['guild_cost'])} · 인원 한도 {cfg['guild_size']}명 · 길드전 {cfg['war_hours']}시간"
                        + ("\n(인원 한도를 낮춰도 이미 들어와 있는 길드원은 그대로예요)" if "guild_size" in changes else ""))


async def setup(bot):
    await bot.add_cog(Guild(bot))
