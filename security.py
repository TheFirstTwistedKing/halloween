"""대테러(서버 테러 대응) 규칙: 설정(화이트·블랙리스트, 알림 받을 관리자 역할, 감지 기준, 잠금 상태)과 감지기. 디스코드와 상관없다.

- 화이트리스트(채널·유저·봇·역할): 모든 대응·감지에서 빠진다. 화이트 채널은 서버 잠금과 도배 감지에서 빠진다.
- 블랙리스트(유저·봇·역할): 유저·봇은 넣는 즉시 차단하고, 다시 들어오면 또 차단한다. 역할은 그 역할을 받는(또는 가지고 들어오는) 사람을 차단한다.
- 감지는 알림만 한다(가입 폭주·도배·채널/역할 대량 삭제·모르는 봇 가입). 대응은 관리자가 명령어로 한다.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

LIST_MAX = 100                     # 목록마다 담을 수 있는 수
ADMIN_ROLES_MAX = 10
ALERT_COOLDOWN = 300               # 같은 종류의 알림은 5분에 한 번
DETECT_DEFAULT = {"on": True, "joins": 10, "join_seconds": 60, "messages": 8, "message_seconds": 10, "deletes": 3, "delete_seconds": 60}
DETECT_LIMITS = {"joins": (2, 500), "join_seconds": (5, 3600), "messages": (3, 100), "message_seconds": (2, 600),
                 "deletes": (2, 100), "delete_seconds": (5, 3600)}
WHITE_KINDS = ("channels", "users", "bots", "roles")
BLACK_KINDS = ("users", "bots", "roles")


def _ids(value) -> list[int]:
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if not isinstance(v, bool) and isinstance(v, int) and v > 0 and v not in out:
            out.append(v)
    return out[:LIST_MAX]


def clean(raw) -> dict:
    """저장된 대테러 설정을 쓸 수 있는 모양으로(없거나 잘못된 칸은 기본값)."""
    raw = raw if isinstance(raw, dict) else {}
    white = raw.get("white") if isinstance(raw.get("white"), dict) else {}
    black = raw.get("black") if isinstance(raw.get("black"), dict) else {}
    detect = dict(DETECT_DEFAULT)
    given = raw.get("detect") if isinstance(raw.get("detect"), dict) else {}
    for key, (low, high) in DETECT_LIMITS.items():
        value = given.get(key)
        if not isinstance(value, bool) and isinstance(value, int) and low <= value <= high:
            detect[key] = value
    if isinstance(given.get("on"), bool):
        detect["on"] = given["on"]
    lock = raw.get("lock") if isinstance(raw.get("lock"), dict) else {}
    channels = {}
    for cid, saved in (lock.get("channels") or {}).items() if isinstance(lock.get("channels"), dict) else []:
        if str(cid).isdigit() and isinstance(saved, dict):
            send = saved.get("send")
            slow = saved.get("slow")
            channels[str(cid)] = {"send": send if send in (True, False, None) else None,
                                  "slow": slow if isinstance(slow, int) and not isinstance(slow, bool) and 0 <= slow <= 21600 else 0}
    return {"admin_roles": _ids(raw.get("admin_roles"))[:ADMIN_ROLES_MAX],
            "white": {k: _ids(white.get(k)) for k in WHITE_KINDS},
            "black": {k: _ids(black.get(k)) for k in BLACK_KINDS},
            "detect": detect,
            "lock": {"active": bool(lock.get("active")) and bool(channels), "channels": channels, "at": lock.get("at") or 0}}


def check(raw) -> None:
    """설정 저장 전 검사(모양만). 잘못되면 ValueError."""
    if not isinstance(raw, dict) or len(raw) > 10:
        raise ValueError("대테러 설정이 올바르지 않아요.")


def is_white_member(cfg: dict, user_id: int, role_ids, is_bot: bool = False) -> bool:
    white = cfg["white"]
    return user_id in white["users"] or (is_bot and user_id in white["bots"]) or any(r in white["roles"] for r in role_ids)


def is_black(cfg: dict, user_id: int, is_bot: bool = False) -> bool:
    black = cfg["black"]
    return user_id in black["users"] or (is_bot and user_id in black["bots"])


def black_roles(cfg: dict, role_ids) -> list[int]:
    return [r for r in role_ids if r in cfg["black"]["roles"]]


class Watch:
    """시간 창 안의 일을 센다. 기준을 넘으면 True(같은 열쇠는 ALERT_COOLDOWN에 한 번만)."""

    def __init__(self):
        self.events: dict = defaultdict(deque)
        self.alerted: dict = {}

    def hit(self, key, count: int, seconds: int, now: float | None = None) -> bool:
        t = time.time() if now is None else now
        q = self.events[key]
        q.append(t)
        while q and q[0] <= t - seconds:
            q.popleft()
        if len(q) >= count and t - self.alerted.get(key, -ALERT_COOLDOWN * 2) >= ALERT_COOLDOWN:
            self.alerted[key] = t
            return True
        return False

    def forget(self, now: float | None = None, older: int = 3600) -> None:
        """오래된 기록을 지운다(메모리가 계속 늘지 않게)."""
        t = time.time() if now is None else now
        for key in [k for k, q in self.events.items() if not q or q[-1] <= t - older]:
            del self.events[key]
        for key in [k for k, at in self.alerted.items() if at <= t - older]:
            del self.alerted[key]
