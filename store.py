"""할로윈 봇의 규칙과 저장소. 디스코드에 접속하지 않는 순수 로직이라 그대로 시험할 수 있다."""
from __future__ import annotations

import copy
import json
import random
import re
import sqlite3
from pathlib import Path
from typing import NamedTuple

from josa import i_ga

import balance as balance_rules

CANDY = "🍬"                     # 할로윈 재화의 아이콘
CANDY_NAME = "할로윈 재화"
CANDY_MAX = 99999                # 보유할 수 있는 최대 재화
MAX_SHOP_ITEMS = 25              # 선택창 한도(디스코드): 상점에 올릴 수 있는 물품 수
QTY_MAX = 999                    # 한 번에 사고팔 수 있는 수량
SEED_VERSION = 1                 # 기본 물품·패키지 결과표의 버전. 올리면 이미 운영 중인 서버도 한 번 이전한다(_upgrade_seed).
PACKAGE_CANDY = 50
MAX_PACKAGE_ENTRIES = 20
SYSTEM_ITEMS = {
    "key": ("열쇠", "🗝️", "어디에 쓰는지는 모르지만 왠지 소중해 보이는 낡은 열쇠예요."),
    "trash": ("쓰레기", "🗑️", "쓸모없어 보이는 쓰레기예요. 누군가는 사 갈지도 몰라요."),
}
# 패키지에서 나오는 물품(버전 1에서 추가). (시스템 이름, 이름, 이모지, 설명, 판매가(최소), 판매 최대가(없으면 고정), 패키지 확률 가중치)
# 판매가가 음수면 팔 때 재화를 내고, 최소~최대면 팔 때마다 그 사이에서 뽑는다. 좋은 물품일수록 가중치가 낮고, 나쁜 물품은 흔하다.
NEW_ITEMS = (
    ("witch_hat", "마녀 모자", "🧙", "마녀가 쓰다 놓고 간 뾰족한 모자예요. 은근히 값이 나가요.", 100, None, 5.5),
    ("ghost_cloth", "유령의 천", "👻", "유령이 두르고 있던 얇고 서늘한 천이에요.", 100, None, 5.5),
    ("broken_glass", "깨진 유리조각", "🥃", "날카로운 유리조각이에요. 치우려면 처리 비용을 내야 해요.", -150, None, 8),
    ("jack_o_lantern", "잭 오 랜턴", "🎃", "속이 빈 호박에 불이 켜져 있어요. 꺼지지 않는 신기한 불이에요.", 250, None, 3),
    ("moon_shard", "달 조각", "🌙", "달에서 떨어져 나온 은빛 조각이에요. 아주 귀해요.", 300, None, 1.5),
    ("vampire_wine", "뱀파이어맛 와인", "🍷", "붉고 진한 와인이에요. 한 모금이면 송곳니가 자란다는 소문이 있어요.", 500, None, 0.5),
    ("mystery_bottle", "수상한 액체가 담긴 병", "⚗️", "뭐가 들었는지 아무도 몰라요. 팔 때마다 값이 달라져요.", -300, 300, 4),
)
TRASH_SELL = (-20, 20)           # 쓰레기의 판매가 범위(팔 때마다 이 사이에서 뽑는다)
DEFAULT_SETTINGS = {
    "channels": [],              # 재화·패키지가 나오는 채널
    "candy_chance": 50.0,        # 채팅 한 번당 재화를 얻을 확률(%)
    "candy_min": 1, "candy_max": 5,
    "candy_cooldown": 3,         # 같은 사람이 재화를 다시 얻을 수 있기까지(초). 0이면 제한 없음
    "package_chance": 0.1,       # 채팅 한 번당 패키지가 나올 확률(%)
    "package_mode": "all",       # all: 누구나 각자 한 번씩 / first: 먼저 뜯은 한 명만
    "package_seconds": 120,      # 패키지가 머무는 시간(초)
    "package_cooldown": 30,      # 패키지를 뜯은 사람이 다음 패키지를 뜯을 수 있기까지(초). 0이면 제한 없음
    "reaction": True,            # 재화를 얻으면 메시지에 재화 이모지 반응을 단다
    "currency_emoji": CANDY, "currency_name": CANDY_NAME, "currency_max": CANDY_MAX,   # 재화 자체(/재화설정)
    "seeded": False,             # 기본 물품(열쇠·쓰레기)과 기본 패키지 결과표를 한 번 만들었는지
    "seed_version": 0,           # 기본 물품·결과표를 어느 버전까지 이전했는지(SEED_VERSION)
    "entry_item": None,          # 던전에 들어갈 때 내는 물품 id. None이면 무료 입장
    "entry_cost": 1,             # 입장에 필요한 개수
    "package_table": [],         # 패키지 결과표 [{id, kind(none|candy|item), item, amount, weight, emoji, title}]
    "clear_rewards": [],         # 던전 클리어 때 추가로 주는 물품 [{id, item, amount, chance(%)}]. 재화·획득권은 /던전설정의 보상.* 항목
    "dungeon_content": {},       # 던전 콘텐츠 {monsters|bosses|gear|consumables: [...]}. 없는 항목은 기본값 (balance.py)
    "dungeon": {},               # 던전 밸런스 중 관리자가 바꾼 값 {항목 이름: 숫자}. 비어 있으면 모두 기본값 (balance.py)
}


class StoreError(Exception):
    """사용자에게 그대로 보여줘도 되는 오류 문장."""


def valid_emoji(text: str) -> bool:
    """유니코드 이모지(짧고 글자·숫자가 아닌 것) 또는 서버 이모지(<:이름:ID>)만 허용한다."""
    if re.fullmatch(r"<a?:\w{2,32}:\d{15,25}>", text):
        return True
    return 0 < len(text) <= 8 and not any(c.isalnum() or c.isspace() for c in text)


def validate_settings(cfg: dict) -> None:
    def number(key, low, high, integer=False):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, int if integer else (int, float)) or not low <= value <= high:
            raise ValueError(f"{key} 값은 {low}~{high} 사이여야 해요.")
    number("candy_chance", 0, 100)
    number("package_chance", 0, 100)
    number("candy_min", 0, 1000, True)
    number("candy_max", 0, 1000, True)
    number("candy_cooldown", 0, 3600, True)
    number("package_seconds", 10, 3600, True)
    number("package_cooldown", 0, 3600, True)
    if cfg["candy_min"] > cfg["candy_max"]:
        raise ValueError("최소값이 최대값보다 클 수 없어요.")
    if cfg["package_mode"] not in ("all", "first"):
        raise ValueError("패키지 방식은 all 또는 first여야 해요.")
    if not isinstance(cfg["reaction"], bool):
        raise ValueError("reaction은 켜짐/꺼짐이어야 해요.")
    dungeon = cfg["dungeon"]
    if not isinstance(dungeon, dict) or len(dungeon) > 500 or any(
            not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)) for k, v in dungeon.items()):
        raise ValueError("던전 밸런스 설정이 올바르지 않아요.")
    if not valid_emoji(cfg["currency_emoji"]):
        raise ValueError("재화 이모지는 😀 같은 이모지 하나이거나 서버 이모지여야 해요.")
    if not isinstance(cfg["currency_name"], str) or not 1 <= len(cfg["currency_name"]) <= 20 or cfg["currency_name"] != cfg["currency_name"].strip():
        raise ValueError("재화 이름은 앞뒤 공백 없이 1~20자여야 해요.")
    number("currency_max", 1, 10**9, True)
    if cfg["entry_item"] is not None and (isinstance(cfg["entry_item"], bool) or not isinstance(cfg["entry_item"], int)):
        raise ValueError("입장 물품 설정이 올바르지 않아요.")
    number("entry_cost", 1, 99, True)
    if not isinstance(cfg["seeded"], bool):
        raise ValueError("seeded 값이 올바르지 않아요.")
    number("seed_version", 0, 10**6, True)
    check_package_table(cfg["package_table"])
    check_clear_rewards(cfg["clear_rewards"])
    if not isinstance(cfg["dungeon_content"], dict):
        raise ValueError("던전 콘텐츠 설정이 올바르지 않아요.")
    channels = cfg["channels"]
    if not isinstance(channels, list) or len(channels) > 100 or any(isinstance(c, bool) or not isinstance(c, int) for c in channels):
        raise ValueError("이벤트 채널 목록이 올바르지 않아요.")


# ---------------------------------------------------------------- 확률 (난수 생성기를 받아서 시험하기 쉽다)
def roll_candy(cfg: dict, rng) -> int:
    """채팅 한 번의 재화 보상. 못 받으면 0."""
    if cfg["candy_chance"] <= 0 or rng.random() * 100 >= cfg["candy_chance"]:
        return 0
    return rng.randint(cfg["candy_min"], cfg["candy_max"])


def roll_package_spawn(cfg: dict, rng) -> bool:
    return cfg["package_chance"] > 0 and rng.random() * 100 < cfg["package_chance"]


def check_package_table(table) -> None:
    """패키지 결과표가 올바른지 검사한다(비어 있는 표는 아직 만들기 전이라 허용)."""
    if not isinstance(table, list) or len(table) > MAX_PACKAGE_ENTRIES:
        raise ValueError(f"패키지 결과표가 올바르지 않아요. (최대 {MAX_PACKAGE_ENTRIES}칸)")
    ids = set()
    for e in table:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or e["id"] in ids or e.get("kind") not in ("none", "candy", "item"):
            raise ValueError("패키지 결과표가 올바르지 않아요.")
        ids.add(e["id"])
        w, a = e.get("weight"), e.get("amount", 1)
        if isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 <= w <= 1000 or w != w:
            raise ValueError("패키지 결과의 확률은 0~1000이어야 해요.")
        if isinstance(a, bool) or not isinstance(a, int) or not 1 <= a <= 99999:
            raise ValueError("패키지 결과의 개수는 1~99,999여야 해요.")
        if e["kind"] == "item" and (isinstance(e.get("item"), bool) or not isinstance(e.get("item"), int)):
            raise ValueError("패키지 결과의 물품이 올바르지 않아요.")
        for field in ("emoji", "title"):
            if e.get(field) is not None and not isinstance(e[field], str):
                raise ValueError("패키지 결과의 이름·이모지가 올바르지 않아요.")
    if table and sum(e["weight"] for e in table) <= 0:
        raise ValueError("패키지 결과의 확률이 전부 0이에요. 하나는 0보다 커야 해요.")


MAX_CLEAR_REWARDS = 10


def check_clear_rewards(rewards) -> None:
    if not isinstance(rewards, list) or len(rewards) > MAX_CLEAR_REWARDS:
        raise ValueError(f"클리어 보상이 올바르지 않아요. (최대 {MAX_CLEAR_REWARDS}개)")
    ids = set()
    for r in rewards:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or r["id"] in ids:
            raise ValueError("클리어 보상이 올바르지 않아요.")
        ids.add(r["id"])
        item, amount, chance = r.get("item"), r.get("amount"), r.get("chance")
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError("클리어 보상의 물품이 올바르지 않아요.")
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 99999:
            raise ValueError("클리어 보상의 개수는 1~99,999여야 해요.")
        if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not 0 <= chance <= 100 or chance != chance:
            raise ValueError("클리어 보상의 확률은 0~100(%)이어야 해요.")


def roll_package_result(table, rng) -> dict:
    """결과표에서 확률(비율)대로 한 칸을 뽑는다. 확률의 합이 100일 필요는 없다."""
    total = sum(e["weight"] for e in table)
    draw, edge = rng.random() * total, 0
    for entry in table:
        edge += entry["weight"]
        if draw < edge:
            return entry
    return next(e for e in reversed(table) if e["weight"] > 0)


class Currency(NamedTuple):
    emoji: str
    name: str
    max: int

    def money(self, amount: int) -> str:
        return f"{self.emoji} {amount:,}"


DEFAULT_CURRENCY = Currency(CANDY, CANDY_NAME, CANDY_MAX)


def first_package_table(ids: dict) -> list[dict]:
    """처음 만들어지던 기본 패키지 결과표(열쇠·쓰레기만 있는 것). 서버가 아직 이 표 그대로인지 알아보는 데에도 쓴다."""
    return [
        {"id": "none", "kind": "none", "weight": 60, "amount": 1, "emoji": "👻", "title": "꽝"},
        {"id": "key", "kind": "item", "item": ids["key"], "weight": 20, "amount": 1},
        {"id": "candy", "kind": "candy", "weight": 10, "amount": PACKAGE_CANDY},
        {"id": "trash", "kind": "item", "item": ids["trash"], "weight": 10, "amount": 1}]


def default_package_table(ids: dict, new_ids: dict) -> list[dict]:
    """버전 1의 기본 패키지 결과표(가중치 합 100이라 가중치가 곧 퍼센트). 값어치가 높을수록 드물고, 쓸모없거나 손해인 물품은 흔하다."""
    table = [
        {"id": "none", "kind": "none", "weight": 40, "amount": 1, "emoji": "👻", "title": "꽝"},
        {"id": "key", "kind": "item", "item": ids["key"], "weight": 14, "amount": 1},
        {"id": "candy", "kind": "candy", "weight": 7, "amount": PACKAGE_CANDY},
        {"id": "trash", "kind": "item", "item": ids["trash"], "weight": 11, "amount": 1}]
    for system, _name, _emoji, _description, _low, _high, weight in NEW_ITEMS:
        table.append({"id": system, "kind": "item", "item": new_ids[system], "weight": weight, "amount": 1})
    return table


# ---------------------------------------------------------------- 저장소
class Store:
    def __init__(self, path):
        self.in_memory = str(path) == ":memory:"
        if not self.in_memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self._settings: dict[int, dict] = {}
        self._balances: dict[int, balance_rules.Balance] = {}
        self._rng = random.Random()                          # 판매가 범위를 뽑을 때(시험에서는 sell_item에 직접 넘긴다)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(guild INTEGER PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS wallets(
                guild INTEGER, user INTEGER, candy INTEGER NOT NULL DEFAULT 0 CHECK(candy >= 0),
                exp INTEGER NOT NULL DEFAULT 0 CHECK(exp >= 0), PRIMARY KEY(guild, user));
            CREATE TABLE IF NOT EXISTS items(
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL CHECK(kind IN ('item','role')), role_id INTEGER,
                buy_price INTEGER NOT NULL DEFAULT 0, sell_price INTEGER NOT NULL DEFAULT 0,
                buyable INTEGER NOT NULL DEFAULT 0, sellable INTEGER NOT NULL DEFAULT 0, system TEXT,
                sell_max INTEGER);
            CREATE UNIQUE INDEX IF NOT EXISTS items_name ON items(guild, lower(name));
            CREATE UNIQUE INDEX IF NOT EXISTS items_role ON items(guild, role_id) WHERE role_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS items_system ON items(guild, system) WHERE system IS NOT NULL;
            CREATE TABLE IF NOT EXISTS inventory(
                guild INTEGER, user INTEGER, item INTEGER, qty INTEGER NOT NULL CHECK(qty > 0), PRIMARY KEY(guild, user, item));
            CREATE TABLE IF NOT EXISTS shops(channel INTEGER PRIMARY KEY, guild INTEGER NOT NULL, message INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS packages(
                message INTEGER PRIMARY KEY, guild INTEGER NOT NULL, channel INTEGER NOT NULL,
                expires REAL NOT NULL, mode TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS package_opens(
                message INTEGER, user INTEGER, result TEXT NOT NULL, PRIMARY KEY(message, user));
            CREATE TABLE IF NOT EXISTS package_cooldowns(
                guild INTEGER, user INTEGER, at REAL NOT NULL, PRIMARY KEY(guild, user));
            CREATE TABLE IF NOT EXISTS dungeons(
                guild INTEGER, user INTEGER, data TEXT NOT NULL, PRIMARY KEY(guild, user));
            CREATE TABLE IF NOT EXISTS tickets(
                guild INTEGER, user INTEGER, count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0), PRIMARY KEY(guild, user));
        """)
        # 판매가 범위(sell_max)가 생기기 전에 만든 DB에는 칸이 없다. 기존 데이터는 그대로 두고 칸만 더한다.
        if "sell_max" not in {row["name"] for row in self.db.execute("PRAGMA table_info(items)")}:
            with self.db:
                self.db.execute("ALTER TABLE items ADD COLUMN sell_max INTEGER")

    def close(self):
        self.db.close()

    # ---- 설정 ----
    def settings(self, guild: int) -> dict:
        cached = self._settings.get(guild)
        if cached is None:
            row = self.db.execute("SELECT data FROM settings WHERE guild=?", (guild,)).fetchone()
            cached = copy.deepcopy(DEFAULT_SETTINGS)
            if row:
                cached.update({k: v for k, v in json.loads(row["data"]).items() if k in DEFAULT_SETTINGS})   # 예전 버전의 exp_* 같은 옛 키는 버린다
            self._settings[guild] = cached
        return copy.deepcopy(cached)

    def update_settings(self, guild: int, **changes) -> dict:
        if changes.keys() - DEFAULT_SETTINGS.keys():
            raise ValueError("알 수 없는 설정이에요.")
        cfg = self.settings(guild)
        cfg.update(changes)
        validate_settings(cfg)
        with self.db:
            self.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data",
                            (guild, json.dumps(cfg, ensure_ascii=False)))
        self._settings[guild] = copy.deepcopy(cfg)
        self._balances.pop(guild, None)
        return cfg

    def balance(self, guild: int) -> "balance_rules.Balance":
        """이 서버의 던전 밸런스(기본값 위에 관리자가 바꾼 값을 덮어쓴 것). 설정이 바뀔 때까지 캐시한다."""
        cached = self._balances.get(guild)
        if cached is None:
            cfg = self.settings(guild)
            cached = self._balances[guild] = balance_rules.Balance.build(cfg["dungeon"], cfg["dungeon_content"])
        return cached

    # ---- 지갑 ----
    def currency(self, guild: int) -> Currency:
        """이 서버의 재화(이모지·이름·최대 보유량)."""
        cfg = self._settings.get(guild) or self.settings(guild)
        return Currency(cfg["currency_emoji"], cfg["currency_name"], cfg["currency_max"])

    def set_currency(self, guild: int, **changes) -> tuple[Currency, int]:
        """재화의 이모지·이름·최대 보유량(emoji/name/max)을 바꾼다. 최대를 낮추면 그보다 많이 가진 사람은 새 최대로 맞춰진다.
        (새 재화, 줄어든 사람 수)를 돌려준다."""
        mapping = {"emoji": "currency_emoji", "name": "currency_name", "max": "currency_max"}
        if changes.keys() - mapping.keys():
            raise ValueError("알 수 없는 재화 설정이에요.")
        cfg = self.settings(guild)
        cfg.update({mapping[k]: v for k, v in changes.items()})
        validate_settings(cfg)                      # 잘못된 값이면 여기서 막아서 지갑을 건드리지 않는다.
        trimmed = 0
        with self.db:
            if "max" in changes:
                trimmed = self.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND candy>?",
                                          (changes["max"], guild, changes["max"])).rowcount
            self.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data",
                            (guild, json.dumps(cfg, ensure_ascii=False)))
        self._settings[guild] = copy.deepcopy(cfg)
        self._balances.pop(guild, None)
        return self.currency(guild), trimmed

    def wallets_above(self, guild: int, limit: int) -> int:
        """재화를 limit보다 많이 가진 사람 수(최대 보유량을 낮추면 줄어드는 사람 수)."""
        return self.db.execute("SELECT COUNT(*) AS n FROM wallets WHERE guild=? AND candy>?", (guild, limit)).fetchone()["n"]

    def wallet(self, guild: int, user: int) -> int:
        row = self.db.execute("SELECT candy FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()
        return row["candy"] if row else 0

    def _ensure_wallet(self, guild, user):
        self.db.execute("INSERT OR IGNORE INTO wallets(guild, user) VALUES(?,?)", (guild, user))

    def add_candy(self, guild: int, user: int, amount: int) -> int:
        """재화를 더하고 실제로 바뀐 양을 돌려준다. 최대 보유량을 넘는 만큼은 버려지고, 0 아래로는 내려가지 않는다."""
        with self.db:
            self._ensure_wallet(guild, user)
            old = self.db.execute("SELECT candy FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()["candy"]
            new = min(self.currency(guild).max, max(0, old + amount))
            self.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (new, guild, user))
        return new - old

    def add_candy_many(self, guild: int, users, amount: int) -> tuple[int, int, int]:
        """여러 명에게 같은 양의 재화를 한 번에 더한다(전부 한 번의 저장이라 수천 명도 빠르고, 중간에 멈춰도 일부만 반영되지 않는다).
        각자 최대 보유량과 0을 넘지 않게 맞춘다. (대상 수, 실제로 바뀐 재화의 합, 요청한 양만큼 바뀌지 못한 사람 수)를 돌려준다."""
        users = list(dict.fromkeys(users))
        total = partial = 0
        top = self.currency(guild).max
        with self.db:
            for user in users:
                self._ensure_wallet(guild, user)
                old = self.db.execute("SELECT candy FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()["candy"]
                new = min(top, max(0, old + amount))
                self.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (new, guild, user))
                total += new - old
                partial += new - old != amount
        return len(users), total, partial

    # ---- 물품 ----
    def _check_prices(self, guild, buyable, sellable, buy_price, sell_price, sell_max=None, kind="item"):
        """가격 검사. 판매가는 음수(팔 때 재화를 낸다)일 수 있고, sell_max를 주면 판매가~sell_max 사이에서 팔 때마다 뽑는다."""
        top = self.currency(guild).max
        for price, low, label in ((buy_price, 0, "구입 가격"), (sell_price, -top, "판매 가격")):
            if isinstance(price, bool) or not isinstance(price, int) or not low <= price <= top:
                raise StoreError(f"{label}은 {low:,}~{top:,} 사이의 정수여야 해요.")
        if sell_max is not None:
            if isinstance(sell_max, bool) or not isinstance(sell_max, int) or not sell_price <= sell_max <= top:
                raise StoreError(f"판매 최대가는 판매가({sell_price:,}) 이상 {top:,} 이하의 정수여야 해요. (판매가를 올렸다면 판매최대도 함께 정해 주세요)")
        if kind == "role" and (sell_price < 0 or sell_max is not None):
            raise StoreError("역할의 판매가는 1 이상의 고정값만 쓸 수 있어요.")
        if buyable and buy_price < 1:
            raise StoreError("구입을 켜려면 구매가를 1 이상으로 정해 주세요.")
        if sellable and sell_price == 0 and not sell_max:
            raise StoreError("판매를 켜려면 판매가를 0이 아닌 값으로 정해 주세요. (음수면 팔 때 재화를 내고, 판매최대를 주면 범위가 돼요)")

    @staticmethod
    def _check_text(name, emoji, description):
        if not 1 <= len(name) <= 30 or name != name.strip():
            raise StoreError("이름은 앞뒤 공백 없이 1~30자로 써 주세요.")
        if not valid_emoji(emoji):
            raise StoreError("이모지는 😀 같은 이모지 하나이거나 서버 이모지여야 해요.")
        if len(description) > 200:
            raise StoreError("설명은 200자까지예요.")

    def ensure_defaults(self, guild: int) -> None:
        """서버마다 한 번: 기본 물품(열쇠·쓰레기), 던전 입장 물품(열쇠), 기본 패키지 결과표를 만든다.
        그 뒤로는 관리자가 이름·이모지를 바꾸거나 지워도 다시 만들지 않는다. (예전 버전이 이미 만든 열쇠·쓰레기는 그대로 쓴다.)
        기본 구성이 바뀌어 SEED_VERSION이 올라가면 이미 운영 중인 서버도 한 번만 이전한다(_upgrade_seed)."""
        cfg = self.settings(guild)
        if not cfg["seeded"]:
            with self.db:
                for key, (name, emoji, description) in SYSTEM_ITEMS.items():
                    self.db.execute("INSERT OR IGNORE INTO items(guild, name, emoji, description, kind, system) "
                                    "VALUES(?,?,?,?,'item',?)", (guild, name, emoji, description, key))
            ids = {row["system"]: row["id"] for row in
                   self.db.execute("SELECT id, system FROM items WHERE guild=? AND system IS NOT NULL", (guild,)).fetchall()}
            self.update_settings(guild, seeded=True, entry_item=ids["key"], entry_cost=1, package_table=first_package_table(ids))
            cfg = self.settings(guild)
        if cfg["seed_version"] < SEED_VERSION:
            self._upgrade_seed(guild)

    def _upgrade_seed(self, guild: int) -> None:
        """버전 1: 패키지에서 나오는 물품 7종을 만들고, 쓰레기를 팔 수 있게 하고, 패키지 결과표를 좋은 물품은 드물게·나쁜 물품은 흔하게 바꾼다.
        - 관리자가 같은 이름의 물품을 이미 만들어 뒀다면 그 물품을 그대로 쓰고 값은 건드리지 않는다.
        - 쓰레기는 아직 손대지 않았을 때만(판매 꺼짐·판매가 0) 판매가 범위를 정한다.
        - 결과표는 예전 기본값 그대로일 때만 새 기본값으로 바꾼다. 관리자가 손댄 표는 건드리지 않는다(물품만 만들어 둔다).
        - 상점에는 최대 MAX_SHOP_ITEMS개까지만 올라가서, 넘치는 물품은 판매를 꺼 둔 채로 만든다(/물품수정으로 켤 수 있다)."""
        new_ids = self._ensure_new_items(guild)
        with self.db:
            listed = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE guild=? AND (buyable=1 OR sellable=1)", (guild,)).fetchone()["n"]
            trash = self.db.execute("SELECT * FROM items WHERE guild=? AND system='trash'", (guild,)).fetchone()
            if (trash and not trash["sellable"] and not trash["buyable"] and trash["sell_price"] == 0
                    and trash["sell_max"] is None and listed < MAX_SHOP_ITEMS):
                self.db.execute("UPDATE items SET sell_price=?, sell_max=?, sellable=1 WHERE guild=? AND id=?",
                                (*TRASH_SELL, guild, trash["id"]))
        cfg = self.settings(guild)
        ids = {row["system"]: row["id"] for row in
               self.db.execute("SELECT id, system FROM items WHERE guild=? AND system IN ('key','trash')", (guild,)).fetchall()}
        table = cfg["package_table"]
        if "key" in ids and "trash" in ids and table == first_package_table(ids):
            table = default_package_table(ids, new_ids)
        self.update_settings(guild, seed_version=SEED_VERSION, package_table=table)

    def _ensure_new_items(self, guild: int) -> dict[str, int]:
        """패키지 물품 7종 중 없는 것을 만든다. 시스템 이름이나 같은 이름의 물품이 이미 있으면 그걸 그대로 쓰고 값은 건드리지 않는다.
        상점 25칸이 차면 남은 물품은 판매가 꺼진 채 만든다. {시스템 이름: 물품 id}를 돌려준다."""
        listed = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE guild=? AND (buyable=1 OR sellable=1)", (guild,)).fetchone()["n"]
        new_ids = {}
        with self.db:
            for system, name, emoji, description, low, high, _weight in NEW_ITEMS:
                row = (self.db.execute("SELECT id FROM items WHERE guild=? AND system=?", (guild, system)).fetchone()
                       or self.db.execute("SELECT id FROM items WHERE guild=? AND lower(name)=lower(?)", (guild, name)).fetchone())
                if row is None:
                    sellable = listed < MAX_SHOP_ITEMS
                    row = self.db.execute(
                        "INSERT INTO items(guild, name, emoji, description, kind, sell_price, sell_max, sellable, system) "
                        "VALUES(?,?,?,?,'item',?,?,?,?) RETURNING id", (guild, name, emoji, description, low, high, int(sellable), system)).fetchone()
                    listed += sellable
                new_ids[system] = row["id"]
        return new_ids

    def apply_default_package_table(self, guild: int) -> list[dict]:
        """패키지 결과표를 지금의 기본 확률표로 통째로 바꾼다(관리자가 손댄 표여도). 표에 필요한 물품(열쇠·쓰레기·새 물품 7종) 중 없는 것은 만들고,
        있는 것은 이름·가격을 그대로 둔다. 가방·지갑·다른 설정은 건드리지 않는다. 새 표를 돌려준다."""
        self.ensure_defaults(guild)
        with self.db:
            for key, (name, emoji, description) in SYSTEM_ITEMS.items():
                has = (self.db.execute("SELECT 1 FROM items WHERE guild=? AND system=?", (guild, key)).fetchone()
                       or self.db.execute("SELECT 1 FROM items WHERE guild=? AND lower(name)=lower(?)", (guild, name)).fetchone())
                if not has:
                    self.db.execute("INSERT INTO items(guild, name, emoji, description, kind, system) VALUES(?,?,?,?,'item',?)",
                                    (guild, name, emoji, description, key))
        new_ids = self._ensure_new_items(guild)
        ids = {}
        for key, (name, _emoji, _description) in SYSTEM_ITEMS.items():
            row = (self.db.execute("SELECT id FROM items WHERE guild=? AND system=?", (guild, key)).fetchone()
                   or self.db.execute("SELECT id FROM items WHERE guild=? AND lower(name)=lower(?)", (guild, name)).fetchone())
            ids[key] = row["id"]
        table = default_package_table(ids, new_ids)
        self.update_settings(guild, package_table=table)
        return table

    def ensure_system_items(self, guild: int) -> dict[str, int]:
        """기본 물품 중 아직 남아 있는 것의 id({'key': ..., 'trash': ...}). 관리자가 지웠다면 그 항목은 빠진다."""
        self.ensure_defaults(guild)
        rows = self.db.execute("SELECT id, system FROM items WHERE guild=? AND system IS NOT NULL", (guild,)).fetchall()
        return {row["system"]: row["id"] for row in rows}

    def package_table(self, guild: int) -> list[dict]:
        self.ensure_defaults(guild)
        return self.settings(guild)["package_table"]

    def item_users(self, guild: int, item_id: int) -> list[str]:
        """이 물품을 쓰고 있는 설정(던전 입장, 패키지 결과). 쓰는 곳이 있으면 지울 수 없다."""
        cfg = self.settings(guild)
        out = []
        if cfg["entry_item"] == item_id:
            out.append("던전 입장 물품(`/던전입장물품`)")
        if any(e["kind"] == "item" and e.get("item") == item_id for e in cfg["package_table"]):
            out.append("패키지 결과표(`/패키지결과 목록`)")
        if any(r["item"] == item_id for r in cfg["clear_rewards"]):
            out.append("던전 클리어 보상(`/던전보상 목록`)")
        return out

    def create_item(self, guild, name, *, emoji="🎃", description="", kind="item", role_id=None,
                    buy_price=0, sell_price=0, buyable=False, sellable=False, sell_max=None):
        self._check_text(name, emoji, description)
        sell_max = None if sell_max == sell_price else sell_max          # 최소=최대면 고정값
        self._check_prices(guild, buyable, sellable, buy_price, sell_price, sell_max, kind)
        if kind == "role" and role_id is None:
            raise StoreError("역할 물품에는 역할이 필요해요.")
        total = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE guild=? AND (buyable=1 OR sellable=1)", (guild,)).fetchone()["n"]
        if (buyable or sellable) and total >= MAX_SHOP_ITEMS:
            raise StoreError(f"상점에는 물품을 최대 {MAX_SHOP_ITEMS}개까지 올릴 수 있어요.")
        try:
            with self.db:
                cursor = self.db.execute(
                    "INSERT INTO items(guild, name, emoji, description, kind, role_id, buy_price, sell_price, sell_max, buyable, sellable) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (guild, name, emoji, description, kind, role_id, buy_price, sell_price, sell_max, int(buyable), int(sellable)))
        except sqlite3.IntegrityError as exc:
            if "role_id" in str(exc):
                raise StoreError("이미 상점 물품으로 등록된 역할이에요.") from exc
            raise StoreError("같은 이름의 물품이 이미 있어요.") from exc
        return self.item(guild, cursor.lastrowid)

    def item(self, guild: int, item_id: int):
        return self.db.execute("SELECT * FROM items WHERE guild=? AND id=?", (guild, item_id)).fetchone()

    def item_by_name(self, guild: int, name: str):
        return self.db.execute("SELECT * FROM items WHERE guild=? AND lower(name)=lower(?)", (guild, name.strip())).fetchone()

    def items(self, guild: int, *, shop_only: bool = False):
        query = "SELECT * FROM items WHERE guild=?" + (" AND (buyable=1 OR sellable=1)" if shop_only else "")
        return self.db.execute(query + " ORDER BY kind, id", (guild,)).fetchall()

    def update_item(self, guild: int, item_id: int, **changes):
        row = self.item(guild, item_id)
        if row is None:
            raise StoreError("그런 물품이 없어요.")
        merged = {k: row[k] for k in ("name", "emoji", "description", "buy_price", "sell_price", "sell_max", "buyable", "sellable")}
        merged.update(changes)
        self._check_text(merged["name"], merged["emoji"], merged["description"])
        merged["buyable"], merged["sellable"] = bool(merged["buyable"]), bool(merged["sellable"])
        if merged["sell_max"] == merged["sell_price"]:                   # 최소=최대면 고정값
            merged["sell_max"] = None
        self._check_prices(guild, merged["buyable"], merged["sellable"], merged["buy_price"], merged["sell_price"],
                           merged["sell_max"], row["kind"])
        listed = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE guild=? AND (buyable=1 OR sellable=1) AND id<>?",
                                 (guild, item_id)).fetchone()["n"]
        if (merged["buyable"] or merged["sellable"]) and listed >= MAX_SHOP_ITEMS:
            raise StoreError(f"상점에는 물품을 최대 {MAX_SHOP_ITEMS}개까지 올릴 수 있어요.")
        try:
            with self.db:
                self.db.execute("UPDATE items SET name=?, emoji=?, description=?, buy_price=?, sell_price=?, sell_max=?, buyable=?, sellable=? "
                                "WHERE guild=? AND id=?",
                                (merged["name"], merged["emoji"], merged["description"], merged["buy_price"], merged["sell_price"],
                                 merged["sell_max"], int(merged["buyable"]), int(merged["sellable"]), guild, item_id))
        except sqlite3.IntegrityError as exc:
            raise StoreError("같은 이름의 물품이 이미 있어요.") from exc
        return self.item(guild, item_id)

    def holdings(self, guild: int, item_id: int) -> int:
        """이 물품이 사람들의 가방에 들어 있는 총 개수."""
        return self.db.execute("SELECT COALESCE(SUM(qty), 0) AS n FROM inventory WHERE guild=? AND item=?", (guild, item_id)).fetchone()["n"]

    def delete_item(self, guild: int, item_id: int) -> int:
        """물품을 지우고, 사람들의 가방에서도 뺀다. 가방에서 사라진 개수를 돌려준다."""
        self.ensure_defaults(guild)
        row = self.item(guild, item_id)
        if row is None:
            raise StoreError("그런 물품이 없어요.")
        users = self.item_users(guild, item_id)
        if users:
            raise StoreError("이 물품을 쓰는 설정이 있어서 지울 수 없어요: " + ", ".join(users) + ". 먼저 그쪽을 바꿔 주세요.")
        with self.db:
            lost = self.db.execute("SELECT COALESCE(SUM(qty), 0) AS n FROM inventory WHERE guild=? AND item=?", (guild, item_id)).fetchone()["n"]
            self.db.execute("DELETE FROM inventory WHERE guild=? AND item=?", (guild, item_id))
            self.db.execute("DELETE FROM items WHERE guild=? AND id=?", (guild, item_id))
        return lost

    # ---- 가방 ----
    def qty(self, guild: int, user: int, item_id: int) -> int:
        row = self.db.execute("SELECT qty FROM inventory WHERE guild=? AND user=? AND item=?", (guild, user, item_id)).fetchone()
        return row["qty"] if row else 0

    def add_item(self, guild: int, user: int, item_id: int, amount: int) -> int:
        """물품을 더하거나(양수) 뺀다(음수). 없는 만큼은 빼지 않는다. 실제로 바뀐 양을 돌려준다."""
        with self.db:
            old = self.qty(guild, user, item_id)
            new = max(0, old + amount)
            if new:
                self.db.execute("INSERT INTO inventory VALUES(?,?,?,?) ON CONFLICT(guild,user,item) DO UPDATE SET qty=excluded.qty",
                                (guild, user, item_id, new))
            else:
                self.db.execute("DELETE FROM inventory WHERE guild=? AND user=? AND item=?", (guild, user, item_id))
        return new - old

    def inventory(self, guild: int, user: int):
        return self.db.execute(
            "SELECT items.*, inventory.qty AS qty FROM inventory JOIN items ON items.id = inventory.item "
            "WHERE inventory.guild=? AND inventory.user=? AND items.kind='item' ORDER BY items.system IS NULL, items.id",
            (guild, user)).fetchall()

    # ---- 사고팔기 (물품) ----
    def buy_item(self, guild: int, user: int, item_id: int, qty: int) -> int:
        """재화를 내고 물품을 받는다. 낸 재화를 돌려준다."""
        item = self._tradeable(guild, item_id, qty, "item")
        if not item["buyable"]:
            raise StoreError("지금은 살 수 없는 물품이에요.")
        total = item["buy_price"] * qty
        with self.db:
            cur, candy = self.currency(guild), self.wallet(guild, user)
            if candy < total:
                raise StoreError(f"{i_ga(cur.name)} 부족해요. 필요 {cur.emoji}{total:,} · 보유 {cur.emoji}{candy:,}")
            self._ensure_wallet(guild, user)
            self.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (total, guild, user))
            self.add_item(guild, user, item_id, qty)
        return total

    def sell_item(self, guild: int, user: int, item_id: int, qty: int, rng=None) -> int:
        """물품을 팔고 재화를 받는다. 받은 재화(팔 때 재화를 내는 물품이면 음수)를 돌려준다.
        판매가가 범위(판매가~판매 최대가)면 한 개마다 그 사이에서 뽑는다. 최대 보유량을 넘길 수 있거나, 낼 재화가 모자랄 수 있으면
        아무것도 팔지 않고 거절한다(운이 좋을 때까지 다시 눌러 보는 일을 막으려고 가장 나쁜 경우를 기준으로 본다)."""
        item = self._tradeable(guild, item_id, qty, "item")
        if not item["sellable"]:
            raise StoreError("지금은 팔 수 없는 물품이에요.")
        low = item["sell_price"]
        high = low if item["sell_max"] is None else item["sell_max"]
        with self.db:
            have = self.qty(guild, user, item_id)
            if have < qty:
                raise StoreError(f"가진 수량이 모자라요. 보유 {have}개")
            cur, candy = self.currency(guild), self.wallet(guild, user)
            if low < 0 and candy < -low * qty:
                raise StoreError(f"{i_ga(cur.name)} 모자라서 팔 수 없어요. 이 물품은 팔 때 최대 {cur.emoji}{-low * qty:,}까지 내야 해요. (보유 {cur.emoji}{candy:,})")
            if high > 0 and candy + high * qty > cur.max:
                raise StoreError(f"{i_ga(cur.name)} 최대({cur.max:,}개)를 넘어서 팔 수 없어요. 먼저 재화를 써 주세요.")
            rng = rng or self._rng
            total = low * qty if high == low else sum(rng.randint(low, high) for _ in range(qty))
            self.add_item(guild, user, item_id, -qty)
            self._ensure_wallet(guild, user)
            self.db.execute("UPDATE wallets SET candy=candy+? WHERE guild=? AND user=?", (total, guild, user))
        return total

    def _tradeable(self, guild, item_id, qty, kind):
        item = self.item(guild, item_id)
        if item is None:
            raise StoreError("이제 상점에 없는 물품이에요.")
        if item["kind"] != kind:
            raise StoreError("이 방식으로 거래할 수 없는 물품이에요.")
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= QTY_MAX:
            raise StoreError(f"수량은 1~{QTY_MAX}개로 입력해 주세요.")
        return item

    def spend_candy(self, guild: int, user: int, amount: int):
        with self.db:
            cur, candy = self.currency(guild), self.wallet(guild, user)
            if candy < amount:
                raise StoreError(f"{i_ga(cur.name)} 부족해요. 필요 {cur.emoji}{amount:,} · 보유 {cur.emoji}{candy:,}")
            self._ensure_wallet(guild, user)
            self.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (amount, guild, user))

    # ---- 상점 패널 ----
    def set_shop(self, guild: int, channel: int, message: int):
        with self.db:
            self.db.execute("INSERT INTO shops VALUES(?,?,?) ON CONFLICT(channel) DO UPDATE SET message=excluded.message",
                            (channel, guild, message))

    def shops(self, guild: int):
        return self.db.execute("SELECT * FROM shops WHERE guild=?", (guild,)).fetchall()

    def remove_shop(self, channel: int) -> bool:
        with self.db:
            return self.db.execute("DELETE FROM shops WHERE channel=?", (channel,)).rowcount > 0

    # ---- 패키지 ----
    def create_package(self, guild: int, channel: int, message: int, expires: float, mode: str):
        with self.db:
            self.db.execute("INSERT INTO packages(message, guild, channel, expires, mode) VALUES(?,?,?,?,?)",
                            (message, guild, channel, expires, mode))

    def package(self, message: int):
        return self.db.execute("SELECT * FROM packages WHERE message=?", (message,)).fetchone()

    def active_package(self, channel: int, now: float):
        return self.db.execute("SELECT * FROM packages WHERE channel=? AND closed=0 AND expires>?", (channel, now)).fetchone()

    def package_wait(self, guild: int, user: int, now: float, cooldown: float) -> float:
        """이 사람이 다음 패키지를 뜯을 수 있기까지 남은 초. 바로 뜯을 수 있으면 0."""
        if cooldown <= 0:
            return 0.0
        row = self.db.execute("SELECT at FROM package_cooldowns WHERE guild=? AND user=?", (guild, user)).fetchone()
        return max(0.0, row["at"] + cooldown - now) if row else 0.0

    def open_package(self, message: int, user: int, result: str, now: float, cooldown: float = 0) -> str:
        """패키지를 뜯은 기록을 남긴다. 'ok' 또는 'gone'(없거나 끝남) / 'closed'(선착순에서 이미 뜯김) / 'again'(이미 뜯음) /
        'wait'(방금 다른 패키지를 뜯어서 cooldown초가 아직 안 지남: 아무것도 기록하지 않는다). 뜯으면 쿨타임이 새로 시작된다."""
        with self.db:
            row = self.package(message)
            if row is None or row["expires"] <= now:
                return "gone"
            if row["closed"]:
                return "closed"
            if self.db.execute("SELECT 1 FROM package_opens WHERE message=? AND user=?", (message, user)).fetchone():
                return "again"
            if self.package_wait(row["guild"], user, now, cooldown) > 0:
                return "wait"
            try:
                self.db.execute("INSERT INTO package_opens VALUES(?,?,?)", (message, user, result))
            except sqlite3.IntegrityError:
                return "again"
            if row["mode"] == "first":
                self.db.execute("UPDATE packages SET closed=1 WHERE message=?", (message,))
            self.db.execute("INSERT INTO package_cooldowns VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET at=excluded.at",
                            (row["guild"], user, now))
        return "ok"

    def has_opened(self, message: int, user: int) -> bool:
        return self.db.execute("SELECT 1 FROM package_opens WHERE message=? AND user=?", (message, user)).fetchone() is not None

    def package_openers(self, message: int):
        return self.db.execute("SELECT user, result FROM package_opens WHERE message=? ORDER BY rowid", (message,)).fetchall()

    def finished_packages(self, now: float):
        """정리할 패키지: 시간이 지났거나, 선착순으로 닫혔지만 메시지를 아직 요약으로 바꾸지 못한 것(closed=1)."""
        return self.db.execute("SELECT * FROM packages WHERE closed=1 OR expires<=?", (now,)).fetchall()

    def mark_summarized(self, message: int):
        """메시지를 요약으로 바꿨다는 표시(closed=2). 늦게 누른 사람에게 '이미 뜯겼어요'라고 안내하려고 만료 때까지 기록을 남긴다."""
        with self.db:
            self.db.execute("UPDATE packages SET closed=2 WHERE message=? AND closed=1", (message,))

    def delete_package(self, message: int):
        with self.db:
            self.db.execute("DELETE FROM package_opens WHERE message=?", (message,))
            self.db.execute("DELETE FROM packages WHERE message=?", (message,))

    # ---- 던전 ----
    def tickets(self, guild: int, user: int) -> int:
        """가진 히든 칭호 획득권 수."""
        row = self.db.execute("SELECT count FROM tickets WHERE guild=? AND user=?", (guild, user)).fetchone()
        return row["count"] if row else 0

    def dungeon_run(self, guild: int, user: int) -> str | None:
        """진행 중인 던전(JSON). 없으면 None."""
        row = self.db.execute("SELECT data FROM dungeons WHERE guild=? AND user=?", (guild, user)).fetchone()
        return row["data"] if row else None

    def enter_dungeon(self, guild: int, user: int, new_data: str) -> tuple[str, bool]:
        """진행 중인 던전이 있으면 그대로 돌려주고(입장 물품 없음), 없으면 입장 물품을 내고 new_data로 새로 시작한다.
        입장 물품이 없음으로 설정돼 있으면 무료다. (던전 JSON, 새로 시작했는지)를 돌려준다. 물품이 모자라면 StoreError."""
        existing = self.dungeon_run(guild, user)
        if existing is not None:
            return existing, False
        self.ensure_defaults(guild)                              # 이 안에서 따로 커밋하므로 아래 거래 밖에서 먼저 한다.
        cfg = self.settings(guild)
        entry, cost = cfg["entry_item"], cfg["entry_cost"]
        with self.db:                                              # 입장 물품 차감과 던전 생성은 한 덩어리
            if entry is not None:
                item = self.item(guild, entry)
                row = self.db.execute("SELECT qty FROM inventory WHERE guild=? AND user=? AND item=?", (guild, user, entry)).fetchone()
                have = row["qty"] if row else 0
                if have < cost:
                    label = f"{item['emoji']} {item['name']}" if item else "입장 물품"
                    raise StoreError(f"{i_ga(label)} {cost}개 필요해요. (보유 {have}개) 패키지 등에서 찾아보세요!")
                if have > cost:
                    self.db.execute("UPDATE inventory SET qty=qty-? WHERE guild=? AND user=? AND item=?", (cost, guild, user, entry))
                else:
                    self.db.execute("DELETE FROM inventory WHERE guild=? AND user=? AND item=?", (guild, user, entry))
            self.db.execute("INSERT INTO dungeons VALUES(?,?,?)", (guild, user, new_data))
        return new_data, True

    def save_dungeon(self, guild: int, user: int, data: str) -> None:
        """진행 상황을 저장한다. 이미 끝난(지워진) 던전은 되살리지 않는다."""
        with self.db:
            self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user))

    def sell_in_dungeon(self, guild: int, user: int, data: str, total: int) -> int:
        """던전 안에서 장비·소모품을 판 결과를 저장하고 재화를 준다(한 덩어리). 지금 가진 재화를 돌려준다.
        재화가 최대 보유량을 넘게 되면 거절한다(팔았는데 재화가 버려지는 일이 없게). 진행 중인 던전이 없어도 거절한다."""
        with self.db:
            self._ensure_wallet(guild, user)
            old = self.wallet(guild, user)
            cur = self.currency(guild)
            if old + total > cur.max:
                raise StoreError(f"{i_ga(cur.name)} 최대({cur.max:,}개)를 넘어서 팔 수 없어요. 먼저 재화를 써 주세요.")
            if self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user)).rowcount == 0:
                raise StoreError("진행 중인 던전이 없어요.")
            self.db.execute("UPDATE wallets SET candy=candy+? WHERE guild=? AND user=?", (total, guild, user))
        return old + total

    def end_dungeon(self, guild: int, user: int, *, candy: int = 0, tickets: int = 0, items=()) -> tuple[int, int] | None:
        """던전을 끝내고 기록을 지운다. 보상(재화는 최대 보유량까지)을 한 번에 준다.
        (실제로 받은 재화, 지금 가진 획득권 수)를 돌려준다. 이미 끝난 던전이면 None이고 보상도 없다."""
        with self.db:
            if self.db.execute("DELETE FROM dungeons WHERE guild=? AND user=?", (guild, user)).rowcount == 0:
                return None
            gained = 0
            if candy:
                self._ensure_wallet(guild, user)
                old = self.db.execute("SELECT candy FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()["candy"]
                gained = min(self.currency(guild).max, old + candy) - old
                self.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (old + gained, guild, user))
            for item_id, amount in items:                         # 그 사이 지워진 물품은 건너뛴다(가방에 남지 않게).
                if self.db.execute("SELECT 1 FROM items WHERE guild=? AND id=?", (guild, item_id)).fetchone():
                    self.db.execute("INSERT INTO inventory VALUES(?,?,?,?) ON CONFLICT(guild,user,item) DO UPDATE SET qty=qty+excluded.qty",
                                    (guild, user, item_id, amount))
            if tickets:
                self.db.execute("INSERT INTO tickets VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET count=count+excluded.count",
                                (guild, user, tickets))
        return gained, self.tickets(guild, user)
