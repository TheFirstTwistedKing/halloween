"""기지(/기지): 보관함의 장비를 종류마다(무기·방패·갑옷) 하나씩 전시한다.

- 전시한 장비는 보관함에서 빠지므로 **던전에 가져갈 수 없다**. 대신 능력치의 일부(기본 10%, `기지.패시브`)가
  던전에서 늘 더해진다: 무기는 공격력, 방패는 가드(피해 감소)·반사, 갑옷은 피해 감소. 던전에 들어갈 때 정해진다.
- 전시를 바꾸거나 비우면 원래 전시하던 장비는 보관함으로 돌아간다(보관함이 가득 차 있으면 거절).
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import dungeon_service as service
import theme
import workshop
from balance import base_bonus
from cogs.vault import SLOTS, contents
from owner import owner_only
from store import StoreError

EMPTY = "__empty__"
OPTION_MAX = 25


def base_embed(store, guild_id: int, user_id: int, note: str | None = None) -> discord.Embed:
    bal = store.balance(guild_id)
    shown = store.base(guild_id, user_id)
    lines = ([note, ""] if note else []) + [
        "보관함의 장비를 **종류마다 하나씩 전시**해요. 전시한 장비는 **던전에 가져갈 수 없지만**,",
        f"능력치의 **{bal.base_passive}%** 가 던전에서 늘 더해져요(무기 공격력 · 방패 가드·반사 · 갑옷 피해 감소). 던전에 들어갈 때 정해져요."]
    embed = theme.make_embed("🏠 기지", "\n".join(lines), color=theme.ORANGE)
    for slot, emoji, label in SLOTS:
        gear = bal.gear_by_key.get(shown.get(slot))
        if gear is not None and gear.slot == slot:
            one = base_bonus({slot: gear.key}, bal)
            value = f"{gear.emoji} **{gear.name}** ({gear.tier})\n패시브: {service.passive_text(one)}"
        else:
            value = "비어 있어요"
        embed.add_field(name=f"{emoji} {label} 전시대", value=value, inline=True)
    embed.add_field(name="🏠 던전에서 받는 패시브", value=service.passive_text(base_bonus(shown, bal)), inline=False)
    return embed


class BaseView(discord.ui.View):
    """기지 화면: 칸마다 보관함의 장비를 골라 전시하거나 비운다. 연 사람만 누를 수 있다."""

    def __init__(self, client, user_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.client, self.user_id, self.guild_id = client, user_id, guild_id
        store = client.store
        bal = store.balance(guild_id)
        shown = store.base(guild_id, user_id)
        gear, _items = contents(store, guild_id, user_id, bal)
        for row, (slot, emoji, label) in enumerate(SLOTS):
            current = bal.gear_by_key.get(shown.get(slot))
            choices = [g for g, _n in gear if g.slot == slot and (current is None or g.key != current.key)][:OPTION_MAX - 2]
            options = []
            if current is not None:
                options.append(discord.SelectOption(label=f"전시 중: {current.name}"[:100], value=current.key, emoji=current.emoji, default=True))
                options.append(discord.SelectOption(label=f"{label} 전시대 비우기 (보관함으로)", value=EMPTY, emoji="✖️"))
            options += [discord.SelectOption(label=f"{g.name} ({g.tier})"[:100], value=g.key, emoji=g.emoji,
                                             description=f"패시브: {service.passive_text(base_bonus({slot: g.key}, bal))}"[:100]) for g in choices]
            if not options:
                continue
            select = discord.ui.Select(placeholder=f"{emoji} {label} 전시하기 (보관함에서)", options=options, row=row)
            select.callback = self._slot_callback(slot)
            self.add_item(select)
        for kind, (emoji, verb, _what) in workshop.KINDS.items():         # 요리·제작은 기지에서만 한다
            button = discord.ui.Button(label=verb, emoji=emoji, style=discord.ButtonStyle.primary, row=3)
            button.callback = self._workshop_callback(kind)
            self.add_item(button)

    def _workshop_callback(self, kind: str):
        async def callback(interaction: discord.Interaction):
            await interaction.response.edit_message(embed=workshop_embed(self.client.store, self.guild_id, self.user_id, kind),
                                                    view=WorkshopView(self.client, self.user_id, self.guild_id, kind))
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    def _slot_callback(self, slot: str):
        async def callback(interaction: discord.Interaction):
            value = (interaction.data.get("values") or [EMPTY])[0]
            store = self.client.store
            async with self.client.locks.get(self.guild_id, self.user_id):     # 던전 입장(보관함에서 꺼내기)·보관함 팔기와 겹치지 않게
                bal = store.balance(self.guild_id)
                gear = bal.gear_by_key.get(value)
                try:
                    if value != EMPTY and (gear is None or gear.slot != slot):
                        raise StoreError("그 칸에 전시할 수 없는 장비예요.")
                    store.set_display(self.guild_id, self.user_id, slot, None if value == EMPTY else value)
                except StoreError as exc:
                    note = f"⚠️ {exc}"
                else:
                    note = f"🏠 {gear.emoji} **{gear.name}**을(를) 전시했어요." if value != EMPTY else "✖️ 전시대를 비우고 장비를 보관함에 돌려놨어요."
            await interaction.response.edit_message(embed=base_embed(store, self.guild_id, self.user_id, note),
                                                    view=BaseView(self.client, self.user_id, self.guild_id))
        return callback


def workshop_embed(store, guild_id: int, user_id: int, kind: str, note: str | None = None) -> discord.Embed:
    emoji, verb, what = workshop.KINDS[kind]
    found = workshop.recipes(store, guild_id, kind)
    lines = ([note, ""] if note else []) + [f"가방 물품과 {store.currency(guild_id).name}로 던전 {what}을 만들어요. 만든 것은 **📦 보관함**에 들어가요."]
    if found:
        lines += [("✅ " if workshop.can_make(store, guild_id, user_id, r) else "▫️ ") + workshop.recipe_line(store, guild_id, r, user_id)
                  for r in found[:OPTION_MAX]]
    else:
        lines.append(f"아직 {verb} 레시피가 없어요. 관리자가 `/기지레시피`로 만들어요.")
    return theme.make_embed(f"{emoji} 기지 {verb}", "\n".join(lines)[:4000], color=theme.ORANGE)


class WorkshopView(discord.ui.View):
    """요리·제작 화면: 레시피를 골라 [만들기]. 연 사람만 누를 수 있다."""

    def __init__(self, client, user_id: int, guild_id: int, kind: str, picked: int | None = None):
        super().__init__(timeout=900)
        self.client, self.user_id, self.guild_id, self.kind, self.picked = client, user_id, guild_id, kind, picked
        store = client.store
        bal = store.balance(guild_id)
        found = [r for r in workshop.recipes(store, guild_id, kind) if workshop.result_of(bal, r) is not None][:OPTION_MAX]
        if found:
            options = []
            for r in found:
                result = workshop.result_of(bal, r)
                options.append(discord.SelectOption(label=f"#{r['id']} {result.name}" + (f" ×{r['amount']}" if r["amount"] > 1 else ""),
                                                    value=str(r["id"]), emoji=result.emoji, default=r["id"] == picked,
                                                    description=workshop.ingredient_text(store, guild_id, r)[:100]))
            select = discord.ui.Select(placeholder=f"{workshop.KINDS[kind][0]} 만들 것을 골라요", options=options, row=0)
            select.callback = self.on_pick
            self.add_item(select)
            make = discord.ui.Button(label=f"{workshop.KINDS[kind][1]}하기", emoji=workshop.KINDS[kind][0], style=discord.ButtonStyle.success,
                                     row=1, disabled=picked is None)
            make.callback = self.make
            self.add_item(make)
        back = discord.ui.Button(label="기지로", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
        back.callback = self.back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def _redraw(self, interaction, note=None):
        await interaction.response.edit_message(embed=workshop_embed(self.client.store, self.guild_id, self.user_id, self.kind, note),
                                                view=WorkshopView(self.client, self.user_id, self.guild_id, self.kind, self.picked))

    async def on_pick(self, interaction: discord.Interaction):
        self.picked = int((interaction.data.get("values") or ["0"])[0])
        await self._redraw(interaction)

    async def make(self, interaction: discord.Interaction):
        store = self.client.store
        async with self.client.locks.get(self.guild_id, self.user_id):     # 던전 입장·보관함 팔기·전시와 겹치지 않게
            try:
                recipe, result = workshop.make(store, self.guild_id, self.user_id, self.picked or 0)
            except StoreError as exc:
                note = f"⚠️ {exc}"
            else:
                note = (f"{workshop.KINDS[self.kind][0]} {result.emoji} **{result.name}**" + (f" ×{recipe['amount']}" if recipe["amount"] > 1 else "")
                        + "을(를) 만들어 📦 보관함에 넣었어요!")
        await self._redraw(interaction, note)

    async def back(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=base_embed(self.client.store, self.guild_id, self.user_id),
                                                view=BaseView(self.client, self.user_id, self.guild_id))


class Base(commands.Cog):
    """🏠 기지."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="기지", description="보관함의 장비를 전시해서 던전에서 패시브를 받아요(전시한 장비는 던전에 못 가져가요)")
    @app_commands.guild_only()
    async def base(self, interaction: discord.Interaction):
        store, gid, uid = self.bot.store, interaction.guild_id, interaction.user.id
        await interaction.response.send_message(embed=base_embed(store, gid, uid), view=BaseView(self.bot, uid, gid), ephemeral=False)


async def setup(bot):
    await bot.add_cog(Base(bot))
