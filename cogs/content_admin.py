"""던전 콘텐츠(몬스터·보스·장비·소모품)를 관리자가 더하고 빼고 바꾸는 명령어. 규칙은 content.py가 맡는다."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import content
import theme
from access import require_admin
from balance import SECTION_LABELS, SECTIONS, TIER_ORDER
from confirm import Confirm

Choice = app_commands.Choice
TIERS = [Choice(name=t, value=t) for t in TIER_ORDER]
SLOTS = [Choice(name="무기", value="weapon"), Choice(name="방패", value="shield")]
LIST_CHOICES = [Choice(name=SECTION_LABELS[s], value=s) for s in SECTIONS]
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))


def names_for(store, guild_id: int, sections, current: str) -> list[Choice]:
    needle = current.strip().lower()
    out = []
    for section in sections:
        for data in content.current(store, guild_id, section):
            if needle in data["name"].lower():
                out.append(Choice(name=f"{data['emoji']} {data['name']}"[:100], value=data["name"]))
    return out[:25]


class ContentAdmin(commands.Cog):
    """던전 몬스터·장비·소모품 편집 (관리자 전용)."""

    def __init__(self, bot):
        self.bot = bot

    async def _deny(self, interaction, text):
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=True)

    async def _done(self, interaction, head: str, section: str, data: dict | None = None):
        line = f"\n{content.describe(section, data)}" if data else ""
        await interaction.response.send_message(embed=theme.notice(
            f"{head}{line}\n지금 진행 중인 던전에도 바로 적용돼요(이미 나온 몬스터의 체력·공격은 그대로). `/던전시뮬`로 확인해 보세요."), ephemeral=True)

    async def _run(self, interaction, func, head: str):
        """content 함수를 부르고 결과를 알린다. func() → (구역, 개체 사전)."""
        if not await require_admin(interaction):
            return
        try:
            section, data = func()
        except content.ContentError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._done(interaction, head, section, data)

    def _entity_dict(self, entity) -> dict:
        from balance import entity_dict
        return entity_dict(entity)

    # ---------------------------------------------------------------- 몬스터 / 보스
    monster = app_commands.Group(name="몬스터", description="던전 몬스터·보스를 더하고 빼고 바꿔요 (관리자)", **PERMS)

    @monster.command(name="추가", description="몬스터나 보스를 새로 만들어요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", hp="체력", damage="공격", dodge="회피", boss="보스")
    @app_commands.describe(name="이름 (30자까지)", emoji="이모지", hp="체력", damage="한 번 때리는 피해", dodge="내 공격을 피할 확률(%, 기본 10). 보스는 회피 불가라 무시돼요",
                           boss="미니 보스로 만들까요? (마지막 방에 나와요)")
    async def monster_add(self, interaction: discord.Interaction, name: str, emoji: str, hp: app_commands.Range[int, 1, 100000],
                          damage: app_commands.Range[int, 0, 100000], dodge: app_commands.Range[int, 0, 95] = 10, boss: bool = False):
        def go():
            section = "bosses" if boss else "monsters"
            entity = content.add(self.bot.store, interaction.guild_id, section, dict(name=name, emoji=emoji.strip(), hp=hp, damage=damage, dodge=dodge))
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "➕ 만들었어요.")

    @monster.command(name="수정", description="몬스터·보스의 값을 고쳐요. 바꿀 것만 적으세요 (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", hp="체력", damage="공격", dodge="회피")
    async def monster_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                           hp: app_commands.Range[int, 1, 100000] | None = None, damage: app_commands.Range[int, 0, 100000] | None = None,
                           dodge: app_commands.Range[int, 0, 95] | None = None):
        def go():
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), hp=hp, damage=damage, dodge=dodge)
            changes = {k: v for k, v in changes.items() if v is not None}
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("monsters", "bosses"), target, changes)
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "✏️ 고쳤어요.")

    @monster.command(name="삭제", description="몬스터·보스를 지워요 (관리자)")
    @app_commands.rename(target="대상")
    async def monster_delete(self, interaction: discord.Interaction, target: str):
        def go():
            return content.delete(self.bot.store, interaction.guild_id, ("monsters", "bosses"), target)
        await self._run(interaction, go, "🗑️ 지웠어요.")

    @monster_edit.autocomplete("target")
    @monster_delete.autocomplete("target")
    async def monster_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("monsters", "bosses"), current) if interaction.guild_id else []

    # ---------------------------------------------------------------- 장비
    gear = app_commands.Group(name="장비", description="던전 장비(무기·방패)를 더하고 빼고 바꿔요 (관리자)", **PERMS)

    @gear.command(name="추가", description="장비를 새로 만들어요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", slot="종류", tier="등급", weight="등장", attack="공격력", crit="크리티컬",
                         dodge="회피변환", guard="가드", reflect="반사")
    @app_commands.describe(slot="무기 / 방패", weight="상자에서 나올 비율(좋을수록 낮게)", attack="무기: 공격 피해 추가", crit="무기: 크리티컬(2배) 확률(%)",
                           dodge="무기: 잡몹의 회피 확률을 이 값으로 바꿔요(%)", guard="방패: 가드 성공 시 피해 감소(%)(필수)",
                           reflect="방패: 가드 성공 시 반사 피해(%)")
    @app_commands.choices(slot=SLOTS, tier=TIERS)
    async def gear_add(self, interaction: discord.Interaction, name: str, emoji: str, slot: Choice[str], tier: Choice[str],
                       weight: app_commands.Range[int, 0, 1000], attack: app_commands.Range[int, 0, 100000] = 0,
                       crit: app_commands.Range[int, 0, 100] = 0, dodge: app_commands.Range[int, 0, 95] | None = None,
                       guard: app_commands.Range[int, 0, 100] | None = None, reflect: app_commands.Range[int, 0, 500] | None = None):
        def go():
            entity = content.add(self.bot.store, interaction.guild_id, "gear", dict(
                name=name, emoji=emoji.strip(), slot=slot.value, tier=tier.value, weight=weight, attack=attack, crit=crit,
                dodge=dodge, guard=guard, reflect=reflect))
            return "gear", self._entity_dict(entity)
        await self._run(interaction, go, "➕ 만들었어요.")

    @gear.command(name="수정", description="장비의 값을 고쳐요. 바꿀 것만 적으세요 (-1은 해제) (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", tier="등급", weight="등장", attack="공격력", crit="크리티컬",
                         dodge="회피변환", guard="가드", reflect="반사")
    @app_commands.choices(tier=TIERS)
    async def gear_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                        tier: Choice[str] | None = None, weight: app_commands.Range[int, 0, 1000] | None = None,
                        attack: app_commands.Range[int, 0, 100000] | None = None, crit: app_commands.Range[int, 0, 100] | None = None,
                        dodge: app_commands.Range[int, -1, 95] | None = None, guard: app_commands.Range[int, 0, 100] | None = None,
                        reflect: app_commands.Range[int, -1, 500] | None = None):
        def go():
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), tier=tier and tier.value, weight=weight, attack=attack,
                           crit=crit, guard=guard)
            changes = {k: v for k, v in changes.items() if v is not None}
            if dodge is not None:                       # -1은 '해제'(None)
                changes["dodge"] = None if dodge < 0 else dodge
            if reflect is not None:
                changes["reflect"] = None if reflect < 0 else reflect
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("gear",), target, changes)
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "✏️ 고쳤어요.")

    @gear.command(name="삭제", description="장비를 지워요 (관리자)")
    @app_commands.rename(target="대상")
    async def gear_delete(self, interaction: discord.Interaction, target: str):
        await self._run(interaction, lambda: content.delete(self.bot.store, interaction.guild_id, ("gear",), target), "🗑️ 지웠어요.")

    @gear_edit.autocomplete("target")
    @gear_delete.autocomplete("target")
    async def gear_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("gear",), current) if interaction.guild_id else []

    # ---------------------------------------------------------------- 소모품
    potion = app_commands.Group(name="소모품", description="던전 소모품(회복 아이템)을 더하고 빼고 바꿔요 (관리자)", **PERMS)

    @potion.command(name="추가", description="소모품을 새로 만들어요. 회복량·전체회복·지속회복 중 하나를 정해요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", tier="등급", weight="등장", heal="회복량", full="전체회복", regen="지속회복량", turns="지속턴")
    @app_commands.describe(weight="상자에서 나올 비율", heal="한 번에 회복하는 HP", full="HP를 전부 회복", regen="턴마다 회복하는 HP", turns="지속 회복이 이어지는 턴 수")
    @app_commands.choices(tier=TIERS)
    async def potion_add(self, interaction: discord.Interaction, name: str, emoji: str, tier: Choice[str],
                         weight: app_commands.Range[int, 0, 1000], heal: app_commands.Range[int, 0, 100000] = 0, full: bool = False,
                         regen: app_commands.Range[int, 0, 100000] = 0, turns: app_commands.Range[int, 0, 1000] = 0):
        def go():
            entity = content.add(self.bot.store, interaction.guild_id, "consumables", dict(
                name=name, emoji=emoji.strip(), tier=tier.value, weight=weight, heal=heal, full=full, regen=regen, regen_turns=turns))
            return "consumables", self._entity_dict(entity)
        await self._run(interaction, go, "➕ 만들었어요.")

    @potion.command(name="수정", description="소모품의 값을 고쳐요. 효과를 바꾸면 이전 효과는 사라져요 (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", tier="등급", weight="등장", heal="회복량", full="전체회복",
                         regen="지속회복량", turns="지속턴")
    @app_commands.choices(tier=TIERS)
    async def potion_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                          tier: Choice[str] | None = None, weight: app_commands.Range[int, 0, 1000] | None = None,
                          heal: app_commands.Range[int, 1, 100000] | None = None, full: bool | None = None,
                          regen: app_commands.Range[int, 1, 100000] | None = None, turns: app_commands.Range[int, 1, 1000] | None = None):
        def go():
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), tier=tier and tier.value, weight=weight)
            changes = {k: v for k, v in changes.items() if v is not None}
            if full:                                    # 효과는 하나만: 새 효과를 정하면 이전 효과를 지운다.
                changes.update(full=True, heal=0, regen=0, regen_turns=0)
            elif regen is not None:
                changes.update(full=False, heal=0, regen=regen)
                if turns is not None:
                    changes["regen_turns"] = turns
            elif heal is not None:
                changes.update(full=False, heal=heal, regen=0, regen_turns=0)
            elif turns is not None:
                changes["regen_turns"] = turns
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("consumables",), target, changes)
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "✏️ 고쳤어요.")

    @potion.command(name="삭제", description="소모품을 지워요 (관리자)")
    @app_commands.rename(target="대상")
    async def potion_delete(self, interaction: discord.Interaction, target: str):
        await self._run(interaction, lambda: content.delete(self.bot.store, interaction.guild_id, ("consumables",), target), "🗑️ 지웠어요.")

    @potion_edit.autocomplete("target")
    @potion_delete.autocomplete("target")
    async def potion_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("consumables",), current) if interaction.guild_id else []

    # ---------------------------------------------------------------- 목록 · 초기화
    @app_commands.command(name="던전목록", description="던전의 몬스터·보스·장비·소모품을 모두 봐요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(section="분류")
    @app_commands.choices(section=LIST_CHOICES)
    async def dungeon_list(self, interaction: discord.Interaction, section: Choice[str] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        custom = store.settings(gid)["dungeon_content"]
        embed = theme.make_embed("🏚️ 던전 목록", "바꾸려면 `/몬스터` `/장비` `/소모품`. ✏️ = 직접 편집한 분류", color=theme.PURPLE)
        for sec in ([section.value] if section else SECTIONS):
            lines = [content.describe(sec, d) for d in content.current(store, gid, sec)]
            mark = " ✏️" if sec in custom else ""
            embed.add_field(name=f"{SECTION_LABELS[sec]} {len(lines)}개{mark}", value="\n".join(lines)[:1000] or "없음", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="던전콘텐츠초기화", description="몬스터·장비·소모품을 기본 목록으로 되돌려요. 분류를 비우면 전부 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(section="분류")
    @app_commands.choices(section=LIST_CHOICES)
    async def content_reset(self, interaction: discord.Interaction, section: Choice[str] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        targets = (section.value,) if section else SECTIONS
        label = SECTION_LABELS[section.value] if section else "몬스터·보스·장비·소모품 전부"
        edited = [s for s in targets if s in store.settings(gid)["dungeon_content"]]
        if not edited:
            await self._deny(interaction, "직접 편집한 게 없어요. 이미 기본 목록이에요.")
            return

        def apply() -> str:
            return f"♻️ {label}을(를) 기본 목록으로 되돌렸어요. (직접 만든 항목은 사라져요)"

        def do() -> str:
            content.reset(store, gid, targets)
            return apply()

        await interaction.response.send_message(embed=theme.make_embed(
            "♻️ 기본 목록으로 되돌릴까요?", f"**{label}**에서 직접 만들거나 바꾼 내용이 모두 사라지고 기본값으로 돌아가요.", color=theme.RED),
            view=Confirm(interaction.user.id, do, label="되돌리기", emoji="♻️"), ephemeral=True)


async def setup(bot):
    await bot.add_cog(ContentAdmin(bot))
