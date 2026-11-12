"""길드전: 길드장이 다른 길드에 선전포고 → 상대 길드장이 수락하면 정한 시간(기본 24시간) 동안 점수전.

- 점수: 길드전 중에 길드원이 던전을 끝낼 때마다(클리어·다운·나가기 모두) 그 던전에서 오른 층 수만큼,
  클리어하면 +10, 무한모드 최종보스를 쓰러뜨리면 +50을 더 받는다(dungeon_service._finish가 add_points를 부른다).
- 시간이 끝나면 점수가 높은 길드가 이긴다. **진 길드의 길드원 모두가 가진 재화의 50%(내림)** 를 잃고,
  그 합계를 **이긴 길드의 길드원들이 똑같이 나눠 받는다**(받는 사람의 최대 보유량을 넘는 만큼은 버려진다, 나머지는 길드장).
  점수가 같으면 무승부로 아무것도 옮기지 않는다. 길드는 파괴되지 않는다.
- 길드전 중에는 두 길드 모두 가입·탈퇴·추방·해체·금고 입금을 할 수 없다(guilds.locked_by_war).
- 한 길드는 한 번에 하나의 길드전(대기 중인 선전포고 포함)만 할 수 있다.
"""
from __future__ import annotations

import time

import guilds
from guilds import GuildError

CLEAR_POINTS = 10                 # 클리어하면 더 받는 점수
FINAL_POINTS = 50                 # 최종보스를 쓰러뜨리면 더 받는 점수
LOSS_PERCENT = 50                 # 진 길드원이 잃는 재화 %
OPEN = ("pending", "active")


def _now(now):
    return time.time() if now is None else now


def get(store, guild: int, war_id: int):
    return store.db.execute("SELECT * FROM guild_wars WHERE guild=? AND id=?", (guild, war_id)).fetchone()


def open_war(store, guild: int, gid: int):
    """그 길드가 걸려 있는 대기·진행 중인 길드전(행). 없으면 None."""
    return store.db.execute("SELECT * FROM guild_wars WHERE guild=? AND state IN ('pending','active') AND (attacker=? OR defender=?) "
                            "ORDER BY id DESC", (guild, gid, gid)).fetchone()


def history(store, guild: int, gid: int, limit: int = 5):
    return store.db.execute("SELECT * FROM guild_wars WHERE guild=? AND state='done' AND (attacker=? OR defender=?) ORDER BY id DESC LIMIT ?",
                            (guild, gid, gid, limit)).fetchall()


def expire(store, guild: int, now=None) -> int:
    """기간 안에 받지 않은 선전포고를 지운다(취소로 둔다). 지운 수."""
    hours = store.settings(guild)["war_hours"]
    with store.db:
        return store.db.execute("UPDATE guild_wars SET state='cancelled' WHERE guild=? AND state='pending' AND created<=?",
                                (guild, _now(now) - hours * 3600)).rowcount


def declare(store, guild: int, leader: int, target_name: str, now=None):
    """길드장이 다른 길드에 선전포고를 한다(상대 길드장이 수락해야 시작한다)."""
    expire(store, guild, now)
    mine = guilds._leader_guild(store, guild, leader)
    target = guilds.by_name(store, guild, target_name)
    if target is None:
        raise GuildError(f"**{target_name}** 길드가 없어요. 이름은 자동완성에서 골라 주세요.")
    if target["id"] == mine["id"]:
        raise GuildError("우리 길드에는 선전포고를 할 수 없어요.")
    for row, who in ((mine, "우리 길드"), (target, f"**{target['name']}**")):
        if open_war(store, guild, row["id"]) is not None:
            raise GuildError(f"{who}는 이미 길드전(또는 대기 중인 선전포고)이 있어요.")
    with store.db:
        war_id = store.db.execute("INSERT INTO guild_wars(guild, attacker, defender, attacker_name, defender_name, state, created) "
                                  "VALUES(?,?,?,?,?,'pending',?)", (guild, mine["id"], target["id"], mine["name"], target["name"], _now(now))).lastrowid
    return get(store, guild, war_id)


def _pending_for(store, guild: int, leader: int, attacker_name: str | None):
    mine = guilds._leader_guild(store, guild, leader)
    rows = store.db.execute("SELECT * FROM guild_wars WHERE guild=? AND state='pending' AND defender=? ORDER BY id", (guild, mine["id"])).fetchall()
    if attacker_name:
        rows = [r for r in rows if r["attacker_name"].lower() == attacker_name.strip().lower()]
    if not rows:
        raise GuildError("우리 길드에 온 선전포고가 없어요.")
    return mine, rows[0]


def accept(store, guild: int, leader: int, attacker_name: str | None = None, channel: int | None = None, now=None):
    """선전포고를 받는다: 지금부터 war_hours 동안 길드전이 열린다. 결과는 channel(받은 채널)에 알린다."""
    expire(store, guild, now)
    mine, war = _pending_for(store, guild, leader, attacker_name)
    if guilds.get(store, guild, war["attacker"]) is None:
        with store.db:
            store.db.execute("UPDATE guild_wars SET state='cancelled' WHERE id=?", (war["id"],))
        raise GuildError("선전포고한 길드가 이미 없어졌어요.")
    start = _now(now)
    with store.db:
        store.db.execute("UPDATE guild_wars SET state='active', started=?, ends=?, channel=? WHERE id=? AND state='pending'",
                         (start, start + store.settings(guild)["war_hours"] * 3600, channel, war["id"]))
    return get(store, guild, war["id"])


def decline(store, guild: int, leader: int, attacker_name: str | None = None):
    _mine, war = _pending_for(store, guild, leader, attacker_name)
    with store.db:
        store.db.execute("UPDATE guild_wars SET state='declined' WHERE id=?", (war["id"],))
    return war


def cancel(store, guild: int, leader: int):
    """선전포고한 쪽이 아직 받지 않은 선전포고를 거둔다."""
    mine = guilds._leader_guild(store, guild, leader)
    war = store.db.execute("SELECT * FROM guild_wars WHERE guild=? AND state='pending' AND attacker=?", (guild, mine["id"])).fetchone()
    if war is None:
        raise GuildError("거둘 선전포고가 없어요.")
    with store.db:
        store.db.execute("UPDATE guild_wars SET state='cancelled' WHERE id=?", (war["id"],))
    return war


def points_for(run) -> int:
    """던전 하나가 끝났을 때의 길드전 점수: 오른 층 수 + 클리어 +10 + 최종보스 +50."""
    ended = run.phase
    return max(1, run.floor) + (CLEAR_POINTS if ended == "clear" else 0) + (FINAL_POINTS if getattr(run, "final_clear", False) else 0)


def add_points(store, guild: int, user: int, points: int, now=None) -> int:
    """그 사람의 길드가 진행 중인 길드전(끝나는 시각 전)에 점수를 더한다. 더한 점수(길드전이 없으면 0)."""
    row = guilds.of_user(store, guild, user)
    if row is None or points <= 0:
        return 0
    war = store.db.execute("SELECT * FROM guild_wars WHERE guild=? AND state='active' AND ends>? AND (attacker=? OR defender=?)",
                           (guild, _now(now), row["id"], row["id"])).fetchone()
    if war is None:
        return 0
    side = "attacker_score" if war["attacker"] == row["id"] else "defender_score"
    with store.db:
        store.db.execute(f"UPDATE guild_wars SET {side}={side}+? WHERE id=?", (points, war["id"]))
        store.db.execute("INSERT INTO guild_war_points VALUES(?,?,?,?) ON CONFLICT(war,user) DO UPDATE SET points=points+excluded.points",
                         (war["id"], user, row["id"], points))
    return points


def top_scorers(store, war_id: int, gid: int, limit: int = 5) -> list[tuple[int, int]]:
    return [(r["user"], r["points"]) for r in store.db.execute(
        "SELECT user, points FROM guild_war_points WHERE war=? AND gid=? ORDER BY points DESC, user LIMIT ?", (war_id, gid, limit))]


def settle(store, guild: int, war_id: int) -> dict:
    """끝난 길드전의 결과를 정한다(한 번만): 진 길드원 모두에게서 재화의 50%를 거둬 이긴 길드원들에게 똑같이 나눈다.
    {'war', 'winner', 'loser', 'taken': {사람: 잃은 양}, 'given': {사람: 받은 양}, 'total'}. 무승부면 winner None."""
    war = get(store, guild, war_id)
    out = {"war": war, "winner": None, "loser": None, "taken": {}, "given": {}, "total": 0}
    if war is None or war["state"] != "active":
        return out
    if war["attacker_score"] == war["defender_score"]:
        winner = loser = None
    elif war["attacker_score"] > war["defender_score"]:
        winner, loser = war["attacker"], war["defender"]
    else:
        winner, loser = war["defender"], war["attacker"]
    cap = store.currency(guild).max
    with store.db:
        if store.db.execute("UPDATE guild_wars SET state='done', winner=?, moved=0 WHERE id=? AND state='active'", (winner, war_id)).rowcount == 0:
            return out                                       # 이미 다른 곳에서 정했다
        win_row, lose_row = guilds.get(store, guild, winner) if winner else None, guilds.get(store, guild, loser) if loser else None
        if win_row is not None and lose_row is not None:
            taken = {}
            for user in guilds.members(store, guild, loser):
                store._ensure_wallet(guild, user)
                have = store.wallet(guild, user)
                lose = have * LOSS_PERCENT // 100
                if lose:
                    store.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (lose, guild, user))
                taken[user] = lose
            total = sum(taken.values())
            winners = guilds.members(store, guild, winner)
            share, rest = divmod(total, len(winners))
            given = {}
            for user in winners:
                amount = share + (rest if user == win_row["leader"] else 0)
                store._ensure_wallet(guild, user)
                old = store.wallet(guild, user)
                new = min(cap, old + amount)
                store.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (new, guild, user))
                given[user] = new - old
            store.db.execute("UPDATE guild_wars SET moved=? WHERE id=?", (total, war_id))
            store.db.execute("UPDATE guilds SET wins=wins+1 WHERE guild=? AND id=?", (guild, winner))
            store.db.execute("UPDATE guilds SET losses=losses+1 WHERE guild=? AND id=?", (guild, loser))
            out.update(winner=win_row, loser=lose_row, taken=taken, given=given, total=total)
    out["war"] = get(store, guild, war_id)
    return out


def settle_due(store, now=None) -> list[tuple[int, dict]]:
    """시간이 끝난 길드전을 모두 정한다(모든 서버). [(서버, 결과)]."""
    done = []
    rows = store.db.execute("SELECT guild, id FROM guild_wars WHERE state='active' AND ends<=?", (_now(now),)).fetchall()
    for row in rows:
        result = settle(store, row["guild"], row["id"])
        if result["war"] is not None and result["war"]["state"] == "done":
            done.append((row["guild"], result))
    for guild in {r["guild"] for r in store.db.execute("SELECT DISTINCT guild FROM guild_wars WHERE state='pending'")}:
        expire(store, guild, now)
    return done


def time_left(war, now=None) -> str:
    seconds = max(0, int(war["ends"] - _now(now)))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}시간 {rest // 60}분"
