"""던전 콘텐츠(몬스터·보스·장비·소모품)를 관리자가 더하고 빼고 바꾸는 명령어. 규칙은 content.py가 맡는다."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import content
import theme
from access import require_admin
import balance
from bulk import BulkEntry, send_bulk
from balance import (COND_EVENT, COND_ITEM, COND_LABELS, COND_STATE, MAX_CONDITIONS, SECTION_LABELS, SECTIONS, SELL_MAX, TIER_ORDER,
                     condition_text)
from confirm import Confirm
from paging import send_paged, split_pages

Choice = app_commands.Choice
TIERS = [Choice(name=t, value=t) for t in TIER_ORDER]
SLOTS = [Choice(name="무기", value="weapon"), Choice(name="방패", value="shield"), Choice(name="갑옷", value="armor")]
LIST_CHOICES = [Choice(name=SECTION_LABELS[s], value=s) for s in SECTIONS]
REDUCTION_HELP = f"갑옷: 받는 모든 피해를 줄이는 비율(%, 1~{balance.ARMOR_MAX}). 갑옷에는 필수예요. 가드·소모품 효과와 겹쳐 적용돼요"
GUARD_CHANCE_HELP = "방패: 이 방패로 방어할 때 가드에 성공할 확률(%). 비우면 서버 기본(/던전설정 방어.성공확률)을 써요."
ROOM_WEIGHT_HELP = "방 배치에서 다른 방(상자·빈방·잡몹…)과 겨루는 가중치. 소수도 돼요. 예: 빈 방 40·잡몹 60인 방에 10을 주면 약 9%로 나와요"
ROOM_PLACES_HELP = "나올 방 번호(1~9번째. 10번째는 보스 방): 3,5,8-9처럼. 비우거나 '전체'면 1~9번째 모두"
FLOORS_HELP = "나올 층: 3,5,8-10처럼 여러 층을 쉼표로, 범위는 -로. '전체'를 쓰면 모든 층. 비우면 지금 설정만 봐요"
WEIGHT_HELP = "상자에서 나올 비율. 소수도 돼요(예: 0.5, 0.05). 다른 것들과의 비율로 계산하고 높을수록 자주 나와요"
SELL_HELP = "던전 안에서 팔 때 받는 재화(개당). 0이면 팔 수 없어요. 비우면 그대로(추가할 땐 등급별 기본값)"
SELL_TARGETS = [Choice(name="장비와 소모품 모두", value="both"), Choice(name="장비만", value="gear"), Choice(name="소모품만", value="consumables")]
COND_CHOICES = [Choice(name=COND_LABELS[k].format(v="N"), value=k) for k in COND_STATE + COND_EVENT]
CHANCE_HELP = "그 드롭이 나올 확률(%). 소수도 돼요(기본 100). 둘 다 안 나오면 확률이 더 높은 쪽이 반드시 나와요"
IMAGE_HELP = "등장 연출과 전투 화면에 붙일 그림 주소(http/https). 움직이는 gif 주소도 돼요."
MATERIAL_HELP = "개조 재료로 만들까요? 재료는 효과가 없고, /장비개조 레시피에 있는 무기를 들고 쓰면 그 무기가 개조돼요"
DAMAGE_HELP = "쓰면 싸우는 몬스터에게 바로 주는 피해. 싸우는 중에만 쓸 수 있어요"
DOT_HELP = "몬스터에게 턴마다 주는 피해(독·화상). 지속피해턴과 함께 정해요"
DOT_TURNS_HELP = "지속 피해가 이어지는 턴 수"
SELF_HELP = "쓰면 내 HP가 줄어요(1 밑으로는 안 줄어서 이것만으로 죽지는 않아요). 강한 효과의 대가로 붙여 보세요"
SELF_TURNS_HELP = "자해가 이어지는 턴 수. 2 이상이면 첫 번째는 바로, 나머지는 턴마다 줄어요. 비우면 한 번만"
INVULN_HELP = "이 턴 수 동안 몬스터의 공격 피해를 받지 않아요. 턴은 싸우는 중에만 지나가요"
REDUCE_HELP = "받는 피해를 줄이는 %(1~100). 피해감소턴과 함께 정해요. 가드와 겹치면 둘 다 적용돼요"
REDUCE_TURNS_HELP = "피해 감소가 이어지는 턴 수"
EFFECT_NAMES = dict(damage="몬스터피해", dot="지속피해", dot_turns="지속피해턴", self_damage="자해", self_turns="자해턴",
                    invuln_turns="무적턴", reduce="피해감소", reduce_turns="피해감소턴")
MODIFY_HELP = "무기 개조: 쓰면 들고 있는 무기가 이 무기로 바뀌어요(무기 이름). 회복 효과와는 함께 못 써요"
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
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=False)

    async def _done(self, interaction, head: str, section: str, data: dict | None = None):
        line = f"\n{content.describe(section, data, self.bot.store.balance(interaction.guild_id))}" if data else ""
        await interaction.response.send_message(embed=theme.notice(
            f"{head}{line}\n지금 진행 중인 던전에도 바로 적용돼요(이미 나온 몬스터의 체력·공격은 그대로). `/던전시뮬`로 확인해 보세요."), ephemeral=False)

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

    async def _floors(self, interaction, sections, command: str, noun: str, target: str, floors: str | None, label_of, when: str) -> None:
        """몬스터·장비·소모품이 나올 층을 보고 정하는 공통 처리. 층이 비는 설정은 content.edit가 거절한다."""
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        hit = content.find(store, gid, sections, target)
        if hit is None:
            await self._deny(interaction, f"그런 {noun} 없어요. 이름은 자동완성에서 골라 주세요.")
            return
        section, data = hit
        label = label_of(section)
        if floors is None:
            await interaction.response.send_message(embed=theme.notice(
                f"{data['emoji']} **{data['name']}**({label})은(는) **{balance.floors_text(data['floors'])}**에서 나와요.\n"
                f"`{command} 대상:{data['name']} 층:3,5,8-10`처럼 바꿔요."), ephemeral=False)
            return
        chosen, problem = balance.parse_floors(floors)
        if problem:
            await self._deny(interaction, problem)
            return
        try:
            _, entity = content.edit(store, gid, sections, data["name"], {"floors": chosen})
        except content.ContentError as exc:
            await self._deny(interaction, str(exc))
            return
        bal = store.balance(gid)
        notes = []
        if chosen and min(chosen) > bal.floors and not bal.infinite:
            notes.append(f"⚠️ 지금은 {bal.floors}층까지만 있고 무한모드도 꺼져 있어서(`일반.무한모드`) 이 층들에서는 나올 수 없어요.")
        elif chosen and max(chosen) > bal.floors:
            notes.append(f"ℹ️ {bal.floors + 1}층부터는 무한모드를 골라야 갈 수 있어요.")
        await interaction.response.send_message(embed=theme.notice(
            f"{entity.emoji} **{entity.name}**({label})이(가) 나올 층: **{balance.floors_text(entity.floors)}**\n"
            f"{when}, 나올 수 있는 {label}들 중에서 `등장` 비율로 골라요." + "".join("\n" + n for n in notes)), ephemeral=False)

    async def _bulk(self, interaction, noun: str, sections) -> None:
        """던전 콘텐츠(몬스터·장비·소모품·시크릿 보스)를 여러 개 골라 지우는 화면. sections = ((구역, 종류 표시), ...)."""
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        entries = []
        for section, kind in sections:
            for d in content.current(store, gid, section):
                entries.append(BulkEntry(f"{section}:{d['name']}", d["name"] + (f" ({kind})" if kind else ""), d.get("emoji"),
                                         content.describe(section, d, store.balance(gid)).split(" — ", 1)[-1][:100]))
        names = {section for section, _ in sections}

        def delete(value: str) -> str:
            section, name = value.split(":", 1)
            content.delete(store, gid, (section,), name)
            return ""
        await send_bulk(interaction, noun, entries, delete, (content.ContentError,))

    def _entity_dict(self, entity) -> dict:
        from balance import entity_dict
        return entity_dict(entity)

    # ---------------------------------------------------------------- 몬스터 / 보스
    monster = app_commands.Group(name="몬스터", description="던전 몬스터·보스를 더하고 빼고 바꿔요 (관리자)", **PERMS)

    @monster.command(name="추가", description="몬스터나 보스를 새로 만들어요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", hp="체력", damage="공격", dodge="회피", boss="보스", weight="등장")
    @app_commands.describe(name="이름 (30자까지)", emoji="이모지", hp="체력", damage="한 번 때리는 피해", dodge="내 공격을 피할 확률(%, 기본 10). 보스는 회피 불가라 무시돼요",
                           boss="미니 보스로 만들까요? (마지막 방에 나와요)",
                           weight="같은 종류(잡몹끼리, 보스끼리) 안에서 나올 비율. 소수도 돼요(예: 0.5). 기본 1 = 다른 몬스터와 같은 확률")
    async def monster_add(self, interaction: discord.Interaction, name: str, emoji: str, hp: app_commands.Range[int, 1, 100000],
                          damage: app_commands.Range[int, 0, 100000], dodge: app_commands.Range[int, 0, 95] = 10, boss: bool = False,
                          weight: app_commands.Range[float, 0, 1000] = 1):
        def go():
            section = "bosses" if boss else "monsters"
            entity = content.add(self.bot.store, interaction.guild_id, section, dict(name=name, emoji=emoji.strip(), hp=hp, damage=damage, dodge=dodge, weight=weight))
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "➕ 만들었어요.")

    @monster.command(name="수정", description="몬스터·보스의 값을 고쳐요. 바꿀 것만 적으세요 (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", hp="체력", damage="공격", dodge="회피", weight="등장")
    @app_commands.describe(weight="같은 종류 안에서 나올 비율. 소수도 돼요(예: 0.5). 0이면 안 나와요")
    async def monster_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                           hp: app_commands.Range[int, 1, 100000] | None = None, damage: app_commands.Range[int, 0, 100000] | None = None,
                           dodge: app_commands.Range[int, 0, 95] | None = None, weight: app_commands.Range[float, 0, 1000] | None = None):
        def go():
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), hp=hp, damage=damage, dodge=dodge, weight=weight)
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

    @monster.command(name="층", description="이 몬스터·보스가 나올 층을 정해요. 여러 층은 쉼표로(3,5,8-10). 비우면 지금 설정을 봐요 (관리자)")
    @app_commands.rename(target="대상", floors="층")
    @app_commands.describe(target="층을 정할 몬스터나 보스", floors=FLOORS_HELP)
    async def monster_floors(self, interaction: discord.Interaction, target: str, floors: str | None = None):
        await self._floors(interaction, ("monsters", "bosses"), "/몬스터 층", "몬스터·보스가", target, floors,
                           lambda section: "보스" if section == "bosses" else "몬스터", "그 층에서 이 종류의 방이 나올 때")

    @monster.command(name="대량삭제", description="몬스터·보스를 여러 개 골라 한 번에 지워요 (관리자)")
    async def monster_bulk(self, interaction: discord.Interaction):
        await self._bulk(interaction, "몬스터·보스", (("monsters", "몬스터"), ("bosses", "보스")))

    @monster_edit.autocomplete("target")
    @monster_delete.autocomplete("target")
    @monster_floors.autocomplete("target")
    async def monster_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("monsters", "bosses"), current) if interaction.guild_id else []

    # ---------------------------------------------------------------- 장비
    gear = app_commands.Group(name="장비", description="던전 장비(무기·방패·갑옷)를 더하고 빼고 바꿔요 (관리자)", **PERMS)

    @gear.command(name="추가", description="장비를 새로 만들어요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", slot="종류", tier="등급", weight="등장", attack="공격력", crit="크리티컬",
                         dodge="회피변환", guard="가드", reflect="반사", guard_chance="가드성공률", sell_price="판매가", reduction="피해감소")
    @app_commands.describe(slot="무기 / 방패 / 갑옷", weight=WEIGHT_HELP, reduction=REDUCTION_HELP, attack="무기: 공격 피해 추가", crit="무기: 크리티컬(2배) 확률(%)",
                           dodge="무기: 잡몹의 회피 확률을 이 값으로 바꿔요(%)", guard="방패: 가드 성공 시 피해 감소(%)(필수)",
                           reflect="방패: 가드 성공 시 반사 피해(%)", guard_chance=GUARD_CHANCE_HELP, sell_price=SELL_HELP)
    @app_commands.choices(slot=SLOTS, tier=TIERS)
    async def gear_add(self, interaction: discord.Interaction, name: str, emoji: str, slot: Choice[str], tier: Choice[str],
                       weight: app_commands.Range[float, 0, 1000], attack: app_commands.Range[int, 0, 100000] = 0,
                       crit: app_commands.Range[int, 0, 100] = 0, dodge: app_commands.Range[int, 0, 95] | None = None,
                       guard: app_commands.Range[int, 0, 100] | None = None, reflect: app_commands.Range[int, 0, 500] | None = None,
                       sell_price: app_commands.Range[int, 0, SELL_MAX] | None = None,
                       guard_chance: app_commands.Range[int, 0, 100] | None = None,
                       reduction: app_commands.Range[int, 0, balance.ARMOR_MAX] = 0):
        def go():
            data = dict(name=name, emoji=emoji.strip(), slot=slot.value, tier=tier.value, weight=weight, attack=attack, crit=crit,
                        dodge=dodge, guard=guard, reflect=reflect, guard_chance=guard_chance, reduction=reduction)
            if sell_price is not None:                  # 비우면 등급별 기본 판매가
                data["sell_price"] = sell_price
            entity = content.add(self.bot.store, interaction.guild_id, "gear", data)
            return "gear", self._entity_dict(entity)
        await self._run(interaction, go, "➕ 만들었어요.")

    @gear.command(name="수정", description="장비의 값을 고쳐요. 바꿀 것만 적으세요 (-1은 해제) (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", tier="등급", weight="등장", attack="공격력", crit="크리티컬",
                         dodge="회피변환", guard="가드", reflect="반사", guard_chance="가드성공률", sell_price="판매가", reduction="피해감소")
    @app_commands.describe(weight=WEIGHT_HELP, guard_chance=GUARD_CHANCE_HELP + " -1은 해제(서버 기본으로)", sell_price=SELL_HELP, reduction=REDUCTION_HELP)
    @app_commands.choices(tier=TIERS)
    async def gear_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                        tier: Choice[str] | None = None, weight: app_commands.Range[float, 0, 1000] | None = None,
                        attack: app_commands.Range[int, 0, 100000] | None = None, crit: app_commands.Range[int, 0, 100] | None = None,
                        dodge: app_commands.Range[int, -1, 95] | None = None, guard: app_commands.Range[int, 0, 100] | None = None,
                        reflect: app_commands.Range[int, -1, 500] | None = None,
                        sell_price: app_commands.Range[int, 0, SELL_MAX] | None = None,
                        guard_chance: app_commands.Range[int, -1, 100] | None = None,
                        reduction: app_commands.Range[int, 1, balance.ARMOR_MAX] | None = None):
        def go():
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), tier=tier and tier.value, weight=weight, attack=attack,
                           crit=crit, guard=guard, sell_price=sell_price, reduction=reduction)
            changes = {k: v for k, v in changes.items() if v is not None}
            if dodge is not None:                       # -1은 '해제'(None)
                changes["dodge"] = None if dodge < 0 else dodge
            if reflect is not None:
                changes["reflect"] = None if reflect < 0 else reflect
            if guard_chance is not None:                # -1은 '해제'(서버 기본 성공률로)
                changes["guard_chance"] = None if guard_chance < 0 else guard_chance
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("gear",), target, changes)
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "✏️ 고쳤어요.")

    @gear.command(name="삭제", description="장비를 지워요 (관리자)")
    @app_commands.rename(target="대상")
    async def gear_delete(self, interaction: discord.Interaction, target: str):
        await self._run(interaction, lambda: content.delete(self.bot.store, interaction.guild_id, ("gear",), target), "🗑️ 지웠어요.")

    @gear.command(name="층", description="이 장비가 상자에서 나올 층을 정해요. 여러 층은 쉼표로(3,5,8-10). 비우면 지금 설정을 봐요 (관리자)")
    @app_commands.rename(target="대상", floors="층")
    @app_commands.describe(target="층을 정할 장비", floors=FLOORS_HELP)
    async def gear_floors(self, interaction: discord.Interaction, target: str, floors: str | None = None):
        await self._floors(interaction, ("gear",), "/장비 층", "장비가", target, floors, lambda section: "장비", "그 층의 상자에서 장비가 나올 때")

    @gear.command(name="대량삭제", description="장비를 여러 개 골라 한 번에 지워요 (관리자)")
    async def gear_bulk(self, interaction: discord.Interaction):
        await self._bulk(interaction, "장비", (("gear", ""),))

    @gear_edit.autocomplete("target")
    @gear_delete.autocomplete("target")
    @gear_floors.autocomplete("target")
    async def gear_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("gear",), current) if interaction.guild_id else []

    # ---------------------------------------------------------------- 소모품
    potion = app_commands.Group(name="소모품", description="던전 소모품(회복 아이템)을 더하고 빼고 바꿔요 (관리자)", **PERMS)

    @potion.command(name="추가", description="소모품을 새로 만들어요. 회복량·전체회복·지속회복·무기 개조 중 하나를 정해요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", tier="등급", weight="등장", heal="회복량", full="전체회복", regen="지속회복량", turns="지속턴",
                         sell_price="판매가", modify="개조무기", material="재료", **EFFECT_NAMES)
    @app_commands.describe(weight=WEIGHT_HELP, heal="한 번에 회복하는 HP", full="HP를 전부 회복", regen="턴마다 회복하는 HP", turns="지속 회복이 이어지는 턴 수",
                           sell_price=SELL_HELP, modify=MODIFY_HELP, material=MATERIAL_HELP, damage=DAMAGE_HELP, dot=DOT_HELP, dot_turns=DOT_TURNS_HELP,
                           self_damage=SELF_HELP, self_turns=SELF_TURNS_HELP, invuln_turns=INVULN_HELP, reduce=REDUCE_HELP, reduce_turns=REDUCE_TURNS_HELP)
    @app_commands.choices(tier=TIERS)
    async def potion_add(self, interaction: discord.Interaction, name: str, emoji: str, tier: Choice[str],
                         weight: app_commands.Range[float, 0, 1000], heal: app_commands.Range[int, 0, 100000] = 0, full: bool = False,
                         regen: app_commands.Range[int, 0, 100000] = 0, turns: app_commands.Range[int, 0, 1000] = 0,
                         sell_price: app_commands.Range[int, 0, SELL_MAX] | None = None, modify: str | None = None, material: bool = False,
                         damage: app_commands.Range[int, 0, 1000000] = 0, dot: app_commands.Range[int, 0, 1000000] = 0,
                         dot_turns: app_commands.Range[int, 0, 1000] = 0, self_damage: app_commands.Range[int, 0, 100000] = 0,
                         self_turns: app_commands.Range[int, 0, 1000] = 0, invuln_turns: app_commands.Range[int, 0, 1000] = 0,
                         reduce: app_commands.Range[int, 0, 100] = 0, reduce_turns: app_commands.Range[int, 0, 1000] = 0):
        def go():
            effects = dict(damage=damage, dot=dot, dot_turns=dot_turns, self_damage=self_damage, self_turns=self_turns,
                           invuln_turns=invuln_turns, reduce=reduce, reduce_turns=reduce_turns)
            data = dict(name=name, emoji=emoji.strip(), tier=tier.value, weight=weight, heal=heal, full=full, regen=regen, regen_turns=turns, **effects)
            if material:
                if heal or full or regen or turns or modify or any(effects.values()):
                    raise content.ContentError("개조 재료는 효과가 없어요. 회복·피해·개조무기 같은 옵션과 함께 쓸 수 없어요.")
                data["material"] = True
            if modify:
                if heal or full or regen or turns or any(effects.values()):
                    raise content.ContentError("개조 소모품은 다른 효과와 함께 쓸 수 없어요. 개조무기만 적어 주세요.")
                data["modify"] = self._weapon_key(modify, interaction.guild_id)
            if sell_price is not None:                  # 비우면 등급별 기본 판매가
                data["sell_price"] = sell_price
            entity = content.add(self.bot.store, interaction.guild_id, "consumables", data)
            return "consumables", self._entity_dict(entity)
        await self._run(interaction, go, "➕ 만들었어요.")

    @potion.command(name="수정", description="소모품의 값을 고쳐요. 효과를 바꾸면 이전 효과는 사라져요 (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", tier="등급", weight="등장", heal="회복량", full="전체회복",
                         regen="지속회복량", turns="지속턴", sell_price="판매가", modify="개조무기", material="재료", **EFFECT_NAMES)
    @app_commands.describe(weight=WEIGHT_HELP, sell_price=SELL_HELP, modify=MODIFY_HELP, material=MATERIAL_HELP, damage=DAMAGE_HELP + " (0이면 없애요)",
                           dot=DOT_HELP + " (0이면 없애요)", dot_turns=DOT_TURNS_HELP, self_damage=SELF_HELP + " (0이면 없애요)", self_turns=SELF_TURNS_HELP,
                           invuln_turns=INVULN_HELP + " (0이면 없애요)", reduce=REDUCE_HELP + " (0이면 없애요)", reduce_turns=REDUCE_TURNS_HELP)
    @app_commands.choices(tier=TIERS)
    async def potion_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                          tier: Choice[str] | None = None, weight: app_commands.Range[float, 0, 1000] | None = None,
                          heal: app_commands.Range[int, 1, 100000] | None = None, full: bool | None = None,
                          regen: app_commands.Range[int, 1, 100000] | None = None, turns: app_commands.Range[int, 1, 1000] | None = None,
                          sell_price: app_commands.Range[int, 0, SELL_MAX] | None = None, modify: str | None = None, material: bool | None = None,
                          damage: app_commands.Range[int, 0, 1000000] | None = None, dot: app_commands.Range[int, 0, 1000000] | None = None,
                          dot_turns: app_commands.Range[int, 0, 1000] | None = None, self_damage: app_commands.Range[int, 0, 100000] | None = None,
                          self_turns: app_commands.Range[int, 0, 1000] | None = None, invuln_turns: app_commands.Range[int, 0, 1000] | None = None,
                          reduce: app_commands.Range[int, 0, 100] | None = None, reduce_turns: app_commands.Range[int, 0, 1000] | None = None):
        def go():
            effects = {k: v for k, v in dict(damage=damage, dot=dot, dot_turns=dot_turns, self_damage=self_damage, self_turns=self_turns,
                                              invuln_turns=invuln_turns, reduce=reduce, reduce_turns=reduce_turns).items() if v is not None}
            cleared = {k: 0 for k in EFFECT_NAMES}      # 개조 소모품·재료는 다른 효과가 없다
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), tier=tier and tier.value, weight=weight, sell_price=sell_price)
            changes = {k: v for k, v in changes.items() if v is not None}
            changes.update(effects)
            if any(effects.values()) and not material and not modify:     # 새 효과를 정하면 개조 소모품·재료였던 것도 일반 소모품이 된다
                changes.update(modify=None, material=False)
            if material:                                # 개조 재료로 바꾼다: 효과는 모두 지운다.
                if heal is not None or full or regen is not None or turns is not None or modify or any(effects.values()):
                    raise content.ContentError("개조 재료는 효과가 없어요. 회복·피해·개조무기 같은 옵션과 함께 쓸 수 없어요.")
                changes.update(material=True, modify=None, full=False, heal=0, regen=0, regen_turns=0, **cleared)
            elif modify:                                # 개조 소모품으로 바꾼다: 다른 효과는 지운다.
                if heal is not None or full or regen is not None or turns is not None or any(effects.values()):
                    raise content.ContentError("개조 소모품은 다른 효과와 함께 쓸 수 없어요. 개조무기만 적어 주세요.")
                changes.update(modify=self._weapon_key(modify, interaction.guild_id), material=False, full=False, heal=0, regen=0, regen_turns=0, **cleared)
            elif full:                                  # 효과는 하나만: 새 효과를 정하면 이전 효과를 지운다.
                changes.update(full=True, heal=0, regen=0, regen_turns=0, modify=None, material=False)
            elif regen is not None:
                changes.update(full=False, heal=0, regen=regen, modify=None, material=False)
                if turns is not None:
                    changes["regen_turns"] = turns
            elif heal is not None:
                changes.update(full=False, heal=heal, regen=0, regen_turns=0, modify=None, material=False)
            elif turns is not None:
                changes["regen_turns"] = turns
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("consumables",), target, changes)
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "✏️ 고쳤어요.")

    @potion.command(name="삭제", description="소모품을 지워요 (관리자)")
    @app_commands.rename(target="대상")
    async def potion_delete(self, interaction: discord.Interaction, target: str):
        await self._run(interaction, lambda: content.delete(self.bot.store, interaction.guild_id, ("consumables",), target), "🗑️ 지웠어요.")

    @potion.command(name="층", description="이 소모품이 상자에서 나올 층을 정해요. 여러 층은 쉼표로(3,5,8-10). 비우면 지금 설정을 봐요 (관리자)")
    @app_commands.rename(target="대상", floors="층")
    @app_commands.describe(target="층을 정할 소모품", floors=FLOORS_HELP)
    async def potion_floors(self, interaction: discord.Interaction, target: str, floors: str | None = None):
        await self._floors(interaction, ("consumables",), "/소모품 층", "소모품이", target, floors, lambda section: "소모품", "그 층의 상자에서 소모품이 나올 때")

    @potion.command(name="대량삭제", description="소모품을 여러 개 골라 한 번에 지워요 (관리자)")
    async def potion_bulk(self, interaction: discord.Interaction):
        await self._bulk(interaction, "소모품", (("consumables", ""),))

    @potion_edit.autocomplete("target")
    @potion_delete.autocomplete("target")
    @potion_floors.autocomplete("target")
    async def potion_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("consumables",), current) if interaction.guild_id else []

    def _weapon_key(self, name: str, guild_id: int) -> str:
        """무기 이름 → key. 무기(방패 아님)가 아니면 ContentError."""
        hit = content.find(self.bot.store, guild_id, ("gear",), name)
        if hit is None or hit[1]["slot"] != "weapon":
            raise content.ContentError("개조무기는 이미 있는 무기 이름이어야 해요(자동완성에서 골라 주세요). 방패는 안 돼요.")
        return hit[1]["key"]

    @potion_add.autocomplete("modify")
    @potion_edit.autocomplete("modify")
    async def weapon_names(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id:
            return []
        needle = current.strip().lower()
        return [Choice(name=f"{d['emoji']} {d['name']}"[:100], value=d["name"]) for d in content.current(self.bot.store, interaction.guild_id, "gear")
                if d["slot"] == "weapon" and needle in d["name"].lower()][:25]

    # ---------------------------------------------------------------- 시크릿 보스
    secret = app_commands.Group(name="시크릿보스", description="보스를 이기고 올라갈 때 가끔 열리는 11번째 방의 시크릿 보스 (관리자)", **PERMS)

    def _gear_key(self, name: str, guild_id: int) -> str:
        hit = content.find(self.bot.store, guild_id, ("gear",), name)
        if hit is None:
            raise content.ContentError("드롭 장비가 없어요. 이름은 자동완성에서 골라 주세요.")
        return hit[1]["key"]

    def _item_key(self, name: str, guild_id: int) -> str:
        hit = content.find(self.bot.store, guild_id, ("consumables",), name)
        if hit is None:
            raise content.ContentError("드롭 아이템이 없어요. 이름은 자동완성에서 골라 주세요.")
        return hit[1]["key"]

    @secret.command(name="추가", description="시크릿 보스를 만들어요. 장비나 아이템 드롭을 하나는 정해야 해요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", hp="체력", damage="공격", drop_gear="드롭장비", gear_chance="장비확률", drop_item="드롭아이템",
                         item_chance="아이템확률", amount="아이템개수", weight="등장", intro="등장문구", image="그림")
    @app_commands.describe(drop_gear="떨어뜨릴 장비(자동완성)", gear_chance=CHANCE_HELP, drop_item="떨어뜨릴 소모품(자동완성)", item_chance=CHANCE_HELP,
                           amount="아이템을 몇 개 떨어뜨릴까요(기본 1)", weight="시크릿 보스가 여럿일 때 나올 비율. 소수도 돼요(기본 1)",
                           intro="등장 연출에 나올 한마디(200자까지)", image=IMAGE_HELP)
    async def secret_add(self, interaction: discord.Interaction, name: str, emoji: str, hp: app_commands.Range[int, 1, 1000000],
                         damage: app_commands.Range[int, 0, 100000], drop_gear: str | None = None,
                         gear_chance: app_commands.Range[float, 0, 100] = 100, drop_item: str | None = None,
                         item_chance: app_commands.Range[float, 0, 100] = 100, amount: app_commands.Range[int, 1, 99] = 1,
                         weight: app_commands.Range[float, 0, 1000] = 1, intro: str = "", image: str = ""):
        def go():
            data = dict(name=name, emoji=emoji.strip(), hp=hp, damage=damage, drop_gear_chance=gear_chance, drop_item_chance=item_chance,
                        drop_item_amount=amount, weight=weight, intro=intro, image=image.strip())
            data["drop_gear"] = self._gear_key(drop_gear, interaction.guild_id) if drop_gear else None
            data["drop_item"] = self._item_key(drop_item, interaction.guild_id) if drop_item else None
            entity = content.add(self.bot.store, interaction.guild_id, "secrets", data)
            return "secrets", self._entity_dict(entity)
        await self._run(interaction, go, "➕ 시크릿 보스를 만들었어요.")

    @secret.command(name="확률", description="시크릿 방이 열릴 확률(%)을 바꿔요. 값을 비우면 지금 확률을 보여줘요 (관리자)")
    @app_commands.rename(percent="퍼센트")
    @app_commands.describe(percent="보스를 이길 때마다 시크릿 방이 열릴 확률(%). 소수도 돼요(예: 2.5). 0이면 안 열려요")
    async def secret_chance(self, interaction: discord.Interaction, percent: app_commands.Range[float, 0, 100] | None = None):
        if not await require_admin(interaction):
            return
        store, gid, key = self.bot.store, interaction.guild_id, "일반.시크릿확률"
        param = balance.PARAMS[key]
        overrides = dict(store.settings(gid)["dungeon"])
        old = overrides.get(key, param.default)
        floors = store.balance(gid).floors

        def per_run(value):
            return f"보스를 이길 때마다 이 확률로 열려요. {floors}층을 모두 오르면 한 판에 한 번 이상 열릴 확률은 약 {(1 - (1 - value / 100) ** floors) * 100:.0f}%예요."
        if percent is None:
            await interaction.response.send_message(embed=theme.notice(
                f"🌌 지금 시크릿 방 확률은 **{old:g}%**예요 (기본 {param.default:g}%).\n{per_run(old)}\n`/시크릿보스 확률 퍼센트:`로 바꿔요."), ephemeral=False)
            return
        new, problem = balance.check_value(key, percent)
        if problem:
            await self._deny(interaction, problem)
            return
        if new == old:
            await self._deny(interaction, f"시크릿 방 확률은 이미 {old:g}%예요.")
            return
        if new == param.default:
            overrides.pop(key, None)                       # 기본값으로 돌아오면 기록을 지운다.
        else:
            overrides[key] = new
        problem = balance.validate(overrides, store.settings(gid)["dungeon_content"])
        if problem:
            await self._deny(interaction, problem)
            return
        store.update_settings(gid, dungeon=overrides)
        extra = "" if new > 0 else "\n⚠️ 0%라서 시크릿 방이 열리지 않아요."
        await interaction.response.send_message(embed=theme.notice(
            f"🌌 시크릿 방 확률: {old:g}% → **{new:g}%** (기본 {param.default:g}%)\n{per_run(new)}{extra}\n지금 진행 중인 던전에도 바로 적용돼요."), ephemeral=False)

    @secret.command(name="수정", description="시크릿 보스의 값을 고쳐요. 바꿀 것만 적으세요. 드롭장비·드롭아이템·등장문구·그림은 -를 적으면 지워요 (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", hp="체력", damage="공격", drop_gear="드롭장비", gear_chance="장비확률",
                         drop_item="드롭아이템", item_chance="아이템확률", amount="아이템개수", weight="등장", intro="등장문구", image="그림")
    @app_commands.describe(drop_gear="떨어뜨릴 장비(자동완성). -는 지움", gear_chance=CHANCE_HELP, drop_item="떨어뜨릴 소모품(자동완성). -는 지움",
                           item_chance=CHANCE_HELP, intro="등장 연출에 나올 한마디. -는 지움", image=IMAGE_HELP + " -는 지움")
    async def secret_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                          hp: app_commands.Range[int, 1, 1000000] | None = None, damage: app_commands.Range[int, 0, 100000] | None = None,
                          drop_gear: str | None = None, gear_chance: app_commands.Range[float, 0, 100] | None = None,
                          drop_item: str | None = None, item_chance: app_commands.Range[float, 0, 100] | None = None,
                          amount: app_commands.Range[int, 1, 99] | None = None, weight: app_commands.Range[float, 0, 1000] | None = None,
                          intro: str | None = None, image: str | None = None):
        def go():
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), hp=hp, damage=damage, drop_gear_chance=gear_chance,
                           drop_item_chance=item_chance, drop_item_amount=amount, weight=weight)
            changes = {k: v for k, v in changes.items() if v is not None}
            if drop_gear is not None:
                changes["drop_gear"] = None if drop_gear.strip() == "-" else self._gear_key(drop_gear, interaction.guild_id)
            if drop_item is not None:
                changes["drop_item"] = None if drop_item.strip() == "-" else self._item_key(drop_item, interaction.guild_id)
            if intro is not None:
                changes["intro"] = "" if intro.strip() == "-" else intro
            if image is not None:
                changes["image"] = "" if image.strip() == "-" else image.strip()
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("secrets",), target, changes)
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "✏️ 고쳤어요.")

    @secret.command(name="삭제", description="시크릿 보스를 지워요. 모두 지우면 시크릿 방이 생기지 않아요 (관리자)")
    @app_commands.rename(target="대상")
    async def secret_delete(self, interaction: discord.Interaction, target: str):
        await self._run(interaction, lambda: content.delete(self.bot.store, interaction.guild_id, ("secrets",), target), "🗑️ 지웠어요.")

    @secret.command(name="층", description="이 시크릿 보스가 나올 층을 정해요. 여러 층은 쉼표로(3,5,8-10). 비우면 지금 설정을 봐요 (관리자)")
    @app_commands.rename(target="대상", floors="층")
    @app_commands.describe(target="층을 정할 시크릿 보스", floors="나올 층: 3,5,8-10처럼 여러 층을 쉼표로, 범위는 -로. '전체'를 쓰면 모든 층. 비우면 지금 설정만 봐요")
    async def secret_floors(self, interaction: discord.Interaction, target: str, floors: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        hit = content.find(store, gid, ("secrets",), target)
        if hit is None:
            await self._deny(interaction, "그런 시크릿 보스가 없어요. 이름은 자동완성에서 골라 주세요.")
            return
        data = hit[1]
        if floors is None:
            await interaction.response.send_message(embed=theme.notice(
                f"🌌 **{data['name']}**은(는) **{balance.floors_text(data['floors'])}**에서 나와요.\n`/시크릿보스 층 대상:{data['name']} 층:3,5,8-10`처럼 바꿔요."), ephemeral=False)
            return
        chosen, problem = balance.parse_floors(floors)
        if problem:
            await self._deny(interaction, problem)
            return
        try:
            section, entity = content.edit(store, gid, ("secrets",), data["name"], {"floors": chosen})
        except content.ContentError as exc:
            await self._deny(interaction, str(exc))
            return
        bal = store.balance(gid)
        notes = []
        if chosen and min(chosen) > bal.floors and not bal.infinite:
            notes.append(f"⚠️ 지금은 {bal.floors}층까지만 있고 무한모드도 꺼져 있어서(`일반.무한모드`) 이 층들에서는 나올 수 없어요.")
        elif chosen and max(chosen) > bal.floors:
            notes.append(f"ℹ️ {bal.floors + 1}층부터는 무한모드를 골라야 갈 수 있어요.")
        if bal.secret_chance == 0:
            notes.append("⚠️ 시크릿 방 확률이 0%라 어디서도 나오지 않아요(`/시크릿보스 확률`).")
        await interaction.response.send_message(embed=theme.notice(
            f"🌌 **{entity.name}**이(가) 나올 층: **{balance.floors_text(entity.floors)}**\n"
            "그 층의 보스를 이기고 올라갈 때, 시크릿 방이 열리면(확률은 `/시크릿보스 확률`) 이 보스가 나와요."
            + ("".join("\n" + n for n in notes))), ephemeral=False)

    @secret_edit.autocomplete("target")
    @secret_delete.autocomplete("target")
    @secret_floors.autocomplete("target")
    async def secret_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("secrets",), current) if interaction.guild_id else []

    @secret.command(name="대량삭제", description="시크릿 보스를 여러 개 골라 한 번에 지워요 (관리자)")
    async def secret_bulk(self, interaction: discord.Interaction):
        await self._bulk(interaction, "시크릿 보스", (("secrets", ""),))

    @secret_add.autocomplete("drop_gear")
    @secret_edit.autocomplete("drop_gear")
    async def secret_gear_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("gear",), current) if interaction.guild_id else []

    @secret_add.autocomplete("drop_item")
    @secret_edit.autocomplete("drop_item")
    async def secret_item_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("consumables",), current) if interaction.guild_id else []

    # ---------------------------------------------------------------- 커스텀 방 · 함정
    room = app_commands.Group(name="방", description="커스텀 방을 만들고, 함정 방의 확률·피해를 정해요 (관리자)", **PERMS)

    @staticmethod
    def _numbers(text: str | None, what: str):
        """'3,5,8-9'처럼 쓴 방 번호·층 목록 → 정렬된 목록. 전체/비움은 []. 잘못되면 ContentError."""
        listed, problem = balance.parse_floors(text if text is not None else "전체")
        if problem:
            raise content.ContentError(problem.replace("층", what) if what != "층" else problem)
        return listed

    def _room_item_key(self, name: str, guild_id: int) -> str:
        return self._consumable(name, guild_id)["key"]

    @room.command(name="추가", description="커스텀 방을 만들어요. 방 배치에서 다른 방과 가중치로 겨뤄 나오고, 들어서면 효과가 일어나요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", text="문구", weight="가중치", damage="피해", heal="회복", candy="재화", item="소모품",
                         amount="소모품개수", places="방번호", floors="층")
    @app_commands.describe(text="방에 들어서면 보이는 문구(300자까지)", weight=ROOM_WEIGHT_HELP, damage="들어서면 입는 피해(갑옷·피해 감소·무적은 적용, 가드는 못 써요)",
                           heal="들어서면 회복하는 HP", candy="들어서면 얻는 재화", item="들어서면 얻는 소모품(자동완성)", amount="소모품 개수(기본 1)",
                           places=ROOM_PLACES_HELP, floors=FLOORS_HELP)
    async def room_add(self, interaction: discord.Interaction, name: str, emoji: str, text: str | None = None,
                       weight: app_commands.Range[float, 0, 1000] = 10, damage: app_commands.Range[int, 0, 100000] = 0,
                       heal: app_commands.Range[int, 0, 100000] = 0, candy: app_commands.Range[int, 0, 1000000] = 0, item: str | None = None,
                       amount: app_commands.Range[int, 1, 99] = 1, places: str | None = None, floors: str | None = None):
        def go():
            data = dict(name=name, emoji=emoji.strip(), text=text or "", weight=weight, damage=damage, heal=heal, candy=candy,
                        item_amount=amount, rooms=self._numbers(places, "방"), floors=self._numbers(floors, "층"))
            if item:
                data["item"] = self._room_item_key(item, interaction.guild_id)
            entity = content.add(self.bot.store, interaction.guild_id, "rooms", data)
            return "rooms", self._entity_dict(entity)
        await self._run(interaction, go, "➕ 커스텀 방을 만들었어요. 방 배치에 섞여서 나와요.")

    @room.command(name="수정", description="커스텀 방을 고쳐요. 바꿀 것만 적으세요. 문구·소모품은 -를 적으면 지워요 (관리자)")
    @app_commands.rename(target="대상", new_name="새이름", emoji="이모지", text="문구", weight="가중치", damage="피해", heal="회복", candy="재화",
                         item="소모품", amount="소모품개수", places="방번호", floors="층")
    @app_commands.describe(text="방 문구. -는 지움", weight=ROOM_WEIGHT_HELP, item="들어서면 얻는 소모품(자동완성). -는 지움",
                           damage="입는 피해(0이면 없앰)", heal="회복(0이면 없앰)", candy="재화(0이면 없앰)", places=ROOM_PLACES_HELP, floors=FLOORS_HELP)
    async def room_edit(self, interaction: discord.Interaction, target: str, new_name: str | None = None, emoji: str | None = None,
                        text: str | None = None, weight: app_commands.Range[float, 0, 1000] | None = None,
                        damage: app_commands.Range[int, 0, 100000] | None = None, heal: app_commands.Range[int, 0, 100000] | None = None,
                        candy: app_commands.Range[int, 0, 1000000] | None = None, item: str | None = None,
                        amount: app_commands.Range[int, 1, 99] | None = None, places: str | None = None, floors: str | None = None):
        def go():
            changes = dict(name=new_name, emoji=emoji and emoji.strip(), weight=weight, damage=damage, heal=heal, candy=candy, item_amount=amount)
            changes = {k: v for k, v in changes.items() if v is not None}
            if text is not None:
                changes["text"] = "" if text.strip() == "-" else text
            if item is not None:
                changes["item"] = None if item.strip() == "-" else self._room_item_key(item, interaction.guild_id)
            if places is not None:
                changes["rooms"] = self._numbers(places, "방")
            if floors is not None:
                changes["floors"] = self._numbers(floors, "층")
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("rooms",), target, changes)
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "✏️ 고쳤어요.")

    @room.command(name="삭제", description="커스텀 방을 지워요 (관리자)")
    @app_commands.rename(target="대상")
    async def room_delete(self, interaction: discord.Interaction, target: str):
        await self._run(interaction, lambda: content.delete(self.bot.store, interaction.guild_id, ("rooms",), target), "🗑️ 지웠어요.")

    @room.command(name="대량삭제", description="커스텀 방을 여러 개 골라 한 번에 지워요 (관리자)")
    async def room_bulk(self, interaction: discord.Interaction):
        await self._bulk(interaction, "커스텀 방", (("rooms", ""),))

    @room_edit.autocomplete("target")
    @room_delete.autocomplete("target")
    async def room_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("rooms",), current) if interaction.guild_id else []

    @room_add.autocomplete("item")
    @room_edit.autocomplete("item")
    async def room_item_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("consumables",), current) if interaction.guild_id else []

    TRAP_ROOMS = (2, 4, 6, 8)                  # 기본 방 배치에서 빈 방·잡몹이 섞여 나오는 방(함정이 섞일 수 있는 방)

    def _trap_percent(self, bal, room: int) -> float:
        table = dict(bal.room_plan[room - 1])
        total = sum(table.values())
        return 100 * table.get("trap", 0) / total if total else 0.0

    @room.command(name="함정확률", description="함정 방이 나올 확률(%)을 정해요. 비우면 지금 확률을 봐요 (관리자)")
    @app_commands.rename(percent="퍼센트")
    @app_commands.describe(percent="2·4·6·8번째 방에서 함정 방이 나올 확률(%). 0이면 함정이 안 나와요. 방마다 따로 정하려면 /던전설정 방N.함정")
    async def trap_chance(self, interaction: discord.Interaction, percent: app_commands.Range[float, 0, 90] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        bal = store.balance(gid)
        if percent is None:
            shown = ", ".join(f"{r}번째 {self._trap_percent(bal, r):.1f}%" for r in self.TRAP_ROOMS)
            await interaction.response.send_message(embed=theme.notice(
                f"🪤 지금 함정 방 확률: {shown}\n피해는 `/방 함정피해`(지금 {bal.trap_damage:,}, 1층 기준)예요. `/방 함정확률 퍼센트:10`처럼 바꿔요."), ephemeral=False)
            return
        overrides = dict(store.settings(gid)["dungeon"])
        for r in self.TRAP_ROOMS:
            others = sum(w for kind, w in bal.room_plan[r - 1] if kind != "trap")
            key = f"방{r}.함정"
            weight = round(others * percent / (100 - percent), 4) if others > 0 and percent > 0 else 0
            if weight <= 0 or weight == balance.PARAMS[key].default:
                overrides.pop(key, None)
            else:
                overrides[key] = min(weight, balance.PARAMS[key].high)
        problem = balance.validate(overrides, store.settings(gid)["dungeon_content"])
        if problem:
            await self._deny(interaction, problem)
            return
        store.update_settings(gid, dungeon=overrides)
        shown = ", ".join(f"{r}번째 {self._trap_percent(store.balance(gid), r):.1f}%" for r in self.TRAP_ROOMS)
        await interaction.response.send_message(embed=theme.notice(
            f"🪤 함정 방 확률을 바꿨어요: {shown}\n(빈 방·잡몹 등 다른 방과의 비율로 계산해요.) 지금 진행 중인 던전에도 바로 적용돼요."), ephemeral=False)

    @room.command(name="함정피해", description="함정 방에서 입는 피해를 정해요(1층 기준, 층마다 늘어나요). 비우면 지금 값을 봐요 (관리자)")
    @app_commands.rename(damage="피해")
    async def trap_damage(self, interaction: discord.Interaction, damage: app_commands.Range[int, 0, 100000] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        key, param = "함정.피해", balance.PARAMS["함정.피해"]
        overrides = dict(store.settings(gid)["dungeon"])
        old = overrides.get(key, param.default)
        if damage is None:
            await interaction.response.send_message(embed=theme.notice(
                f"🪤 함정 피해: **{old:,}** (기본 {param.default:,}, 1층 기준. 층마다 몬스터처럼 늘어나요)\n"
                "`/방 함정피해 피해:150`으로 바꿔요. 갑옷·피해 감소·무적은 적용되고 가드는 못 써요."), ephemeral=False)
            return
        if damage == param.default:
            overrides.pop(key, None)
        else:
            overrides[key] = damage
        store.update_settings(gid, dungeon=overrides)
        await interaction.response.send_message(embed=theme.notice(
            f"🪤 함정 피해: {old:,} → **{damage:,}** (기본 {param.default:,}). 함정 방이 나오게 하려면 `/방 함정확률`을 정하세요."), ephemeral=False)

    # ---------------------------------------------------------------- 장비 개조 레시피
    mod = app_commands.Group(name="장비개조", description="특정 재료를 특정 장비(무기·방패)에 쓰면 다른 장비로 바뀌는 레시피 (관리자)", **PERMS)

    @mod.command(name="추가", description="레시피를 만들어요: 장비(무기·방패)를 든 채 재료를 쓰면 결과 장비로 바뀌어요 (관리자)")
    @app_commands.rename(weapon="장비", item="재료", result="결과")
    @app_commands.describe(weapon="개조할 장비(무기나 방패. 이걸 들고 있어야 해요)", item="쓸 재료(개조 재료로 만든 소모품)",
                           result="개조된 뒤의 장비(무기는 무기로, 방패는 방패로만 돼요)")
    async def mod_add(self, interaction: discord.Interaction, weapon: str, item: str, result: str):
        if not await require_admin(interaction):
            return
        try:
            entry = content.add_mod(self.bot.store, interaction.guild_id, weapon, item, result)
        except content.ContentError as exc:
            await self._deny(interaction, str(exc))
            return
        await interaction.response.send_message(embed=theme.notice(
            f"🔧 개조 레시피를 만들었어요.\n{content.mod_text(self.bot.store, interaction.guild_id, entry)}\n"
            "플레이어가 그 장비를 든 채 던전에서 재료를 쓰면 장비가 바뀌어요."), ephemeral=False)

    @mod.command(name="삭제", description="개조 레시피를 지워요. 번호는 목록에서 골라요 (관리자)")
    @app_commands.rename(ident="레시피")
    async def mod_delete(self, interaction: discord.Interaction, ident: str):
        if not await require_admin(interaction):
            return
        try:
            entry = content.delete_mod(self.bot.store, interaction.guild_id, ident)
        except content.ContentError as exc:
            await self._deny(interaction, str(exc))
            return
        await interaction.response.send_message(embed=theme.notice(
            f"🗑️ 개조 레시피를 지웠어요.\n{content.mod_text(self.bot.store, interaction.guild_id, entry)}"), ephemeral=False)

    @mod.command(name="목록", description="개조 레시피를 봐요 (관리자)")
    async def mod_list(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        saved = content.mods(store, gid)
        usable = {(w, i) for w, i, _ in store.balance(gid).mods}
        lines = [f"`{m['id']}` {content.mod_text(store, gid, m)}" + ("" if (m["weapon"], m["item"]) in usable else " ⚠️ 쓸 수 없음(지워지거나 바뀐 항목이 있어요)")
                 for m in saved]
        pages = split_pages(lines, 3500) or ["개조 레시피가 없어요. `/소모품 추가 재료:True`로 재료를 만들고 `/장비개조 추가`로 레시피를 만들어 보세요."]
        await send_paged(interaction, interaction.user.id, len(pages), lambda i: theme.make_embed(
            f"🔧 장비 개조 레시피 {len(saved)}개" + (f" — {i + 1}/{len(pages)}쪽" if len(pages) > 1 else ""), pages[i], color=theme.PURPLE))

    def _gear_choices(self, guild_id: int, current: str) -> list:
        """무기·방패 모두(자동완성에서 구분되게 종류를 붙인다)."""
        needle = current.strip().lower()
        return [Choice(name=f"{d['emoji']} {d['name']} ({balance.SLOT_NAMES.get(d['slot'], '장비')})"[:100], value=d["name"])
                for d in content.current(self.bot.store, guild_id, "gear") if needle in d["name"].lower()][:25]

    @mod.command(name="대량삭제", description="개조 레시피를 여러 개 골라 한 번에 지워요 (관리자)")
    async def mod_bulk(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        entries = [BulkEntry(str(m["id"]), f"{m['id']}. {content.mod_text(store, gid, m)}") for m in content.mods(store, gid)]
        await send_bulk(interaction, "개조 레시피", entries, lambda ident: (content.delete_mod(store, gid, ident), "")[1], (content.ContentError,))

    @mod_add.autocomplete("weapon")
    @mod_add.autocomplete("result")
    async def mod_weapons(self, interaction: discord.Interaction, current: str):
        return self._gear_choices(interaction.guild_id, current) if interaction.guild_id else []

    @mod_add.autocomplete("item")
    async def mod_materials(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id:
            return []
        needle = current.strip().lower()
        return [Choice(name=f"{d['emoji']} {d['name']}"[:100], value=d["name"]) for d in content.current(self.bot.store, interaction.guild_id, "consumables")
                if d.get("material") and needle in d["name"].lower()][:25]

    @mod_delete.autocomplete("ident")
    async def mod_idents(self, interaction: discord.Interaction, current: str):
        if not interaction.guild_id:
            return []
        needle = current.strip().lower()
        out = []
        for m in content.mods(self.bot.store, interaction.guild_id):
            label = f"{m['id']}. {content.mod_text(self.bot.store, interaction.guild_id, m)}"
            if needle in label.lower():
                out.append(Choice(name=label[:100], value=str(m["id"])))
        return out[:25]

    # ---------------------------------------------------------------- 소모품 사용 조건
    cond = app_commands.Group(name="소모품조건", description="소모품을 쓸 수 있는 조건을 붙이고 뗘요 (관리자)", **PERMS)

    @cond.command(name="추가", description="소모품에 사용 조건을 붙여요. 붙인 조건을 전부 만족해야 쓸 수 있어요 (관리자)")
    @app_commands.rename(target="소모품", kind="조건", value="값", other="아이템")
    @app_commands.describe(target="조건을 붙일 소모품", kind=f"조건 종류. HP 조건은 최대 체력의 %, 나머지는 바로 직전 행동에서 일어난 일이에요(최대 {MAX_CONDITIONS}개)",
                           value="HP 조건의 퍼센트(0~100)", other="'아이템 획득/사용' 조건에서 지켜볼 소모품")
    @app_commands.choices(kind=COND_CHOICES)
    async def cond_add(self, interaction: discord.Interaction, target: str, kind: Choice[str], value: app_commands.Range[int, 0, 100] | None = None,
                       other: str | None = None):
        def go():
            hit = self._consumable(target, interaction.guild_id)
            if kind.value in COND_STATE:
                if value is None:
                    raise content.ContentError("HP 조건에는 '값'(0~100%)이 필요해요.")
                if other:
                    raise content.ContentError("HP 조건에는 '아이템'이 필요 없어요.")
                entry = [kind.value, value]
            elif kind.value in COND_ITEM:
                if not other:
                    raise content.ContentError("'아이템 획득/사용' 조건에는 지켜볼 '아이템'(소모품)이 필요해요.")
                if value is not None:
                    raise content.ContentError("이 조건에는 '값'이 필요 없어요.")
                watched = content.find(self.bot.store, interaction.guild_id, ("consumables",), other)
                if watched is None:
                    raise content.ContentError("그런 소모품이 없어요. 아이템은 자동완성에서 골라 주세요.")
                entry = [kind.value, watched[1]["key"]]
            else:
                if value is not None or other:
                    raise content.ContentError("이 조건에는 '값'이나 '아이템'이 필요 없어요.")
                entry = [kind.value, None]
            new = [list(c) for c in hit["conditions"]] + [entry]
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("consumables",), hit["name"], {"conditions": new})
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "➕ 사용 조건을 붙였어요.")

    @cond.command(name="삭제", description="소모품의 사용 조건 하나를 떼요. 번호는 /소모품조건 목록에서 봐요 (관리자)")
    @app_commands.rename(target="소모품", number="번호")
    async def cond_delete(self, interaction: discord.Interaction, target: str, number: app_commands.Range[int, 1, MAX_CONDITIONS]):
        def go():
            hit = self._consumable(target, interaction.guild_id)
            if number > len(hit["conditions"]):
                raise content.ContentError(f"{hit['name']}의 조건은 {len(hit['conditions'])}개예요. 번호는 `/소모품조건 목록`에서 봐요.")
            new = [list(c) for i, c in enumerate(hit["conditions"], start=1) if i != number]
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("consumables",), hit["name"], {"conditions": new})
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "➖ 사용 조건을 뗐어요.")

    @cond.command(name="비우기", description="소모품의 사용 조건을 전부 떼요. 언제나 쓸 수 있게 돼요 (관리자)")
    @app_commands.rename(target="소모품")
    async def cond_clear(self, interaction: discord.Interaction, target: str):
        def go():
            hit = self._consumable(target, interaction.guild_id)
            if not hit["conditions"]:
                raise content.ContentError(f"{hit['name']}에는 사용 조건이 없어요.")
            section, entity = content.edit(self.bot.store, interaction.guild_id, ("consumables",), hit["name"], {"conditions": []})
            return section, self._entity_dict(entity)
        await self._run(interaction, go, "🧹 사용 조건을 전부 뗐어요.")

    @cond.command(name="목록", description="조건이 붙은 소모품과 조건을 봐요 (관리자)")
    @app_commands.rename(target="소모품")
    async def cond_list(self, interaction: discord.Interaction, target: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        rows = content.current(store, gid, "consumables")
        if target:
            hit = content.find(store, gid, ("consumables",), target)
            if hit is None:
                await self._deny(interaction, "그런 소모품이 없어요. 이름은 자동완성에서 골라 주세요.")
                return
            rows = [hit[1]]
        else:
            rows = [d for d in rows if d["conditions"] or d.get("modify")]
        names = {d["key"]: d["name"] for d in content.current(store, gid, "consumables")}
        gear = {g.key: g for g in store.balance(gid).gear}
        lines = []
        for d in rows:
            target_gear = gear.get(d.get("modify"))
            effect = (f" — 🔧 {target_gear.emoji} {target_gear.name}(으)로 개조" if target_gear else " — 🔧 개조(목표 무기가 사라졌어요)") if d.get("modify") else ""
            conds = "\n".join(f"　{i}. {condition_text(c, names)}" for i, c in enumerate(d["conditions"], start=1)) or "　조건 없음(언제나 사용 가능)"
            lines.append(f"{d['emoji']} **{d['name']}**{effect}\n{conds}")
        pages = split_pages(lines, 3500) or ["조건이 붙은 소모품이 없어요. `/소모품조건 추가`로 붙여 보세요."]
        await send_paged(interaction, interaction.user.id, len(pages), lambda i: theme.make_embed(
            "🔒 소모품 사용 조건" + (f" — {i + 1}/{len(pages)}쪽" if len(pages) > 1 else ""), pages[i], color=theme.PURPLE))

    def _consumable(self, name: str, guild_id: int) -> dict:
        hit = content.find(self.bot.store, guild_id, ("consumables",), name)
        if hit is None:
            raise content.ContentError("그런 소모품이 없어요. 이름은 자동완성에서 골라 주세요.")
        return hit[1]

    @cond_add.autocomplete("target")
    @cond_add.autocomplete("other")
    @cond_delete.autocomplete("target")
    @cond_clear.autocomplete("target")
    @cond_list.autocomplete("target")
    async def cond_names(self, interaction: discord.Interaction, current: str):
        return names_for(self.bot.store, interaction.guild_id, ("consumables",), current) if interaction.guild_id else []

    # ---------------------------------------------------------------- 판매가 일괄 설정
    @app_commands.command(name="던전판매가", description="던전 장비·소모품의 판매가를 한꺼번에 바꿔요. 0이면 팔 수 없어요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(price="가격", target="대상", tier="등급")
    @app_commands.describe(price="개당 판매가(재화). 0이면 팔 수 없어요", target="어느 쪽을 바꿀까요? (비우면 둘 다)",
                           tier="이 등급만 바꿔요 (비우면 모든 등급)")
    @app_commands.choices(target=SELL_TARGETS, tier=TIERS)
    async def sell_price_bulk(self, interaction: discord.Interaction, price: app_commands.Range[int, 0, SELL_MAX],
                              target: Choice[str] | None = None, tier: Choice[str] | None = None):
        if not await require_admin(interaction):
            return
        which = target.value if target else "both"
        sections = ("gear", "consumables") if which == "both" else (which,)
        try:
            changed = content.set_sell_price(self.bot.store, interaction.guild_id, sections, price, tier.value if tier else None)
        except content.ContentError as exc:
            await self._deny(interaction, str(exc))
            return
        cur = self.bot.store.currency(interaction.guild_id)
        parts = " · ".join(f"{SECTION_LABELS[sec]} {count}개" for sec, count in changed.items())
        scope = f"{tier.value} 등급 " if tier else ""
        result = "이제 팔 수 없어요." if price == 0 else f"개당 {cur.money(price)}에 팔 수 있어요."
        await interaction.response.send_message(embed=theme.notice(
            f"💰 {scope}{parts}의 판매가를 바꿨어요. {result}\n하나씩 고치려면 `/장비 수정` `/소모품 수정`의 판매가를 쓰세요."), ephemeral=False)

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
        intro = "바꾸려면 `/몬스터` `/장비` `/소모품` `/시크릿보스`. ✏️ = 직접 편집한 분류"
        blocks = []                                        # (칸 이름, 내용): 한 칸은 1000자 안에서 줄 단위로 나눈다.
        for sec in ([section.value] if section else SECTIONS):
            lines = [content.describe(sec, d, store.balance(gid)) for d in content.current(store, gid, sec)]
            if sec == "rooms" and not lines and not section:      # 커스텀 방은 만든 게 있을 때만 전체 목록에 보여 준다
                continue
            mark = " ✏️" if sec in custom else ""
            parts = split_pages(lines, 1000) or ["없음"]
            for number, part in enumerate(parts, start=1):
                tail = f" ({number}/{len(parts)})" if len(parts) > 1 else ""
                blocks.append((f"{SECTION_LABELS[sec]} {len(lines)}개{mark}{tail}", part))
        pages = self._pack_fields(blocks)

        def build(index: int) -> discord.Embed:
            embed = theme.make_embed("🏚️ 던전 목록" + (f" — {index + 1}/{len(pages)}쪽" if len(pages) > 1 else ""), intro, color=theme.PURPLE)
            for name, value in pages[index]:
                embed.add_field(name=name, value=value, inline=False)
            return embed
        await send_paged(interaction, interaction.user.id, len(pages), build)

    @staticmethod
    def _pack_fields(blocks, max_fields: int = 5, max_chars: int = 4200) -> list[list[tuple[str, str]]]:
        """칸들을 한 임베드에 5칸·4200자까지 차례로 담는다(디스코드는 임베드 전체를 6000자까지만 받는다). 넘치면 다음 페이지."""
        pages, current, size = [], [], 0
        for name, value in blocks:
            cost = len(name) + len(value)
            if current and (len(current) >= max_fields or size + cost > max_chars):
                pages.append(current)
                current, size = [], 0
            current.append((name, value))
            size += cost
        if current:
            pages.append(current)
        return pages or [[("없음", "없음")]]

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
            view=Confirm(interaction.user.id, do, label="되돌리기", emoji="♻️"), ephemeral=False)


async def setup(bot):
    await bot.add_cog(ContentAdmin(bot))
