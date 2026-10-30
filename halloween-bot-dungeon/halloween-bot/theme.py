"""할로윈 분위기의 화면 조각: 색, 이모지, 임베드. 심플하게 호박·캔디·거미줄만 쓴다."""
from __future__ import annotations

import discord

from store import CANDY, CANDY_MAX, CANDY_NAME, EXCHANGE_CANDY, EXCHANGE_EXP

ORANGE = 0xFF7518
PURPLE = 0x6A4C93
GREEN = 0x7BC043
RED = 0xC0392B
FOOTER = "🕸️ Happy Halloween"
LINE = "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️"


def money(amount: int) -> str:
    return f"{CANDY} {amount:,}"


def make_embed(title: str, description: str = "", *, color: int = ORANGE) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or None, color=color)
    embed.set_footer(text=FOOTER)
    return embed


def notice(text: str, ok: bool = True) -> discord.Embed:
    """한 줄 결과 알림(구매 성공·실패 등)."""
    return make_embed("🎃 완료!" if ok else "👻 앗!", text, color=GREEN if ok else RED)


def item_line(row, role_name: str | None = None) -> str:
    buy = money(row["buy_price"]) if row["buyable"] else "—"
    sell = money(row["sell_price"]) if row["sellable"] else "—"
    tag = " `역할`" if row["kind"] == "role" else ""
    title = f"**{role_name or row['name']}**" if row["kind"] == "role" else f"**{row['name']}**"
    return f"{row['emoji']} {title}{tag}\n┕ 구매 {buy} · 판매 {sell}"


def shop_embed(rows, role_names: dict[int, str] | None = None) -> discord.Embed:
    role_names = role_names or {}
    if rows:
        body = "\n".join(item_line(r, role_names.get(r["role_id"])) for r in rows)
    else:
        body = "아직 진열된 물품이 없어요. 🕸️"
    embed = make_embed("🎃 할로윈 상점", f"아래 목록에서 물품을 골라 보세요.\n{LINE}\n{body}", color=ORANGE)
    embed.add_field(name=f"{CANDY} = {CANDY_NAME}", value="채팅하고, 패키지를 뜯고, EXP를 바꿔서 모아요.", inline=False)
    return embed


def detail_embed(row, *, candy: int, owned: int | None, role_name: str | None = None) -> discord.Embed:
    name = role_name or row["name"]
    embed = make_embed(f"{row['emoji']} {name}", row["description"] or "설명이 없어요.", color=PURPLE)
    embed.add_field(name="구매", value=money(row["buy_price"]) if row["buyable"] else "살 수 없어요", inline=True)
    embed.add_field(name="판매", value=money(row["sell_price"]) if row["sellable"] else "팔 수 없어요", inline=True)
    if row["kind"] == "role":
        embed.add_field(name="상태", value="가지고 있어요 ✅" if owned else "아직 없어요", inline=True)
    else:
        embed.add_field(name="보유", value=f"×{owned or 0}", inline=True)
    embed.add_field(name="내 재화", value=money(candy), inline=False)
    return embed


def candy_bar(candy: int, width: int = 10) -> str:
    filled = round(candy / CANDY_MAX * width)
    return "🟧" * filled + "⬛" * (width - filled)


def bag_embed(name: str, *, candy: int, exp: int, items, roles: list[str], tickets: int = 0) -> discord.Embed:
    embed = make_embed(f"🎒 {name}의 가방", color=ORANGE)
    embed.add_field(name=f"{CANDY} {CANDY_NAME}",
                    value=f"**{candy:,}** / {CANDY_MAX:,}\n{candy_bar(candy)}", inline=False)
    ready = exp // EXCHANGE_EXP
    hint = f"{EXCHANGE_EXP:,} EXP → {CANDY} {EXCHANGE_CANDY}" + (f" · **{ready}번** 교환 가능" if ready else "")
    embed.add_field(name="✨ 경험치", value=f"**{exp:,}**\n{hint}", inline=False)
    lines = [f"{r['emoji']} {r['name']} ×{r['qty']:,}" for r in items]
    embed.add_field(name="🎒 물품", value="\n".join(lines) if lines else "비어 있어요 🕸️", inline=False)
    if tickets:
        embed.add_field(name="🎟️ 히든 칭호 획득권", value=f"×{tickets:,}", inline=False)
    if roles:
        embed.add_field(name="👑 역할", value="\n".join(roles), inline=False)
    return embed


def package_embed(mode: str, seconds: int) -> discord.Embed:
    rule = "가장 먼저 뜯는 **한 명**이 가져가요!" if mode == "first" else "누구나 **한 번씩** 뜯을 수 있어요!"
    return make_embed("📦 패키지가 나타났어요!", f"수상한 상자예요… 뜯어볼까요?\n{rule}\n⏳ {seconds}초 동안만 머물러요.",
                      color=PURPLE)


def package_result_text(result: str, gained: int = 0) -> tuple[str, str]:
    """(제목 줄, 설명). result는 none/key/candy/trash."""
    if result == "key":
        return "🗝️ 열쇠!", "낡은 열쇠를 얻었어요. 가방에 넣어 둘게요."
    if result == "candy":
        extra = "" if gained == 50 else f"\n(최대 보유량이라 {gained}개만 받았어요)"
        return f"{CANDY} {CANDY_NAME} 50!", f"{CANDY} {gained}개가 가방에 들어왔어요.{extra}"
    if result == "trash":
        return "🗑️ 쓰레기…", "으… 쓰레기가 나왔어요. 그래도 가방에 넣어 둘게요."
    return "👻 꽝!", "텅 비어 있었어요. 다음 기회에!"
