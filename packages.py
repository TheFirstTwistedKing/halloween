"""패키지: 채팅 중에 가끔 나타나는 상자. 누구나 뜯을 수 있고, 결과는 서버마다 정한 결과표(`/패키지결과`)에서 뽑는다."""
from __future__ import annotations

import logging
import time

import discord

import theme
from store import roll_package_result

LOG = logging.getLogger("halloween")
PKG_OPEN_ID = "hw:pkg:open"
PKG_SKIP_ID = "hw:pkg:skip"


class PackageView(discord.ui.View):
    """패키지 메시지의 버튼. 봇을 껐다 켜도 이미 나온 패키지를 뜯을 수 있도록 고정형으로 만든다."""

    def __init__(self):
        super().__init__(timeout=None)
        opener = discord.ui.Button(custom_id=PKG_OPEN_ID, label="뜯기", emoji="📦", style=discord.ButtonStyle.success)
        skipper = discord.ui.Button(custom_id=PKG_SKIP_ID, label="그냥 두기", emoji="🚪", style=discord.ButtonStyle.secondary)
        opener.callback, skipper.callback = self.on_open, self.on_skip
        self.add_item(opener)
        self.add_item(skipper)

    async def on_open(self, interaction: discord.Interaction):
        await open_package(interaction)

    async def on_skip(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=theme.make_embed("🚪 그냥 두었어요", "마음이 바뀌면 [뜯기]를 눌러도 돼요.", color=theme.PURPLE), ephemeral=True)


def apply_reward(store, guild_id: int, user_id: int, entry: dict) -> int:
    """결과표의 한 칸을 가방에 넣는다. 받은 재화 수(재화가 아니면 0)를 돌려준다. 물품이 그 사이 지워졌다면 아무것도 주지 않는다."""
    if entry["kind"] == "candy":
        return store.add_candy(guild_id, user_id, entry["amount"])
    if entry["kind"] == "item" and store.item(guild_id, entry["item"]) is not None:
        store.add_item(guild_id, user_id, entry["item"], entry["amount"])
    return 0


async def spawn(client, message, cfg) -> discord.Message | None:
    """채팅이 올라온 그 채널에 패키지를 낸다."""
    embed = theme.package_embed(cfg["package_mode"], cfg["package_seconds"])
    try:
        sent = await message.channel.send(embed=embed, view=PackageView())
    except discord.HTTPException:
        LOG.warning("패키지를 올리지 못했어요: 채널 %s", message.channel.id)
        return None
    client.store.create_package(message.guild.id, message.channel.id, sent.id, time.time() + cfg["package_seconds"], cfg["package_mode"])
    return sent


async def open_package(interaction: discord.Interaction):
    client, store = interaction.client, interaction.client.store
    now = time.time()
    table = store.package_table(interaction.guild_id)
    if not table or sum(e["weight"] for e in table) <= 0:      # 표가 비었다면(관리자가 직접 고치지 않는 한 없는 일) 꽝으로 둔다.
        table = [{"id": "none", "kind": "none", "weight": 1, "amount": 1}]
    entry = roll_package_result(table, client.rng)
    status = store.open_package(interaction.message.id, interaction.user.id, entry["id"], now)
    if status != "ok":
        text = {"again": "이미 이 패키지를 뜯었어요. 다음 패키지를 기다려 보세요!",
                "closed": "한발 늦었어요. 이미 다른 분이 뜯었어요!",
                "gone": "패키지가 이미 사라졌어요."}[status]
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=True)
        return
    gained = apply_reward(store, interaction.guild_id, interaction.user.id, entry)
    title, description = theme.package_result_text(entry, store, interaction.guild_id, gained)
    await interaction.response.send_message(embed=theme.make_embed(title, description, color=theme.PURPLE), ephemeral=True)
    row = store.package(interaction.message.id)
    if row and row["closed"] == 1:     # 선착순: 뜯은 사람이 나오면 바로 결과를 모두에게 보여준다.
        await summarize(client, row)
        store.mark_summarized(row["message"])


def summary_embed(row, openers, store=None) -> discord.Embed:
    """패키지가 끝났을 때 메시지를 바꿀 요약. 결과표가 그 사이 바뀌어 사라진 결과는 '사라진 결과'로 보여준다."""
    table = {e["id"]: e for e in store.settings(row["guild"])["package_table"]} if store else {}

    def label(result_id: str) -> str:
        entry = table.get(result_id)
        if entry is None:
            return "📦 사라진 결과"
        emoji, title = theme.entry_icon_title(entry, store, row["guild"])
        return f"{emoji} {title}"

    if row["mode"] == "first" and openers:
        return theme.make_embed("📦 패키지가 열렸어요!", f"<@{openers[0]['user']}> 님이 뜯었어요 → **{label(openers[0]['result'])}**", color=theme.PURPLE)
    if openers:
        counts = {}
        for opener in openers:
            counts[opener["result"]] = counts.get(opener["result"], 0) + 1
        detail = " · ".join(f"{label(k)} ×{v}" for k, v in counts.items())
        return theme.make_embed("📦 패키지가 사라졌어요", f"{len(openers)}명이 뜯었어요.\n{detail}", color=theme.PURPLE)
    return theme.make_embed("📦 패키지가 사라졌어요", "아무도 뜯지 않았어요… 🕸️", color=theme.PURPLE)


async def summarize(client, row):
    """패키지 메시지를 결과 요약으로 바꾸고 버튼을 없앤다. 메시지가 지워졌거나 권한이 없어도 오류로 끝나지 않는다."""
    channel = client.get_channel(row["channel"])
    if channel is None:
        return
    try:
        await channel.get_partial_message(row["message"]).edit(
            embed=summary_embed(row, client.store.package_openers(row["message"]), client.store), view=None)
    except discord.HTTPException:
        pass


async def finalize_due(client, now: float | None = None) -> int:
    """끝난 패키지를 정리한다. 시간이 지난 것은 요약(이미 했다면 생략) 후 기록을 지우고,
    선착순으로 이미 뜯긴 것은 만료 때까지 '뜯김' 상태로 둔다."""
    now = time.time() if now is None else now
    rows = client.store.finished_packages(now)
    for row in rows:
        if row["closed"] != 2:
            await summarize(client, row)
        if row["expires"] <= now:
            client.store.delete_package(row["message"])
        else:
            client.store.mark_summarized(row["message"])
    return len(rows)
