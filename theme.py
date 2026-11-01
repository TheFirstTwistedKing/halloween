"""할로윈 분위기의 화면 조각: 색, 이모지, 임베드. 심플하게 호박·캔디·거미줄만 쓴다."""
from __future__ import annotations

import discord

from store import DEFAULT_CURRENCY, Currency

ORANGE = 0xFF7518
PURPLE = 0x6A4C93
GOLD = 0xF1C40F
GREEN = 0x7BC043
RED = 0xC0392B
FOOTER = "🕸️ Happy Halloween"
LINE = "▫️▫️▫️▫️▫️▫️▫️▫️▫️▫️"


def money(amount: int, cur: Currency | None = None) -> str:
    return (cur or DEFAULT_CURRENCY).money(amount)


def plain_money(amount: int, cur: Currency | None = None) -> str:
    """선택창 설명처럼 서버 이모지(<:이름:ID>)가 그림으로 보이지 않는 곳에 쓴다. 서버 이모지면 이모지 대신 재화 이름을 쓴다."""
    cur = cur or DEFAULT_CURRENCY
    return f"{cur.name} {amount:,}" if cur.emoji.startswith("<") else cur.money(amount)


def sell_text(row, cur: Currency | None = None, *, plain: bool = False) -> str:
    """물품의 판매가 글자. 고정이면 '🍬 100', 음수면 '🍬 -150'(팔 때 재화를 낸다), 범위면 '🍬 -300 ~ 300'(팔 때마다 그 사이에서 뽑는다)."""
    show = plain_money if plain else money
    high = row["sell_max"] if "sell_max" in row.keys() else None
    low = row["sell_price"]
    return show(low, cur) if high is None else f"{show(low, cur)} ~ {high:,}"


def sell_note(row) -> str:
    """판매가가 보통과 다를 때(내야 하거나 매번 달라질 때) 붙이는 짧은 안내. 보통이면 빈 문자열."""
    high = row["sell_max"] if "sell_max" in row.keys() else None
    if high is not None:
        return "\n🎲 팔 때마다 값이 달라져요" + (" (손해를 볼 수도 있어요)" if row["sell_price"] < 0 else "")
    return "\n⚠️ 팔 때 재화를 내야 해요" if row["sell_price"] < 0 else ""


def make_embed(title: str, description: str = "", *, color: int = ORANGE) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or None, color=color)
    embed.set_footer(text=FOOTER)
    return embed


def notice(text: str, ok: bool = True) -> discord.Embed:
    """한 줄 결과 알림(구매 성공·실패 등)."""
    return make_embed("🎃 완료!" if ok else "👻 앗!", text, color=GREEN if ok else RED)


def item_line(row, role_name: str | None = None, cur: Currency | None = None) -> str:
    buy = money(row["buy_price"], cur) if row["buyable"] else "—"
    sell = sell_text(row, cur) if row["sellable"] else "—"
    tag = " `역할`" if row["kind"] == "role" else ""
    title = f"**{role_name or row['name']}**" if row["kind"] == "role" else f"**{row['name']}**"
    return f"{row['emoji']} {title}{tag}\n┕ 구매 {buy} · 판매 {sell}"


def shop_embed(rows, role_names: dict[int, str] | None = None, cur: Currency | None = None) -> discord.Embed:
    role_names = role_names or {}
    cur = cur or DEFAULT_CURRENCY
    if rows:
        body = "\n".join(item_line(r, role_names.get(r["role_id"]), cur) for r in rows)
    else:
        body = "아직 진열된 물품이 없어요. 🕸️"
    embed = make_embed("🎃 할로윈 상점", f"아래 목록에서 물품을 골라 보세요.\n{LINE}\n{body}", color=ORANGE)
    embed.add_field(name=f"{cur.emoji} = {cur.name}", value="채팅하고, 패키지를 뜯고, 던전을 돌아서 모아요.", inline=False)
    return embed


def detail_embed(row, *, candy: int, owned: int | None, role_name: str | None = None, cur: Currency | None = None) -> discord.Embed:
    name = role_name or row["name"]
    embed = make_embed(f"{row['emoji']} {name}", row["description"] or "설명이 없어요.", color=PURPLE)
    embed.add_field(name="구매", value=money(row["buy_price"], cur) if row["buyable"] else "살 수 없어요", inline=True)
    embed.add_field(name="판매", value=(sell_text(row, cur) + sell_note(row)) if row["sellable"] else "팔 수 없어요", inline=True)
    if row["kind"] == "role":
        embed.add_field(name="상태", value="가지고 있어요 ✅" if owned else "아직 없어요", inline=True)
    else:
        embed.add_field(name="보유", value=f"×{owned or 0}", inline=True)
    embed.add_field(name="내 재화", value=money(candy, cur), inline=False)
    return embed


def candy_bar(candy: int, top: int, width: int = 10) -> str:
    filled = min(width, round(candy / top * width)) if top else 0
    return "🟧" * filled + "⬛" * (width - filled)


def bag_embed(name: str, *, candy: int, items, roles: list[str], tickets: int = 0, cur: Currency | None = None) -> discord.Embed:
    cur = cur or DEFAULT_CURRENCY
    embed = make_embed(f"🎒 {name}의 가방", color=ORANGE)
    embed.add_field(name=f"{cur.emoji} {cur.name}",
                    value=f"**{candy:,}** / {cur.max:,}\n{candy_bar(candy, cur.max)}", inline=False)
    lines = [f"{r['emoji']} {r['name']} ×{r['qty']:,}" for r in items]
    embed.add_field(name="🎒 물품", value="\n".join(lines) if lines else "비어 있어요 🕸️", inline=False)
    if tickets:
        embed.add_field(name="🎟️ 히든 칭호 획득권", value=f"×{tickets:,}", inline=False)
    if roles:
        embed.add_field(name="👑 역할", value="\n".join(roles), inline=False)
    return embed


def package_embed(mode: str, seconds: int, pile: int = 0) -> discord.Embed:
    """패키지가 나타났다는 메시지. pile이 0보다 크면 변이한 '패키지 더미'(뜯으면 결과가 pile개)다."""
    rule = "가장 먼저 뜯는 **한 명**이 가져가요!" if mode == "first" else "누구나 **한 번씩** 뜯을 수 있어요!"
    if pile:
        return make_embed("📦📦📦 패키지 더미가 나타났어요!",
                          f"상자가 변이해서 **더미**로 쌓여 있어요! 뜯으면 **{pile}개**가 나와요.\n{rule}\n⏳ {seconds}초 동안만 머물러요.", color=GOLD)
    return make_embed("📦 패키지가 나타났어요!", f"수상한 상자예요… 뜯어볼까요?\n{rule}\n⏳ {seconds}초 동안만 머물러요.",
                      color=PURPLE)


def entry_icon_title(entry: dict, store, guild_id: int) -> tuple[str, str]:
    """패키지 결과표 한 칸의 (이모지, 이름). 관리자가 정한 값이 있으면 그걸, 없으면 물품·재화에서 가져온다."""
    cur = store.currency(guild_id)
    if entry["kind"] == "candy":
        emoji, title = cur.emoji, f"{cur.name} {entry.get('amount', 1):,}"
    elif entry["kind"] == "item":
        item = store.item(guild_id, entry.get("item"))
        emoji, title = (item["emoji"], item["name"] + (f" ×{entry['amount']}" if entry.get("amount", 1) != 1 else "")) if item else ("❔", "사라진 물품")
    else:
        emoji, title = "👻", "꽝"
    return entry.get("emoji") or emoji, entry.get("title") or title


def package_pile_text(entries: list[dict | None], gains: list[int], store, guild_id: int) -> tuple[str, str]:
    """패키지 더미를 뜯은 결과: (제목, 설명). 결과마다 한 줄이고, 재화가 최대 보유량 때문에 덜 들어왔다면 그것도 알려 준다."""
    cur = store.currency(guild_id)
    lines = []
    for number, (entry, gained) in enumerate(zip(entries, gains), 1):
        if entry is None:
            lines.append(f"`{number}` 📦 사라진 결과")
            continue
        emoji, title = entry_icon_title(entry, store, guild_id)
        line = f"`{number}` {emoji} {title}"
        if entry["kind"] == "none":
            line += " — 텅 비어 있었어요"
        elif entry["kind"] == "candy" and gained != entry["amount"]:
            line += f" — {cur.emoji} {gained:,}개만 받았어요(최대 보유량)"
        lines.append(line)
    candy = sum(gains)
    footer = f"\n\n{cur.emoji} 재화 합계 **+{candy:,}** · 나머지는 가방에 넣어 둘게요." if candy else "\n\n받은 물품은 가방에 넣어 둘게요."
    return f"📦📦📦 패키지 더미! ({len(entries)}개)", "\n".join(lines) + footer


def package_result_text(entry: dict | None, store, guild_id: int, gained: int = 0) -> tuple[str, str]:
    """(제목 줄, 설명). entry는 결과표의 한 칸. 없으면(표가 바뀌어 사라진 결과) 안내 문구."""
    if entry is None:
        return "📦 사라진 결과", "그 사이 결과표가 바뀌어서 결과를 알 수 없어요."
    emoji, title = entry_icon_title(entry, store, guild_id)
    cur = store.currency(guild_id)
    if entry["kind"] == "none":
        return f"{emoji} {title}!", "텅 비어 있었어요. 다음 기회에!"
    if entry["kind"] == "candy":
        extra = "" if gained == entry["amount"] else f"\n(최대 보유량이라 {gained:,}개만 받았어요)"
        return f"{emoji} {title}!", f"{cur.emoji} {gained:,}개가 가방에 들어왔어요.{extra}"
    return f"{emoji} {title}!", "가방에 넣어 둘게요."
