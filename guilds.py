"""길드: 만들기·가입(신청 → 길드장 수락)·탈퇴·추방·위임·해체·금고·버프·랭킹. 디스코드와 상관없이 저장소(store.db)만 다룬다.

- 한 사람은 한 길드에만. 길드장은 길드원이 남아 있으면 먼저 위임해야 나갈 수 있다(혼자면 나가면 해체).
- 금고: 길드원 누구나 재화를 넣고, 길드장만 길드원에게 지급한다. 해체하면 금고는 길드원에게 똑같이 나눈다(나머지는 길드장).
- 버프(길드장이 고른다, 길드 전체): 장비 강화(장착한 무기 공격 +80 · 방패 가드 +10%p · 갑옷 피해 감소 +2%p) 또는 던전 RAM +2%.
  던전에 들어갈 때 정해진다(dungeon_service.enter).
- 길드전 중인 길드는 가입·탈퇴·추방·해체를 할 수 없다(locked_by_war — 길드전은 guild_war.py).
"""
from __future__ import annotations

import time

from josa import i_ga
from store import StoreError

NAME_MAX = 20
BUFFS = {"gear": ("⚙️ 장비 강화", "장착한 무기 공격 +80 · 방패 가드 +10%p · 갑옷 피해 감소 +2%p"),
         "ram": ("💾 RAM 증가", "던전에서 얻는 RAM +2%(클리어 보상·층 보너스·방에서 얻는 재화)")}
from balance import GUILD_GEAR_ATTACK as GEAR_ATTACK, GUILD_GEAR_GUARD as GEAR_GUARD, GUILD_GEAR_REDUCTION as GEAR_REDUCTION, \
    GUILD_RAM_PERCENT as RAM_PERCENT  # noqa: E402,F401  (버프 수치는 balance에 있다: 던전이 쓴다)


class GuildError(StoreError):
    pass


def locked_by_war(store, guild: int, gid: int) -> bool:
    """이 길드가 지금 길드전 중인가(선전포고 대기 포함은 아니다). guild_war가 없거나 표가 없으면 False."""
    try:
        row = store.db.execute("SELECT 1 FROM guild_wars WHERE guild=? AND state='active' AND (attacker=? OR defender=?)",
                               (guild, gid, gid)).fetchone()
    except Exception:                                       # 길드전 표가 아직 없는 저장소(시험 등)
        return False
    return row is not None


def _war_check(store, guild: int, gid: int, what: str) -> None:
    if locked_by_war(store, guild, gid):
        raise GuildError(f"길드전 중에는 {what}할 수 없어요. 길드전이 끝난 뒤에 해 주세요.")


def get(store, guild: int, gid: int):
    return store.db.execute("SELECT * FROM guilds WHERE guild=? AND id=?", (guild, gid)).fetchone()


def by_name(store, guild: int, name: str):
    return store.db.execute("SELECT * FROM guilds WHERE guild=? AND lower(name)=lower(?)", (guild, (name or "").strip())).fetchone()


def all_guilds(store, guild: int):
    return store.db.execute("SELECT * FROM guilds WHERE guild=? ORDER BY id", (guild,)).fetchall()


def of_user(store, guild: int, user: int):
    """그 사람이 들어가 있는 길드(행). 없으면 None."""
    return store.db.execute("SELECT g.* FROM guilds g JOIN guild_members m ON m.gid=g.id AND m.guild=g.guild WHERE m.guild=? AND m.user=?",
                            (guild, user)).fetchone()


def members(store, guild: int, gid: int) -> list[int]:
    """길드원(길드장이 맨 앞)."""
    row = get(store, guild, gid)
    users = [r["user"] for r in store.db.execute("SELECT user FROM guild_members WHERE guild=? AND gid=? ORDER BY rowid", (guild, gid))]
    if row is not None and row["leader"] in users:
        users.remove(row["leader"])
        users.insert(0, row["leader"])
    return users


def requests(store, guild: int, gid: int) -> list[int]:
    return [r["user"] for r in store.db.execute("SELECT user FROM guild_requests WHERE guild=? AND gid=? ORDER BY at, rowid", (guild, gid))]


def buff_of(store, guild: int, user: int) -> str | None:
    row = of_user(store, guild, user)
    return row["buff"] if row is not None and row["buff"] in BUFFS else None


def _leader_guild(store, guild: int, leader: int):
    row = of_user(store, guild, leader)
    if row is None:
        raise GuildError("길드에 들어가 있지 않아요.")
    if row["leader"] != leader:
        raise GuildError("길드장만 할 수 있어요.")
    return row


def _clean_name(name: str) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= NAME_MAX or any(c in name for c in "@#<>`*_~|\n"):
        raise GuildError(f"길드 이름은 1~{NAME_MAX}자로, @ # < > ` * _ ~ | 없이 써 주세요.")
    return name


def create(store, guild: int, user: int, name: str):
    """길드를 만든다(만든 사람이 길드장). 만드는 비용(guild_cost)을 낸다. 이미 길드에 있거나 이름이 겹치면 GuildError."""
    name = _clean_name(name)
    cost = store.settings(guild)["guild_cost"]
    cur = store.currency(guild)
    with store.db:
        if of_user(store, guild, user) is not None:
            raise GuildError("이미 길드에 들어가 있어요. 먼저 `/길드 탈퇴`를 해 주세요.")
        if by_name(store, guild, name) is not None:
            raise GuildError(f"**{name}** 길드가 이미 있어요. 다른 이름을 써 주세요.")
        store._ensure_wallet(guild, user)
        have = store.wallet(guild, user)
        if have < cost:
            raise GuildError(f"길드를 만들려면 {cur.money(cost)}이(가) 필요해요. (가진 것 {cur.money(have)})")
        if cost:
            store.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (cost, guild, user))
        gid = store.db.execute("INSERT INTO guilds(guild, name, leader, created) VALUES(?,?,?,?)", (guild, name, user, time.time())).lastrowid
        store.db.execute("INSERT INTO guild_members VALUES(?,?,?)", (guild, user, gid))
        store.db.execute("DELETE FROM guild_requests WHERE guild=? AND user=?", (guild, user))
    return get(store, guild, gid)


def apply(store, guild: int, user: int, gid: int):
    """가입 신청. 길드장이 수락해야 들어간다."""
    row = get(store, guild, gid)
    if row is None:
        raise GuildError("그런 길드가 없어요.")
    if of_user(store, guild, user) is not None:
        raise GuildError("이미 길드에 들어가 있어요.")
    if len(members(store, guild, gid)) >= store.settings(guild)["guild_size"]:
        raise GuildError(f"**{row['name']}** 길드는 인원이 꽉 찼어요.")
    _war_check(store, guild, gid, "가입 신청")
    with store.db:
        if store.db.execute("SELECT 1 FROM guild_requests WHERE guild=? AND user=? AND gid=?", (guild, user, gid)).fetchone():
            raise GuildError("이미 가입 신청을 했어요. 길드장의 수락을 기다려 주세요.")
        store.db.execute("INSERT INTO guild_requests VALUES(?,?,?,?)", (guild, user, gid, time.time()))
    return row


def cancel_request(store, guild: int, user: int, gid: int) -> bool:
    with store.db:
        return bool(store.db.execute("DELETE FROM guild_requests WHERE guild=? AND user=? AND gid=?", (guild, user, gid)).rowcount)


def accept(store, guild: int, leader: int, user: int):
    """길드장이 가입 신청을 받아 준다. 그 사람의 다른 신청은 모두 지운다."""
    row = _leader_guild(store, guild, leader)
    _war_check(store, guild, row["id"], "가입을 받을")
    if not store.db.execute("SELECT 1 FROM guild_requests WHERE guild=? AND user=? AND gid=?", (guild, user, row["id"])).fetchone():
        raise GuildError("그 사람의 가입 신청이 없어요.")
    if of_user(store, guild, user) is not None:                  # 그 사이 다른 길드에 들어갔다: 남은 신청을 지우고(커밋) 알려 준다
        with store.db:
            store.db.execute("DELETE FROM guild_requests WHERE guild=? AND user=?", (guild, user))
        raise GuildError("그 사람은 이미 다른 길드에 들어갔어요. 신청을 지웠어요.")
    with store.db:
        if len(members(store, guild, row["id"])) >= store.settings(guild)["guild_size"]:
            raise GuildError("길드 인원이 꽉 찼어요.")
        store.db.execute("INSERT INTO guild_members VALUES(?,?,?)", (guild, user, row["id"]))
        store.db.execute("DELETE FROM guild_requests WHERE guild=? AND user=?", (guild, user))
    return row


def decline(store, guild: int, leader: int, user: int):
    row = _leader_guild(store, guild, leader)
    if not cancel_request(store, guild, user, row["id"]):
        raise GuildError("그 사람의 가입 신청이 없어요.")
    return row


def leave(store, guild: int, user: int) -> tuple[object, bool]:
    """길드를 나간다. (길드 행, 해체됐는지). 길드장은 혼자일 때만 나갈 수 있고, 그러면 길드가 해체된다."""
    row = of_user(store, guild, user)
    if row is None:
        raise GuildError("길드에 들어가 있지 않아요.")
    _war_check(store, guild, row["id"], "탈퇴")
    if row["leader"] == user:
        if len(members(store, guild, row["id"])) > 1:
            raise GuildError("길드장은 길드원이 남아 있으면 나갈 수 없어요. 먼저 `/길드 위임`으로 길드장을 넘기거나 `/길드 해체`를 해 주세요.")
        disband(store, guild, user)
        return row, True
    with store.db:
        store.db.execute("DELETE FROM guild_members WHERE guild=? AND user=?", (guild, user))
    return row, False


def kick(store, guild: int, leader: int, user: int):
    row = _leader_guild(store, guild, leader)
    if user == leader:
        raise GuildError("길드장은 스스로를 내보낼 수 없어요.")
    _war_check(store, guild, row["id"], "추방")
    with store.db:
        if not store.db.execute("DELETE FROM guild_members WHERE guild=? AND user=? AND gid=?", (guild, user, row["id"])).rowcount:
            raise GuildError("그 사람은 우리 길드원이 아니에요.")
    return row


def transfer(store, guild: int, leader: int, user: int):
    row = _leader_guild(store, guild, leader)
    if user == leader or user not in members(store, guild, row["id"]):
        raise GuildError("길드장은 우리 길드원에게만 넘길 수 있어요.")
    with store.db:
        store.db.execute("UPDATE guilds SET leader=? WHERE guild=? AND id=?", (user, guild, row["id"]))
    return get(store, guild, row["id"])


def disband(store, guild: int, leader: int) -> dict[int, int]:
    """길드를 없앤다. 금고는 길드원에게 똑같이 나누고 나머지는 길드장에게(최대 보유량을 넘는 만큼은 버려진다). {사람: 받은 재화}."""
    row = _leader_guild(store, guild, leader)
    _war_check(store, guild, row["id"], "해체")
    users = members(store, guild, row["id"])
    share, rest = divmod(row["vault"], len(users))
    cap = store.currency(guild).max
    paid = {}
    with store.db:
        for user in users:
            amount = share + (rest if user == row["leader"] else 0)
            store._ensure_wallet(guild, user)
            old = store.wallet(guild, user)
            new = min(cap, old + amount)
            store.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (new, guild, user))
            paid[user] = new - old
        store.db.execute("DELETE FROM guild_members WHERE guild=? AND gid=?", (guild, row["id"]))
        store.db.execute("DELETE FROM guild_requests WHERE guild=? AND gid=?", (guild, row["id"]))
        store.db.execute("DELETE FROM guilds WHERE guild=? AND id=?", (guild, row["id"]))
    return paid


def deposit(store, guild: int, user: int, amount: int) -> int:
    """길드 금고에 재화를 넣는다. 지금 금고 금액을 돌려준다."""
    row = of_user(store, guild, user)
    if row is None:
        raise GuildError("길드에 들어가 있지 않아요.")
    if amount < 1:
        raise GuildError("1 이상 넣어 주세요.")
    cur = store.currency(guild)
    with store.db:
        store._ensure_wallet(guild, user)
        have = store.wallet(guild, user)
        if have < amount:
            raise GuildError(f"{i_ga(cur.name)} 모자라요. (가진 것 {cur.money(have)})")
        store.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (amount, guild, user))
        store.db.execute("UPDATE guilds SET vault=vault+? WHERE guild=? AND id=?", (amount, guild, row["id"]))
    return get(store, guild, row["id"])["vault"]


def pay(store, guild: int, leader: int, user: int, amount: int) -> int:
    """길드장이 금고에서 길드원(자기 포함)에게 재화를 준다. 받는 사람의 최대 보유량을 넘으면 거절. 지금 금고 금액."""
    row = _leader_guild(store, guild, leader)
    if user not in members(store, guild, row["id"]):
        raise GuildError("금고는 우리 길드원에게만 줄 수 있어요.")
    if amount < 1 or amount > row["vault"]:
        raise GuildError(f"금고에 있는 만큼만 줄 수 있어요. (금고 {store.currency(guild).money(row['vault'])})")
    cur = store.currency(guild)
    with store.db:
        store._ensure_wallet(guild, user)
        if store.wallet(guild, user) + amount > cur.max:
            raise GuildError(f"받는 사람의 {i_ga(cur.name)} 최대({cur.max:,})를 넘어서 줄 수 없어요.")
        store.db.execute("UPDATE guilds SET vault=vault-? WHERE guild=? AND id=?", (amount, guild, row["id"]))
        store.db.execute("UPDATE wallets SET candy=candy+? WHERE guild=? AND user=?", (amount, guild, user))
    return get(store, guild, row["id"])["vault"]


def set_buff(store, guild: int, leader: int, buff: str):
    if buff not in BUFFS:
        raise GuildError("알 수 없는 버프예요.")
    row = _leader_guild(store, guild, leader)
    with store.db:
        store.db.execute("UPDATE guilds SET buff=? WHERE guild=? AND id=?", (buff, guild, row["id"]))
    return get(store, guild, row["id"])


def ranking(store, guild: int) -> list[dict]:
    """길드 순위: 길드전 승리 수 → 길드원 재화 합계 → 금고 순."""
    out = []
    for row in all_guilds(store, guild):
        users = members(store, guild, row["id"])
        out.append({"row": row, "members": len(users), "wealth": sum(store.wallet(guild, u) for u in users)})
    out.sort(key=lambda e: (-e["row"]["wins"], -e["wealth"], -e["row"]["vault"], e["row"]["id"]))
    return out


def buff_text(buff: str | None) -> str:
    return f"{BUFFS[buff][0]} — {BUFFS[buff][1]}" if buff in BUFFS else "없음 (길드장이 `/길드 버프`로 골라요)"

