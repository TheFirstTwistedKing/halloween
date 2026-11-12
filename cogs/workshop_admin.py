"""기지 요리·제작 레시피 관리(/기지레시피, 관리자).

- 요리추가: 가방 물품 1~3종 × 개수 + 재화 → 던전 소모품(한 번에 1~10개).
- 제작추가: 가방 물품 1~3종 × 개수 + 재화 → 던전 장비 1개.
- 결과물은 플레이어의 📦 보관함으로 간다. 플레이어는 /기지 화면에서만 만든다.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import theme
import workshop
from access import require_admin
from store import WORKSHOP_COST_MAX, StoreError

Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
QTY = app_commands.Range[int, 1, 999]
COST = app_commands.Range[int, 0, WORKSHOP_COST_MAX]


class WorkshopAdmin(commands.Cog):
    """🍳🔨 기지 요리·제작 레시피 (관리자)."""

    group = app_commands.Group(name="기지레시피", description="기지에서 하는 요리(소모품)·제작(장비) 레시피를 만들고 지워요 (관리자)", **PERMS)

    def __init__(self, bot):
        self.bot = bot

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    async def _add(self, interaction, kind, result, parts, cost, amount=1):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        try:
            recipe = workshop.add_recipe(store, gid, kind, result, [(n, q) for n, q in parts if n], cost, amount)
        except StoreError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"{workshop.KINDS[kind][0]} 레시피를 더했어요.\n{workshop.recipe_line(store, gid, recipe)}\n"
                                     "플레이어는 `/기지`의 버튼에서 만들고, 만든 것은 보관함에 들어가요.")

    @group.command(name="요리추가", description="가방 물품(+재화)으로 던전 소모품을 만드는 요리 레시피를 더해요 (관리자)")
    @app_commands.rename(result="결과물", first="재료1", first_qty="개수1", second="재료2", second_qty="개수2", third="재료3", third_qty="개수3",
                         cost="비용", amount="만드는개수")
    @app_commands.describe(result="만들어지는 던전 소모품(자동완성)", first="가방 물품(자동완성)", first_qty="재료1 개수", second="가방 물품",
                           second_qty="재료2 개수", third="가방 물품", third_qty="재료3 개수", cost="함께 내는 재화(0이면 없음)",
                           amount=f"한 번에 나오는 개수(1~{workshop.AMOUNT_MAX})")
    async def add_cook(self, interaction: discord.Interaction, result: str, first: str, first_qty: QTY = 1, second: str | None = None,
                       second_qty: QTY = 1, third: str | None = None, third_qty: QTY = 1, cost: COST = 0,
                       amount: app_commands.Range[int, 1, workshop.AMOUNT_MAX] = 1):
        await self._add(interaction, "cook", result, [(first, first_qty), (second, second_qty), (third, third_qty)], cost, amount)

    @group.command(name="제작추가", description="가방 물품(+재화)으로 던전 장비를 만드는 제작 레시피를 더해요 (관리자)")
    @app_commands.rename(result="결과물", first="재료1", first_qty="개수1", second="재료2", second_qty="개수2", third="재료3", third_qty="개수3",
                         cost="비용")
    @app_commands.describe(result="만들어지는 던전 장비(자동완성)", first="가방 물품(자동완성)", first_qty="재료1 개수", second="가방 물품",
                           second_qty="재료2 개수", third="가방 물품", third_qty="재료3 개수", cost="함께 내는 재화(0이면 없음)")
    async def add_craft(self, interaction: discord.Interaction, result: str, first: str, first_qty: QTY = 1, second: str | None = None,
                        second_qty: QTY = 1, third: str | None = None, third_qty: QTY = 1, cost: COST = 0):
        await self._add(interaction, "craft", result, [(first, first_qty), (second, second_qty), (third, third_qty)], cost)

    @group.command(name="목록", description="기지 요리·제작 레시피를 모두 봐요 (관리자)")
    async def list_recipes(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        found = workshop.recipes(store, gid)
        body = "\n".join(workshop.recipe_line(store, gid, r) for r in found) or "레시피가 없어요. `/기지레시피 요리추가`·`제작추가`로 더해요."
        await interaction.response.send_message(embed=theme.make_embed(f"🍳🔨 기지 레시피 {len(found)}개", body[:4000], color=theme.ORANGE),
                                                ephemeral=False)

    @group.command(name="삭제", description="기지 레시피를 지워요. 번호는 목록에서 봐요 (관리자)")
    @app_commands.rename(recipe_id="번호")
    @app_commands.describe(recipe_id="지울 레시피(자동완성)")
    async def delete(self, interaction: discord.Interaction, recipe_id: int):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        try:
            found = next((r for r in workshop.recipes(store, gid) if r["id"] == recipe_id), None)
            line = workshop.recipe_line(store, gid, found) if found else ""        # 지우기 전에 설명을 만들어 둔다
            workshop.delete_recipe(store, gid, recipe_id)
        except StoreError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"🗑️ 레시피를 지웠어요.\n{line}")

    # ---------------------------------------------------------------- 자동완성
    @add_cook.autocomplete("result")
    async def cook_results(self, interaction: discord.Interaction, current: str):
        return self._results(interaction, current, "cook")

    @add_craft.autocomplete("result")
    async def craft_results(self, interaction: discord.Interaction, current: str):
        return self._results(interaction, current, "craft")

    def _results(self, interaction, current, kind):
        if interaction.guild_id is None:
            return []
        bal = self.bot.store.balance(interaction.guild_id)
        needle = current.strip().lower()
        pool = bal.consumables if kind == "cook" else bal.gear
        return [Choice(name=f"{x.emoji} {x.name} ({x.tier})"[:100], value=x.name) for x in pool if needle in x.name.lower()][:25]

    @add_cook.autocomplete("first")
    @add_cook.autocomplete("second")
    @add_cook.autocomplete("third")
    @add_craft.autocomplete("first")
    @add_craft.autocomplete("second")
    @add_craft.autocomplete("third")
    async def ingredients(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        return [Choice(name=f"{r['emoji']} {r['name']}"[:100], value=r["name"]) for r in self.bot.store.items(interaction.guild_id)
                if r["kind"] == "item" and needle in r["name"].lower()][:25]

    @delete.autocomplete("recipe_id")
    async def recipe_ids(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        store, gid = self.bot.store, interaction.guild_id
        bal = store.balance(gid)
        out = []
        for r in workshop.recipes(store, gid):
            result = workshop.result_of(bal, r)
            label = f"#{r['id']} {workshop.KINDS[r['kind']][1]} {result.name if result else '지워진 결과물'}"
            if current.strip() in label:
                out.append(Choice(name=label[:100], value=r["id"]))
        return out[:25]


async def setup(bot):
    await bot.add_cog(WorkshopAdmin(bot))
