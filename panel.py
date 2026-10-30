"""상점 패널(채널에 고정되는 메시지)을 만들고 새로 고친다."""
from __future__ import annotations

import logging

import discord

import theme
from store import CANDY, valid_emoji

LOG = logging.getLogger("halloween")
SHOP_SELECT_ID = "hw:shop:select"
SHOP_BAG_ID = "hw:shop:bag"


def partial_emoji(text: str) -> discord.PartialEmoji | None:
    if not valid_emoji(text):
        return None
    return discord.PartialEmoji.from_str(text)


def display_name(guild, row) -> str:
    if row["kind"] == "role":
        role = guild.get_role(row["role_id"]) if guild else None
        return role.name if role else row["name"]
    return row["name"]


def role_names(guild, rows) -> dict[int, str]:
    return {r["role_id"]: display_name(guild, r) for r in rows if r["kind"] == "role"}


class ShopPanelView(discord.ui.View):
    """채널에 올라가는 상점. 봇을 껐다 켜도 계속 동작하도록 timeout이 없고 버튼 ID가 고정이다."""

    def __init__(self, rows=None, guild=None, *, with_emoji: bool = True):
        super().__init__(timeout=None)
        options = []
        for row in rows or []:
            buy = f"구매 {CANDY} {row['buy_price']:,}" if row["buyable"] else "구매 불가"
            sell = f"판매 {CANDY} {row['sell_price']:,}" if row["sellable"] else "판매 불가"
            options.append(discord.SelectOption(
                label=display_name(guild, row)[:100], value=str(row["id"]), description=f"{buy} · {sell}"[:100],
                emoji=partial_emoji(row["emoji"]) if with_emoji else None))
        select = discord.ui.Select(
            custom_id=SHOP_SELECT_ID, placeholder="🎃 물품을 골라 보세요",
            options=options or [discord.SelectOption(label="(물품이 아직 없어요)", value="0")], disabled=not options)
        select.callback = self.on_select
        self.add_item(select)
        bag = discord.ui.Button(custom_id=SHOP_BAG_ID, label="가방", emoji="🎒", style=discord.ButtonStyle.secondary)
        bag.callback = self.on_bag
        self.add_item(bag)

    async def on_select(self, interaction: discord.Interaction):
        from views import DetailView        # 서로를 가져오는 순환을 피한다.
        client = interaction.client
        try:
            item = client.store.item(interaction.guild_id, int(interaction.data["values"][0]))
        except (ValueError, KeyError, IndexError):
            item = None
        if item is None or not (item["buyable"] or item["sellable"]):
            await interaction.response.send_message(embed=theme.notice("이제 상점에 없는 물품이에요.", False), ephemeral=True)
            return
        view = DetailView(client, item["id"], interaction.user)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    async def on_bag(self, interaction: discord.Interaction):
        from views import send_bag
        await send_bag(interaction)


def build_panel(store, guild, *, with_emoji: bool = True):
    rows = store.items(guild.id, shop_only=True)
    return theme.shop_embed(rows, role_names(guild, rows)), ShopPanelView(rows, guild, with_emoji=with_emoji)


async def post_panel(client, channel) -> discord.Message:
    """이 채널에 상점을 올리고 기록한다. 이 채널에 이미 있던 상점 메시지는 지운다."""
    guild = channel.guild
    old = next((r for r in client.store.shops(guild.id) if r["channel"] == channel.id), None)
    embed, view = build_panel(client.store, guild)
    try:
        message = await channel.send(embed=embed, view=view)
    except discord.HTTPException:
        embed, view = build_panel(client.store, guild, with_emoji=False)     # 이모지 때문에 거절됐을 수 있어 한 번 더
        message = await channel.send(embed=embed, view=view)
    client.store.set_shop(guild.id, channel.id, message.id)
    if old:
        try:
            await channel.get_partial_message(old["message"]).delete()
        except discord.HTTPException:
            pass
    return message


async def refresh_panels(client, guild) -> int:
    """물품이 바뀌었을 때 이 서버의 모든 상점 메시지를 새 목록으로 고친다. 고친 개수를 돌려준다."""
    done = 0
    for row in client.store.shops(guild.id):
        channel = guild.get_channel(row["channel"])
        if channel is None:
            client.store.remove_shop(row["channel"])
            continue
        for with_emoji in (True, False):
            embed, view = build_panel(client.store, guild, with_emoji=with_emoji)
            try:
                await channel.get_partial_message(row["message"]).edit(embed=embed, view=view)
                done += 1
                break
            except discord.NotFound:
                client.store.remove_shop(row["channel"])       # 메시지가 지워졌으면 기록도 지운다.
                break
            except discord.HTTPException as exc:
                if exc.status == 400 and with_emoji:
                    continue
                LOG.warning("상점 패널을 고치지 못했어요: 채널 %s (HTTP %s)", row["channel"], exc.status)
                break
    return done
