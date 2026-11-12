"""무한모드 최종보스(/최종보스): 무한모드의 정한 층(기본 100층) 10번째 방에 나오는 마지막 보스를 정한다.

- 쓰러뜨리면 그 자리에서 던전을 '완전 클리어'로 끝내고, 클리어 보상에 최종보스 보상(재화·히든 칭호 획득권)을 더 받는다.
- 체력·공격은 1층 기준으로 적고, 층이 오를수록 다른 보스처럼 강해진다. 2페이즈와 스킬도 붙일 수 있다(기본: 둘 다 있음).
- 설정은 서버 설정의 final_boss 칸에 저장한다. 비운 칸은 기본값(balance.FINAL_BOSS_DEFAULT·FINAL_RULES_DEFAULT).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import theme
from access import require_admin
from balance import (BOSS_SKILLS, FINAL_LIMITS, PHASE2_HP_MAX, RESET_NAME, SKILL_EVERY_MAX, SKILL_NAME_MAX, SKILL_TURNS_MAX, check_final, name_change,
                     skill_text)
from cogs.elements import elements, find_element
from store import valid_emoji

Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
SKILL_CHOICES = [Choice(name=f"{name} — {meaning}", value=key) for key, (name, meaning, _r, _t) in BOSS_SKILLS.items()] + \
    [Choice(name="없음 (스킬 지우기)", value="none")]
CLEAR_ELEMENT = "없음"
NAME_HELP = f"스킬의 보이는 이름(1~{SKILL_NAME_MAX}자). 비우면 그대로, '{RESET_NAME}'이면 종류의 기본 이름"
REWARD_MAX = FINAL_LIMITS["reward"][1]
FLOOR_MAX = FINAL_LIMITS["floor"][1]
TICKETS_MAX = FINAL_LIMITS["tickets"][1]


def final_text(bal, cur=None) -> str:
    """지금 최종보스 설정 설명."""
    boss = bal.final_boss
    lines = [f"{boss.emoji} **{boss.name}**" + (f" [{bal.element_label(boss.element)}]" if bal.element_label(boss.element) else ""),
             f"❤️ HP {boss.hp:,} · ⚔️ 공격 {boss.damage:,} (1층 기준, 층이 오를수록 강해져요)"]
    lines.append(f"🔴 2페이즈: HP **{boss.phase2_hp:,}**" + (f" · 공격 **{boss.phase2_damage:,}**" if boss.phase2_damage else " · 공격은 그대로")
                 if boss.phase2_hp else "🔴 2페이즈: 없음")
    skill = skill_text(boss.skill, boss.skill_every, boss.skill_power, boss.skill_turns, boss.skill_name)
    lines.append(f"🌀 스킬: {skill}" if skill else "🌀 스킬: 없음")
    if bal.final_floor <= 0:
        lines.append("📍 등장: **꺼져 있어요** (층 0)")
    elif bal.final_floor <= bal.floors:
        lines.append(f"📍 등장: 무한 {bal.final_floor}층 — ⚠️ 마지막 층({bal.floors}층) 이하라서 나오지 않아요. {bal.floors + 1}층 이상으로 정해 주세요.")
    else:
        lines.append(f"📍 등장: 무한모드 **{bal.final_floor}층**의 10번째 방" + ("" if bal.infinite else " — ⚠️ 무한모드가 꺼져 있어서 지금은 나오지 않아요(`/던전설정`의 일반.무한모드)"))
    lines.append(f"🎁 보상: 클리어 보상 + {theme.money(bal.final_reward, cur)}" + (f" + 🎟️ 히든 칭호 획득권 {bal.final_tickets}장" if bal.final_tickets else ""))
    return "\n".join(lines)


class FinalBoss(commands.Cog):
    """🌀 무한모드 최종보스 (관리자)."""

    group = app_commands.Group(name="최종보스", description="무한모드 마지막 층(기본 100층)의 최종보스를 정해요 (관리자)", **PERMS)

    def __init__(self, bot):
        self.bot = bot

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    async def _save(self, interaction, changes: dict, head: str, *, replace: bool = False):
        """설정 칸을 바꾸고 검사해서 저장한다(검사에 걸리면 저장하지 않고 이유를 알려 준다)."""
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        saved = {} if replace else dict(store.settings(gid)["final_boss"])
        saved.update(changes)
        saved = {k: v for k, v in saved.items() if v is not None}      # None은 '지우기'(속성·스킬): 기본값이 아니라 없음으로 둔다
        _clean, problem = check_final(saved)
        if problem:
            await self._say(interaction, problem, False)
            return
        store.update_settings(gid, final_boss=saved)
        bal = store.balance(gid)
        await self._say(interaction, f"{head}\n{final_text(bal, store.currency(gid))}\n지금 시작하는 싸움부터 적용돼요.")

    @group.command(name="설정", description="최종보스의 이름·능력치·등장 층·보상을 정해요. 비운 칸은 그대로예요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", hp="체력", damage="공격", element="속성", floor="층", reward="보상", tickets="획득권")
    @app_commands.describe(name="최종보스 이름", emoji="이모지", hp="1페이즈 체력(1층 기준)", damage="공격(1층 기준)",
                           element=f"속성(/속성으로 만든 것). '{CLEAR_ELEMENT}'이면 지워요", floor=f"나오는 무한 층(마지막 층보다 높게, 0이면 끄기, ~{FLOOR_MAX:,})",
                           reward="쓰러뜨리면 클리어 보상에 더해 받는 재화", tickets=f"쓰러뜨리면 받는 히든 칭호 획득권(0~{TICKETS_MAX})")
    async def setup_boss(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 30] | None = None, emoji: str | None = None,
                         hp: app_commands.Range[int, 1, 100000] | None = None, damage: app_commands.Range[int, 0, 100000] | None = None,
                         element: str | None = None, floor: app_commands.Range[int, 0, FLOOR_MAX] | None = None,
                         reward: app_commands.Range[int, 0, REWARD_MAX] | None = None, tickets: app_commands.Range[int, 0, TICKETS_MAX] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        changes = {k: v for k, v in dict(name=name.strip() if name else None, hp=hp, damage=damage, floor=floor, reward=reward,
                                         tickets=tickets).items() if v is not None}
        if emoji is not None:
            emoji = emoji.strip()
            if not valid_emoji(emoji):
                await self._say(interaction, "이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요.", False)
                return
            changes["emoji"] = emoji
        if element is not None:
            if element.strip() == CLEAR_ELEMENT:
                changes["element"] = None
            else:
                found = find_element(store, gid, element)
                if found is None:
                    await self._say(interaction, "그런 속성이 없어요. `/속성 목록`에서 확인하고 자동완성에서 골라 주세요.", False)
                    return
                changes["element"] = found["key"]
        if not changes:
            await interaction.response.send_message(embed=theme.make_embed("🌀 최종보스", final_text(store.balance(gid), store.currency(gid)),
                                                                           color=theme.GOLD), ephemeral=False)
            return
        await self._save(interaction, changes, "🌀 최종보스를 바꿨어요.")

    @group.command(name="페이즈", description="최종보스의 2페이즈 체력(0이면 없음)과 2페이즈 공격을 정해요 (관리자)")
    @app_commands.rename(hp="체력", damage="공격")
    @app_commands.describe(hp=f"2페이즈 체력 바(1층 기준). 0이면 2페이즈 없음(0~{PHASE2_HP_MAX:,})", damage="2페이즈의 공격(1층 기준). 비우거나 0이면 1페이즈와 같아요")
    async def phase(self, interaction: discord.Interaction, hp: app_commands.Range[int, 0, PHASE2_HP_MAX], damage: app_commands.Range[int, 0, 100000] = 0):
        await self._save(interaction, {"phase2_hp": hp, "phase2_damage": damage if hp else 0},
                         "🔴 최종보스의 2페이즈를 정했어요." if hp else "🔴 최종보스의 2페이즈를 없앴어요.")

    @group.command(name="스킬", description="최종보스가 N번째 공격마다 쓰는 스킬을 정해요. '없음'이면 지워요 (관리자)")
    @app_commands.rename(kind="종류", every="주기", power="세기", turns="턴", name="이름")
    @app_commands.describe(kind="스킬 종류", every=f"몇 번째 공격마다 쓸까요(1~{SKILL_EVERY_MAX})",
                           power="세기: 강타는 공격의 %(100~1000), 자가 수리는 최대 체력의 %(1~100), 방어막은 피해 감소 %(1~100), 과열은 턴마다 피해",
                           turns=f"방어막·과열이 이어지는 턴(1~{SKILL_TURNS_MAX})", name=NAME_HELP)
    @app_commands.choices(kind=SKILL_CHOICES)
    async def skill(self, interaction: discord.Interaction, kind: Choice[str], every: app_commands.Range[int, 1, SKILL_EVERY_MAX] = 3,
                    power: app_commands.Range[int, 1, 100000] = 150, turns: app_commands.Range[int, 0, SKILL_TURNS_MAX] = 0,
                    name: str | None = None):
        if kind.value == "none":
            await self._save(interaction, {"skill": "none", "skill_every": 0, "skill_power": 0, "skill_turns": 0, "skill_name": None},
                             "🌀 최종보스의 스킬을 지웠어요.")
            return
        changes = {"skill": kind.value, "skill_every": every, "skill_power": power, "skill_turns": turns}
        if not await self._name_into(interaction, name, changes):
            return
        await self._save(interaction, changes, "🌀 최종보스의 스킬을 정했어요.")

    @group.command(name="스킬이름", description="최종보스 스킬의 이름만 바꿔요. '기본'이면 원래 이름 (관리자)")
    @app_commands.rename(name="이름")
    @app_commands.describe(name=NAME_HELP)
    async def skill_rename(self, interaction: discord.Interaction, name: str):
        if not await require_admin(interaction):
            return
        if self.bot.store.balance(interaction.guild_id).final_boss.skill is None:
            await self._say(interaction, "최종보스에 스킬이 없어요. 먼저 `/최종보스 스킬`로 붙여 주세요.", False)
            return
        changes = {}
        if not await self._name_into(interaction, name, changes):
            return
        await self._save(interaction, changes, "🏷️ 최종보스의 스킬 이름을 바꿨어요.")

    async def _name_into(self, interaction, name: str | None, changes: dict) -> bool:
        """'이름' 칸을 changes에 넣는다. 잘못된 이름이면 이유를 보여 주고 False."""
        if not await require_admin(interaction):
            return False
        rename, value, problem = name_change(name)
        if problem:
            await self._say(interaction, problem, False)
            return False
        if rename:
            changes["skill_name"] = value                    # None이면 저장에서 빠져 기본 이름이 된다
        return True

    @group.command(name="보기", description="지금 최종보스 설정을 봐요 (관리자)")
    async def show(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        await interaction.response.send_message(embed=theme.make_embed("🌀 최종보스", final_text(store.balance(gid), store.currency(gid)), color=theme.GOLD),
                                                ephemeral=False)

    @group.command(name="초기화", description="최종보스를 기본값(오메가 코어 · 무한 100층)으로 되돌려요 (관리자)")
    async def reset(self, interaction: discord.Interaction):
        await self._save(interaction, {}, "🌀 최종보스를 기본값으로 되돌렸어요.", replace=True)

    @setup_boss.autocomplete("element")
    async def element_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        out = [Choice(name=f"{e['emoji']} {e['name']}"[:100], value=e["name"]) for e in elements(self.bot.store, interaction.guild_id)
               if needle in e["name"].lower()]
        return [Choice(name=f"{CLEAR_ELEMENT} (속성 지우기)", value=CLEAR_ELEMENT), *out][:25]


async def setup(bot):
    await bot.add_cog(FinalBoss(bot))
