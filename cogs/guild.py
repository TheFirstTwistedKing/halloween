"""길드(/길드 …)와 길드 설정(/길드설정, 관리자). 규칙은 guilds.py에 있다."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import guilds
import theme
from access import require_admin
from guilds import GuildError

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


class Guild(commands.Cog):
    """🛡️ 길드."""

    group = app_commands.Group(name="길드", description="길드를 만들고 들어가고, 금고·버프·랭킹을 봐요", guild_only=True)

    def __init__(self, bot):
        self.bot = bot

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

    @app_commands.command(name="길드설정", description="길드를 만드는 비용과 길드 인원 한도를 정해요. 비우면 지금 설정 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(cost="생성비용", size="인원")
    @app_commands.describe(cost="길드를 만들 때 내는 재화(0이면 무료)", size="길드 인원 한도(길드장 포함, 2~100)")
    async def settings(self, interaction: discord.Interaction, cost: app_commands.Range[int, 0, 1_000_000_000] | None = None,
                       size: app_commands.Range[int, 2, 100] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        changes = {k: v for k, v in dict(guild_cost=cost, guild_size=size).items() if v is not None}
        if changes:
            store.update_settings(gid, **changes)
        cfg, cur = store.settings(gid), store.currency(gid)
        await self._say(interaction, ("🛡️ 길드 설정을 바꿨어요.\n" if changes else "🛡️ 지금 길드 설정\n")
                        + f"만드는 비용 {cur.money(cfg['guild_cost'])} · 인원 한도 {cfg['guild_size']}명"
                        + ("\n(인원 한도를 낮춰도 이미 들어와 있는 길드원은 그대로예요)" if "guild_size" in changes else ""))


async def setup(bot):
    await bot.add_cog(Guild(bot))
