"""속성 상성(/속성): 관리자가 속성을 만들고, 어느 속성이 어느 속성에 강한지(피해 배율 %)를 정하고, 무기·갑옷·몬스터·보스에 붙인다.

- 내 공격: 무기 속성 → 몬스터 속성 배율. 몬스터의 공격: 몬스터 속성 → 내 갑옷 속성 배율. 정하지 않은 조합·속성이 없는 쪽은 100%.
- 방패에는 속성이 없다. 속성을 지우면 그 속성을 붙인 장비·몬스터는 무속성으로 친다(상성표에서도 빠진다).
"""
from __future__ import annotations

import secrets

import discord
from discord import app_commands
from discord.ext import commands

import content
import theme
from access import require_admin
from balance import AFFINITY_MAX, MAX_ELEMENTS
from paging import send_paged, split_pages
from store import valid_emoji

Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
TARGET_SECTIONS = ("gear", "monsters", "bosses", "secrets")
SECTION_NAMES = {"gear": "장비", "monsters": "몬스터", "bosses": "보스", "secrets": "시크릿 보스"}
NAME_MAX = 20


class ElementError(Exception):
    """사용자에게 그대로 보여 줄 문장."""


def elements(store, guild: int) -> list[dict]:
    return store.settings(guild)["elements"]


def affinity(store, guild: int) -> list[dict]:
    return store.settings(guild)["affinity"]


def find_element(store, guild: int, name: str) -> dict | None:
    needle = (name or "").strip().lower()
    return next((e for e in elements(store, guild) if e["name"].lower() == needle or e["key"] == (name or "").strip()), None)


def add_element(store, guild: int, name: str, emoji: str) -> dict:
    name, emoji = name.strip(), emoji.strip()
    saved = elements(store, guild)
    if len(saved) >= MAX_ELEMENTS:
        raise ElementError(f"속성은 {MAX_ELEMENTS}개까지예요.")
    if not 1 <= len(name) <= NAME_MAX:
        raise ElementError(f"속성 이름은 1~{NAME_MAX}자로 써 주세요.")
    if not valid_emoji(emoji):
        raise ElementError("이모지는 😀 같은 이모지 하나이거나 서버 이모지여야 해요.")
    if find_element(store, guild, name):
        raise ElementError("같은 이름의 속성이 이미 있어요.")
    keys = {e["key"] for e in saved}
    key = "el" + secrets.token_hex(3)
    while key in keys:
        key = "el" + secrets.token_hex(3)
    entry = {"key": key, "name": name, "emoji": emoji}
    store.update_settings(guild, elements=[*saved, entry])
    return entry


def delete_element(store, guild: int, name: str) -> tuple[dict, int]:
    """속성을 지우고 상성표에서 그 속성이 든 칸도 지운다. (지운 속성, 그 속성을 붙이고 있던 장비·몬스터 수)."""
    found = find_element(store, guild, name)
    if found is None:
        raise ElementError("그런 속성이 없어요. 이름은 자동완성에서 골라 주세요.")
    users = len(users_of(store, guild, found["key"]))
    store.update_settings(guild, elements=[e for e in elements(store, guild) if e["key"] != found["key"]],
                          affinity=[r for r in affinity(store, guild) if found["key"] not in (r["atk"], r["def"])])
    return found, users


def set_affinity(store, guild: int, attack: str, defend: str, percent: int) -> tuple[dict, dict, int]:
    """attack 속성이 defend 속성을 때릴 때 배율을 정한다. 100이면 칸을 지운다(기본값). (공격 속성, 방어 속성, 예전 배율)."""
    atk, dfn = find_element(store, guild, attack), find_element(store, guild, defend)
    if atk is None or dfn is None:
        raise ElementError("그런 속성이 없어요. 이름은 자동완성에서 골라 주세요.")
    if not 0 <= percent <= AFFINITY_MAX:
        raise ElementError(f"배율은 0~{AFFINITY_MAX}%여야 해요.")
    rows = affinity(store, guild)
    old = next((r["percent"] for r in rows if (r["atk"], r["def"]) == (atk["key"], dfn["key"])), 100)
    rows = [r for r in rows if (r["atk"], r["def"]) != (atk["key"], dfn["key"])]
    if percent != 100:
        rows.append({"atk": atk["key"], "def": dfn["key"], "percent": percent})
    store.update_settings(guild, affinity=rows)
    return atk, dfn, old


def users_of(store, guild: int, key: str) -> list[str]:
    """그 속성을 붙인 장비·몬스터 이름."""
    out = []
    for section in TARGET_SECTIONS:
        out += [d["name"] for d in content.current(store, guild, section) if d.get("element") == key]
    return out


def resolve_target(store, guild: int, target: str) -> tuple[str, dict]:
    """'구역:key'(자동완성 값) 또는 이름으로 장비·몬스터·보스·시크릿 보스를 찾는다."""
    text = (target or "").strip()
    if ":" in text:
        section, key = text.split(":", 1)
        if section in TARGET_SECTIONS:
            hit = next((d for d in content.current(store, guild, section) if d["key"] == key), None)
            if hit:
                return section, hit
    found = content.find(store, guild, TARGET_SECTIONS, text)
    if found is None:
        raise ElementError("그런 장비·몬스터가 없어요. 이름은 자동완성에서 골라 주세요.")
    return found


def assign(store, guild: int, target: str, element: str | None) -> tuple[str, dict, dict | None]:
    """대상에 속성을 붙인다(element가 '-'나 None이면 뗀다). (구역, 바뀐 개체 사전, 속성)."""
    section, data = resolve_target(store, guild, target)
    if section == "gear" and data["slot"] == "shield":
        raise ElementError("방패에는 속성을 붙일 수 없어요. 공격은 무기, 받는 피해는 갑옷의 속성을 따라요.")
    chosen = None
    if element and element.strip() != "-":
        chosen = find_element(store, guild, element)
        if chosen is None:
            raise ElementError("그런 속성이 없어요. 이름은 자동완성에서 골라 주세요(떼려면 -).")
    try:
        content.edit(store, guild, (section,), data["name"], {"element": chosen["key"] if chosen else None})
    except content.ContentError as exc:
        raise ElementError(str(exc))
    fresh = next(d for d in content.current(store, guild, section) if d["key"] == data["key"])
    return section, fresh, chosen


def label(entry: dict | None) -> str:
    return f"{entry['emoji']} {entry['name']}" if entry else "무속성"


def overview_lines(store, guild: int) -> list[str]:
    """목록 화면의 줄: 속성마다 이름·붙은 곳 수, 그다음 상성표."""
    saved = elements(store, guild)
    by_key = {e["key"]: e for e in saved}
    lines = [f"{label(e)} — 붙은 장비·몬스터 {len(users_of(store, guild, e['key']))}개" for e in saved]
    rows = [r for r in affinity(store, guild) if r["atk"] in by_key and r["def"] in by_key]
    if rows:
        lines.append("")
        lines.append("**상성표** (공격 → 방어: 피해 배율, 적지 않은 조합은 100%)")
        lines += [f"{label(by_key[r['atk']])} → {label(by_key[r['def']])}: **{r['percent']}%**"
                  + (" 🔺" if r["percent"] > 100 else " 🔻" if r["percent"] < 100 else "") for r in rows]
    return lines


class Elements(commands.Cog):
    """🔷 속성 상성 (관리자)."""

    group = app_commands.Group(name="속성", description="속성과 상성(피해 배율)을 만들고 무기·갑옷·몬스터·보스에 붙여요 (관리자)", **PERMS)

    def __init__(self, bot):
        self.bot = bot

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    async def _guard(self, interaction, func):
        if not await require_admin(interaction):
            return
        try:
            text = func()
        except ElementError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, text)

    @group.command(name="추가", description="속성을 새로 만들어요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지")
    @app_commands.describe(name="속성 이름(20자까지)", emoji="속성 아이콘")
    async def element_add(self, interaction: discord.Interaction, name: str, emoji: str):
        def go():
            entry = add_element(self.bot.store, interaction.guild_id, name, emoji)
            return (f"➕ 속성 {label(entry)}을(를) 만들었어요. `/속성 상성`으로 강약을 정하고 `/속성 지정`으로 장비·몬스터에 붙이세요.")
        await self._guard(interaction, go)

    @group.command(name="삭제", description="속성을 지워요. 붙어 있던 장비·몬스터는 무속성이 돼요 (관리자)")
    @app_commands.rename(target="대상")
    async def element_delete(self, interaction: discord.Interaction, target: str):
        def go():
            entry, users = delete_element(self.bot.store, interaction.guild_id, target)
            return f"🗑️ 속성 {label(entry)}을(를) 지웠어요." + (f" 붙어 있던 {users}개는 이제 무속성으로 쳐요." if users else "")
        await self._guard(interaction, go)

    @group.command(name="상성", description="공격 속성이 방어 속성을 때릴 때의 피해 배율(%)을 정해요. 100이면 기본으로 되돌려요 (관리자)")
    @app_commands.rename(attack="공격", defend="방어", percent="배율")
    @app_commands.describe(attack="때리는 쪽 속성", defend="맞는 쪽 속성", percent=f"피해 배율(%). 150이면 1.5배, 50이면 절반, 0이면 피해 없음(0~{AFFINITY_MAX})")
    async def element_affinity(self, interaction: discord.Interaction, attack: str, defend: str,
                               percent: app_commands.Range[int, 0, AFFINITY_MAX]):
        def go():
            atk, dfn, old = set_affinity(self.bot.store, interaction.guild_id, attack, defend, percent)
            tail = " (기본으로 되돌렸어요)" if percent == 100 else ""
            return f"🔷 {label(atk)} → {label(dfn)}: {old}% → **{percent}%**{tail}\n지금 진행 중인 싸움에도 바로 적용돼요."
        await self._guard(interaction, go)

    @group.command(name="지정", description="무기·갑옷·몬스터·보스에 속성을 붙이거나 떼요(-) (관리자)")
    @app_commands.rename(target="대상", element="속성")
    @app_commands.describe(target="속성을 붙일 장비·몬스터(자동완성)", element="붙일 속성(자동완성). -를 적으면 떼요")
    async def element_assign(self, interaction: discord.Interaction, target: str, element: str):
        def go():
            section, data, chosen = assign(self.bot.store, interaction.guild_id, target, element)
            where = SECTION_NAMES[section] + (f"({'무기' if data.get('slot') == 'weapon' else '갑옷'})" if section == "gear" else "")
            role = "공격 속성" if section == "gear" and data.get("slot") == "weapon" else "방어 속성" if section == "gear" else "속성"
            return f"🔷 {where} {data['emoji']} **{data['name']}**의 {role}: **{label(chosen)}**"
        await self._guard(interaction, go)

    @group.command(name="목록", description="속성과 상성표를 봐요 (관리자)")
    async def element_list(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        lines = overview_lines(self.bot.store, interaction.guild_id)
        pages = split_pages(lines, 3500) or ["속성이 없어요. `/속성 추가`로 만들어 보세요."]
        await send_paged(interaction, interaction.user.id, len(pages), lambda i: theme.make_embed(
            f"🔷 속성 {len(elements(self.bot.store, interaction.guild_id))}개" + (f" — {i + 1}/{len(pages)}쪽" if len(pages) > 1 else ""),
            pages[i], color=theme.PURPLE))

    @element_delete.autocomplete("target")
    @element_affinity.autocomplete("attack")
    @element_affinity.autocomplete("defend")
    @element_assign.autocomplete("element")
    async def element_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        out = [Choice(name=label(e)[:100], value=e["name"]) for e in elements(self.bot.store, interaction.guild_id) if needle in e["name"].lower()]
        if not needle or needle == "-":
            out.append(Choice(name="- (속성 떼기)", value="-"))
        return out[:25]

    @element_assign.autocomplete("target")
    async def target_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        store, gid = self.bot.store, interaction.guild_id
        by_key = {e["key"]: e for e in elements(store, gid)}
        needle = current.strip().lower()
        out = []
        for section in TARGET_SECTIONS:
            for d in content.current(store, gid, section):
                if section == "gear" and d["slot"] == "shield":
                    continue
                if needle in d["name"].lower():
                    kind = ("무기" if d["slot"] == "weapon" else "갑옷") if section == "gear" else SECTION_NAMES[section]
                    have = by_key.get(d.get("element"))
                    out.append(Choice(name=f"{d['emoji']} {d['name']} ({kind}{', ' + label(have) if have else ''})"[:100], value=f"{section}:{d['key']}"))
        return out[:25]


async def setup(bot):
    await bot.add_cog(Elements(bot))
