"""무기 스킬·궁극기(/무기스킬)와 각성 설정(/각성설정).

- 스킬: 쓰면 정한 턴(쿨타임) 동안 다시 못 쓴다.
- 궁극기: 싸우는 중 공격·방어 한 번마다 게이지가 차고(기본 20%), 100%가 되면 쓸 수 있다. 쓰면 게이지는 0으로.
- 각성: 궁극기를 쓸 때마다 각성 게이지가 차고(기본 10%), 100%가 되면 각성해서 특수 스킬을 쓸 수 있다. 특수 스킬을 쓰면 각성이 끝난다(설정으로 바꿀 수 있다).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import content
import theme
from access import require_admin
from balance import AWAKENING_DEFAULT, RESET_NAME, SKILL_COOLDOWN_MAX, SKILL_NAME_MAX, WEAPON_SKILLS, name_change, weapon_skill_text

Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
KIND_CHOICES = [Choice(name=f"{name} — {meaning}"[:100], value=key) for key, (name, meaning, _r) in WEAPON_SKILLS.items()]
CLEAR = Choice(name="없음 (지우기)", value="none")
NAME_HELP = f"보이는 이름(1~{SKILL_NAME_MAX}자). 비우면 그대로, '{RESET_NAME}'이면 종류의 기본 이름"
POWER_HELP = "세기: 강타·기절탄·관통은 공격 피해의 %, 연타는 때리는 횟수(2~10), 흡혈은 준 피해의 몇 %를 회복할지"


def awakening_text(bal) -> str:
    return (f"🟡 궁극기 게이지: 싸우는 중 공격·방어 한 번마다 **+{bal.ult_charge}%** (100%면 궁극기)\n"
            f"⚡ 각성 게이지: 궁극기를 쓸 때마다 **+{bal.awaken_charge}%** (100%면 각성)\n"
            f"⚡ 특수 스킬: **{weapon_skill_text(bal.special, bal.special_power, bal.special_name)}**\n"
            f"⚡ 특수 스킬을 쓰면 각성이 {'**끝나요**' if bal.special_ends else '**이어져요**(각성 중이면 계속 쓸 수 있어요)'}\n"
            f"⚡ 각성 중 피해 보너스: **+{bal.awaken_bonus}%**")


class WeaponSkills(commands.Cog):
    """✨ 무기 스킬·궁극기·각성 (관리자)."""

    group = app_commands.Group(name="무기스킬", description="무기의 스킬(쿨타임)과 궁극기(게이지)를 정해요 (관리자)", **PERMS)

    def __init__(self, bot):
        self.bot = bot

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    def _weapon(self, guild_id: int, name: str) -> dict:
        hit = content.find(self.bot.store, guild_id, ("gear",), name)
        if hit is None or hit[1]["slot"] != "weapon":
            raise content.ContentError("그런 무기가 없어요. 무기 이름은 자동완성에서 골라 주세요(방패·갑옷에는 스킬이 없어요).")
        return hit[1]

    async def _edit(self, interaction, weapon: str, changes: dict, head: str, name: str | None = None, name_key: str | None = None):
        if not await require_admin(interaction):
            return
        change, value, problem = name_change(name)
        if problem:
            await self._say(interaction, problem, False)
            return
        if change and name_key:
            changes = dict(changes, **{name_key: value})
        store, gid = self.bot.store, interaction.guild_id
        try:
            data = self._weapon(gid, weapon)
            content.edit(store, gid, ("gear",), data["name"], changes)
        except content.ContentError as exc:
            await self._say(interaction, str(exc), False)
            return
        gear = store.balance(gid).gear_by_key[data["key"]]
        await self._say(interaction, f"{head} {gear.emoji} **{gear.name}** — {gear.description}\n다음 행동부터 바로 적용돼요.")

    @group.command(name="스킬", description="무기 스킬을 정해요. 쓰면 쿨타임 동안 다시 못 써요. 종류를 '없음'으로 하면 지워요 (관리자)")
    @app_commands.rename(weapon="무기", kind="종류", power="세기", cooldown="쿨타임", name="이름")
    @app_commands.describe(weapon="스킬을 붙일 무기(자동완성)", kind="스킬 종류", power=POWER_HELP, cooldown=f"다시 쓸 수 있을 때까지 쉬는 턴(1~{SKILL_COOLDOWN_MAX}, 기본 3)",
                           name=NAME_HELP)
    @app_commands.choices(kind=KIND_CHOICES + [CLEAR])
    async def skill(self, interaction: discord.Interaction, weapon: str, kind: Choice[str], power: app_commands.Range[int, 1, 2000] = 200,
                    cooldown: app_commands.Range[int, 1, SKILL_COOLDOWN_MAX] = 3, name: str | None = None):
        if kind.value == "none":
            await self._edit(interaction, weapon, {"skill": None, "skill_power": 0, "skill_cooldown": 0, "skill_name": None}, "✨ 스킬을 지웠어요.")
            return
        await self._edit(interaction, weapon, {"skill": kind.value, "skill_power": power, "skill_cooldown": cooldown}, "✨ 스킬을 정했어요.",
                         name, "skill_name")

    @group.command(name="궁극기", description="무기 궁극기를 정해요. 게이지가 100%가 되면 쓸 수 있어요. '없음'이면 지워요 (관리자)")
    @app_commands.rename(weapon="무기", kind="종류", power="세기", name="이름")
    @app_commands.describe(weapon="궁극기를 붙일 무기(자동완성)", kind="궁극기 종류", power=POWER_HELP, name=NAME_HELP)
    @app_commands.choices(kind=KIND_CHOICES + [CLEAR])
    async def ult(self, interaction: discord.Interaction, weapon: str, kind: Choice[str], power: app_commands.Range[int, 1, 2000] = 400,
                  name: str | None = None):
        if kind.value == "none":
            await self._edit(interaction, weapon, {"ult": None, "ult_power": 0, "ult_name": None}, "🟡 궁극기를 지웠어요.")
            return
        await self._edit(interaction, weapon, {"ult": kind.value, "ult_power": power}, "🟡 궁극기를 정했어요.", name, "ult_name")

    @group.command(name="이름", description="무기 스킬·궁극기의 이름만 바꿔요(세기·쿨타임은 그대로). '기본'이면 원래 이름 (관리자)")
    @app_commands.rename(weapon="무기", which="기술", name="이름")
    @app_commands.describe(weapon="무기(자동완성)", which="이름을 바꿀 기술", name=NAME_HELP)
    @app_commands.choices(which=[Choice(name="스킬", value="skill"), Choice(name="궁극기", value="ult")])
    async def rename(self, interaction: discord.Interaction, weapon: str, which: Choice[str], name: str):
        if not await require_admin(interaction):
            return
        try:
            data = self._weapon(interaction.guild_id, weapon)
        except content.ContentError as exc:
            await self._say(interaction, str(exc), False)
            return
        if not data.get(which.value):
            await self._say(interaction, f"이 무기에는 {which.name}이(가) 없어요. 먼저 `/무기스킬 {which.name}`로 붙여 주세요.", False)
            return
        await self._edit(interaction, weapon, {}, f"🏷️ {which.name} 이름을 바꿨어요.", name, f"{which.value}_name")

    @group.command(name="보기", description="무기들의 스킬·궁극기와 각성 설정을 봐요 (관리자)")
    async def show(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        bal = self.bot.store.balance(interaction.guild_id)
        lines = [f"{g.emoji} **{g.name}** — " + " · ".join(part for part in (
            f"스킬 {weapon_skill_text(g.skill, g.skill_power, g.skill_name)}(쿨 {g.skill_cooldown}턴)" if g.skill else "",
            f"궁극기 {weapon_skill_text(g.ult, g.ult_power, g.ult_name)}" if g.ult else "") if part)
            for g in bal.gear if g.slot == "weapon" and (g.skill or g.ult)]
        body = "\n".join(lines) or "스킬·궁극기가 있는 무기가 없어요. `/무기스킬 스킬`·`/무기스킬 궁극기`로 붙여 보세요."
        embed = theme.make_embed("✨ 무기 스킬·궁극기", body[:3900], color=theme.PURPLE)
        embed.add_field(name="⚡ 각성 설정 (/각성설정)", value=awakening_text(bal), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @skill.autocomplete("weapon")
    @ult.autocomplete("weapon")
    @rename.autocomplete("weapon")
    async def weapon_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        return [Choice(name=f"{d['emoji']} {d['name']}"[:100], value=d["name"]) for d in content.current(self.bot.store, interaction.guild_id, "gear")
                if d["slot"] == "weapon" and needle in d["name"].lower()][:25]

    @app_commands.command(name="각성설정", description="궁극기·각성 게이지가 차는 양, 각성 특수 스킬, 쓴 뒤 각성이 끝나는지를 정해요. 비우면 지금 설정 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(awaken_charge="각성충전", ult_charge="궁극기충전", special="특수스킬", power="세기", ends="사용후종료", bonus="피해보너스",
                         special_name="특수스킬이름")
    @app_commands.describe(awaken_charge="궁극기 한 번에 각성 게이지가 차는 %(1~100, 기본 10)", ult_charge="공격·방어 한 번에 궁극기 게이지가 차는 %(1~100, 기본 20)",
                           special="각성 중에 쓰는 특수 스킬 종류", power=POWER_HELP + "(특수 스킬을 고를 때 함께)",
                           ends="특수 스킬을 쓰면 각성이 끝날까요(기본 예)", bonus="각성 중 내 피해 보너스 %(0~1000, 기본 0)",
                           special_name="특수 스킬의 " + NAME_HELP)
    @app_commands.choices(special=KIND_CHOICES)
    async def awakening(self, interaction: discord.Interaction, awaken_charge: app_commands.Range[int, 1, 100] | None = None,
                        ult_charge: app_commands.Range[int, 1, 100] | None = None, special: Choice[str] | None = None,
                        power: app_commands.Range[int, 1, 2000] | None = None, ends: bool | None = None,
                        bonus: app_commands.Range[int, 0, 1000] | None = None, special_name: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        saved = dict(store.settings(gid)["awakening"])
        changes = {k: v for k, v in dict(awaken_charge=awaken_charge, ult_charge=ult_charge, special_ends=ends, awaken_bonus=bonus).items() if v is not None}
        rename, value, problem = name_change(special_name)
        if problem:
            await self._say(interaction, problem, False)
            return
        if rename:
            changes["special_name"] = value
        if special is not None or power is not None:
            kind = special.value if special else store.balance(gid).special
            name, meaning, (low, high) = WEAPON_SKILLS[kind]
            value = power if power is not None else (AWAKENING_DEFAULT["special_power"] if kind == "power" else low)
            if not low <= value <= high:
                await self._say(interaction, f"[{name}]의 세기는 {meaning}이고 {low}~{high:,} 사이여야 해요.", False)
                return
            changes.update(special=kind, special_power=value)
        if not changes:
            await interaction.response.send_message(embed=theme.make_embed("⚡ 각성 설정", awakening_text(store.balance(gid)), color=theme.PURPLE),
                                                    ephemeral=False)
            return
        saved.update(changes)
        store.update_settings(gid, awakening=saved)
        await self._say(interaction, "⚡ 각성 설정을 바꿨어요.\n" + awakening_text(store.balance(gid)) + "\n진행 중인 던전에도 바로 적용돼요.")


async def setup(bot):
    await bot.add_cog(WeaponSkills(bot))
