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
