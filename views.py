"""사용자가 쓰는 화면: 물품 상세, 수량 입력, 가방."""
from __future__ import annotations

import discord

import services
import theme
from owner import owner_only
from panel import display_name, partial_emoji
from store import QTY_MAX


def bag_for(client, member) -> discord.Embed:
    store, guild = client.store, member.guild
    candy = store.wallet(guild.id, member.id)
    roles = []
    for row in store.items(guild.id):
        if row["kind"] == "role" and any(role.id == row["role_id"] for role in member.roles):
            roles.append(f"{row['emoji']} {display_name(guild, row)}")
    return theme.bag_embed(member.display_name, candy=candy, items=store.inventory(guild.id, member.id), roles=roles,
                           tickets=store.tickets(guild.id, member.id), cur=store.currency(guild.id))


async def send_bag(interaction: discord.Interaction, ephemeral: bool = False):
    """가방을 보여 준다. /가방 명령어는 모두가 볼 수 있게(ephemeral=False), 상점 패널의 [가방] 버튼은 누른 사람에게만 보이게 한다.
    팔 수 있는 물품이 있으면 판매 선택창이 붙는다(연 사람만 조작할 수 있다)."""
    view = BagView(interaction.client, interaction.user)
    extra = {"view": view} if view.children else {}
    await interaction.response.send_message(embed=bag_for(interaction.client, interaction.user), ephemeral=ephemeral, **extra)


class BagView(discord.ui.View):
    """가방 화면의 판매 선택창. 가방에 있는 물품 중 팔 수 있는 것을 골라 수량을 적으면 바로 팔고, 같은 가방 메시지가 새 내용으로 바뀐다."""

    def __init__(self, client, member):
        super().__init__(timeout=300)
        self.client, self.member = client, member
        store, guild_id = client.store, member.guild.id
        cur = store.currency(guild_id)
        options = [discord.SelectOption(label=f"{row['name']} ×{row['qty']:,}"[:100], value=str(row["id"]), emoji=partial_emoji(row["emoji"]),
                                        description=f"개당 판매 {theme.sell_text(row, cur, plain=True)}"[:100])
                   for row in store.inventory(guild_id, member.id) if row["sellable"]]
        if options:
            select = discord.ui.Select(placeholder="💰 팔 물품을 골라요", options=options[:25], row=0)
            select.callback = self.on_pick
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.member.id)

    async def on_pick(self, interaction: discord.Interaction):
        try:
            item = self.client.store.item(interaction.guild_id, int(interaction.data["values"][0]))
        except (ValueError, KeyError, IndexError):
            item = None
        if item is None or item["kind"] != "item" or not item["sellable"]:
            await interaction.response.send_message(embed=theme.notice("이제 팔 수 없는 물품이에요.", False), ephemeral=True)
            return
        await interaction.response.send_modal(QuantityModal("sell", item["id"], f"{item['name']} 판매", bag=True))


class QuantityModal(discord.ui.Modal):
    def __init__(self, action: str, item_id: int, title: str, *, bag: bool = False):
        """bag=True면 가방에서 연 입력창이다: 결과를 따로 보내지 않고 그 가방 메시지를 새 내용(과 결과)으로 바꾼다."""
        super().__init__(title=title[:45])
        self.action, self.item_id, self.bag = action, item_id, bag
        self.amount = discord.ui.TextInput(label=f"수량 (1~{QTY_MAX})", default="1", min_length=1, max_length=len(str(QTY_MAX)))
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.amount.value.strip()
        if not (text.isascii() and text.isdigit()):
            await interaction.response.send_message(embed=theme.notice("수량은 숫자로 입력해 주세요.", False), ephemeral=True)
            return
        client = interaction.client
        item = client.store.item(interaction.guild_id, self.item_id)
        if item is None:
            await interaction.response.send_message(embed=theme.notice("이제 상점에 없는 물품이에요.", False), ephemeral=True)
            return
        if self.action == "buy":
            result = await services.buy(client.store, client.locks, interaction.user, item, int(text))
        else:
            result = await services.sell(client.store, client.locks, interaction.user, item, int(text), client.rng)
        if self.bag:
            embed = bag_for(client, interaction.user)
            embed.description = ("✅ " if result.ok else "❌ ") + result.text
            view = BagView(client, interaction.user)
            await interaction.response.edit_message(embed=embed, **({"view": view} if view.children else {"view": None}))
            return
        await interaction.response.send_message(embed=theme.notice(result.text, result.ok), ephemeral=True)


class DetailView(discord.ui.View):
    """고른 물품의 자세한 정보와 [구매]·[판매] 버튼. 본인에게만 보이는 임시 화면이다."""

    def __init__(self, client, item_id: int, member):
        super().__init__(timeout=180)
        self.client, self.item_id, self.member = client, item_id, member
        item = client.store.item(member.guild.id, item_id)
        self.buy_button = discord.ui.Button(label="구매", emoji="🛒", style=discord.ButtonStyle.success,
                                            disabled=not (item and item["buyable"]))
        self.sell_button = discord.ui.Button(label="판매", emoji="🎃", style=discord.ButtonStyle.danger,
                                             disabled=not (item and item["sellable"]))
        self.buy_button.callback = lambda i: self.start(i, "buy")
        self.sell_button.callback = lambda i: self.start(i, "sell")
        self.add_item(self.buy_button)
        self.add_item(self.sell_button)

    def embed(self) -> discord.Embed:
        store, guild = self.client.store, self.member.guild
        item = store.item(guild.id, self.item_id)
        candy = store.wallet(guild.id, self.member.id)
        if item["kind"] == "role":
            owned = any(role.id == item["role_id"] for role in self.member.roles)
        else:
            owned = store.qty(guild.id, self.member.id, item["id"])
        return theme.detail_embed(item, candy=candy, owned=owned, role_name=display_name(guild, item),
                                  cur=store.currency(guild.id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.member.id)

    async def start(self, interaction: discord.Interaction, action: str):
        item = self.client.store.item(interaction.guild_id, self.item_id)
        if item is None:
            await interaction.response.send_message(embed=theme.notice("이제 상점에 없는 물품이에요.", False), ephemeral=True)
            return
        if item["kind"] == "role":                      # 역할은 한 개뿐이라 수량을 묻지 않는다.
            trade = services.buy if action == "buy" else services.sell
            await interaction.response.defer(ephemeral=True)
            result = await trade(self.client.store, self.client.locks, interaction.user, item, 1)
            await interaction.followup.send(embed=theme.notice(result.text, result.ok), ephemeral=True)
            return
        await interaction.response.send_modal(QuantityModal(action, item["id"], f"{item['name']} {'구매' if action == 'buy' else '판매'}"))
