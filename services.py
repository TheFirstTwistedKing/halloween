"""상점 거래: 물품·역할 사고팔기, EXP 교환. 역할은 디스코드에서 실제로 주고 빼므로 실패하면 되돌린다."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import discord

from store import CANDY_MAX, CANDY_NAME, Store, StoreError
from theme import money

# 상점에서 팔면 안 되는 위험한 권한. 이 권한이 하나라도 있는 역할은 상점에 올릴 수 없다.
DANGEROUS_PERMISSIONS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks",
                         "manage_messages", "manage_nicknames", "manage_threads", "manage_events", "manage_expressions",
                         "kick_members", "ban_members", "moderate_members", "mention_everyone", "view_audit_log",
                         "manage_guild_expressions", "create_expressions")


@dataclass
class Result:
    ok: bool
    text: str


class Locks:
    """같은 사람의 구매·판매가 겹쳐서 이중으로 처리되지 않게 하는 잠금(개수는 고정)."""

    def __init__(self, size: int = 128):
        self._locks = [asyncio.Lock() for _ in range(size)]

    def get(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._locks[hash((guild_id, user_id)) % len(self._locks)]


def role_problem(guild, role) -> str | None:
    """이 역할을 상점에서 줄 수 있는지 확인한다. 문제가 있으면 이유를, 없으면 None."""
    if role.is_default():
        return "@everyone은 상점에 올릴 수 없어요."
    if role.managed:
        return "봇·연동이 관리하는 역할은 줄 수 없어요."
    dangerous = [name for name in DANGEROUS_PERMISSIONS if getattr(role.permissions, name, False)]
    if dangerous:
        return "관리 권한이 있는 역할은 상점에 올릴 수 없어요. (예: " + ", ".join(dangerous[:3]) + ")"
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return "봇에 '역할 관리' 권한이 필요해요."
    if role >= me.top_role:
        return "봇의 역할보다 위에 있는 역할은 줄 수 없어요. 서버 설정에서 봇 역할을 더 위로 옮겨 주세요."
    return None


async def buy(store: Store, locks: Locks, member, item, qty: int) -> Result:
    guild_id = member.guild.id
    async with locks.get(guild_id, member.id):
        item = store.item(guild_id, item["id"])            # 눌러 두었다가 그 사이 바뀐 값을 쓰지 않도록 다시 읽는다.
        try:
            if item is None:
                raise StoreError("이제 상점에 없는 물품이에요.")
            if item["kind"] == "role":
                return await _buy_role(store, member, item)
            total = store.buy_item(guild_id, member.id, item["id"], qty)
        except StoreError as exc:
            return Result(False, str(exc))
        return Result(True, f"{money(total)}을 내고 {item['emoji']} **{item['name']}** ×{qty}을(를) 샀어요.")


async def _buy_role(store: Store, member, item) -> Result:
    guild = member.guild
    role = guild.get_role(item["role_id"])
    if role is None:
        return Result(False, "역할이 사라졌어요. 관리자에게 알려 주세요.")
    if not item["buyable"]:
        return Result(False, "지금은 살 수 없는 역할이에요.")
    if role in member.roles:
        return Result(False, "이미 가지고 있는 역할이에요.")
    problem = role_problem(guild, role)
    if problem:
        return Result(False, problem)
    price = item["buy_price"]
    store.spend_candy(guild.id, member.id, price)          # 부족하면 StoreError → 위에서 안내
    try:
        await member.add_roles(role, reason="할로윈 상점 구매")
    except discord.HTTPException:
        store.add_candy(guild.id, member.id, price)        # 역할을 못 줬으면 낸 재화를 돌려준다.
        return Result(False, "역할을 줄 수 없어서 재화를 돌려 드렸어요. 관리자에게 알려 주세요.")
    return Result(True, f"{money(price)}을 내고 {item['emoji']} {role.mention} 역할을 받았어요!")


async def sell(store: Store, locks: Locks, member, item, qty: int) -> Result:
    guild = member.guild
    async with locks.get(guild.id, member.id):
        item = store.item(guild.id, item["id"])
        try:
            if item is None:
                raise StoreError("이제 상점에 없는 물품이에요.")
            if item["kind"] == "role":
                return await _sell_role(store, member, item)
            total = store.sell_item(guild.id, member.id, item["id"], qty)
        except StoreError as exc:
            return Result(False, str(exc))
        return Result(True, f"{item['emoji']} **{item['name']}** ×{qty}을(를) 팔고 {money(total)}을 받았어요.")


async def _sell_role(store: Store, member, item) -> Result:
    guild = member.guild
    role = guild.get_role(item["role_id"])
    if role is None:
        return Result(False, "역할이 사라졌어요. 관리자에게 알려 주세요.")
    if not item["sellable"]:
        return Result(False, "지금은 팔 수 없는 역할이에요.")
    if role not in member.roles:
        return Result(False, "가지고 있지 않은 역할이에요.")
    price = item["sell_price"]
    candy, _ = store.wallet(guild.id, member.id)
    if candy + price > CANDY_MAX:
        return Result(False, f"{CANDY_NAME}이 최대({CANDY_MAX:,}개)를 넘어서 팔 수 없어요. 먼저 재화를 써 주세요.")
    try:
        await member.remove_roles(role, reason="할로윈 상점 판매")
    except discord.HTTPException:
        return Result(False, "역할을 거둘 수 없어서 판매하지 못했어요. 관리자에게 알려 주세요.")
    store.add_candy(guild.id, member.id, price)
    return Result(True, f"{item['emoji']} {role.mention} 역할을 반납하고 {money(price)}을 받았어요.")


async def exchange(store: Store, locks: Locks, member, batches: int) -> Result:
    async with locks.get(member.guild.id, member.id):
        try:
            cost, gain = store.exchange_exp(member.guild.id, member.id, batches)
        except StoreError as exc:
            return Result(False, str(exc))
    return Result(True, f"✨ EXP {cost:,}을 내고 {money(gain)}을 샀어요.")
