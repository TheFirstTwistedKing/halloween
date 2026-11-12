"""기계 분위기 이벤트 봇의 규칙과 저장소. 디스코드에 접속하지 않는 순수 로직이라 그대로 시험할 수 있다."""
from __future__ import annotations

import copy
import json
import random
import re
import sqlite3
from pathlib import Path
from typing import NamedTuple

import alchemy
from josa import i_ga

import balance as balance_rules

CANDY = "💾"                     # 재화(RAM)의 아이콘. 코드 안의 이름은 예전 그대로 CANDY다.
CANDY_NAME = "RAM"
LEGACY_CURRENCY = ("🍬", "할로윈 재화")   # 예전 기본 재화(이전 판 서버가 저장해 둔 값): 그대로면 새 기본으로 바꾼다(SEED_VERSION 3).
CANDY_MAX = 9_999_999_999         # 보유할 수 있는 최대 재화(기본값이자 /재화설정으로 정할 수 있는 한도). 예전 기본은 99,999였다.
LEGACY_CANDY_MAX = 99999
MAX_SHOP_ITEMS = 25              # 선택창 한도(디스코드): 상점에 올릴 수 있는 물품 수
QTY_MAX = 999                    # 한 번에 사고팔 수 있는 수량
TICKET_MAX = 99_999              # 한 사람이 가질 수 있는 히든 칭호 획득권의 최대(관리자 지급 기준)
MAX_RECIPES = 100                # 서버마다 연금술 레시피 수 한도
SEED_VERSION = 4                 # 기본 물품·패키지 결과표의 버전. 올리면 이미 운영 중인 서버도 한 번 이전한다(_upgrade_seed).
PACKAGE_CANDY = 50
MAX_PACKAGE_ENTRIES = 20
SYSTEM_ITEMS = {
    "key": ("열쇠", "🗝️", "어디에 쓰는지는 모르지만 왠지 소중해 보이는 낡은 열쇠예요."),
    "trash": ("쓰레기", "🗑️", "쓸모없어 보이는 쓰레기예요. 누군가는 사 갈지도 몰라요."),
}
# 패키지에서 나오는 물품(버전 1에서 추가). (시스템 이름, 이름, 이모지, 설명, 판매가(최소), 판매 최대가(없으면 고정), 패키지 확률 가중치)
# 판매가가 음수면 팔 때 재화를 내고, 최소~최대면 팔 때마다 그 사이에서 뽑는다. 좋은 물품일수록 가중치가 낮고, 나쁜 물품은 흔하다.
NEW_ITEMS = (
    ("witch_hat", "구형 CPU", "🔲", "한 시대를 풍미했던 구형 프로세서예요. 은근히 값이 나가요.", 100, None, 5.5),
    ("ghost_cloth", "방열 패드", "🧊", "발열을 잡아 주던 얇고 서늘한 패드예요.", 100, None, 5.5),
    ("broken_glass", "깨진 디스플레이 파편", "📺", "날카로운 파편이에요. 치우려면 처리 비용을 내야 해요.", -150, None, 8),
    ("jack_o_lantern", "무정전 전원 장치", "🔌", "전원이 끊겨도 꺼지지 않는 신기한 전원 장치예요.", 250, None, 3),
    ("moon_shard", "실리콘 웨이퍼", "💿", "거울처럼 반짝이는 얇은 웨이퍼예요. 아주 귀해요.", 300, None, 1.5),
    ("vampire_wine", "액체 냉각제", "🧪", "차갑고 푸른 냉각액이에요. 한 방울이면 CPU가 식는다는 소문이 있어요.", 500, None, 0.5),
    ("mystery_bottle", "출처 불명 부품 키트", "🧰", "뭐가 들었는지 아무도 몰라요. 팔 때마다 값이 달라져요.", -300, 300, 4),
)
# 예전(할로윈 판) 기본 물품 이름·아이콘·설명: 이전 판 서버의 물품이 아직 이 값 그대로일 때만 새 이름으로 바꾼다(SEED_VERSION 3). {시스템 이름: (이름, 이모지, 설명)}
LEGACY_ITEMS = {
    "witch_hat": ("마녀 모자", "🧙", "마녀가 쓰다 놓고 간 뾰족한 모자예요. 은근히 값이 나가요."),
    "ghost_cloth": ("유령의 천", "👻", "유령이 두르고 있던 얇고 서늘한 천이에요."),
    "broken_glass": ("깨진 유리조각", "🥃", "날카로운 유리조각이에요. 치우려면 처리 비용을 내야 해요."),
    "jack_o_lantern": ("잭 오 랜턴", "🎃", "속이 빈 호박에 불이 켜져 있어요. 꺼지지 않는 신기한 불이에요."),
    "moon_shard": ("달 조각", "🌙", "달에서 떨어져 나온 은빛 조각이에요. 아주 귀해요."),
    "vampire_wine": ("뱀파이어맛 와인", "🍷", "붉고 진한 와인이에요. 한 모금이면 송곳니가 자란다는 소문이 있어요."),
    "mystery_bottle": ("수상한 액체가 담긴 병", "⚗️", "뭐가 들었는지 아무도 몰라요. 팔 때마다 값이 달라져요."),
}
EMPTY_EMOJI, EMPTY_TITLE = "📭", "빈 패킷"        # 패키지 결과표의 '꽝'(아무것도 안 나옴)
LEGACY_EMPTY = ("👻", "꽝")                          # 예전 기본 '꽝'. 결과표의 이 칸이 그대로면 새 값으로 바꾼다.
# 부활권(버전 2에서 추가): 던전에서 쓰러졌을 때 가방에 있으면 자동으로 한 장 쓰여서 되살아난다. 팔 수도 살 수도 없게 만든다(관리자가 /물품수정으로 바꿀 수 있다).
REVIVE_SYSTEM = "revive"
REVIVE_ITEM = ("부활권", "🎟️", "던전에서 쓰러졌을 때 가방에 있으면 자동으로 한 장 쓰여서, 그 자리에서 다시 일어나요.")
REVIVE_PERCENT = 0.1             # 패키지 한 번에서 부활권이 나올 확률(%)
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
    "package_pile_chance": 35.0, # 패키지가 나타날 때 '더미'로 변이할 확률(%). 0이면 더미는 안 나온다
    "package_pile_size": 3,      # 더미를 뜯으면 나오는 결과 개수(결과표에서 그만큼 따로 뽑는다)
    "reaction": True,            # 재화를 얻으면 메시지에 재화 이모지 반응을 단다
    "currency_emoji": CANDY, "currency_name": CANDY_NAME, "currency_max": CANDY_MAX,   # 재화 자체(/재화설정)
    "seeded": False,             # 기본 물품(열쇠·쓰레기)과 기본 패키지 결과표를 한 번 만들었는지
    "seed_version": 0,           # 기본 물품·결과표를 어느 버전까지 이전했는지(SEED_VERSION)
    "entry_item": None,          # 던전에 들어갈 때 내는 물품 id. None이면 무료 입장
    "entry_cost": 1,             # 입장에 필요한 개수
    "package_table": [],         # 패키지 결과표 [{id, kind(none|candy|item), item, amount, weight, emoji, title}]
    "clear_rewards": [],         # 던전 클리어 때 추가로 주는 물품 [{id, item, amount, chance(%)}]. 재화·획득권은 /던전설정의 보상.* 항목
    "dungeon_content": {},       # 던전 콘텐츠 {monsters|bosses|gear|consumables: [...]}. 없는 항목은 기본값 (balance.py)
    "weapon_mods": [],           # 장비 개조 레시피 [{id, weapon, item, result}] (무기·재료·결과 무기의 key). balance.clean_mods가 쓸 수 있는 것만 고른다
    "elements": [],              # 속성 [{key, name, emoji}] (/속성 추가)
    "awakening": {},             # 각성 설정(/각성설정): 궁극기·각성 게이지 충전, 특수 스킬. 빈 칸은 기본값(balance.AWAKENING_DEFAULT)
    "affinity": [],              # 상성표 [{atk, def, percent}] (/속성 상성). 없는 조합은 100%
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
    number("package_pile_chance", 0, 100)
    number("package_pile_size", 1, 10, True)
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
    number("currency_max", 1, CANDY_MAX, True)
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
    mods = cfg["weapon_mods"]
    if not isinstance(mods, list) or len(mods) > balance_rules.MAX_MODS or any(
            not isinstance(m, dict) or isinstance(m.get("id"), bool) or not isinstance(m.get("id"), int)
            or any(not isinstance(m.get(k), str) for k in ("weapon", "item", "result")) for m in mods):
        raise ValueError("장비 개조 설정이 올바르지 않아요.")
    elements = cfg["elements"]
    if not isinstance(elements, list) or len(elements) > balance_rules.MAX_ELEMENTS or any(
            not isinstance(e, dict) or any(not isinstance(e.get(k), str) for k in ("key", "name", "emoji")) for e in elements):
        raise ValueError("속성 설정이 올바르지 않아요.")
    affinity = cfg["affinity"]
    if not isinstance(affinity, list) or len(affinity) > balance_rules.MAX_ELEMENTS ** 2 or any(
            not isinstance(r, dict) or not isinstance(r.get("atk"), str) or not isinstance(r.get("def"), str)
            or isinstance(r.get("percent"), bool) or not isinstance(r.get("percent"), int) or not 0 <= r["percent"] <= balance_rules.AFFINITY_MAX
            for r in affinity):
        raise ValueError("상성표 설정이 올바르지 않아요.")
    if not isinstance(cfg["awakening"], dict) or len(cfg["awakening"]) > 20:
        raise ValueError("각성 설정이 올바르지 않아요.")
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


def roll_package_pile(cfg: dict, rng) -> bool:
    """방금 나타난 패키지가 '더미'로 변이할까(기본 35%)."""
    return cfg["package_pile_chance"] > 0 and rng.random() * 100 < cfg["package_pile_chance"]


def split_results(text: str) -> list[str]:
    """package_opens에 저장된 결과(더미는 쉼표로 이은 여러 개)를 결과 id 목록으로."""
    return [part for part in text.split(",") if part]


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


def package_table_v1(ids: dict, new_ids: dict) -> list[dict]:
    """버전 1의 기본 패키지 결과표(가중치 합 100이라 가중치가 곧 퍼센트). 값어치가 높을수록 드물고, 쓸모없거나 손해인 물품은 흔하다.
    버전 2로 올릴 때 서버의 표가 아직 이것 그대로인지 알아보는 데에도 쓴다."""
    table = [
        {"id": "none", "kind": "none", "weight": 40, "amount": 1, "emoji": "👻", "title": "꽝"},
        {"id": "key", "kind": "item", "item": ids["key"], "weight": 14, "amount": 1},
        {"id": "candy", "kind": "candy", "weight": 7, "amount": PACKAGE_CANDY},
        {"id": "trash", "kind": "item", "item": ids["trash"], "weight": 11, "amount": 1}]
    for system, _name, _emoji, _description, _low, _high, weight in NEW_ITEMS:
        table.append({"id": system, "kind": "item", "item": new_ids[system], "weight": weight, "amount": 1})
    return table


def _is_package_table_v1(table: list[dict]) -> bool:
    by_id = {e.get("id"): e for e in table}
    try:
        ids = {key: by_id[key]["item"] for key in ("key", "trash")}
        new_ids = {system: by_id[system]["item"] for system, *_ in NEW_ITEMS}
    except KeyError:
        return False
    return table == package_table_v1(ids, new_ids)


def table_with_revive(table: list[dict], revive_id: int) -> list[dict]:
    """패키지 결과표에 부활권을 REVIVE_PERCENT(0.1%)로 넣는다. 이미 있으면 그대로 둔다.
    버전 1 기본 표 그대로면 꽝에서 0.1을 덜어 합이 100인 채로 정확히 0.1%가 되게 하고,
    관리자가 손댄 표면 다른 결과의 비율은 건드리지 않고 부활권의 몫이 0.1% 안팎이 되게 가중치를 계산해 덧붙인다(칸이 가득 찼거나 표가 비면 못 넣는다)."""
    if any(e["kind"] == "item" and e.get("item") == revive_id for e in table):
        return table
    if len(table) >= MAX_PACKAGE_ENTRIES:
        return table
    new_id = REVIVE_SYSTEM
    while any(e.get("id") == new_id for e in table):
        new_id += "_"
    entry = {"id": new_id, "kind": "item", "item": revive_id, "weight": REVIVE_PERCENT, "amount": 1}
    if _is_package_table_v1(table):
        out = [dict(e) for e in table]
        out[0]["weight"] = round(out[0]["weight"] - REVIVE_PERCENT, 4)
        return out + [entry]
    total = sum(e["weight"] for e in table)
    if total <= 0:
        return table
    entry["weight"] = max(0.0001, round(total * REVIVE_PERCENT / (100 - REVIVE_PERCENT), 4))
    return [dict(e) for e in table] + [entry]


def modernize_table(table: list[dict]) -> list[dict]:
    """결과표에서 예전 기본 '꽝'(👻 꽝) 칸의 아이콘·이름을 지금 값(📭 빈 패킷)으로 바꾼다. 관리자가 바꾼 칸은 건드리지 않는다."""
    return [{**e, "emoji": EMPTY_EMOJI, "title": EMPTY_TITLE}
            if e.get("kind") == "none" and (e.get("emoji"), e.get("title")) == LEGACY_EMPTY else e for e in table]


def default_package_table(ids: dict, new_ids: dict, revive_id: int | None = None) -> list[dict]:
    """지금의 기본 패키지 결과표(가중치 합 100이라 가중치가 곧 퍼센트). 버전 1의 표에 부활권 0.1%를 더한 것(꽝에서 0.1을 덜어낸다)."""
    table = package_table_v1(ids, new_ids)
    return modernize_table(table_with_revive(table, revive_id) if revive_id is not None else table)


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
                expires REAL NOT NULL, mode TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0, pile INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS package_opens(
                message INTEGER, user INTEGER, result TEXT NOT NULL, PRIMARY KEY(message, user));
            CREATE TABLE IF NOT EXISTS package_cooldowns(
                guild INTEGER, user INTEGER, at REAL NOT NULL, PRIMARY KEY(guild, user));
            CREATE TABLE IF NOT EXISTS recipes(
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, result INTEGER NOT NULL, ingredients TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS recipes_ingredients ON recipes(guild, ingredients);
            CREATE UNIQUE INDEX IF NOT EXISTS recipes_result ON recipes(guild, result);
            CREATE TABLE IF NOT EXISTS dungeons(
                guild INTEGER, user INTEGER, data TEXT NOT NULL, PRIMARY KEY(guild, user));
            CREATE TABLE IF NOT EXISTS tickets(
                guild INTEGER, user INTEGER, count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0), PRIMARY KEY(guild, user));
        """)
        # 판매가 범위(sell_max)가 생기기 전에 만든 DB에는 칸이 없다. 기존 데이터는 그대로 두고 칸만 더한다.
        if "sell_max" not in {row["name"] for row in self.db.execute("PRAGMA table_info(items)")}:
            with self.db:
                self.db.execute("ALTER TABLE items ADD COLUMN sell_max INTEGER")
        # 패키지 더미가 생기기 전에 만든 DB의 packages 표에는 pile 칸이 없다. 이미 나와 있던 패키지는 일반(0)으로 둔다.
        if "pile" not in {row["name"] for row in self.db.execute("PRAGMA table_info(packages)")}:
            with self.db:
                self.db.execute("ALTER TABLE packages ADD COLUMN pile INTEGER NOT NULL DEFAULT 0")

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
            cached = self._balances[guild] = balance_rules.Balance.build(cfg["dungeon"], cfg["dungeon_content"], cfg["weapon_mods"],
                                                                         cfg["elements"], cfg["affinity"], cfg["awakening"])
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
                    if self.db.execute("SELECT 1 FROM items WHERE guild=? AND system=?", (guild, key)).fetchone():
                        continue
                    # 기본 물품을 만들기 전에 관리자가 같은 이름의 물품을 먼저 만들었다면(예: '열쇠') 그 물품을 기본 물품으로 이어받는다.
                    # 그냥 두면 이름이 겹쳐 기본 물품이 안 만들어지고 아래에서 KeyError가 난다. 이름·가격·가방 속 개수는 그대로다.
                    same = self.db.execute("SELECT id FROM items WHERE guild=? AND lower(name)=lower(?) AND kind='item'", (guild, name)).fetchone()
                    if same:
                        self.db.execute("UPDATE items SET system=? WHERE id=?", (key, same["id"]))
                    else:
                        self.db.execute("INSERT INTO items(guild, name, emoji, description, kind, system) VALUES(?,?,?,?,'item',?)",
                                        (guild, name, emoji, description, key))
            ids = {row["system"]: row["id"] for row in
                   self.db.execute("SELECT id, system FROM items WHERE guild=? AND system IS NOT NULL", (guild,)).fetchall()}
            self.update_settings(guild, seeded=True, entry_item=ids["key"], entry_cost=1, package_table=first_package_table(ids))
            cfg = self.settings(guild)
        if cfg["seed_version"] < SEED_VERSION:
            self._upgrade_seed(guild)

    def _upgrade_seed(self, guild: int) -> None:
        """서버의 기본 구성을 SEED_VERSION까지 올린다. 한 단계씩 차례로 거친다(새 서버는 0 → 1 → 2)."""
        if self.settings(guild)["seed_version"] < 1:
            self._upgrade_seed_v1(guild)
        if self.settings(guild)["seed_version"] < 2:
            self._upgrade_seed_v2(guild)
        if self.settings(guild)["seed_version"] < 3:
            self._upgrade_seed_v3(guild)
        if self.settings(guild)["seed_version"] < 4:
            self._upgrade_seed_v4(guild)

    def _upgrade_seed_v4(self, guild: int) -> None:
        """버전 4: 재화 최대 보유량의 기본값을 99,999 → 9,999,999,999로 늘렸다. 예전 기본값(99,999) 그대로인 서버만 새 기본값으로 올리고,
        관리자가 /재화설정으로 정해 둔 값은 건드리지 않는다. 가진 재화는 그대로다(늘어나기만 한다)."""
        cfg = self.settings(guild)
        changes = {"currency_max": CANDY_MAX} if cfg["currency_max"] == LEGACY_CANDY_MAX else {}
        self.update_settings(guild, seed_version=4, **changes)

    def _upgrade_seed_v3(self, guild: int) -> None:
        """버전 3(기계 분위기 개편): 예전 기본값 그대로인 것만 새 이름으로 바꾼다. 관리자가 직접 바꾼 것은 건드리지 않는다.
        - 재화: 아이콘 🍬 → 💾, 이름 '할로윈 재화' → 'RAM' (각각 예전 기본값일 때만)
        - 기본 패키지 물품 7종: 이름·아이콘(·설명)이 예전 기본 그대로일 때만. 새 이름과 겹치는 다른 물품이 있으면 건너뛴다.
        - 패키지 결과표의 기본 '꽝' 칸(👻 꽝 → 📭 빈 패킷)
        - 던전: 기본 몬스터·보스·시크릿 보스·장비·소모품의 예전 이름(내용을 직접 편집해 저장해 둔 구역)과 /던전설정으로 정한 숫자의 항목 이름"""
        cfg = self.settings(guild)
        changes = {}
        if cfg["currency_emoji"] == LEGACY_CURRENCY[0]:
            changes["currency_emoji"] = CANDY
        if cfg["currency_name"] == LEGACY_CURRENCY[1]:
            changes["currency_name"] = CANDY_NAME
        new_text = {system: (name, emoji, description) for system, name, emoji, description, *_ in NEW_ITEMS}
        with self.db:
            for system, (old_name, old_emoji, old_description) in LEGACY_ITEMS.items():
                row = self.db.execute("SELECT id, name, emoji, description FROM items WHERE guild=? AND system=?", (guild, system)).fetchone()
                if row is None or (row["name"], row["emoji"]) != (old_name, old_emoji):
                    continue
                name, emoji, description = new_text[system]
                if self.db.execute("SELECT 1 FROM items WHERE guild=? AND lower(name)=lower(?) AND id<>?", (guild, name, row["id"])).fetchone():
                    continue
                self.db.execute("UPDATE items SET name=?, emoji=?, description=? WHERE id=?",
                                (name, emoji, description if row["description"] == old_description else row["description"], row["id"]))
        self.update_settings(guild, seed_version=3, package_table=modernize_table(cfg["package_table"]),
                             dungeon_content=balance_rules.migrate_content_names(cfg["dungeon_content"]),
                             dungeon=balance_rules.migrate_override_keys(cfg["dungeon"]), **changes)

    def _upgrade_seed_v2(self, guild: int) -> None:
        """버전 2: 부활권을 만들고 패키지 결과표에 0.1%로 넣는다(table_with_revive). 부활권이 이미 표에 있으면 그대로 둔다.
        - 같은 이름('부활권')의 물품을 관리자가 이미 만들어 뒀다면 그 물품을 부활권으로 이어받는다(이름·가격·가방 속 개수는 그대로)."""
        revive_id = self._ensure_revive_item(guild)
        table = table_with_revive(self.settings(guild)["package_table"], revive_id)
        self.update_settings(guild, seed_version=2, package_table=table)

    def _ensure_revive_item(self, guild: int) -> int:
        """부활권 물품의 id. 없으면 만든다(팔 수 없고 살 수 없다). 같은 이름의 물품이 있으면 그걸 부활권으로 삼는다."""
        name, emoji, description = REVIVE_ITEM
        with self.db:
            row = self.db.execute("SELECT id FROM items WHERE guild=? AND system=?", (guild, REVIVE_SYSTEM)).fetchone()
            if row is None:
                same = self.db.execute("SELECT id, kind FROM items WHERE guild=? AND lower(name)=lower(?)", (guild, name)).fetchone()
                if same and same["kind"] == "item":
                    self.db.execute("UPDATE items SET system=? WHERE id=?", (REVIVE_SYSTEM, same["id"]))
                    return same["id"]
                if same:                                           # 같은 이름의 역할 상품이 있다면 이름만 살짝 다르게 만든다.
                    name = "던전 " + name
                cursor = self.db.execute("INSERT INTO items(guild, name, emoji, description, kind, system) VALUES(?,?,?,?,'item',?)",
                                         (guild, name, emoji, description, REVIVE_SYSTEM))
                return cursor.lastrowid
            return row["id"]

    def _upgrade_seed_v1(self, guild: int) -> None:
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
            table = package_table_v1(ids, new_ids)
        self.update_settings(guild, seed_version=1, package_table=table)

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
                    cursor = self.db.execute(                       # RETURNING은 SQLite 3.35 이상에서만 되므로 lastrowid를 쓴다.
                        "INSERT INTO items(guild, name, emoji, description, kind, sell_price, sell_max, sellable, system) "
                        "VALUES(?,?,?,?,'item',?,?,?,?)", (guild, name, emoji, description, low, high, int(sellable), system))
                    row = {"id": cursor.lastrowid}
                    listed += sellable
                new_ids[system] = row["id"]
        return new_ids

    def apply_default_package_table(self, guild: int) -> list[dict]:
        """패키지 결과표를 지금의 기본 확률표로 통째로 바꾼다(관리자가 손댄 표여도). 표에 필요한 물품(열쇠·쓰레기·새 물품 7종·부활권) 중 없는 것은 만들고,
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
        table = default_package_table(ids, new_ids, self._ensure_revive_item(guild))
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
        if any(item_id == r["result"] or item_id in r["ingredients"] for r in self.recipes(guild)):
            out.append("연금술 레시피(`/연금술레시피 목록`)")
        return out

    # ---- 연금술 ----
    def recipes(self, guild: int) -> list[dict]:
        """이 서버의 연금술 레시피 [{id, result, ingredients: [물품 id, ...](작은 id부터)}]."""
        return [{"id": r["id"], "result": r["result"], "ingredients": json.loads(r["ingredients"])}
                for r in self.db.execute("SELECT id, result, ingredients FROM recipes WHERE guild=? ORDER BY id", (guild,))]

    def recipe_for(self, guild: int, ingredients) -> dict | None:
        """재료(순서·중복 상관없이 같은 구성)에 맞는 레시피. 없으면 None."""
        key = json.dumps(sorted(ingredients))
        row = self.db.execute("SELECT id, result, ingredients FROM recipes WHERE guild=? AND ingredients=?", (guild, key)).fetchone()
        return {"id": row["id"], "result": row["result"], "ingredients": json.loads(row["ingredients"])} if row else None

    def recipe_price(self, guild: int, ingredients) -> int:
        """재료들의 지금 판매가로 계산한 합성물의 판매가."""
        return alchemy.combined_price([self.item(guild, i)["sell_price"] for i in ingredients])

    def alchemy_stock(self, guild: int, user: int):
        """이 사람의 가방에서 레시피 재료로 쓰이는 물품(만든 순서). 합성 화면의 선택지에 쓴다."""
        used = sorted({i for r in self.recipes(guild) for i in r["ingredients"]})
        if not used:
            return []
        marks = ",".join("?" * len(used))
        return self.db.execute(
            f"SELECT items.*, inventory.qty AS qty FROM inventory JOIN items ON items.id = inventory.item "
            f"WHERE inventory.guild=? AND inventory.user=? AND items.kind='item' AND items.id IN ({marks}) ORDER BY items.id",
            (guild, user, *used)).fetchall()

    def add_recipe(self, guild: int, result_id: int, ingredient_ids) -> dict:
        """레시피를 더한다. 결과물의 판매가는 재료 판매가로 정해 바로 적용한다(상점 자리가 있으면 판매도 켠다).
        {recipe, price, listed(상점에 올라가 있는지)}를 돌려준다. 규칙에 어긋나면 StoreError."""
        ids = sorted(ingredient_ids)
        if not alchemy.MIN_INGREDIENTS <= len(ids) <= alchemy.MAX_INGREDIENTS:
            raise StoreError(f"재료는 {alchemy.MIN_INGREDIENTS}~{alchemy.MAX_INGREDIENTS}개로 정해 주세요.")
        if len(self.recipes(guild)) >= MAX_RECIPES:
            raise StoreError(f"레시피는 최대 {MAX_RECIPES}개까지예요.")
        rows = {i: self.item(guild, i) for i in {*ids, result_id}}
        if any(row is None for row in rows.values()):
            raise StoreError("그런 물품이 없어요.")
        if any(row["kind"] != "item" for row in rows.values()):
            raise StoreError("연금술에는 물품만 쓸 수 있어요. 역할은 안 돼요.")
        result = rows[result_id]
        if result_id in ids:
            raise StoreError("결과물이 재료로 들어갈 수는 없어요.")
        existing = self.recipes(guild)
        results = {r["result"] for r in existing}
        used = {i for r in existing for i in r["ingredients"]}
        if result_id in results:
            raise StoreError(f"{result['emoji']} {result['name']}은(는) 이미 다른 레시피의 결과물이에요. 한 물품에는 레시피를 하나만 둘 수 있어요.")
        if result_id in used:
            raise StoreError(f"{result['emoji']} {result['name']}은(는) 다른 레시피의 재료예요. 재료는 결과물이 될 수 없어요.")
        for i in set(ids):
            if i in results:
                raise StoreError(f"{rows[i]['emoji']} {rows[i]['name']}은(는) 연금술 결과물이라 재료로 쓸 수 없어요. (합성물을 다시 합칠 수는 없어요)")
            if rows[i]["sell_price"] < 1 or rows[i]["sell_max"] is not None:
                raise StoreError(f"{rows[i]['emoji']} {rows[i]['name']}의 판매가가 1 이상의 고정값이어야 재료로 쓸 수 있어요. (지금: 음수·0·범위 판매가는 값을 정할 수 없어요)")
        if self.recipe_for(guild, ids) is not None:
            other = self.item(guild, self.recipe_for(guild, ids)["result"])
            raise StoreError(f"같은 재료의 레시피가 이미 있어요: {other['emoji']} {other['name']}")
        price = alchemy.combined_price([rows[i]["sell_price"] for i in ids])
        top = self.currency(guild).max
        if price > top:
            raise StoreError(f"합성물의 판매가가 {price:,}이라 재화 최대({top:,})를 넘어요. 재료의 판매가를 낮춰 주세요.")
        listed_count = self.db.execute("SELECT COUNT(*) AS n FROM items WHERE guild=? AND (buyable=1 OR sellable=1) AND id<>?",
                                       (guild, result_id)).fetchone()["n"]
        can_list = bool(result["sellable"] or result["buyable"]) or listed_count < MAX_SHOP_ITEMS
        with self.db:
            cursor = self.db.execute("INSERT INTO recipes(guild, result, ingredients) VALUES(?,?,?)", (guild, result_id, json.dumps(ids)))
            self.db.execute("UPDATE items SET sell_price=?, sell_max=NULL, sellable=? WHERE guild=? AND id=?",
                            (price, 1 if (can_list or result["sellable"]) else 0, guild, result_id))
        listed = bool(self.item(guild, result_id)["sellable"] or self.item(guild, result_id)["buyable"])
        return {"recipe": {"id": cursor.lastrowid, "result": result_id, "ingredients": ids}, "price": price, "listed": listed}

    def delete_recipe(self, guild: int, result_id: int) -> bool:
        """결과물로 레시피를 찾아 지운다. 결과물 물품과 그 판매가는 그대로 둔다."""
        with self.db:
            return self.db.execute("DELETE FROM recipes WHERE guild=? AND result=?", (guild, result_id)).rowcount > 0

    def _refresh_recipe_prices(self, guild: int, ingredient_id: int) -> None:
        """재료의 판매가가 바뀌면 그 재료를 쓰는 레시피의 결과물 판매가를 다시 계산한다."""
        with self.db:
            for recipe in self.recipes(guild):
                if ingredient_id in recipe["ingredients"]:
                    self.db.execute("UPDATE items SET sell_price=?, sell_max=NULL WHERE guild=? AND id=?",
                                    (self.recipe_price(guild, recipe["ingredients"]), guild, recipe["result"]))

    def alchemize(self, guild: int, user: int, ingredient_ids) -> dict:
        """재료를 합친다: 맞는 레시피가 있고 재료를 다 가졌으면 재료를 빼고 결과물을 1개 넣는다(한 덩어리).
        레시피가 없거나 재료가 모자라면 아무것도 바꾸지 않고 StoreError. 시간 제한은 부르는 쪽(alchemy.is_open)이 본다."""
        ids = sorted(ingredient_ids)
        if not alchemy.MIN_INGREDIENTS <= len(ids) <= alchemy.MAX_INGREDIENTS:
            raise StoreError(f"재료는 {alchemy.MIN_INGREDIENTS}~{alchemy.MAX_INGREDIENTS}개를 골라 주세요.")
        recipe = self.recipe_for(guild, ids)
        if recipe is None:
            raise StoreError("그런 조합의 레시피는 없어요. 재료는 그대로예요.")
        need = {i: ids.count(i) for i in set(ids)}
        for item_id, count in need.items():
            have = self.qty(guild, user, item_id)
            if have < count:
                row = self.item(guild, item_id)
                raise StoreError(f"{row['emoji']} {row['name']}이(가) {count}개 필요해요. (보유 {have}개)")
        result = self.item(guild, recipe["result"])
        if result is None:
            raise StoreError("결과물이 사라졌어요. 관리자에게 알려 주세요.")
        with self.db:                                         # add_item은 따로 저장하므로 쓰지 않고 한 번에 처리한다.
            for item_id, count in need.items():                # 인벤토리는 수량 0을 허용하지 않아서, 다 쓰면 행을 지운다.
                if self.qty(guild, user, item_id) == count:
                    self.db.execute("DELETE FROM inventory WHERE guild=? AND user=? AND item=?", (guild, user, item_id))
                else:
                    self.db.execute("UPDATE inventory SET qty=qty-? WHERE guild=? AND user=? AND item=?", (count, guild, user, item_id))
            self.db.execute("INSERT INTO inventory VALUES(?,?,?,1) ON CONFLICT(guild,user,item) DO UPDATE SET qty=qty+1",
                            (guild, user, recipe["result"]))
        return {"recipe": recipe, "result": result, "price": result["sell_price"]}

    def create_item(self, guild, name, *, emoji="⚙️", description="", kind="item", role_id=None,
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
        price_changed = (merged["sell_price"], merged["sell_max"]) != (row["sell_price"], row["sell_max"])
        if price_changed:                                         # 연금술과 얽힌 물품의 판매가 규칙
            for recipe in self.recipes(guild):
                if item_id == recipe["result"]:
                    raise StoreError("연금술 결과물의 판매가는 재료 판매가로 자동 계산돼요. 직접 바꾸려면 먼저 `/연금술레시피 삭제`로 레시피를 지워 주세요.")
                if item_id in recipe["ingredients"] and (merged["sell_price"] < 1 or merged["sell_max"] is not None):
                    raise StoreError("이 물품은 연금술 재료라서 판매가를 1 이상의 고정값으로만 정할 수 있어요. 다른 값으로 바꾸려면 먼저 레시피를 지워 주세요.")
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
        if price_changed:
            self._refresh_recipe_prices(guild, item_id)
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
    def create_package(self, guild: int, channel: int, message: int, expires: float, mode: str, pile: int = 0):
        """패키지 하나를 기록한다. pile이 0보다 크면 '더미'이고, 뜯으면 결과가 pile개 나온다."""
        with self.db:
            self.db.execute("INSERT INTO packages(message, guild, channel, expires, mode, pile) VALUES(?,?,?,?,?,?)",
                            (message, guild, channel, expires, mode, pile))

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
    def add_tickets(self, guild: int, user: int, amount: int) -> int:
        """히든 칭호 획득권을 더하거나(양수) 뺀다(음수). 0 아래로는 내려가지 않고 TICKET_MAX를 넘지 않는다. 실제로 바뀐 개수를 돌려준다."""
        with self.db:
            old = self.tickets(guild, user)
            new = min(TICKET_MAX, max(0, old + amount))
            if new:
                self.db.execute("INSERT INTO tickets VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET count=excluded.count", (guild, user, new))
            else:
                self.db.execute("DELETE FROM tickets WHERE guild=? AND user=?", (guild, user))
        return new - old

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
        with self.db:                                              # 입장 물품 차감과 던전 생성은 한 덩어리
            self._take_entry(guild, user)
            self.db.execute("INSERT INTO dungeons VALUES(?,?,?)", (guild, user, new_data))
        return new_data, True

    def _take_entry(self, guild: int, user: int) -> None:
        """입장 물품을 정해진 개수만큼 낸다(거래 안에서 불러야 한다). 모자라면 StoreError. 무료 입장이면 아무것도 안 한다."""
        cfg = self.settings(guild)
        entry, cost = cfg["entry_item"], cfg["entry_cost"]
        if entry is None:
            return
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

    def start_auto(self, guild: int, user: int, data: str, charge: bool) -> None:
        """빠른진행을 켠 던전 상태를 저장한다. charge면 입장 물품을 한 번 더 내는데(이 던전에서 처음 켤 때만), 저장과 한 덩어리다.
        물품이 모자라거나 진행 중인 던전이 없으면 아무것도 바꾸지 않고 StoreError."""
        self.ensure_defaults(guild)
        with self.db:
            if charge:
                self._take_entry(guild, user)
            if self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user)).rowcount == 0:
                raise StoreError("진행 중인 던전이 없어요.")

    def save_dungeon(self, guild: int, user: int, data: str) -> None:
        """진행 상황을 저장한다. 이미 끝난(지워진) 던전은 되살리지 않는다."""
        with self.db:
            self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user))

    def revive_item(self, guild: int):
        """부활권 물품(행). 관리자가 지웠다면 None."""
        return self.db.execute("SELECT * FROM items WHERE guild=? AND system=?", (guild, REVIVE_SYSTEM)).fetchone()

    def use_revive(self, guild: int, user: int, data: str) -> bool:
        """부활권 한 장을 쓰고, 되살아난 던전 상태를 저장한다(한 덩어리). 부활권이 없거나 진행 중인 던전이 없으면 아무것도 바꾸지 않고 False."""
        class _No(Exception):
            pass
        try:
            with self.db:
                item = self.revive_item(guild)
                if item is None:
                    raise _No
                if self.db.execute("UPDATE inventory SET qty=qty-1 WHERE guild=? AND user=? AND item=? AND qty>1",
                                   (guild, user, item["id"])).rowcount == 0:
                    if self.db.execute("DELETE FROM inventory WHERE guild=? AND user=? AND item=? AND qty=1",
                                       (guild, user, item["id"])).rowcount == 0:
                        raise _No
                if self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user)).rowcount == 0:
                    raise _No
        except _No:
            return False
        return True

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

    def spend_in_dungeon(self, guild: int, user: int, data: str, total: int) -> int:
        """던전 안 상점에서 산 결과를 저장하고 재화를 뺀다(한 덩어리). 지금 가진 재화를 돌려준다.
        재화가 모자라거나 진행 중인 던전이 없으면 거절하고 아무것도 바꾸지 않는다."""
        with self.db:
            self._ensure_wallet(guild, user)
            old = self.wallet(guild, user)
            cur = self.currency(guild)
            if old < total:
                raise StoreError(f"{i_ga(cur.name)} 모자라요. 가진 것 {cur.money(old)}, 필요한 것 {cur.money(total)}.")
            if self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user)).rowcount == 0:
                raise StoreError("진행 중인 던전이 없어요.")
            self.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (total, guild, user))
        return old - total

    def end_dungeon(self, guild: int, user: int, *, candy: int = 0, tickets: int = 0, items=()) -> tuple[int, int] | None:
        """던전을 끝내고 기록을 지운다. 보상(재화는 최대 보유량까지)을 한 번에 준다.
        (실제로 받은 재화, 지금 가진 획득권 수)를 돌려준다. 이미 끝난 던전이면 None이고 보상도 없다."""
        with self.db:
            if self.db.execute("DELETE FROM dungeons WHERE guild=? AND user=?", (guild, user)).rowcount == 0:
                return None
            gained = self._pay_rewards(guild, user, candy, tickets, items)
        return gained, self.tickets(guild, user)

    def _pay_rewards(self, guild: int, user: int, candy: int, tickets: int, items) -> int:
        """재화(최대 보유량까지)·획득권·물품을 준다(거래 안에서 불러야 한다). 실제로 받은 재화를 돌려준다."""
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
        return gained

    def reward_in_dungeon(self, guild: int, user: int, data: str, *, candy: int = 0, tickets: int = 0, items=()) -> tuple[int, int] | None:
        """던전은 끝내지 않고 진행 상태를 저장하면서 보상을 한 번에 준다(무한모드의 클리어 보상·층 보너스).
        (실제로 받은 재화, 지금 가진 획득권 수)를 돌려준다. 이미 끝난 던전이면 None이고 아무것도 주지 않는다."""
        with self.db:
            if self.db.execute("UPDATE dungeons SET data=? WHERE guild=? AND user=?", (data, guild, user)).rowcount == 0:
                return None
            gained = self._pay_rewards(guild, user, candy, tickets, items)
        return gained, self.tickets(guild, user)
