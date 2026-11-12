"""보스 패턴(/보스패턴): 보스·시크릿 보스의 2페이즈(체력 바 2개)와 스킬을 정한다.

- 2페이즈: 1페이즈 체력 바를 다 깎으면 정한 체력으로 다시 일어나 2페이즈가 된다(공격도 바꿀 수 있다). 그 턴에는 공격하지 않는다.
- 스킬: N번째 공격마다 일반 공격 대신 스킬을 쓴다 — 강타(세게 때림)·자가 수리(회복)·방어막(받는 피해 감소)·과열(턴마다 피해).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import balance
import content
import theme
from access import require_admin
from balance import BOSS_SKILLS, PHASE2_HP_MAX, SKILL_EVERY_MAX, SKILL_TURNS_MAX, skill_text

Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
SECTIONS = ("bosses", "secrets")
SKILL_CHOICES = [Choice(name=f"{name} — {meaning}", value=key) for key, (name, meaning, _r, _t) in BOSS_SKILLS.items()] + \
    [Choice(name="없음 (스킬 지우기)", value="none")]


def pattern_text(data: dict) -> str:
    """보스의 2페이즈·스킬 설명."""
    lines = []
    if data.get("phase2_hp"):
        lines.append(f"🔴 2페이즈: 1페이즈 체력 바(HP {data['hp']:,})를 다 깎으면 **HP {data['phase2_hp']:,}**로 다시 일어나요"
                     + (f" · 공격 {data['damage']:,} → **{data['phase2_damage']:,}**" if data.get("phase2_damage") else " · 공격은 그대로"))
    else:
        lines.append("🔴 2페이즈: 없음")
    skill = skill_text(data.get("skill"), data.get("skill_every", 0), data.get("skill_power", 0), data.get("skill_turns", 0))
    lines.append(f"🌀 스킬: {skill}" if skill else "🌀 스킬: 없음")
    lines.append("(체력·공격·과열 피해는 층이 오를수록 몬스터처럼 강해져요)")
    return "\n".join(lines)


class BossPatterns(commands.Cog):
    """🔴 보스 2페이즈·스킬 (관리자)."""

    group = app_commands.Group(name="보스패턴", description="보스·시크릿 보스의 2페이즈(체력 바 2개)와 스킬을 정해요 (관리자)", **PERMS)

    def __init__(self, bot):
        self.bot = bot

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    def _find(self, guild_id: int, target: str) -> tuple[str, dict]:
        hit = content.find(self.bot.store, guild_id, SECTIONS, target)
        if hit is None:
            raise content.ContentError("그런 보스가 없어요. 보스·시크릿 보스 이름은 자동완성에서 골라 주세요.")
        return hit

    async def _apply(self, interaction, target: str, changes: dict, head: str):
        if not await require_admin(interaction):
            return
        try:
            section, data = self._find(interaction.guild_id, target)
            content.edit(self.bot.store, interaction.guild_id, (section,), data["name"], changes)
            fresh = next(d for d in content.current(self.bot.store, interaction.guild_id, section) if d["key"] == data["key"])
        except content.ContentError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"{head} {fresh['emoji']} **{fresh['name']}**\n{pattern_text(fresh)}\n지금 시작하는 싸움부터 적용돼요.")

    @group.command(name="페이즈", description="보스의 2페이즈 체력(0이면 없음)과 2페이즈 공격을 정해요 (관리자)")
    @app_commands.rename(target="대상", hp="체력", damage="공격")
    @app_commands.describe(target="보스·시크릿 보스(자동완성)", hp=f"2페이즈 체력 바(1층 기준). 0이면 2페이즈 없음(0~{PHASE2_HP_MAX:,})",
                           damage="2페이즈의 공격(1층 기준). 비우거나 0이면 1페이즈와 같아요")
    async def phase(self, interaction: discord.Interaction, target: str, hp: app_commands.Range[int, 0, PHASE2_HP_MAX],
                    damage: app_commands.Range[int, 0, 100000] = 0):
        await self._apply(interaction, target, {"phase2_hp": hp, "phase2_damage": damage if hp else 0},
                          "🔴 2페이즈를 정했어요." if hp else "🔴 2페이즈를 없앴어요.")

    @group.command(name="스킬", description="보스가 N번째 공격마다 쓰는 스킬을 정해요. 종류를 '없음'으로 하면 지워요 (관리자)")
    @app_commands.rename(target="대상", kind="종류", every="주기", power="세기", turns="턴")
    @app_commands.describe(target="보스·시크릿 보스(자동완성)", kind="스킬 종류", every=f"몇 번째 공격마다 쓸까요(1~{SKILL_EVERY_MAX}). 3이면 3·6·9번째",
                           power="세기: 강타는 공격의 %(100~1000), 자가 수리는 최대 체력의 %(1~100), 방어막은 피해 감소 %(1~100), 과열은 턴마다 피해",
                           turns=f"방어막·과열이 이어지는 턴(1~{SKILL_TURNS_MAX})")
    @app_commands.choices(kind=SKILL_CHOICES)
    async def skill(self, interaction: discord.Interaction, target: str, kind: Choice[str],
                    every: app_commands.Range[int, 1, SKILL_EVERY_MAX] = 3, power: app_commands.Range[int, 1, 100000] = 150,
                    turns: app_commands.Range[int, 0, SKILL_TURNS_MAX] = 0):
        if kind.value == "none":
            await self._apply(interaction, target, {"skill": None}, "🌀 스킬을 지웠어요.")
            return
        await self._apply(interaction, target, {"skill": kind.value, "skill_every": every, "skill_power": power, "skill_turns": turns},
                          "🌀 스킬을 정했어요.")

    @group.command(name="보기", description="보스의 2페이즈·스킬을 봐요 (관리자)")
    @app_commands.rename(target="대상")
    async def show(self, interaction: discord.Interaction, target: str):
        if not await require_admin(interaction):
            return
        try:
            _section, data = self._find(interaction.guild_id, target)
        except content.ContentError as exc:
            await self._say(interaction, str(exc), False)
            return
        await interaction.response.send_message(embed=theme.make_embed(f"🔴 {data['emoji']} {data['name']}의 패턴", pattern_text(data), color=theme.PURPLE),
                                                ephemeral=False)

    @phase.autocomplete("target")
    @skill.autocomplete("target")
    @show.autocomplete("target")
    async def boss_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        out = []
        for section in SECTIONS:
            for d in content.current(self.bot.store, interaction.guild_id, section):
                if needle in d["name"].lower():
                    tag = "시크릿 보스" if section == "secrets" else "보스"
                    mark = (" · 2페이즈" if d.get("phase2_hp") else "") + (f" · {balance.BOSS_SKILLS[d['skill']][0]}" if d.get("skill") else "")
                    out.append(Choice(name=f"{d['emoji']} {d['name']} ({tag}{mark})"[:100], value=d["name"]))
        return out[:25]


async def setup(bot):
    await bot.add_cog(BossPatterns(bot))
