"""연금술: 가방의 물품 2~3개를 합쳐 새 물품을 얻는다. 레시피는 관리자가 직접 더한다(기본으로 들어 있는 것은 없다).

- /연금술: 재료를 고르고 [합성]을 누르는 화면(모두가 볼 수 있고 연 사람만 조작). 현실 시간 오후 6시~자정에만 할 수 있다.
- /연금술레시피 추가·목록·삭제: 관리자 전용. 합성물의 판매가는 재료 판매가의 합 × 0.8(2개) / × 0.6(3개)로 자동으로 정해진다.
"""
from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

import alchemy
import theme
from access import require_admin
from bulk import BulkEntry, send_bulk
from owner import owner_only
from panel import partial_emoji, refresh_panels
from store import StoreError

Choice = app_commands.Choice


def label(row) -> str:
    return f"{row['emoji']} {row['name']}"


def recipe_line(store, guild_id: int, recipe: dict) -> str:
    """'🧙 마녀 모자 + 👻 유령의 천 → 🎃 결과물 (판매가 🍬 160)' 한 줄. 사라진 물품이 있어도 오류 없이 표시한다."""
    def name(item_id):
        row = store.item(guild_id, item_id)
        return label(row) if row else "❔ 사라진 물품"
    cur = store.currency(guild_id)
    result = store.item(guild_id, recipe["result"])
    price = cur.money(result["sell_price"]) if result else "—"
    return f"{' + '.join(name(i) for i in recipe['ingredients'])} → {name(recipe['result'])} (판매가 {price})"


def alchemy_embed(store, guild_id: int, user_id: int, now: float, note: str | None = None, ok: bool | None = None) -> discord.Embed:
    """연금술 화면. note가 있으면 방금 한 일의 결과를 맨 위에 보여 준다."""
    opened = alchemy.is_open(now)
    stock = store.alchemy_stock(guild_id, user_id)
    title = "⚗️ 연금술" if ok is None else ("⚗️ 연금술 성공!" if ok else "⚗️ 연금술 실패")
    embed = theme.make_embed(title, note or "가방의 물품 **2~3개**를 합쳐서 새로운 물품을 만들어요.", color=theme.PURPLE if ok is not False else theme.RED)
    embed.add_field(name="⏰ 열리는 시간", inline=False,
                    value=(f"**지금 열려 있어요!** {alchemy.WINDOW_TEXT}" if opened else f"**지금은 닫혀 있어요.** {alchemy.WINDOW_TEXT} 할 수 있어요.\n{alchemy.next_open_text(now)}"))
    embed.add_field(name="📜 규칙", inline=False,
                    value=(f"• 재료를 2~3개 골라 [합성]을 눌러요. 맞는 레시피가 있어야 해요.\n"
                           f"• 합성물의 판매가 = 재료 판매가의 합 {alchemy.factor_text(2)}(두 개) · {alchemy.factor_text(3)}(세 개)\n"
                           f"• 레시피가 없는 조합이면 재료는 그대로 남아요."))
    kinds = len(stock)
    embed.add_field(name="🎒 합칠 수 있는 재료", inline=False,
                    value=(", ".join(f"{label(r)} ×{r['qty']:,}" for r in stock[:15]) + (f" 외 {kinds - 15}가지" if kinds > 15 else "")) if stock
                    else "가방에 레시피 재료로 쓰이는 물품이 없어요.")
    return embed


class AlchemyView(discord.ui.View):
    """재료 선택창 3개(셋째는 선택)와 [합성] 버튼. 연 사람만 조작할 수 있고, 합성하면 같은 메시지가 결과로 바뀐다."""

    def __init__(self, client, member, now: float | None = None):
        super().__init__(timeout=600)
        self.client, self.member = client, member
        self.picks: list[int | None] = [None, None, None]
        store, guild_id = client.store, member.guild.id
        cur = store.currency(guild_id)
        stock = store.alchemy_stock(guild_id, member.id)
        self.stock = stock
        options = [discord.SelectOption(label=f"{r['name']} ×{r['qty']:,}"[:100], value=str(r["id"]), emoji=partial_emoji(r["emoji"]),
                                        description=f"판매가 {theme.plain_money(r['sell_price'], cur)}"[:100]) for r in stock[:25]]
        if options:
            for slot in range(3):
                select = discord.ui.Select(placeholder=f"재료 {slot + 1}" + (" (선택)" if slot == 2 else ""), min_values=0, max_values=1,
                                           options=options, row=slot)
                select.callback = self._picker(slot)
                self.add_item(select)
        self.craft_button = discord.ui.Button(label="합성", emoji="⚗️", style=discord.ButtonStyle.success, row=3,
                                              disabled=not alchemy.is_open(time.time() if now is None else now) or not options)
        self.craft_button.callback = self.craft
        self.add_item(self.craft_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.member.id)

    def _picker(self, slot: int):
        async def callback(interaction: discord.Interaction):
            values = interaction.data.get("values", [])
            self.picks[slot] = int(values[0]) if values else None
            await interaction.response.defer()
        return callback

    async def craft(self, interaction: discord.Interaction):
        client = self.client
        now = time.time()
        if not alchemy.is_open(now):
            await interaction.response.send_message(embed=theme.notice(
                f"연금술은 {alchemy.WINDOW_TEXT} 할 수 있어요. {alchemy.next_open_text(now)}", False), ephemeral=True)
            return
        ids = [pick for pick in self.picks if pick is not None]
        if len(ids) < alchemy.MIN_INGREDIENTS:
            await interaction.response.send_message(embed=theme.notice("재료를 2개 이상 골라 주세요.", False), ephemeral=True)
            return
        gid, uid = interaction.guild_id, interaction.user.id
        async with client.locks.get(gid, uid):
            try:
                done = client.store.alchemize(gid, uid, ids)
            except StoreError as exc:
                await interaction.response.send_message(embed=theme.notice(str(exc), False), ephemeral=True)
                return
        cur = client.store.currency(gid)
        used = " + ".join(label(client.store.item(gid, i)) for i in done["recipe"]["ingredients"])
        note = (f"{used}\n→ {label(done['result'])} **×1**을(를) 얻었어요!\n"
                f"판매가 {cur.money(done['price'])}")
        await interaction.response.edit_message(embed=alchemy_embed(client.store, gid, uid, now, note, ok=True),
                                                view=AlchemyView(client, interaction.user, now))


class Alchemy(commands.Cog):
    """⚗️ 연금술 화면과 레시피 관리."""

    def __init__(self, bot):
        self.bot = bot

    async def _deny(self, interaction, text):
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=False)

    # ---------------------------------------------------------------- 사용자
    @app_commands.command(name="연금술", description="⚗️ 가방의 물품 2~3개를 합쳐 새 물품을 얻어요 (오후 6시~자정)")
    @app_commands.guild_only()
    async def alchemy(self, interaction: discord.Interaction):
        now = time.time()
        store = self.bot.store
        await interaction.response.send_message(embed=alchemy_embed(store, interaction.guild_id, interaction.user.id, now),
                                                view=AlchemyView(self.bot, interaction.user, now), ephemeral=False)

    # ---------------------------------------------------------------- 관리자: 레시피
    recipe = app_commands.Group(name="연금술레시피", description="연금술 레시피를 더하고 지워요 (관리자)", guild_only=True,
                                default_permissions=discord.Permissions(administrator=True))

    @recipe.command(name="추가", description="레시피를 더해요. 합성물의 판매가는 재료 판매가로 자동 계산돼요 (관리자)")
    @app_commands.rename(result="결과물", first="재료1", second="재료2", third="재료3")
    @app_commands.describe(result="합쳐서 얻는 물품(아직 레시피가 없는 물품)", first="재료 (판매가가 1 이상 고정값인 물품)", second="재료",
                           third="재료 (세 개를 합칠 때만)")
    async def recipe_add(self, interaction: discord.Interaction, result: str, first: str, second: str, third: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        store.ensure_defaults(gid)
        rows = []
        for name in (result, first, second, third):
            if name is None:
                continue
            row = store.item_by_name(gid, name)
            if row is None:
                await self._deny(interaction, f"**{name}** 물품이 없어요. 이름은 자동완성에서 골라 주세요.")
                return
            rows.append(row)
        try:
            done = store.add_recipe(gid, rows[0]["id"], [r["id"] for r in rows[1:]])
        except StoreError as exc:
            await self._deny(interaction, str(exc))
            return
        ingredients = rows[1:]
        prices = [r["sell_price"] for r in ingredients]
        cur = store.currency(gid)
        lines = [f"⚗️ 레시피를 더했어요.", recipe_line(store, gid, done["recipe"]),
                 f"판매가 {cur.money(done['price'])} = ({' + '.join(f'{p:,}' for p in prices)}) {alchemy.factor_text(len(prices))}"]
        lines.append("상점에 올라가 있어요." if done["listed"] else "⚠️ 상점 자리(25칸)가 없어서 판매는 꺼져 있어요. 자리를 비운 뒤 `/상점진열`로 올리세요.")
        if done["price"] <= max(prices):
            lines.append("⚠️ 합성물의 판매가가 가장 비싼 재료보다 낮거나 같아요. (재료 가격 차이가 크면 이렇게 돼요)")
        await interaction.response.send_message(embed=theme.notice("\n".join(lines)), ephemeral=False)
        await refresh_panels(self.bot, interaction.guild)

    @recipe.command(name="목록", description="등록된 연금술 레시피를 모두 봐요 (관리자)")
    async def recipe_list(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        recipes = store.recipes(gid)
        body = "\n".join(f"`{n}` {recipe_line(store, gid, r)}" for n, r in enumerate(recipes, 1)) or "아직 레시피가 없어요. `/연금술레시피 추가`로 더해 주세요."
        await interaction.response.send_message(embed=theme.make_embed(
            f"⚗️ 연금술 레시피 {len(recipes)}개", body[:3800] + f"\n\n{alchemy.WINDOW_TEXT} 할 수 있어요.", color=theme.PURPLE), ephemeral=False)

    @recipe.command(name="삭제", description="결과물로 레시피를 지워요. 결과물 물품과 그 판매가는 그대로예요 (관리자)")
    @app_commands.rename(result="결과물")
    async def recipe_delete(self, interaction: discord.Interaction, result: str):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        row = store.item_by_name(gid, result)
        if row is None or not store.delete_recipe(gid, row["id"]):
            await self._deny(interaction, "그 결과물의 레시피가 없어요. 이름은 자동완성에서 골라 주세요.")
            return
        await interaction.response.send_message(embed=theme.notice(
            f"🗑️ {label(row)}의 레시피를 지웠어요. 물품과 판매가(🍬 {row['sell_price']:,})는 그대로이고, 이제 판매가를 직접 바꿀 수 있어요."), ephemeral=False)

    @recipe.command(name="대량삭제", description="연금술 레시피를 여러 개 골라 한 번에 지워요. 결과물 물품은 그대로예요 (관리자)")
    async def recipe_bulk(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        entries = []
        for r in store.recipes(gid):
            row = store.item(gid, r["result"])
            entries.append(BulkEntry(str(r["result"]), recipe_line(store, gid, r), row["emoji"] if row else None))

        def delete(result_id: str) -> str:
            if not store.delete_recipe(gid, int(result_id)):
                raise StoreError("그 레시피가 이미 없어요.")
            return "물품과 판매가는 그대로"
        await send_bulk(interaction, "연금술 레시피", entries, delete, (StoreError,))

    # ---------------------------------------------------------------- 자동완성
    def _items(self, interaction, current, keep):
        store, gid = self.bot.store, interaction.guild_id
        store.ensure_defaults(gid)
        recipes = store.recipes(gid)
        results = {r["result"] for r in recipes}
        used = {i for r in recipes for i in r["ingredients"]}
        needle = current.strip().lower()
        return [r for r in store.items(gid) if r["kind"] == "item" and needle in r["name"].lower() and keep(r, results, used)]

    @recipe_add.autocomplete("first")
    @recipe_add.autocomplete("second")
    @recipe_add.autocomplete("third")
    async def ingredient_autocomplete(self, interaction: discord.Interaction, current: str):
        """재료로 쓸 수 있는 물품: 판매가가 1 이상의 고정값이고 연금술 결과물이 아닌 것."""
        if interaction.guild_id is None:
            return []
        cur = self.bot.store.currency(interaction.guild_id)
        rows = self._items(interaction, current, lambda r, results, used: r["id"] not in results and r["sell_price"] >= 1 and r["sell_max"] is None)
        return [Choice(name=f"{label(r)} · 판매가 {theme.plain_money(r['sell_price'], cur)}"[:100], value=r["name"]) for r in rows[:25]]

    @recipe_add.autocomplete("result")
    async def result_autocomplete(self, interaction: discord.Interaction, current: str):
        """결과물로 쓸 수 있는 물품: 아직 레시피가 없고 다른 레시피의 재료도 아닌 것."""
        if interaction.guild_id is None:
            return []
        rows = self._items(interaction, current, lambda r, results, used: r["id"] not in results and r["id"] not in used)
        return [Choice(name=label(r)[:100], value=r["name"]) for r in rows[:25]]

    @recipe_delete.autocomplete("result")
    async def delete_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        rows = self._items(interaction, current, lambda r, results, used: r["id"] in results)
        return [Choice(name=label(r)[:100], value=r["name"]) for r in rows[:25]]


async def setup(bot):
    await bot.add_cog(Alchemy(bot))
