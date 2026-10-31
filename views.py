"""사용자가 쓰는 화면: 물품 상세, 수량 입력, 가방."""
from __future__ import annotations

import discord

import services
import theme
from panel import display_name
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


async def send_bag(interaction: discord.Interaction):
    await interaction.response.send_message(embed=bag_for(interaction.client, interaction.user), ephemeral=True)


class QuantityModal(discord.ui.Modal):
    def __init__(self, action: str, item_id: int, title: str):
        super().__init__(title=title[:45])
        self.action, self.item_id = action, item_id
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
        trade = services.buy if self.action == "buy" else services.sell
        result = await trade(client.store, client.locks, interaction.user, item, int(text))
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
        return interaction.user.id == self.member.id

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
