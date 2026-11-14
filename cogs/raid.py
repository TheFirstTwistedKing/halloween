"""레이드 화면과 관리자 명령어(/레이드 열기·시작·취소). 규칙은 raid.py에 있다.

- 레이드 화면은 채널에 하나 올라가는 공용 메시지다. 버튼 ID가 고정이라 봇을 껐다 켜도 계속 동작한다(main.setup_hook에서 add_view).
- 버튼을 누르면 화면을 바로 고치고(너무 자주면 3초마다 모아서), 안 되는 행동(쿨타임·다운 등)만 누른 사람에게 따로 알린다.
- 3초마다 보스 공격·끝났는지를 처리하고 화면을 고친다.
"""
from __future__ import annotations

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import raid
import theme
from access import require_admin
from raid import RaidError
from store import valid_emoji

LOG = logging.getLogger("halloween")
Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
SKILL_CHOICES = [Choice(name=f"{name} — {meaning}", value=key) for key, (name, meaning, _r, _t) in raid.BOSS_SKILLS.items()]
EDIT_GAP = 2.5                   # 버튼을 눌러 화면을 바로 고치는 최소 간격(초). 더 잦으면 다음 정리 때 고친다
BUTTONS = (("join", "참가", "🙋", discord.ButtonStyle.success), ("attack", "공격", "⚔️", discord.ButtonStyle.danger),
           ("defend", "방어", "🛡️", discord.ButtonStyle.primary), ("skill", "스킬", "✨", discord.ButtonStyle.secondary))


def bar(value: int, maximum: int, width: int = 12) -> str:
    filled = 0 if maximum <= 0 else max(0, min(width, round(value * width / maximum)))
    return "🟥" * filled + "⬛" * (width - filled)


def raid_embed(store, row, now=None) -> discord.Embed:
    data = raid.boss(row)
    everyone = raid.members(store, row["id"])
    alive = sum(1 for m in everyone if not m["down"])
    cur = store.currency(row["guild"])
    head = {"open": "🙋 모집 중 — [참가]를 누르고 관리자의 `/레이드 시작`을 기다려요", "active": f"⏳ 남은 시간 {raid.time_left(row, now)}",
            "won": "🏆 레이드 성공!", "lost": "💀 레이드 실패", "cancelled": "🚫 취소된 레이드"}[row["state"]]
    color = {"open": theme.ORANGE, "active": theme.RED, "won": theme.GOLD, "lost": theme.PURPLE, "cancelled": theme.PURPLE}[row["state"]]
    phase = f" [{data['phase']}/2페이즈]" if data["phase2_hp"] or data["phase"] == 2 else ""
    lines = [head, f"{data['emoji']} **{data['name']}**{phase}", f"{bar(data['hp'], data['max_hp'])} {data['hp']:,}/{data['max_hp']:,}",
             f"⚔️ 공격 {data['damage']:,} · {data['attack_every']}초마다 · 행동 쿨타임 {data['cooldown']}초"]
    skill = raid.skill_line(data)
    if skill:
        lines.append(f"🌀 스킬 {skill}")
    if data["barrier_turns"]:
        lines.append(f"🛡️ 보스 방어막: 받는 피해 {data['barrier']}% 감소 (남은 {data['barrier_turns']}번)")
    if data["burn_turns"]:
        lines.append(f"🔥 과열: 보스가 공격할 때마다 모두 {data['burn']:,} 피해 (남은 {data['burn_turns']}번)")
    embed = theme.make_embed("🐉 레이드", "\n".join(lines), color=color)
    embed.add_field(name=f"👥 참가 {len(everyone)}/{raid.MAX_MEMBERS} (살아 있음 {alive})", inline=False,
                    value=(", ".join(f"<@{m['user']}>" + ("💀" if m["down"] else f" {m['hp']:,}") for m in everyone[:30])
                           + (f" 외 {len(everyone) - 30}명" if len(everyone) > 30 else "") or "아직 없어요")[:1024])
    top = sorted((m for m in everyone if m["damage"]), key=lambda m: -m["damage"])[:5]
    if top:
        embed.add_field(name="🏅 준 피해", inline=True, value="\n".join(f"<@{m['user']}> {m['damage']:,}" for m in top))
    reward = f"{cur.money(data['reward'])}을(를) 준 피해 비율대로" + (f" + 참가 보상 {cur.money(data['min_reward'])}" if data["min_reward"] else "")
    embed.add_field(name="🎁 승리 보상", inline=True, value=reward if data["reward"] or data["min_reward"] else "없음")
    if row["state"] == "won" and data.get("payout"):
        paid = sorted(data["payout"].items(), key=lambda p: -p[1])[:15]
        embed.add_field(name="💰 받은 보상", inline=False, value=", ".join(f"<@{u}> +{cur.money(n)}" for u, n in paid)[:1024] or "없음")
    if data.get("log"):
        embed.add_field(name="📜 기록", inline=False, value="\n".join(data["log"])[-1024:])
    embed.set_footer(text="기지에 전시한 장비(무기·방패·갑옷)로 싸워요. 장비는 잃지 않아요.")
    return embed


class RaidView(discord.ui.View):
    """레이드 화면 버튼(누구나 누른다). 버튼 ID가 고정이라 봇이 다시 켜져도 동작한다."""

    def __init__(self, cog: "Raid | None" = None, ended: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        for action, label, emoji, style in BUTTONS:
            button = discord.ui.Button(custom_id=f"raid:{action}", label=label, emoji=emoji, style=style, disabled=ended)
            button.callback = self._callback(action)
            self.add_item(button)

    def _callback(self, action: str):
        async def callback(interaction: discord.Interaction):
            cog = self.cog or interaction.client.get_cog("Raid")
            await cog.press(interaction, action)
        return callback


class Raid(commands.Cog):
    """🐉 레이드."""

    group = app_commands.Group(name="레이드", description="레이드를 열고 시작하고 취소해요 (관리자)", **PERMS)

    def __init__(self, bot, rng=None):
        self.bot = bot
        self.rng = rng or random.Random()
        self.last_edit: dict[int, float] = {}

    async def cog_load(self):
        self.bot.add_view(RaidView(self))
        self.refresh.start()

    async def cog_unload(self):
        self.refresh.cancel()

    # ---------------------------------------------------------------- 버튼
    async def press(self, interaction: discord.Interaction, action: str):
        store, gid = self.bot.store, interaction.guild_id
        row = raid.by_message(store, interaction.message.id) if interaction.message else None
        if row is None or row["state"] not in raid.OPEN:
            await self._private(interaction, "이 레이드는 끝났어요.")
            return
        try:
            if action == "join":
                raid.join(store, gid, interaction.user.id)
            else:
                raid.act(store, gid, interaction.user.id, action, self.rng)
        except RaidError as exc:
            await self._private(interaction, str(exc))
            return
        row = raid.get(store, row["id"])
        now = time.time()
        if now - self.last_edit.get(row["id"], 0) >= EDIT_GAP or row["state"] not in raid.OPEN:
            self.last_edit[row["id"]] = now
            await interaction.response.edit_message(embed=raid_embed(store, row), view=RaidView(self, ended=row["state"] not in raid.OPEN))
            raid.mark_clean(store, row["id"])
        else:
            await interaction.response.defer()                # 화면은 다음 정리 때(3초 안에) 고친다

    async def _private(self, interaction, text: str):
        """레이드 화면 안에서 안 되는 행동은 누른 사람에게만 알린다(공용 화면을 어지럽히지 않게)."""
        try:
            await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=True)
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------------- 정리(3초마다)
    @tasks.loop(seconds=3)
    async def refresh(self):
        try:
            await self.refresh_now()
        except Exception:
            LOG.exception("레이드 정리 중 오류")

    @refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    async def refresh_now(self, now=None) -> list[int]:
        """보스 공격·끝났는지를 처리하고, 바뀐 화면을 고친다. 고친 레이드 id 목록."""
        store, edited = self.bot.store, []
        for row in raid.running(store):
            raid.tick(store, row["guild"], self.rng, now)
            fresh = raid.get(store, row["id"])
            if not raid.boss(fresh).get("dirty") or not fresh["message"]:
                continue
            channel = self.bot.get_channel(fresh["channel"])
            if channel is None or not hasattr(channel, "get_partial_message"):
                continue
            try:
                await channel.get_partial_message(fresh["message"]).edit(embed=raid_embed(store, fresh, now),
                                                                         view=RaidView(self, ended=fresh["state"] not in raid.OPEN))
            except discord.HTTPException:
                LOG.warning("레이드 %s 화면을 고치지 못했어요", fresh["id"])
                continue
            raid.mark_clean(store, fresh["id"])
            self.last_edit[fresh["id"]] = time.time()
            edited.append(fresh["id"])
        return edited

    # ---------------------------------------------------------------- 관리자
    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    @group.command(name="열기", description="레이드를 열어요. 이 채널에 레이드 화면이 올라가고 [참가]로 모여요 (관리자)")
    @app_commands.rename(name="이름", hp="체력", damage="공격", emoji="이모지", minutes="제한시간", reward="보상", min_reward="참가보상",
                         attack_every="공격간격", cooldown="쿨타임", phase2_hp="2페이즈체력", phase2_damage="2페이즈공격", skill="스킬",
                         skill_every="스킬주기", skill_power="스킬세기", skill_turns="스킬턴", skill_name="스킬이름")
    @app_commands.describe(name="레이드 보스 이름", hp="체력(최대 10억)", damage="한 번 공격의 피해", emoji="보스 이모지(기본 🐉)",
                           minutes="싸우는 시간(분, 1~180, 기본 10)", reward="이기면 준 피해 비율대로 나눌 재화 총액",
                           min_reward="이기면 참가자 모두가 더 받는 재화", attack_every="보스가 공격하는 간격(초, 3~120, 기본 8)",
                           cooldown="참가자 행동 쿨타임(초, 1~60, 기본 3)", phase2_hp="2페이즈 체력(0이면 없음)", phase2_damage="2페이즈 공격(0이면 그대로)",
                           skill="보스 스킬(보스 공격 N번마다)", skill_every="몇 번째 공격마다 스킬(1~20)",
                           skill_power="스킬 세기: 강타 공격의 %, 자가 수리 최대 체력의 %, 방어막 감소 %, 과열 피해", skill_turns="방어막·과열이 이어지는 보스 공격 수",
                           skill_name="스킬의 보이는 이름(비우면 기본 이름)")
    @app_commands.choices(skill=SKILL_CHOICES)
    async def open(self, interaction: discord.Interaction, name: str, hp: app_commands.Range[int, 1, raid.HP_MAX],
                   damage: app_commands.Range[int, 0, 10_000_000], emoji: str = "🐉", minutes: app_commands.Range[int, 1, 180] = 10,
                   reward: app_commands.Range[int, 0, raid.REWARD_MAX] = 0, min_reward: app_commands.Range[int, 0, raid.REWARD_MAX] = 0,
                   attack_every: app_commands.Range[int, 3, 120] = 8, cooldown: app_commands.Range[int, 1, 60] = 3,
                   phase2_hp: app_commands.Range[int, 0, raid.HP_MAX] = 0, phase2_damage: app_commands.Range[int, 0, 10_000_000] = 0,
                   skill: Choice[str] | None = None, skill_every: app_commands.Range[int, 1, 20] = 3,
                   skill_power: app_commands.Range[int, 1, 100000] = 150, skill_turns: app_commands.Range[int, 0, 20] = 0,
                   skill_name: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        emoji = emoji.strip()
        if not valid_emoji(emoji):
            await self._say(interaction, "이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요.", False)
            return
        try:
            data = raid.check_boss(name=name, emoji=emoji, hp=hp, damage=damage, phase2_hp=phase2_hp, phase2_damage=phase2_damage,
                                   skill=skill.value if skill else None, skill_every=skill_every if skill else 0,
                                   skill_power=skill_power if skill else 0, skill_turns=skill_turns if skill else 0, skill_name=skill_name)
            raid_id = raid.open_raid(store, gid, getattr(interaction.channel, "id", None), data, minutes=minutes, reward=reward,
                                     min_reward=min_reward, attack_every=attack_every, cooldown=cooldown)
        except RaidError as exc:
            await self._say(interaction, str(exc), False)
            return
        row = raid.get(store, raid_id)
        await interaction.response.send_message(embed=raid_embed(store, row), view=RaidView(self), ephemeral=False)
        try:
            message = await interaction.original_response()
            raid.set_message(store, raid_id, message.id)
        except discord.HTTPException:
            LOG.warning("레이드 %s 화면 메시지를 찾지 못했어요", raid_id)
        raid.mark_clean(store, raid_id)

    @group.command(name="시작", description="모집 중인 레이드를 시작해요. 제한 시간이 흐르기 시작해요 (관리자)")
    async def begin(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        try:
            row = raid.start(self.bot.store, interaction.guild_id)
        except RaidError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"🔥 레이드를 시작했어요! {raid.boss(row)['minutes']}분 동안 싸워요. 레이드 화면의 버튼을 눌러 주세요.")
        await self.refresh_now()

    @group.command(name="취소", description="모집 중이거나 진행 중인 레이드를 취소해요. 보상은 없어요 (관리자)")
    async def cancel(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        try:
            raid.cancel(self.bot.store, interaction.guild_id)
        except RaidError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, "🚫 레이드를 취소했어요.")
        await self.refresh_now()


async def setup(bot):
    await bot.add_cog(Raid(bot))
