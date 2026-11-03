"""던전 밸런스: 기본값, 관리자가 바꿀 수 있는 항목(PARAMS), 서버별 설정을 적용한 Balance.

디스코드에 접속하지 않는 순수 로직이다. 기본값은 이 파일 맨 위에 있고(dungeon.py가 그대로 다시 내보낸다),
관리자가 바꾼 값은 서버마다 '항목 이름 → 숫자' 사전(overrides)으로 저장된다. Balance.build(overrides)가
기본값 위에 그 사전을 덮어써서 던전이 실제로 쓰는 값을 만든다. 알 수 없는 항목이나 범위를 벗어난 값은 무시한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

# ---------------------------------------------------------------- 기본값
MAX_HP = 1000
BASE_DAMAGE = 20                 # 맨손 공격력
ROOMS = 10                       # 한 층의 방은 10개(고정). 10번째 방은 미니 보스
FLOORS = 10                      # 올라가야 하는 층 수. 마지막 층의 보스를 이기면 클리어
FLOOR_SCALE = 4                  # 층이 하나 오를 때마다 몬스터·보스의 체력과 공격이 1층의 몇 %씩 늘어나는지
SECRET_CHANCE = 10.0               # 보스를 이기고 올라갈 때 11번째 방(시크릿 보스)이 생길 확률(%)
REVIVE_HEAL = 50                 # 부활권으로 되살아날 때 최대 HP의 몇 %로 일어나는지
FLOOR_HEAL = 50                  # 위 층으로 올라갈 때 잃은 HP의 몇 %를 회복하는지
BASE_DODGE = 10                  # 잡몹 기본 회피 10% (보스는 회피 불가)
GUARD_BASE = 60                  # 가드 성공 시 피해 60% 감소(방패가 없을 때)
GUARD_CHANCE = 60                # 방어 버튼을 눌렀을 때 가드에 성공할 확률(%)
GUARD_HEAL = 50                  # 가드 성공 시 그 공격 피해(원래 값)의 이 %만큼 HP 회복
REFLECT_BASE = 30                # 가드 성공 시 그 공격 피해의 이 %를 몬스터에게 되돌린다(방패 장비는 더 높다)
CLEAR_CANDY = (50, 150)          # 클리어 보상 재화(최소, 최대)
TICKET_CHANCE = 10               # 클리어했을 때 히든 칭호 획득권이 나올 확률(%)
TIER_ORDER = {"일반": 0, "희귀": 1, "전설": 2}
SELL_DEFAULT = {"일반": 5, "희귀": 15, "전설": 50}     # 던전 장비·소모품의 기본 판매가(등급별). 0이면 팔 수 없다.
SELL_MAX = 1_000_000_000

ROOM_KINDS = (("chest", "상자"), ("empty", "빈방"), ("monster", "잡몹"), ("boss", "보스"))
# 방 배치: 1~10번째 방 각각의 추첨표((종류, 가중치)). 홀수 방은 상자, 짝수 방은 빈 방 또는 잡몹, 마지막 방은 미니 보스.
# 가중치는 합이 100일 필요가 없다(비율로 쓴다). 가중치가 0인 칸은 나오지 않고, 칸이 하나뿐인 방은 난수를 뽑지 않는다.
_CHEST = (("chest", 100),)
_EITHER = (("empty", 40), ("monster", 60))
ROOM_PLAN = (_CHEST, _EITHER, _CHEST, _EITHER, _CHEST, _EITHER, _CHEST, _EITHER, _CHEST, (("boss", 100),))


@dataclass(frozen=True)
class Gear:
    key: str
    name: str
    emoji: str
    slot: str                    # weapon | shield
    tier: str
    weight: float                # 상자에서 나올 가중치(소수 가능). 좋은 장비일수록 낮다.
    attack: int = 0              # 공격 피해량 추가
    crit: int = 0                # 크리티컬 확률(%). 크리티컬은 피해 2배
    dodge: int | None = None     # 잡몹의 회피 확률을 이 값으로 바꾼다(보스에게는 적용되지 않는다)
    guard: int | None = None     # 가드 피해 감소(%)
    reflect: int | None = None   # 가드 성공 시 반사 피해(%). 없으면 REFLECT_BASE
    description: str = ""
    sell_price: int = 0          # 던전 안에서 팔 때 받는 재화(개당). 0이면 팔 수 없다.
    guard_chance: int | None = None  # 방패: 이 방패로 방어할 때 가드에 성공할 확률(%). 없으면 서버 기본(방어.성공확률)


@dataclass(frozen=True)
class Consumable:
    key: str
    name: str
    emoji: str
    tier: str
    weight: float                # 상자에서 나올 가중치(소수 가능)
    heal: int = 0
    full: bool = False           # HP를 전부 회복
    regen: int = 0               # 턴이 지날 때마다 회복하는 양
    regen_turns: int = 0
    description: str = ""
    sell_price: int = 0          # 던전 안에서 팔 때 받는 재화(개당). 0이면 팔 수 없다.
    modify: str | None = None    # 무기 개조: 쓰면 지금 든 무기가 이 무기(key)로 바뀐다. 개조 소모품은 회복 효과가 없다.
    conditions: tuple = ()       # 사용 조건 ((종류, 값), ...). 전부 만족해야 쓸 수 있다. 비면 언제나 쓸 수 있다(아래 COND_* 참고).
    material: bool = False       # 개조 재료: 효과가 없고, /장비개조 레시피에 있는 무기를 들고 쓰면 그 무기가 개조된다.


@dataclass(frozen=True)
class Monster:
    key: str
    name: str
    emoji: str
    hp: int
    damage: int
    dodge: int = BASE_DODGE      # 보스는 0 — 회피 불가
    boss: bool = False
    weight: float = 1            # 같은 방 종류(잡몹끼리, 보스끼리)에서 나올 가중치(소수 가능). 기본은 모두 1 = 같은 확률


@dataclass(frozen=True)
class SecretBoss:
    """시크릿 보스: 보스를 이기고 올라갈 때 가끔(기본 10%) 열리는 11번째 방의 보스. 장비나 아이템을 반드시 하나는 떨어뜨린다."""
    key: str
    name: str
    emoji: str
    hp: int
    damage: int
    weight: float = 1                  # 시크릿 보스가 여럿일 때 나올 비율(소수 가능)
    drop_gear: str | None = None       # 떨어뜨릴 장비(key)
    drop_gear_chance: float = 100      # 장비가 떨어질 확률(%)
    drop_item: str | None = None       # 떨어뜨릴 소모품(key)
    drop_item_chance: float = 100
    drop_item_amount: int = 1
    intro: str = ""                    # 나타날 때 연출에 나오는 한마디
    image: str = ""                    # 연출·전투 화면에 붙일 그림 주소(움직이는 gif도 돼요)
    boss: bool = True                  # 보스처럼 회피 불가
    dodge: int = 0


def describe_gear(gear: Gear) -> str:
    """장비 설명을 지금 수치로 만든다(관리자가 값을 바꿔도 설명이 거짓말이 되지 않게)."""
    if gear.slot == "weapon":
        parts = [f"공격 피해량 +{gear.attack}"]
        if gear.crit:
            parts.append(f"크리티컬(2배) 확률 {gear.crit}%")
        if gear.dodge is not None:
            parts.append(f"대상 회피 확률 {gear.dodge}%로 변환")
        return ", ".join(parts)
    parts = [f"가드 피해 감소 {gear.guard}%"] if gear.guard is not None else []
    if gear.guard_chance is not None:
        parts.append(f"가드 성공률 {gear.guard_chance}%")
    if gear.reflect is not None:
        parts.append(f"가드 성공 시 {gear.reflect}% 반사")
    return ", ".join(parts)


def describe_item(item: Consumable) -> str:
    if item.material:
        return "특정 무기를 개조하는 재료예요"
    if item.modify:
        return "들고 있는 무기를 다른 무기로 개조해요"
    if item.full:
        return "HP를 전부 회복"
    if item.regen:
        return f"{item.regen_turns}턴 동안 턴이 지날 때마다 HP +{item.regen}"
    return f"HP +{item.heal}"


def _gear(*args, **kwargs) -> Gear:
    kwargs.setdefault("sell_price", SELL_DEFAULT[args[4]])        # args[4]는 등급
    gear = Gear(*args, **kwargs)
    return replace(gear, description=describe_gear(gear))


def _item(*args, **kwargs) -> Consumable:
    kwargs.setdefault("sell_price", SELL_DEFAULT[args[3]])        # args[3]은 등급
    item = Consumable(*args, **kwargs)
    return replace(item, description=describe_item(item))


# 장비 — 좋은 장비일수록 가중치가 낮다(기본값은 합계 100).
GEAR = (
    _gear("club", "낡은 몽둥이", "🏏", "weapon", "일반", 25, attack=30),
    _gear("wooden_shield", "나무 방패", "🪵", "shield", "일반", 20, guard=80, reflect=40),
    _gear("dagger", "단검", "🗡️", "weapon", "일반", 18, attack=35, crit=50),
    _gear("longsword", "롱소드", "⚔️", "weapon", "희귀", 12, attack=100),
    _gear("shield", "방패", "🛡️", "shield", "희귀", 10, guard=90, reflect=50),
    _gear("crossbow", "석궁", "🏹", "weapon", "희귀", 8, attack=180, dodge=35),
    _gear("gun", "총", "🔫", "weapon", "전설", 4, attack=300, dodge=50),
    _gear("pumpkin_blade", "호박 대검", "🎃", "weapon", "전설", 3, attack=200, crit=25),
)
CONSUMABLES = (
    _item("candy", "사탕", "🍭", "일반", 38, heal=50),
    _item("cookie", "달콤한 쿠키", "🍪", "일반", 24, heal=100),
    _item("pumpkin_soup", "호박죽", "🍲", "희귀", 18, heal=200),
    _item("soul_essence", "영혼 에센스", "👻", "희귀", 10, regen=70, regen_turns=10),
    _item("witch_elixir", "마녀의 비약", "🧪", "희귀", 7, heal=400),
    _item("golden_soup", "황금 호박죽", "🥣", "전설", 3, full=True),
)
MONSTERS = (                                                    # 원래 수치의 약 90%로 너프
    Monster("giant_bat", "거대 박쥐", "🦇", 225, 65),
    Monster("slime", "슬라임", "🟢", 270, 45),
    Monster("witch", "마녀", "🧙", 135, 135),
    Monster("giant_rat", "거대 쥐", "🐀", 90, 45, dodge=30),        # 특수: 회피 확률 30%
)
SECRET_BOSSES = (
    SecretBoss("void_lord", "공허의 군주", "🌌", 1200, 150, drop_gear="gun", drop_gear_chance=50, drop_item="golden_soup", drop_item_chance=60,
               intro="어둠 속에서 거대한 눈동자가 천천히 떠오른다…"),
)
BOSSES = (                                                      # 보스는 회피 불가(dodge=0)
    Monster("skeleton_king", "해골 왕", "💀", 900, 135, dodge=0, boss=True),
    Monster("giant_slime", "거대 슬라임", "🟩", 1125, 90, dodge=0, boss=True),
    Monster("goblin_king", "고블린 왕", "👺", 810, 180, dodge=0, boss=True),
)


# ---------------------------------------------------------------- 던전 콘텐츠(몬스터·보스·장비·소모품을 관리자가 더하고 빼고 바꾼다)
SECTIONS = ("monsters", "bosses", "gear", "consumables", "secrets")
SECTION_LABELS = {"monsters": "몬스터", "bosses": "보스", "gear": "장비", "consumables": "소모품", "secrets": "시크릿 보스"}
SECTION_LIMITS = {"monsters": 25, "bosses": 25, "gear": 50, "consumables": 50, "secrets": 10}
NAME_MAX = 30


def entity_dict(entity) -> dict:
    """저장용 사전(설명은 수치에서 다시 만들므로 저장하지 않는다)."""
    data = asdict(entity)
    data.pop("description", None)
    return data


def _num(data: dict, key: str, low, high, default=0, none_ok=False):
    value = data.get(key, default)
    if value is None and none_ok:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value) or not low <= value <= high:
        return None, f"{key} 값은 {low}~{high} 사이의 정수여야 해요."
    return int(value), None


# ---------------------------------------------------------------- 소모품 사용 조건
# 상태 조건은 지금 상태를 보고(내 HP, 몬스터 HP: 최대 체력의 %), 사건 조건은 '바로 직전 행동'에서 일어난 일을 본다(다음 행동을 하면 사라진다).
# 반사는 가드에 성공하면 항상 들어가므로 '반사 성공'은 가드 성공, '반사 실패'는 가드 실패와 같은 사건이다.
MAX_CONDITIONS = 5
COND_STATE = ("hp_le", "hp_ge", "enemy_hp_le", "enemy_hp_ge")
COND_ITEM = ("item_gain", "item_use")
COND_EVENT = ("guard_success", "guard_fail", "reflect_success", "reflect_fail", "attack_hit", "attack_miss") + COND_ITEM
COND_LABELS = {
    "hp_le": "내 HP {v}% 이하", "hp_ge": "내 HP {v}% 이상", "enemy_hp_le": "몬스터 HP {v}% 이하", "enemy_hp_ge": "몬스터 HP {v}% 이상",
    "guard_success": "방어 성공 직후", "guard_fail": "방어 실패 직후", "reflect_success": "반사 성공 직후", "reflect_fail": "반사 실패 직후",
    "attack_hit": "공격 적중 직후", "attack_miss": "공격이 빗나간 직후", "item_gain": "{v} 획득 직후", "item_use": "{v} 사용 직후",
}
EVENT_TAG = {"guard_success": "guard_success", "reflect_success": "guard_success", "guard_fail": "guard_fail", "reflect_fail": "guard_fail",
             "attack_hit": "attack_hit", "attack_miss": "attack_miss"}      # 조건 종류 → Run.last에 남는 표시


def condition_text(cond, names: dict | None = None) -> str:
    """조건 하나를 사람이 읽는 문장으로. names는 소모품 key → 이름(없으면 key를 그대로 쓴다)."""
    kind, value = cond
    shown = (names or {}).get(value, value) if kind in COND_ITEM else value
    return COND_LABELS[kind].format(v=shown)


def check_conditions(raw):
    """저장된 조건 목록을 검사해 ((종류, 값), ...)로 만든다. (조건들, None) 또는 (None, 이유)."""
    if raw is None:
        return (), None
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_CONDITIONS:
        return None, f"사용 조건은 {MAX_CONDITIONS}개까지예요."
    out, seen = [], set()
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return None, "사용 조건 형식이 올바르지 않아요."
        kind, value = entry
        if kind in COND_STATE:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                return None, "HP 조건의 값은 0~100(%) 정수여야 해요."
            if kind in seen:
                return None, f"'{condition_text((kind, value))}' 같은 종류의 조건은 하나만 붙일 수 있어요."
        elif kind in COND_ITEM:
            if not isinstance(value, str) or not 1 <= len(value) <= 40:
                return None, "아이템 조건에는 소모품이 필요해요."
        elif kind in COND_EVENT:
            if value is not None:
                return None, "이 조건에는 값이 필요 없어요."
        else:
            return None, "알 수 없는 사용 조건이에요."
        if (kind, value) in seen:
            return None, "같은 조건을 두 번 붙일 수 없어요."
        seen.add((kind, value))
        if kind in COND_STATE:
            seen.add(kind)
        out.append((kind, value))
    events = [c for c in out if c[0] in COND_EVENT]
    if len(events) > 1:
        return None, ("직전 행동 조건(방어·반사·공격·아이템 획득/사용)은 하나만 붙일 수 있어요. 한 번의 행동에서는 한 가지 일만 일어나서 "
                      "둘을 동시에 만족할 수 없어요. HP 조건은 함께 붙일 수 있어요.")
    values = {c[0]: c[1] for c in out if c[0] in COND_STATE}
    for low, high, who in (("hp_ge", "hp_le", "내"), ("enemy_hp_ge", "enemy_hp_le", "몬스터")):
        if low in values and high in values and values[low] > values[high]:
            return None, f"{who} HP의 '이상' 값이 '이하' 값보다 커서 조건을 만족할 수 없어요."
    if events and events[0][0] == "item_gain" and ("enemy_hp_le" in values or "enemy_hp_ge" in values):
        return None, "아이템은 몬스터가 없는 상자 방에서 얻어서, 몬스터 HP 조건과 함께 쓸 수 없어요."
    return tuple(out), None


def item_summary(item: Consumable, bal) -> str:
    """소모품 설명 한 줄: 효과(개조면 목표 무기 이름) + 사용 조건."""
    if item.material:
        recipes = [(bal.gear_by_key[w], bal.gear_by_key[r]) for w, i, r in bal.mods if i == item.key]
        text = "개조 재료" + (": " + ", ".join(f"{w.name}→{r.name}" for w, r in recipes[:3]) + (" 외" if len(recipes) > 3 else "")
                              if recipes else "(쓸 수 있는 장비가 아직 없어요)")
    elif item.modify:
        gear = bal.gear_by_key.get(item.modify)
        text = f"무기를 {gear.emoji} {gear.name}(으)로 개조" if gear else "무기 개조(목표 무기가 사라졌어요)"
    else:
        text = item.description
    if item.conditions:
        names = {c.key: c.name for c in bal.consumables}
        text += " · 조건: " + " & ".join(condition_text(c, names) for c in item.conditions)
    return text


WEIGHT_MAX = 1000
WEIGHT_DECIMALS = 4                      # 등장 비율은 소수 넷째 자리까지 쓴다(0.0001 단위)


def _decimal(data: dict, key: str, high: float, default=0):
    """0~high 사이의 소수(넷째 자리까지). 정수로 딱 떨어지면 int로 돌려줘서 저장·표시가 깔끔하다."""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        return None, f"{key} 값은 0~{high:g} 사이의 숫자여야 해요."
    if not 0 <= value <= high:
        return None, f"{key} 값은 0~{high:g} 사이의 숫자여야 해요."
    rounded = round(float(value), WEIGHT_DECIMALS)
    if value > 0 and rounded == 0:
        return None, f"{key} 값이 너무 작아요. 소수 넷째 자리(0.0001)까지만 쓸 수 있어요."
    return (int(rounded) if rounded.is_integer() else rounded), None


def _weight(data: dict, key: str = "weight", default=0):
    """등장 비율: 0~1000, 소수 가능(넷째 자리까지)."""
    return _decimal(data, key, WEIGHT_MAX, default)


def _check_secret(data: dict, key: str, name: str, emoji: str, numbers):
    n, problem = numbers(("hp", 1, 1000000, 1), ("damage", 0, 100000, 0), ("drop_item_amount", 1, 99, 1))
    if problem:
        return None, problem
    weight, problem = _weight(data, default=1)
    if problem:
        return None, problem
    drops = {}
    for field_name, label in (("drop_gear", "드롭 장비"), ("drop_item", "드롭 아이템")):
        value = data.get(field_name)
        if value not in (None, "") and (not isinstance(value, str) or not 1 <= len(value) <= 40):
            return None, f"{label}가 올바르지 않아요."
        drops[field_name] = value or None
    if drops["drop_gear"] is None and drops["drop_item"] is None:
        return None, "시크릿 보스는 드롭할 장비나 아이템이 하나는 있어야 해요."
    chances = {}
    for field_name in ("drop_gear_chance", "drop_item_chance"):
        chances[field_name], problem = _decimal(data, field_name, 100, 100)
        if problem:
            return None, problem
    intro, image = data.get("intro", ""), data.get("image", "")
    if not isinstance(intro, str) or len(intro) > 200:
        return None, "등장 문구는 200자까지예요."
    if not isinstance(image, str) or len(image) > 300 or (image and (not image.startswith(("http://", "https://")) or any(c.isspace() for c in image))):
        return None, "그림은 공백 없는 http:// 또는 https:// 주소(300자까지)여야 해요."
    boss = SecretBoss(key, name, emoji, n["hp"], n["damage"], weight, drops["drop_gear"], chances["drop_gear_chance"], drops["drop_item"],
                      chances["drop_item_chance"], n["drop_item_amount"], intro.strip(), image)
    return boss, None


def check_entity(section: str, data) -> tuple[object | None, str | None]:
    """사전 하나를 몬스터·장비·소모품으로 바꾸고 검사한다. (개체, None) 또는 (None, 이유). 이모지 형식은 명령어 쪽에서 따로 본다."""
    if section not in SECTIONS or not isinstance(data, dict):
        return None, "올바르지 않은 데이터예요."
    key, name, emoji = data.get("key"), data.get("name"), data.get("emoji")
    if not isinstance(key, str) or not key or len(key) > 40:
        return None, "key가 올바르지 않아요."
    if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX or name != name.strip():
        return None, f"이름은 앞뒤 공백 없이 1~{NAME_MAX}자로 써 주세요."
    if not isinstance(emoji, str) or not 1 <= len(emoji) <= 80:
        return None, "이모지가 올바르지 않아요."

    def numbers(*specs):
        out = {}
        for spec in specs:
            value, problem = _num(data, *spec)
            if problem:
                return None, problem
            out[spec[0]] = value
        return out, None

    if section == "secrets":
        return _check_secret(data, key, name, emoji, numbers)
    if section in ("monsters", "bosses"):
        boss = section == "bosses"
        n, problem = numbers(("hp", 1, 100000, 1), ("damage", 0, 100000, 0), ("dodge", 0, 95, BASE_DODGE))
        if problem:
            return None, problem
        weight, problem = _weight(data, default=1)             # 예전에 저장된 몬스터에는 등장 값이 없어서 기본 1(같은 확률)로 본다
        if problem:
            return None, problem
        return Monster(key, name, emoji, n["hp"], n["damage"], 0 if boss else n["dodge"], boss, weight), None
    tier = data.get("tier", "일반")
    if tier not in TIER_ORDER:
        return None, "등급은 일반·희귀·전설 중 하나여야 해요."
    if section == "gear":
        slot = data.get("slot")
        if slot not in ("weapon", "shield"):
            return None, "장비 종류는 무기·방패 중 하나여야 해요."
        n, problem = numbers(("attack", 0, 100000, 0), ("crit", 0, 100, 0), ("sell_price", 0, SELL_MAX, SELL_DEFAULT[tier]))
        if problem:
            return None, problem
        n["weight"], problem = _weight(data)
        if problem:
            return None, problem
        extra = {}
        for field_name, high in (("dodge", 95), ("guard", 100), ("reflect", 500), ("guard_chance", 100)):
            extra[field_name], problem = _num(data, field_name, 0, high, None, none_ok=True)
            if problem:
                return None, problem
        if slot == "weapon":
            extra["guard"] = extra["reflect"] = extra["guard_chance"] = None
        else:
            n["attack"] = n["crit"] = 0
            extra["dodge"] = None
            if extra["guard"] is None:
                return None, "방패에는 가드(피해 감소 %)가 필요해요."
        gear = Gear(key, name, emoji, slot, tier, n["weight"], attack=n["attack"], crit=n["crit"], dodge=extra["dodge"],
                    guard=extra["guard"], reflect=extra["reflect"], sell_price=n["sell_price"],
                    guard_chance=extra["guard_chance"])
        return replace(gear, description=describe_gear(gear)), None
    n, problem = numbers(("heal", 0, 100000, 0), ("regen", 0, 100000, 0), ("regen_turns", 0, 1000, 0),
                         ("sell_price", 0, SELL_MAX, SELL_DEFAULT[tier]))
    if problem:
        return None, problem
    n["weight"], problem = _weight(data)
    if problem:
        return None, problem
    full = data.get("full", False)
    if not isinstance(full, bool):
        return None, "full 값이 올바르지 않아요."
    modify = data.get("modify")
    if modify is not None and (not isinstance(modify, str) or not 1 <= len(modify) <= 40):
        return None, "개조할 무기가 올바르지 않아요."
    material = data.get("material", False)
    if not isinstance(material, bool):
        return None, "material 값이 올바르지 않아요."
    if material and modify:
        return None, "개조 재료와 '개조무기'(쓰면 정해진 무기로 바뀌는 소모품)는 함께 쓸 수 없어요."
    conditions, problem = check_conditions(data.get("conditions"))
    if problem:
        return None, problem
    if modify or material:                              # 개조 소모품·개조 재료: 회복 효과는 없다.
        full = False
        n["heal"] = n["regen"] = n["regen_turns"] = 0
    elif full:
        n["heal"] = n["regen"] = n["regen_turns"] = 0
    elif n["regen"] > 0:
        n["heal"] = 0
        if n["regen_turns"] < 1:
            return None, "지속 회복에는 지속 턴(1 이상)이 필요해요."
    elif n["heal"] <= 0:
        return None, "회복량·전체 회복·지속 회복 중 하나는 있어야 해요."
    else:
        n["regen_turns"] = 0
    item = Consumable(key, name, emoji, tier, n["weight"], heal=n["heal"], full=full, regen=n["regen"], regen_turns=n["regen_turns"],
                      sell_price=n["sell_price"], modify=modify or None, conditions=conditions, material=material)
    return replace(item, description=describe_item(item)), None


def clean_content(content) -> dict[str, tuple]:
    """저장된 던전 콘텐츠에서 쓸 수 있는 구역만 꺼낸다. 구역이 있으면 기본 표 대신 그것을 쓴다. 잘못된 칸·중복 key는 버린다."""
    out: dict[str, tuple] = {}
    if not isinstance(content, dict):
        return out
    for section in SECTIONS:
        raw = content.get(section)
        if not isinstance(raw, list):
            continue
        items, keys = [], set()
        for data in raw[:SECTION_LIMITS[section]]:
            entity, problem = check_entity(section, data)
            if problem or entity.key in keys:
                continue
            keys.add(entity.key)
            items.append(entity)
        out[section] = tuple(items)
    return out


def floor_scaled(value: int, floor: int, percent: int) -> int:
    """floor층에서의 능력치. 1층은 그대로, 한 층 오를 때마다 percent%씩(1층 기준) 늘어난다(반올림)."""
    return (value * (100 + percent * (max(floor, 1) - 1)) + 50) // 100


def reduce_damage(damage: int, percent: int) -> int:
    """피해를 percent% 줄인다(반올림)."""
    return (damage * (100 - percent) + 50) // 100


# ---------------------------------------------------------------- 관리자가 바꿀 수 있는 항목
@dataclass(frozen=True)
class Param:
    key: str                     # 항목 이름. 관리자가 명령어에서 고르는 값이자 저장되는 이름
    group: str                   # 분류: 일반 / 방 배치 / 몬스터 / 보스 / 장비 / 아이템
    default: float
    low: float
    high: float
    integer: bool = True
    note: str = ""


GROUPS = ("일반", "방 배치", "몬스터", "보스", "장비", "아이템")
SCALAR_GROUPS = ("일반", "방 배치")           # /던전설정으로 바꾸는 분류. 몬스터·장비·소모품은 /몬스터 /장비 /소모품 명령으로 편집한다


def _build_params() -> dict[str, Param]:
    out: dict[str, Param] = {}

    def add(key, group, default, low, high, integer=True, note=""):
        assert key not in out, key
        out[key] = Param(key, group, default, low, high, integer, note)

    add("일반.최대체력", "일반", MAX_HP, 100, 100000, note="던전 시작 HP이자 회복 상한")
    add("일반.맨손공격력", "일반", BASE_DAMAGE, 1, 10000, note="무기가 없을 때의 공격 피해량(무기 공격력은 여기에 더해져요)")
    add("방어.성공확률", "일반", GUARD_CHANCE, 0, 100, note="방어 버튼을 눌렀을 때 가드에 성공할 확률(%)")
    add("방어.기본피해감소", "일반", GUARD_BASE, 0, 100, note="방패가 없을 때 가드 성공 시 피해 감소(%)")
    add("방어.회복률", "일반", GUARD_HEAL, 0, 500, note="가드 성공 시 그 공격 피해의 몇 %만큼 HP를 회복할지. 높으면 방어만 반복해 HP를 채울 수 있어요")
    add("방어.기본반사", "일반", REFLECT_BASE, 0, 500, note="방패가 없을 때 가드 성공 시 반사 피해(%)")
    add("일반.층수", "일반", FLOORS, 1, 100, note="올라가야 하는 층 수(한 층은 방 10개). 마지막 층의 보스를 이기면 클리어")
    add("일반.층당강화", "일반", FLOOR_SCALE, 0, 200, note="층이 하나 오를 때마다 몬스터·보스의 체력과 공격이 1층의 몇 %씩 늘어날지(0이면 그대로)")
    add("일반.층이동회복", "일반", FLOOR_HEAL, 0, 100, note="위 층으로 올라갈 때 잃은 HP의 몇 %를 회복할지")
    add("일반.시크릿확률", "일반", SECRET_CHANCE, 0, 100, integer=False, note="보스를 이기고 올라갈 때 11번째 방(시크릿 보스)이 생길 확률(%). 소수 가능, 0이면 안 생겨요")
    add("일반.부활체력", "일반", REVIVE_HEAL, 1, 100, note="부활권으로 되살아날 때 최대 HP의 몇 %로 일어날지")
    add("보상.최소", "일반", CLEAR_CANDY[0], 0, 99999, note="클리어 보상 재화 최소")
    add("보상.최대", "일반", CLEAR_CANDY[1], 0, 99999, note="클리어 보상 재화 최대")
    add("보상.획득권확률", "일반", TICKET_CHANCE, 0, 100, integer=False, note="클리어했을 때 히든 칭호 획득권이 나올 확률(%)")

    for room, table in enumerate(ROOM_PLAN, start=1):
        weights = dict(table)
        for kind, label in ROOM_KINDS:
            add(f"방{room}.{label}", "방 배치", weights.get(kind, 0), 0, 1000,
                note=f"{room}번째 방이 {label}일 가중치(비율). 한 방의 가중치가 모두 0이면 안 돼요")

    for m in MONSTERS:
        add(f"몬스터.{m.name}.체력", "몬스터", m.hp, 1, 100000)
        add(f"몬스터.{m.name}.공격", "몬스터", m.damage, 0, 100000, note="몬스터가 한 번 때리는 피해")
        add(f"몬스터.{m.name}.회피", "몬스터", m.dodge, 0, 95, note="내 공격을 피할 확률(%). 일부 무기는 이 값을 바꿔 버려요")
        add(f"몬스터.{m.name}.등장", "몬스터", m.weight, 0, WEIGHT_MAX, integer=False, note="잡몹 방에서 나올 가중치(비율, 소수 가능). 전부 0이면 안 돼요")
    for b in BOSSES:
        add(f"보스.{b.name}.체력", "보스", b.hp, 1, 100000)
        add(f"보스.{b.name}.공격", "보스", b.damage, 0, 100000, note="보스는 회피 불가라 회피 항목이 없어요")
        add(f"보스.{b.name}.등장", "보스", b.weight, 0, WEIGHT_MAX, integer=False, note="보스 방에서 나올 가중치(비율, 소수 가능). 전부 0이면 안 돼요")

    for g in GEAR:
        if g.slot == "weapon":
            add(f"장비.{g.name}.공격력", "장비", g.attack, 0, 100000)
            add(f"장비.{g.name}.크리티컬", "장비", g.crit, 0, 100, note="크리티컬(피해 2배) 확률(%)")
            if g.dodge is not None:
                add(f"장비.{g.name}.회피변환", "장비", g.dodge, 0, 95, note="잡몹의 회피 확률을 이 값으로 바꿔요(보스는 영향 없음)")
        else:
            add(f"장비.{g.name}.가드", "장비", g.guard, 0, 100, note="가드 성공 시 피해 감소(%)")
            add(f"장비.{g.name}.반사", "장비", g.reflect, 0, 500, note="가드 성공 시 반사 피해(%)")
        add(f"장비.{g.name}.등장", "장비", g.weight, 0, WEIGHT_MAX, integer=False, note="상자에서 나올 가중치(비율, 소수 가능). 전부 0이면 안 돼요")

    for c in CONSUMABLES:
        if c.regen:
            add(f"아이템.{c.name}.회복량", "아이템", c.regen, 0, 100000, note="턴마다 회복하는 양")
            add(f"아이템.{c.name}.지속턴", "아이템", c.regen_turns, 1, 1000)
        elif not c.full:
            add(f"아이템.{c.name}.회복량", "아이템", c.heal, 0, 100000)
        add(f"아이템.{c.name}.등장", "아이템", c.weight, 0, WEIGHT_MAX, integer=False, note="상자에서 나올 가중치(비율, 소수 가능). 전부 0이면 안 돼요")
    return out


PARAMS: dict[str, Param] = _build_params()


def clean(overrides) -> dict[str, float]:
    """저장된 사전에서 알 수 없는 항목·잘못된 값·범위를 벗어난 값을 뺀다(예전 버전 데이터가 섞여 있어도 안전하게)."""
    out: dict[str, float] = {}
    if not isinstance(overrides, dict):
        return out
    for key, value in overrides.items():
        param = PARAMS.get(key)
        if param is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value != value or value in (float("inf"), float("-inf")):
            continue
        if param.integer and value != int(value):
            continue
        if not param.low <= value <= param.high:
            continue
        out[key] = int(value) if param.integer else float(value)
    return out


def check_value(key: str, value) -> tuple[float | None, str | None]:
    """관리자가 입력한 값을 검사한다. (정리된 값, None) 또는 (None, 이유)."""
    param = PARAMS.get(key)
    if param is None:
        return None, "그런 항목이 없어요. 항목 이름은 자동완성에서 골라 주세요."
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        return None, "값은 숫자여야 해요."
    if param.integer and value != int(value):
        return None, f"**{key}**은(는) 정수만 넣을 수 있어요."
    if not param.low <= value <= param.high:
        return None, f"**{key}**은(는) {_fmt(param.low)}~{_fmt(param.high)} 사이여야 해요."
    return (int(value) if param.integer else float(value)), None


def _fmt(value) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:g}"


fmt = _fmt


@dataclass(frozen=True)
class Balance:
    """던전이 실제로 쓰는 값 한 벌. Balance.build({})는 기본값이다."""
    max_hp: int = MAX_HP
    floors: int = FLOORS
    floor_scale: int = FLOOR_SCALE
    floor_heal: int = FLOOR_HEAL
    revive_heal: int = REVIVE_HEAL
    secrets: tuple = SECRET_BOSSES   # 시크릿 보스들
    secret_chance: float = SECRET_CHANCE
    base_damage: int = BASE_DAMAGE
    guard_chance: int = GUARD_CHANCE
    guard_base: int = GUARD_BASE
    guard_heal: int = GUARD_HEAL
    reflect_base: int = REFLECT_BASE
    clear_candy: tuple = CLEAR_CANDY
    ticket_chance: float = TICKET_CHANCE
    room_plan: tuple = ROOM_PLAN
    monsters: tuple = MONSTERS
    bosses: tuple = BOSSES
    gear: tuple = GEAR
    consumables: tuple = CONSUMABLES
    mods: tuple = ()             # 장비 개조 레시피 ((무기 key, 재료 key, 결과 무기 key), ...). 만든 쪽에서 쓸 수 있는 것만 남긴다(build).
    gear_by_key: dict = field(init=False, repr=False, compare=False)
    item_by_key: dict = field(init=False, repr=False, compare=False)
    monster_by_key: dict = field(init=False, repr=False, compare=False)
    mod_by_pair: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "gear_by_key", {g.key: g for g in self.gear})
        object.__setattr__(self, "item_by_key", {c.key: c for c in self.consumables})
        object.__setattr__(self, "monster_by_key", {m.key: m for m in self.monsters + self.bosses + self.secrets})
        object.__setattr__(self, "mod_by_pair", {(w, i): r for w, i, r in self.mods})

    def mod_result(self, weapon_key: str | None, item_key: str) -> str | None:
        """이 장비(무기나 방패. 안 든 칸이면 None)를 든 채 이 재료를 쓰면 바뀌는 장비의 key. 레시피가 없으면 None."""
        return self.mod_by_pair.get((weapon_key, item_key))

    @classmethod
    def build(cls, overrides=None, content=None, mods=None) -> "Balance":
        """기본값 위에 숫자 덮어쓰기(overrides)를 얹고, 관리자가 만든 콘텐츠(content)가 있는 구역은 그것으로 통째로 바꾼다.
        mods는 장비 개조 레시피 [{weapon, item, result}, ...]: 무기·재료·결과가 지금 실제로 있는 것만 남긴다."""
        o = clean(overrides)
        custom = clean_content(content)

        def get(key):
            return o.get(key, PARAMS[key].default)

        plan = tuple(tuple((kind, get(f"방{room}.{label}")) for kind, label in ROOM_KINDS if get(f"방{room}.{label}") > 0)
                     for room in range(1, ROOMS + 1))
        gear = []
        for g in GEAR:
            if g.slot == "weapon":
                g = replace(g, attack=get(f"장비.{g.name}.공격력"), crit=get(f"장비.{g.name}.크리티컬"),
                            dodge=get(f"장비.{g.name}.회피변환") if g.dodge is not None else None,
                            weight=get(f"장비.{g.name}.등장"))
            else:
                g = replace(g, guard=get(f"장비.{g.name}.가드"), reflect=get(f"장비.{g.name}.반사"),
                            weight=get(f"장비.{g.name}.등장"))
            gear.append(replace(g, description=describe_gear(g)))
        items = []
        for c in CONSUMABLES:
            if c.regen:
                c = replace(c, regen=get(f"아이템.{c.name}.회복량"), regen_turns=get(f"아이템.{c.name}.지속턴"),
                            weight=get(f"아이템.{c.name}.등장"))
            elif c.full:
                c = replace(c, weight=get(f"아이템.{c.name}.등장"))
            else:
                c = replace(c, heal=get(f"아이템.{c.name}.회복량"), weight=get(f"아이템.{c.name}.등장"))
            items.append(replace(c, description=describe_item(c)))
        monsters = tuple(replace(m, hp=get(f"몬스터.{m.name}.체력"), damage=get(f"몬스터.{m.name}.공격"),
                                 dodge=get(f"몬스터.{m.name}.회피"), weight=get(f"몬스터.{m.name}.등장")) for m in MONSTERS)
        bosses = tuple(replace(b, hp=get(f"보스.{b.name}.체력"), damage=get(f"보스.{b.name}.공격"), weight=get(f"보스.{b.name}.등장"))
                       for b in BOSSES)
        final_gear, final_items = custom.get("gear", tuple(gear)), custom.get("consumables", tuple(items))
        return cls(
            max_hp=get("일반.최대체력"), floors=get("일반.층수"), floor_scale=get("일반.층당강화"), floor_heal=get("일반.층이동회복"), revive_heal=get("일반.부활체력"), secret_chance=get("일반.시크릿확률"), secrets=custom.get("secrets", SECRET_BOSSES),
            base_damage=get("일반.맨손공격력"), guard_chance=get("방어.성공확률"),
            guard_base=get("방어.기본피해감소"), guard_heal=get("방어.회복률"), reflect_base=get("방어.기본반사"),
            clear_candy=(get("보상.최소"), get("보상.최대")), ticket_chance=get("보상.획득권확률"), room_plan=plan,
            monsters=custom.get("monsters", monsters), bosses=custom.get("bosses", bosses),
            gear=final_gear, consumables=final_items, mods=clean_mods(mods, final_gear, final_items))


MAX_MODS = 50                    # 서버마다 장비 개조 레시피 수 한도


def clean_mods(raw, gear, items) -> tuple:
    """저장된 개조 레시피에서 쓸 수 있는 것만 ((장비, 재료, 결과), ...)로 꺼낸다. 장비·결과는 실제 있는 장비여야 하고, 서로 달라야 하며,
    **같은 종류**(무기는 무기로, 방패는 방패로)여야 한다. 재료는 '개조 재료' 소모품이어야 한다. 같은 (장비, 재료)가 겹치면 앞의 것만 쓴다."""
    if not isinstance(raw, (list, tuple)):
        return ()
    slot_of = {g.key: g.slot for g in gear}
    materials = {c.key for c in items if c.material}
    out, seen = [], set()
    for entry in raw[:MAX_MODS]:
        if not isinstance(entry, dict):
            continue
        weapon, item, result = entry.get("weapon"), entry.get("item"), entry.get("result")
        if weapon in slot_of and result in slot_of and slot_of[weapon] == slot_of[result] and weapon != result \
                and item in materials and (weapon, item) not in seen:
            seen.add((weapon, item))
            out.append((weapon, item, result))
    return tuple(out)


DEFAULT = Balance.build({})


def validate(overrides: dict, content=None) -> str | None:
    """값 하나하나와, 값들을 합쳤을 때 게임이 깨지지 않는지(한 방의 가중치가 전부 0, 나올 몬스터가 없음 등)를 검사한다. 문제가 있으면 이유를 돌려준다."""
    for key, value in overrides.items():
        _, problem = check_value(key, value)
        if problem:
            return problem
    bal = Balance.build(overrides, content)
    if bal.clear_candy[0] > bal.clear_candy[1]:
        return "클리어 보상의 최소가 최대보다 클 수 없어요."
    for room, table in enumerate(bal.room_plan, start=1):
        if not table:
            return f"{room}번째 방의 상자·빈방·잡몹·보스 가중치가 모두 0이에요. 하나는 0보다 커야 해요."
    kinds = {kind for table in bal.room_plan for kind, _ in table}
    if "monster" in kinds and not bal.monsters:
        return "잡몹이 나오는 방이 있는데 잡몹이 하나도 없어요. 몬스터를 추가하거나 방 배치에서 잡몹 가중치를 0으로 해 주세요."
    if "monster" in kinds and sum(m.weight for m in bal.monsters) <= 0:
        return "잡몹이 나오는 방이 있는데 몬스터의 '등장' 가중치가 전부 0이에요. 하나는 0보다 커야 해요."
    if "boss" in kinds and not bal.bosses:
        return "보스가 나오는 방이 있는데 보스가 하나도 없어요. 보스를 추가하거나 방 배치를 바꿔 주세요."
    if "boss" in kinds and sum(b.weight for b in bal.bosses) <= 0:
        return "보스가 나오는 방이 있는데 보스의 '등장' 가중치가 전부 0이에요. 하나는 0보다 커야 해요."
    if sum(g.weight for g in bal.gear) <= 0:
        return "상자에서 나오는 장비의 '등장' 가중치가 전부 0이에요. 하나는 0보다 커야 해요."
    if sum(c.weight for c in bal.consumables) <= 0:
        return "상자에서 나오는 아이템의 '등장' 가중치가 전부 0이에요. 하나는 0보다 커야 해요."
    return None


def warnings(bal: Balance) -> list[str]:
    """값 자체는 허용되지만 게임을 깨뜨릴 수 있는 조합을 알려 준다."""
    out = []
    shields = [("방패 없음", bal.guard_base, bal.guard_heal, bal.guard_chance)] + [
        (g.name, g.guard, bal.guard_heal, bal.guard_chance if g.guard_chance is None else g.guard_chance) for g in bal.gear if g.slot == "shield"]
    farmable = []
    for monster in bal.monsters + bal.bosses:
        for name, guard, heal_rate, percent in shields:
            chance = percent / 100
            heal = monster.damage * heal_rate // 100
            expected = chance * (heal - reduce_damage(monster.damage, guard)) - (1 - chance) * monster.damage
            if expected >= 0 and monster.damage > 0:
                farmable.append(f"{monster.name}/{name}")
    if farmable:
        out.append("⚠️ 방어를 반복하면 HP가 줄지 않거나 늘어나는 조합이 있어요(" + ", ".join(farmable[:4])
                   + (" 외" if len(farmable) > 4 else "") + "). '방어.회복률'이나 '방어.성공확률'을 낮추는 걸 고려해 보세요.")
    if bal.guard_chance == 0:
        out.append("⚠️ 방어 성공 확률이 0%라 방어가 쓸모없어요.")
    zero = [g.name for g in bal.gear if g.slot == "shield" and g.guard_chance == 0]
    if zero:
        out.append("⚠️ 가드 성공률이 0%인 방패가 있어요(" + ", ".join(zero[:4]) + "). 끼면 방어가 쓸모없어요.")
    return out
