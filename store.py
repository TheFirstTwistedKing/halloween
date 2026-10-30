"""할로윈 봇의 규칙과 저장소. 디스코드에 접속하지 않는 순수 로직이라 그대로 시험할 수 있다."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path

CANDY = "🍬"                     # 할로윈 재화의 아이콘
CANDY_NAME = "할로윈 재화"
CANDY_MAX = 99999                # 보유할 수 있는 최대 재화
EXCHANGE_EXP = 15000             # 이만큼의 EXP를 내면
EXCHANGE_CANDY = 15              # 이만큼의 재화를 살 수 있다
MAX_SHOP_ITEMS = 25              # 선택창 한도(디스코드): 상점에 올릴 수 있는 물품 수
QTY_MAX = 999                    # 한 번에 사고팔 수 있는 수량
PACKAGE_CANDY = 50
# 패키지를 뜯었을 때 결과(%) — 합계 100
PACKAGE_TABLE = (("none", 60), ("key", 20), ("candy", 10), ("trash", 10))
SYSTEM_ITEMS = {
    "key": ("열쇠", "🗝️", "어디에 쓰는지는 모르지만 왠지 소중해 보이는 낡은 열쇠예요."),
    "trash": ("쓰레기", "🗑️", "쓸모없어 보이는 쓰레기예요. 누군가는 사 갈지도 몰라요."),
}
DEFAULT_SETTINGS = {
    "channels": [],              # 재화·EXP·패키지가 나오는 채널
    "candy_chance": 50.0,        # 채팅 한 번당 재화를 얻을 확률(%)
    "candy_min": 1, "candy_max": 5,
    "candy_cooldown": 3,         # 같은 사람이 재화를 다시 얻을 수 있기까지(초). 0이면 제한 없음
    "exp_min": 15, "exp_max": 25, "exp_cooldown": 60,
    "package_chance": 0.1,       # 채팅 한 번당 패키지가 나올 확률(%)
    "package_mode": "all",       # all: 누구나 각자 한 번씩 / first: 먼저 뜯은 한 명만
    "package_seconds": 120,      # 패키지가 머무는 시간(초)
    "reaction": True,            # 재화를 얻으면 메시지에 🍬 반응을 단다
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
    number("exp_min", 0, 100000, True)
    number("exp_max", 0, 100000, True)
    number("candy_cooldown", 0, 3600, True)
    number("exp_cooldown", 0, 3600, True)
    number("package_seconds", 10, 3600, True)
    if cfg["candy_min"] > cfg["candy_max"] or cfg["exp_min"] > cfg["exp_max"]:
        raise ValueError("최소값이 최대값보다 클 수 없어요.")
    if cfg["package_mode"] not in ("all", "first"):
        raise ValueError("패키지 방식은 all 또는 first여야 해요.")
    if not isinstance(cfg["reaction"], bool):
        raise ValueError("reaction은 켜짐/꺼짐이어야 해요.")
    channels = cfg["channels"]
    if not isinstance(channels, list) or len(channels) > 100 or any(isinstance(c, bool) or not isinstance(c, int) for c in channels):
        raise ValueError("이벤트 채널 목록이 올바르지 않아요.")


# ---------------------------------------------------------------- 확률 (난수 생성기를 받아서 시험하기 쉽다)
def roll_candy(cfg: dict, rng) -> int:
    """채팅 한 번의 재화 보상. 못 받으면 0."""
    if cfg["candy_chance"] <= 0 or rng.random() * 100 >= cfg["candy_chance"]:
        return 0
    return rng.randint(cfg["candy_min"], cfg["candy_max"])


def roll_exp(cfg: dict, rng) -> int:
    return rng.randint(cfg["exp_min"], cfg["exp_max"])


def roll_package_spawn(cfg: dict, rng) -> bool:
    return cfg["package_chance"] > 0 and rng.random() * 100 < cfg["package_chance"]


def roll_package_result(rng) -> str:
    """꽝(none) 60%, 열쇠(key) 20%, 재화 50(candy) 10%, 쓰레기(trash) 10%."""
    draw, edge = rng.random() * 100, 0
    for result, percent in PACKAGE_TABLE:
        edge += percent
        if draw < edge:
            return result
    return PACKAGE_TABLE[-1][0]


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
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(guild INTEGER PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS wallets(
                guild INTEGER, user INTEGER, candy INTEGER NOT NULL DEFAULT 0 CHECK(candy >= 0),
                exp INTEGER NOT NULL DEFAULT 0 CHECK(exp >= 0), PRIMARY KEY(guild, user));
            CREATE TABLE IF NOT EXISTS items(
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL CHECK(kind IN ('item','role')), role_id INTEGER,
                buy_price INTEGER NOT NULL DEFAULT 0, sell_price INTEGER NOT NULL DEFAULT 0,
                buyable INTEGER NOT NULL DEFAULT 0, sellable INTEGER NOT NULL DEFAULT 0, system TEXT);
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
            CREATE TABLE IF NOT EXISTS dungeons(
                guild INTEGER, user INTEGER, data TEXT NOT NULL, PRIMARY KEY(guild, user));
            CREATE TABLE IF NOT EXISTS tickets(
                guild INTEGER, user INTEGER, count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0), PRIMARY KEY(guild, user));
        """)

    def close(self):
        self.db.close()

    # ---- 설정 ----
    def settings(self, guild: int) -> dict:
        cached = self._settings.get(guild)
        if cached is None:
            row = self.db.execute("SELECT data FROM settings WHERE guild=?", (guild,)).fetchone()
            cached = copy.deepcopy(DEFAULT_SETTINGS)
            if row:
                cached.update(json.loads(row["data"]))
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
        return cfg

    # ---- 지갑 ----
    def wallet(self, guild: int, user: int) -> tuple[int, int]:
        row = self.db.execute("SELECT candy, exp FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()
        return (row["candy"], row["exp"]) if row else (0, 0)

    def _ensure_wallet(self, guild, user):
        self.db.execute("INSERT OR IGNORE INTO wallets(guild, user) VALUES(?,?)", (guild, user))

    def add_candy(self, guild: int, user: int, amount: int) -> int:
        """재화를 더하고 실제로 바뀐 양을 돌려준다. 최대 보유량(99,999)을 넘는 만큼은 버려지고, 0 아래로는 내려가지 않는다."""
        with self.db:
            self._ensure_wallet(guild, user)
            old = self.db.execute("SELECT candy FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()["candy"]
            new = min(CANDY_MAX, max(0, old + amount))
            self.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (new, guild, user))
        return new - old

    def add_exp(self, guild: int, user: int, amount: int) -> int:
        with self.db:
            self._ensure_wallet(guild, user)
            old = self.db.execute("SELECT exp FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()["exp"]
            new = max(0, old + amount)
            self.db.execute("UPDATE wallets SET exp=? WHERE guild=? AND user=?", (new, guild, user))
        return new - old

    def exchange_exp(self, guild: int, user: int, batches: int) -> tuple[int, int]:
        """EXP 15,000 → 재화 15를 batches번. (쓴 EXP, 받은 재화)를 돌려준다. 최대 보유량을 넘기는 교환은 거절한다."""
        if batches < 1:
            raise StoreError("교환 수량은 1 이상이어야 해요.")
        cost, gain = batches * EXCHANGE_EXP, batches * EXCHANGE_CANDY
        with self.db:
            self._ensure_wallet(guild, user)
            candy, exp = self.wallet(guild, user)
            if exp < cost:
                raise StoreError(f"EXP가 부족해요. 필요 {cost:,} · 보유 {exp:,}")
            if candy + gain > CANDY_MAX:
                room = (CANDY_MAX - candy) // EXCHANGE_CANDY
                raise StoreError(f"{CANDY_NAME}은 최대 {CANDY_MAX:,}개까지 가질 수 있어요. "
                                 f"지금은 {room}번까지만 교환할 수 있어요." if room else
                                 f"{CANDY_NAME}이 가득 차서 더 교환할 수 없어요. (최대 {CANDY_MAX:,}개)")
            self.db.execute("UPDATE wallets SET candy=candy+?, exp=exp-? WHERE guild=? AND user=?", (gain, cost, guild, user))
        return cost, gain

    # ---- 물품 ----
    @staticmethod
    def _check_prices(buyable, sellable, buy_price, sell_price):
        for price in (buy_price, sell_price):
            if isinstance(price, bool) or not isinstance(price, int) or not 0 <= price <= CANDY_MAX:
                raise StoreError(f"가격은 0~{CANDY_MAX:,} 사이의 정수여야 해요.")
        if buyable and buy_price < 1:
            raise StoreError("구입을 켜려면 구매가를 1 이상으로 정해 주세요.")
        if sellable and sell_price < 1:
            raise StoreError("판매를 켜려면 판매가를 1 이상으로 정해 주세요.")

    @staticmethod
    def _check_text(name, emoji, description):
        if not 1 <= len(name) <= 30 or name != name.strip():
            raise StoreError("이름은 앞뒤 공백 없이 1~30자로 써 주세요.")
        if not valid_emoji(emoji):
            raise StoreError("이모지는 😀 같은 이모지 하나이거나 서버 이모지여야 해요.")
        if len(description) > 200:
            raise StoreError("설명은 200자까지예요.")

    def ensure_system_items(self, guild: int) -> dict[str, int]:
        """열쇠·쓰레기를 서버에 만든다(처음 한 번). 기본은 상점에 올라가지 않고 가방에만 쌓인다."""
        with self.db:
            for key, (name, emoji, description) in SYSTEM_ITEMS.items():
                self.db.execute("INSERT OR IGNORE INTO items(guild, name, emoji, description, kind, system) "
                                "VALUES(?,?,?,?,'item',?)", (guild, name, emoji, description, key))
        rows = self.db.execute("SELECT id, system FROM items WHERE guild=? AND system IS NOT NULL", (guild,)).fetchall()
        return {row["system"]: row["id"] for row in rows}

    def create_item(self, guild, name, *, emoji="🎃", description="", kind="item", role_id=None,
                    buy_price=0, sell_price=0, buyable=False, sellable=False):
        self._check_text(name, emoji, description)
        self._check_prices(buyable, sellable, buy_price, sell_price)
        if kind == "role" and role_id is None:
            raise StoreError("역할 물품에는 역할이 필요해요.")
        total = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE guild=? AND (buyable=1 OR sellable=1)", (guild,)).fetchone()["n"]
        if (buyable or sellable) and total >= MAX_SHOP_ITEMS:
            raise StoreError(f"상점에는 물품을 최대 {MAX_SHOP_ITEMS}개까지 올릴 수 있어요.")
        try:
            with self.db:
                cursor = self.db.execute(
                    "INSERT INTO items(guild, name, emoji, description, kind, role_id, buy_price, sell_price, buyable, sellable) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (guild, name, emoji, description, kind, role_id, buy_price, sell_price, int(buyable), int(sellable)))
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
        merged = {k: row[k] for k in ("name", "emoji", "description", "buy_price", "sell_price", "buyable", "sellable")}
        merged.update(changes)
        self._check_text(merged["name"], merged["emoji"], merged["description"])
        merged["buyable"], merged["sellable"] = bool(merged["buyable"]), bool(merged["sellable"])
        self._check_prices(merged["buyable"], merged["sellable"], merged["buy_price"], merged["sell_price"])
        if row["system"] and merged["name"] != row["name"]:
            raise StoreError("기본 물품의 이름은 바꿀 수 없어요.")
        listed = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE guild=? AND (buyable=1 OR sellable=1) AND id<>?",
                                 (guild, item_id)).fetchone()["n"]
        if (merged["buyable"] or merged["sellable"]) and listed >= MAX_SHOP_ITEMS:
            raise StoreError(f"상점에는 물품을 최대 {MAX_SHOP_ITEMS}개까지 올릴 수 있어요.")
        try:
            with self.db:
                self.db.execute("UPDATE items SET name=?, emoji=?, description=?, buy_price=?, sell_price=?, buyable=?, sellable=? "
                                "WHERE guild=? AND id=?",
                                (merged["name"], merged["emoji"], merged["description"], merged["buy_price"], merged["sell_price"],
                                 int(merged["buyable"]), int(merged["sellable"]), guild, item_id))
        except sqlite3.IntegrityError as exc:
            raise StoreError("같은 이름의 물품이 이미 있어요.") from exc
        return self.item(guild, item_id)

    def holdings(self, guild: int, item_id: int) -> int:
        """이 물품이 사람들의 가방에 들어 있는 총 개수."""
        return self.db.execute("SELECT COALESCE(SUM(qty), 0) AS n FROM inventory WHERE guild=? AND item=?", (guild, item_id)).fetchone()["n"]

    def delete_item(self, guild: int, item_id: int) -> int:
        """물품을 지우고, 사람들의 가방에서도 뺀다. 가방에서 사라진 개수를 돌려준다."""
        row = self.item(guild, item_id)
        if row is None:
            raise StoreError("그런 물품이 없어요.")
        if row["system"]:
            raise StoreError("기본 물품(열쇠·쓰레기)은 지울 수 없어요. 상점에서 빼려면 구매·판매를 꺼 주세요.")
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
            candy, _ = self.wallet(guild, user)
            if candy < total:
                raise StoreError(f"{CANDY_NAME}이 부족해요. 필요 {CANDY}{total:,} · 보유 {CANDY}{candy:,}")
            self._ensure_wallet(guild, user)
            self.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (total, guild, user))
            self.add_item(guild, user, item_id, qty)
        return total

    def sell_item(self, guild: int, user: int, item_id: int, qty: int) -> int:
        """물품을 팔고 재화를 받는다. 받은 재화를 돌려준다. 최대 보유량을 넘기게 되면 거절한다."""
        item = self._tradeable(guild, item_id, qty, "item")
        if not item["sellable"]:
            raise StoreError("지금은 팔 수 없는 물품이에요.")
        total = item["sell_price"] * qty
        with self.db:
            have = self.qty(guild, user, item_id)
            if have < qty:
                raise StoreError(f"가진 수량이 모자라요. 보유 {have}개")
            candy, _ = self.wallet(guild, user)
            if candy + total > CANDY_MAX:
                raise StoreError(f"{CANDY_NAME}이 최대({CANDY_MAX:,}개)를 넘어서 팔 수 없어요. 먼저 재화를 써 주세요.")
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
            candy, _ = self.wallet(guild, user)
            if candy < amount:
                raise StoreError(f"{CANDY_NAME}이 부족해요. 필요 {CANDY}{amount:,} · 보유 {CANDY}{candy:,}")
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

    def open_package(self, message: int, user: int, result: str, now: float) -> str:
        """패키지를 뜯은 기록을 남긴다. 'ok' 또는 'gone'(없거나 끝남) / 'closed'(선착순에서 이미 뜯김) / 'again'(이미 뜯음)."""
        with self.db:
            row = self.package(message)
            if row is None or row["expires"] <= now:
                return "gone"
            if row["closed"]:
                return "closed"
            try:
                self.db.execute("INSERT INTO package_opens VALUES(?,?,?)", (message, user, result))
            except sqlite3.IntegrityError:
                return "again"
            if row["mode"] == "first":
                self.db.execute("UPDATE packages SET closed=1 WHERE message=?", (message,))
        return "ok"

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
        """진행 중인 던전이 있으면 그대로 돌려주고(열쇠 없음), 없으면 🗝️ 열쇠 1개를 내고 new_data로 새로 시작한다.
        (던전 JSON, 새로 시작했는지)를 돌려준다. 열쇠가 없으면 StoreError."""
        existing = self.dungeon_run(guild, user)
        if existing is not None:
            return existing, False
        key_id = self.ensure_system_items(guild)["key"]          # 이 안에서 따로 커밋하므로 아래 거래 밖에서 먼저 한다.
        with self.db:                                              # 열쇠 차감과 던전 생성은 한 덩어리
            row = self.db.execute("SELECT qty FROM inventory WHERE guild=? AND user=? AND item=?", (guild, user, key_id)).fetchone()
            if row is None:
                raise StoreError("🗝️ 열쇠가 없어요. 패키지에서 열쇠를 찾아보세요!")
            if row["qty"] > 1:
                self.db.execute("UPDATE inventory SET qty=qty-1 WHERE guild=? AND user=? AND item=?", (guild, user, key_id))
            else:
                self.db.execute("DELETE FROM inventory WHERE guild=? AND user=? AND item=?", (guild, user, key_id))
            self.db.execute("INSERT INTO dungeons VALUES(?,?,?)", (guild, user, new_data))
        return new_data, True

    def save_dungeon(self, guild: int, user: int, data: str) -> None:
        """진행 상황을 저장한다. 이미 끝난(지워진) 던전은 되살리지 않는다."""
        with self.db:
            self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user))

    def end_dungeon(self, guild: int, user: int, *, candy: int = 0, tickets: int = 0) -> tuple[int, int] | None:
        """던전을 끝내고 기록을 지운다. 보상(재화는 최대 보유량까지)을 한 번에 준다.
        (실제로 받은 재화, 지금 가진 획득권 수)를 돌려준다. 이미 끝난 던전이면 None이고 보상도 없다."""
        with self.db:
            if self.db.execute("DELETE FROM dungeons WHERE guild=? AND user=?", (guild, user)).rowcount == 0:
                return None
            gained = 0
            if candy:
                self._ensure_wallet(guild, user)
                old = self.db.execute("SELECT candy FROM wallets WHERE guild=? AND user=?", (guild, user)).fetchone()["candy"]
                gained = min(CANDY_MAX, old + candy) - old
                self.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (old + gained, guild, user))
            if tickets:
                self.db.execute("INSERT INTO tickets VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET count=count+excluded.count",
                                (guild, user, tickets))
        return gained, self.tickets(guild, user)
