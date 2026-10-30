import discord
from discord import app_commands
from discord.ext import commands

import theme
from access import require_admin
from cogs.admin import item_choices
from panel import display_name, post_panel, refresh_panels
from services import role_problem
from store import MAX_SHOP_ITEMS, StoreError

TEXT = discord.TextChannel


class ConfirmDelete(discord.ui.View):
    """물품 삭제는 되돌릴 수 없어서 한 번 더 묻는다."""

    def __init__(self, bot, admin_id: int, item_id: int, name: str):
        super().__init__(timeout=60)
        self.bot, self.admin_id, self.item_id, self.name = bot, admin_id, item_id, name
        yes = discord.ui.Button(label="삭제", emoji="🗑️", style=discord.ButtonStyle.danger)
        no = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.confirm, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin_id

    async def confirm(self, interaction: discord.Interaction):
        self.stop()
        try:
            lost = self.bot.store.delete_item(interaction.guild_id, self.item_id)
        except StoreError as exc:
            await interaction.response.edit_message(embed=theme.notice(str(exc), False), view=None)
            return
        await interaction.response.edit_message(
            embed=theme.notice(f"**{self.name}**을(를) 지웠어요." + (f" 가방에 있던 {lost:,}개도 함께 사라졌어요." if lost else "")), view=None)
        await refresh_panels(self.bot, interaction.guild)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("삭제를 취소했어요."), view=None)


class Shop(commands.Cog):
    """상점 등록과 물품·역할 관리 (관리자 전용)."""

    def __init__(self, bot):
        self.bot = bot

    async def _deny(self, interaction, text):
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=True)

    # ---------------------------------------------------------------- 상점 등록
    @app_commands.command(name="상점등록", description="이 채널에 할로윈 상점을 띄워요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(channel="채널")
    @app_commands.describe(channel="상점을 띄울 채널 (비우면 지금 채널)")
    async def register_shop(self, interaction: discord.Interaction, channel: TEXT | None = None):
        if not await require_admin(interaction):
            return
        channel = channel or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await self._deny(interaction, "일반 텍스트 채널에서만 상점을 열 수 있어요.")
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await self._deny(interaction, f"봇이 {channel.mention}에서 메시지를 보내거나 임베드를 올릴 수 없어요. 채널 권한을 확인해 주세요.")
            return
        await interaction.response.defer(ephemeral=True)
        try:
            message = await post_panel(self.bot, channel)
        except discord.HTTPException:
            await interaction.followup.send(embed=theme.notice("상점을 올리지 못했어요. 채널 권한을 확인해 주세요.", False), ephemeral=True)
            return
        await interaction.followup.send(embed=theme.notice(f"{channel.mention}에 상점을 열었어요! {message.jump_url}"), ephemeral=True)

    @app_commands.command(name="상점해제", description="이 채널의 할로윈 상점을 없애요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(channel="채널")
    async def unregister_shop(self, interaction: discord.Interaction, channel: TEXT | None = None):
        if not await require_admin(interaction):
            return
        channel = channel or interaction.channel
        row = next((r for r in self.bot.store.shops(interaction.guild_id) if r["channel"] == getattr(channel, "id", None)), None)
        if row is None:
            await self._deny(interaction, "이 채널에는 열려 있는 상점이 없어요.")
            return
        self.bot.store.remove_shop(row["channel"])
        try:
            await channel.get_partial_message(row["message"]).delete()
        except discord.HTTPException:
            pass
        await interaction.response.send_message(embed=theme.notice(f"{channel.mention}의 상점을 없앴어요. 물품과 가방은 그대로예요."), ephemeral=True)

    # ---------------------------------------------------------------- 물품 등록
    async def _finish(self, interaction, text):
        await interaction.response.send_message(embed=theme.notice(text), ephemeral=True)
        await refresh_panels(self.bot, interaction.guild)

    @app_commands.command(name="물품등록", description="상점에 물품을 등록해요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(name="이름", buyable="구입", sellable="판매", buy_price="구입가", sell_price="판매가", description="설명", emoji="이모지")
    @app_commands.describe(name="물품 이름 (30자까지)", buyable="사람들이 살 수 있게 할까요? (true/false)",
                           sellable="사람들이 팔 수 있게 할까요? (true/false)", buy_price="구입 가격(🍬). 구입을 켰다면 필수",
                           sell_price="판매 가격(🍬). 판매를 켰다면 필수", description="설명 (200자까지)", emoji="아이콘 (기본 🎃)")
    async def add_item(self, interaction: discord.Interaction, name: str, buyable: bool, sellable: bool,
                       buy_price: app_commands.Range[int, 0, 99999] = 0, sell_price: app_commands.Range[int, 0, 99999] = 0,
                       description: str = "", emoji: str = "🎃"):
        if not await require_admin(interaction):
            return
        try:
            item = self.bot.store.create_item(interaction.guild_id, name.strip(), emoji=emoji.strip(), description=description.strip(),
                                              buy_price=buy_price, sell_price=sell_price, buyable=buyable, sellable=sellable)
        except StoreError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._finish(interaction, f"{item['emoji']} **{item['name']}**을(를) 등록했어요.\n" + self._flags(item))

    @app_commands.command(name="역할등록", description="상점에 역할을 등록해요. 사면 그 역할을 받아요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(role="역할", buyable="구입", sellable="판매", buy_price="구입가", sell_price="판매가", description="설명", emoji="이모지")
    @app_commands.describe(role="상점에 올릴 역할", buyable="사람들이 살 수 있게 할까요? (true/false)",
                           sellable="역할을 반납하고 팔 수 있게 할까요? (true/false)", buy_price="구입 가격(🍬). 구입을 켰다면 필수",
                           sell_price="판매 가격(🍬). 판매를 켰다면 필수", description="설명 (200자까지)", emoji="아이콘 (기본 👑)")
    async def add_role(self, interaction: discord.Interaction, role: discord.Role, buyable: bool, sellable: bool,
                       buy_price: app_commands.Range[int, 0, 99999] = 0, sell_price: app_commands.Range[int, 0, 99999] = 0,
                       description: str = "", emoji: str = "👑"):
        if not await require_admin(interaction):
            return
        if problem := role_problem(interaction.guild, role):
            await self._deny(interaction, problem)
            return
        try:
            item = self.bot.store.create_item(interaction.guild_id, role.name[:30].strip() or "역할", emoji=emoji.strip(),
                                              description=description.strip(), kind="role", role_id=role.id, buy_price=buy_price,
                                              sell_price=sell_price, buyable=buyable, sellable=sellable)
        except StoreError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._finish(interaction, f"{item['emoji']} {role.mention} 역할을 상점에 등록했어요.\n" + self._flags(item))

    @staticmethod
    def _flags(item) -> str:
        buy = f"구입 ✅ {theme.money(item['buy_price'])}" if item["buyable"] else "구입 ❌"
        sell = f"판매 ✅ {theme.money(item['sell_price'])}" if item["sellable"] else "판매 ❌"
        note = "" if item["buyable"] or item["sellable"] else "\n(구입·판매가 모두 꺼져 있어서 상점에는 나오지 않아요.)"
        return f"{buy} · {sell}{note}"

    @app_commands.command(name="물품수정", description="물품의 가격·구입·판매·설명을 고쳐요. 바꿀 것만 적으세요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(item="물품", new_name="새이름", buyable="구입", sellable="판매", buy_price="구입가", sell_price="판매가",
                         description="설명", emoji="이모지")
    async def edit_item(self, interaction: discord.Interaction, item: str, new_name: str | None = None, buyable: bool | None = None,
                        sellable: bool | None = None, buy_price: app_commands.Range[int, 0, 99999] | None = None,
                        sell_price: app_commands.Range[int, 0, 99999] | None = None, description: str | None = None,
                        emoji: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        store.ensure_system_items(gid)
        row = store.item_by_name(gid, item)
        if row is None:
            await self._deny(interaction, "그런 물품이 없어요.")
            return
        changes = {k: v for k, v in dict(name=new_name and new_name.strip(), buyable=buyable, sellable=sellable, buy_price=buy_price,
                                         sell_price=sell_price, description=description and description.strip(),
                                         emoji=emoji and emoji.strip()).items() if v is not None}
        if description == "":
            changes["description"] = ""
        if not changes:
            await self._deny(interaction, "바꿀 내용을 하나 이상 적어 주세요.")
            return
        try:
            updated = store.update_item(gid, row["id"], **changes)
        except StoreError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._finish(interaction, f"{updated['emoji']} **{updated['name']}**을(를) 고쳤어요.\n" + self._flags(updated))

    @app_commands.command(name="물품삭제", description="물품을 지워요. 가방에 있던 것도 사라져요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(item="물품")
    async def delete_item(self, interaction: discord.Interaction, item: str):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        row = store.item_by_name(gid, item)
        if row is None:
            await self._deny(interaction, "그런 물품이 없어요.")
            return
        if row["system"]:
            await self._deny(interaction, "기본 물품(열쇠·쓰레기)은 지울 수 없어요. 상점에서 빼려면 구입·판매를 꺼 주세요.")
            return
        held = store.holdings(gid, row["id"])
        warning = f"가방에 있는 **{held:,}개**도 함께 사라져요. " if held else ""
        await interaction.response.send_message(
            embed=theme.make_embed("🗑️ 정말 지울까요?", f"{row['emoji']} **{row['name']}**\n{warning}되돌릴 수 없어요.", color=theme.RED),
            view=ConfirmDelete(self.bot, interaction.user.id, row["id"], row["name"]), ephemeral=True)

    @app_commands.command(name="물품목록", description="등록된 모든 물품을 봅니다 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def list_items(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        store.ensure_system_items(gid)
        lines = []
        for row in store.items(gid):
            kind = " `역할`" if row["kind"] == "role" else (" `기본`" if row["system"] else "")
            buy = f"구입 {theme.money(row['buy_price'])}" if row["buyable"] else "구입 ❌"
            sell = f"판매 {theme.money(row['sell_price'])}" if row["sellable"] else "판매 ❌"
            lines.append(f"{row['emoji']} **{display_name(interaction.guild, row)}**{kind} · {buy} · {sell}")
        listed = len(store.items(gid, shop_only=True))
        text = "\n".join(lines)[:3800] or "등록된 물품이 없어요."
        await interaction.response.send_message(
            embed=theme.make_embed("📜 물품 목록", f"{text}\n\n상점에 진열 {listed}/{MAX_SHOP_ITEMS}", color=theme.PURPLE), ephemeral=True)

    @edit_item.autocomplete("item")
    @delete_item.autocomplete("item")
    async def item_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        self.bot.store.ensure_system_items(interaction.guild_id)
        return await item_choices(self.bot.store, interaction.guild_id, current)


async def setup(bot):
    await bot.add_cog(Shop(bot))
