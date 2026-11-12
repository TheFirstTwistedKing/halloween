"""보관함(/보관함): 던전 밖 창고.

- 던전을 **클리어**하면 그때 들고 있던 장비(장착·예비)와 소모품이 보관함에 들어온다(dungeon_service.vault_fit).
  다운되거나 스스로 나가면 들어오지 않는다(들고 있던 것은 잃는다).
- 여기서 다음 던전에 **가져갈 것**을 고른다: 무기·방패·갑옷 종류마다 1개, 소모품은 종류마다 1개씩 최대 N종(`보관함.소모품`).
  고른 것은 다음 입장 때 보관함에서 꺼내져 바로 장착·소지된다. 클리어하면 다시 보관함으로 돌아오고, 지면 잃는다.
- 보관함 물건은 던전 판매가로 팔 수 있다.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import dungeon_service as service
import theme
from balance import TIER_ORDER, VAULT_GEAR_MAX, VAULT_ITEM_MAX
from owner import owner_only
from store import StoreError

SLOTS = (("weapon", "⚔️", "무기"), ("shield", "🛡️", "방패"), ("armor", "🧥", "갑옷"))
NONE = "__none__"
OPTION_MAX = 25                  # 선택 메뉴 하나에 담을 수 있는 항목 수(디스코드 한도)


def _tier(thing) -> int:
    return TIER_ORDER.get(thing.tier, len(TIER_ORDER))


def contents(store, guild_id: int, user_id: int, bal):
    """보관함에서 지금 던전에 있는 것만: ([(장비, 개수)], [(소모품, 개수)]). 관리자가 지운 것은 보이지 않는다(저장은 남는다)."""
    raw = store.vault(guild_id, user_id)
    gear = [(bal.gear_by_key[k], n) for k, n in raw["gear"].items() if k in bal.gear_by_key]
    items = [(bal.item_by_key[k], n) for k, n in raw["item"].items() if k in bal.item_by_key]
    gear.sort(key=lambda p: (_tier(p[0]), p[0].name))
    items.sort(key=lambda p: (_tier(p[0]), p[0].name))
    return gear, items


def vault_embed(store, guild_id: int, user_id: int, bal, cur=None, note: str | None = None) -> discord.Embed:
    gear, items = contents(store, guild_id, user_id, bal)
    _gear, _items, _take, brought = service.loadout_plan(store, guild_id, user_id, bal)
    lines = [note, ""] if note else []
    lines.append("던전을 **클리어**하면 들고 있던 장비·소모품이 여기 들어와요. 아래에서 **다음 던전에 가져갈 것**을 골라요.")
    lines.append(f"(장비는 종류마다 1개, 소모품은 종류마다 1개씩 **최대 {bal.vault_items}종**. 클리어하면 돌아오고, 다운되거나 나가면 잃어요)")
    embed = theme.make_embed("📦 보관함", "\n".join(lines), color=theme.PURPLE)
    total = sum(n for _g, n in gear)
    for slot, emoji, label in SLOTS:
        rows = [f"{g.emoji} {g.name} ({g.tier})" + (f" ×{n}" if n > 1 else "") for g, n in gear if g.slot == slot]
        if rows:
            embed.add_field(name=f"{emoji} {label}", value="\n".join(rows)[:1024], inline=True)
    if not gear:
        embed.add_field(name="🎒 장비", value="비어 있어요", inline=True)
    embed.add_field(name="🧪 소모품", inline=False,
                    value=(" · ".join(f"{c.emoji} {c.name} ×{n}" for c, n in items) or "비어 있어요")[:1024])
    embed.add_field(name="🚪 다음 던전에 가져갈 것", inline=False, value=", ".join(brought) or "없음")
    embed.set_footer(text=f"장비 {total}/{VAULT_GEAR_MAX}개 · 소모품은 종류마다 {VAULT_ITEM_MAX}개까지 · 넘치면 버려져요")
    return embed


class VaultView(discord.ui.View):
    """보관함 화면: 가져갈 장비(종류마다 하나)와 소모품을 고르고, 팔기 화면으로 간다. 연 사람만 누를 수 있다."""

    def __init__(self, client, user_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.client, self.user_id, self.guild_id = client, user_id, guild_id
        store = client.store
        bal = store.balance(guild_id)
        gear, items = contents(store, guild_id, user_id, bal)
        plan = store.loadout(guild_id, user_id)
        for row, (slot, emoji, label) in enumerate(SLOTS):
            choices = [g for g, _n in gear if g.slot == slot][:OPTION_MAX - 1]
            if not choices:
                continue
            options = [discord.SelectOption(label=f"{label} 가져가지 않기", value=NONE, emoji="✖️", default=plan[slot] not in {g.key for g in choices})]
            options += [discord.SelectOption(label=f"{g.name} ({g.tier})"[:100], value=g.key, emoji=g.emoji, description=g.description[:100] or None,
                                             default=plan[slot] == g.key) for g in choices]
            select = discord.ui.Select(placeholder=f"{emoji} 가져갈 {label}", options=options, row=row)
            select.callback = self._slot_callback(slot)
            self.add_item(select)
        kinds = [c for c, _n in items][:OPTION_MAX]
        if kinds and bal.vault_items > 0:
            picked = [k for k in plan["items"] if k in {c.key for c in kinds}]
            options = [discord.SelectOption(label=c.name[:100], value=c.key, emoji=c.emoji, description=c.description[:100] or None,
                                            default=c.key in picked) for c in kinds]
            select = discord.ui.Select(placeholder=f"🧪 가져갈 소모품 (최대 {bal.vault_items}종, 안 고르면 없음)", options=options, row=3,
                                       min_values=0, max_values=min(bal.vault_items, len(options)))
            select.callback = self.on_items
            self.add_item(select)
        if any(g.sell_price > 0 for g, _n in gear) or any(c.sell_price > 0 for c, _n in items):
            self._button("팔기", "💰", self.open_sell, discord.ButtonStyle.primary)
        if any(plan[slot] for slot, _e, _l in SLOTS) or plan["items"]:
            self._button("아무것도 안 가져가기", "✖️", self.clear, discord.ButtonStyle.secondary)

    def _button(self, label, emoji, callback, style, row=4):
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row)
        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def _redraw(self, interaction: discord.Interaction, note: str | None = None):
        store = self.client.store
        await interaction.response.edit_message(embed=vault_embed(store, self.guild_id, self.user_id, store.balance(self.guild_id),
                                                                  store.currency(self.guild_id), note),
                                                view=VaultView(self.client, self.user_id, self.guild_id))

    def _save(self, **changes):
        plan = self.client.store.loadout(self.guild_id, self.user_id)
        plan.update(changes)
        self.client.store.set_loadout(self.guild_id, self.user_id, plan)

    def _slot_callback(self, slot: str):
        async def callback(interaction: discord.Interaction):
            value = (interaction.data.get("values") or [NONE])[0]
            self._save(**{slot: None if value == NONE else value})
            await self._redraw(interaction)
        return callback

    async def on_items(self, interaction: discord.Interaction):
        self._save(items=list(interaction.data.get("values", [])))
        await self._redraw(interaction)

    async def clear(self, interaction: discord.Interaction):
        self._save(weapon=None, shield=None, armor=None, items=[])
        await self._redraw(interaction, "✖️ 다음 던전에는 보관함에서 아무것도 가져가지 않아요.")

    async def open_sell(self, interaction: discord.Interaction):
        store = self.client.store
        await interaction.response.edit_message(embed=sell_embed(store, self.guild_id, self.user_id),
                                                view=VaultSellView(self.client, self.user_id, self.guild_id))


def sellable(store, guild_id: int, user_id: int, bal) -> list[dict]:
    """보관함에서 팔 수 있는 것(판매가 1 이상). id는 'gear:key' / 'item:key'."""
    gear, items = contents(store, guild_id, user_id, bal)
    out = [{"id": f"gear:{g.key}", "kind": "gear", "key": g.key, "thing": g, "qty": n} for g, n in gear if g.sell_price > 0]
    out += [{"id": f"item:{c.key}", "kind": "item", "key": c.key, "thing": c, "qty": n} for c, n in items if c.sell_price > 0]
    return out


def sell_embed(store, guild_id: int, user_id: int, note: str | None = None) -> discord.Embed:
    cur = store.currency(guild_id)
    entries = sellable(store, guild_id, user_id, store.balance(guild_id))
    lines = ([note, ""] if note else []) + ["팔 것을 골라서 **1개씩** 또는 **고른 것 전부**를 팔아요. 다음 던전에 가져가려고 고른 것도 팔 수 있어요."]
    lines += [f"{e['thing'].emoji} {e['thing'].name} ×{e['qty']} — 개당 {theme.money(e['thing'].sell_price, cur)}" for e in entries[:OPTION_MAX]]
    if not entries:
        lines.append("팔 수 있는 것이 없어요.")
    embed = theme.make_embed("💰 보관함에서 팔기", "\n".join(lines)[:4000], color=theme.GOLD)
    embed.set_footer(text=f"가진 재화 {cur.money(store.wallet(guild_id, user_id))}")
    return embed


class VaultSellView(discord.ui.View):
    """보관함 팔기 화면. 연 사람만 누를 수 있다."""

    def __init__(self, client, user_id: int, guild_id: int, picks=()):
        super().__init__(timeout=900)
        self.client, self.user_id, self.guild_id = client, user_id, guild_id
        store = client.store
        cur = store.currency(guild_id)
        entries = sellable(store, guild_id, user_id, store.balance(guild_id))[:OPTION_MAX]
        known = {e["id"] for e in entries}
        self.picks = [p for p in picks if p in known]
        if entries:
            options = [discord.SelectOption(label=f"{e['thing'].name} ×{e['qty']}"[:100], value=e["id"], emoji=e["thing"].emoji,
                                            description=f"개당 {theme.plain_money(e['thing'].sell_price, cur)}"[:100], default=e["id"] in self.picks)
                       for e in entries]
            select = discord.ui.Select(placeholder="💰 팔 것을 골라요 (여러 개 가능)", options=options, min_values=0, max_values=len(options), row=0)
            select.callback = self.on_select
            self.add_item(select)
            self._button("1개씩 팔기", "☝️", self.sell_one, discord.ButtonStyle.primary)
            self._button("고른 것 전부 팔기", "💰", self.sell_all, discord.ButtonStyle.success)
        self._button("돌아가기", "↩️", self.back, discord.ButtonStyle.secondary)

    def _button(self, label, emoji, callback, style, row=1):
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row)
        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def on_select(self, interaction: discord.Interaction):
        self.picks = list(interaction.data.get("values", []))
        await interaction.response.defer()

    async def _redraw(self, interaction, note):
        await interaction.response.edit_message(embed=sell_embed(self.client.store, self.guild_id, self.user_id, note),
                                                view=VaultSellView(self.client, self.user_id, self.guild_id))

    async def _sell(self, interaction: discord.Interaction, every: bool):
        if not self.picks:
            await self._redraw(interaction, "⚠️ 먼저 위 목록에서 팔 것을 골라 주세요.")
            return
        store = self.client.store
        async with self.client.locks.get(self.guild_id, self.user_id):       # 던전 입장(보관함에서 꺼내기)과 겹치지 않게
            bal, cur = store.balance(self.guild_id), store.currency(self.guild_id)
            by_id = {e["id"]: e for e in sellable(store, self.guild_id, self.user_id, bal)}
            chosen = [by_id[p] for p in self.picks if p in by_id]
            entries = [(e["kind"], e["key"], e["qty"] if every else 1) for e in chosen]
            total = sum(e["thing"].sell_price * qty for e, (_k, _key, qty) in zip(chosen, entries))
            try:
                if not entries:
                    raise StoreError("고른 것이 보관함에 없어요. 화면을 다시 열어 주세요.")
                store.vault_sell(self.guild_id, self.user_id, entries, total)
            except StoreError as exc:
                note = f"⚠️ {exc}"
            else:
                note = f"💰 {sum(q for _k, _key, q in entries)}개를 팔고 {cur.money(total)}을(를) 받았어요."
        await self._redraw(interaction, note)

    async def sell_one(self, interaction: discord.Interaction):
        await self._sell(interaction, False)

    async def sell_all(self, interaction: discord.Interaction):
        await self._sell(interaction, True)

    async def back(self, interaction: discord.Interaction):
        store = self.client.store
        await interaction.response.edit_message(embed=vault_embed(store, self.guild_id, self.user_id, store.balance(self.guild_id),
                                                                  store.currency(self.guild_id)),
                                                view=VaultView(self.client, self.user_id, self.guild_id))


class Vault(commands.Cog):
    """📦 보관함."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="보관함", description="던전을 클리어하고 가져온 장비·소모품을 보고, 다음 던전에 가져갈 것을 골라요")
    @app_commands.guild_only()
    async def vault(self, interaction: discord.Interaction):
        store, gid, uid = self.bot.store, interaction.guild_id, interaction.user.id
        await interaction.response.send_message(embed=vault_embed(store, gid, uid, store.balance(gid), store.currency(gid)),
                                                view=VaultView(self.bot, uid, gid), ephemeral=False)


async def setup(bot):
    await bot.add_cog(Vault(bot))
