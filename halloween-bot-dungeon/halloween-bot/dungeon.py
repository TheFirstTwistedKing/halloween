"""던전 규칙: 방 구성, 턴제 전투, 장비·아이템·몬스터 표. 디스코드에 접속하지 않는 순수 로직이다.

확률은 모두 퍼센트(정수)이고, 난수 생성기를 받아서 시험하기 쉽다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

MAX_HP = 1000
BASE_DAMAGE = 20
ROOMS = 10                       # 방이 10번 나오면 끝
BASE_DODGE = 20                  # 공격 성공 80% / 몬스터 회피 20%
GUARD_BASE = 60                  # 가드 성공 시 피해 60% 감소
ROOM_TABLE = (("monster", 30), ("empty", 30), ("chest", 20), ("boss", 20))
TIER_ORDER = {"일반": 0, "희귀": 1, "전설": 2}
GUARD_CHANCE = 60                # 방어 버튼을 눌렀을 때 가드에 성공할 확률(%)
CLEAR_CANDY = (50, 150)          # 클리어 보상 재화(최소, 최대)
TICKET_CHANCE = 10               # 클리어했을 때 히든 칭호 획득권이 나올 확률(%)


class DungeonError(Exception):
    """사용자에게 그대로 보여줘도 되는 규칙 위반 문장."""


@dataclass(frozen=True)
class Gear:
    key: str
    name: str
    emoji: str
    slot: str                    # weapon | shield
    tier: str
    weight: int                  # 상자에서 나올 확률(%). 좋은 장비일수록 낮다.
    attack: int = 0              # 공격 피해량 추가
    crit: int = 0                # 크리티컬 확률(%). 크리티컬은 피해 2배
    dodge: int | None = None     # 대상의 회피 확률을 이 값으로 바꾼다
    guard: int | None = None     # 가드 피해 감소(%)
    description: str = ""


@dataclass(frozen=True)
class Consumable:
    key: str
    name: str
    emoji: str
    tier: str
    weight: int
    heal: int = 0
    full: bool = False           # HP를 전부 회복
    regen: int = 0               # 턴이 지날 때마다 회복하는 양
    regen_turns: int = 0
    description: str = ""


@dataclass(frozen=True)
class Monster:
    key: str
    name: str
    emoji: str
    hp: int
    damage: int
    dodge: int = BASE_DODGE
    boss: bool = False


# 장비 — 위의 4개는 요청한 것, 나머지는 추가한 것. 좋은 장비일수록 확률이 낮다(합계 100).
GEAR = (
    Gear("club", "낡은 몽둥이", "🏏", "weapon", "일반", 25, attack=10, description="공격 피해량 +10"),
    Gear("wooden_shield", "나무 방패", "🪵", "shield", "일반", 20, guard=75, description="가드 피해 감소 75%"),
    Gear("dagger", "단검", "🗡️", "weapon", "일반", 18, attack=20, crit=50, description="공격 피해량 +20, 크리티컬(2배) 확률 50%"),
    Gear("longsword", "롱소드", "⚔️", "weapon", "희귀", 12, attack=100, description="공격 피해량 +100"),
    Gear("shield", "방패", "🛡️", "shield", "희귀", 10, guard=90, description="가드 피해 감소 90%"),
    Gear("crossbow", "석궁", "🏹", "weapon", "희귀", 8, attack=180, dodge=35, description="공격 피해량 +180, 대상 회피 확률 35%로 변환"),
    Gear("gun", "총", "🔫", "weapon", "전설", 4, attack=300, dodge=50, description="공격 피해량 +300, 대상 회피 확률 50%로 변환"),
    Gear("pumpkin_blade", "호박 대검", "🎃", "weapon", "전설", 3, attack=200, crit=25, description="공격 피해량 +200, 크리티컬(2배) 확률 25%"),
)
# 아이템 — 호박죽·사탕·영혼 에센스는 요청한 것, 나머지는 추가한 것(합계 100).
CONSUMABLES = (
    Consumable("candy", "사탕", "🍭", "일반", 38, heal=50, description="HP +50"),
    Consumable("cookie", "달콤한 쿠키", "🍪", "일반", 24, heal=100, description="HP +100"),
    Consumable("pumpkin_soup", "호박죽", "🍲", "희귀", 18, heal=200, description="HP +200"),
    Consumable("soul_essence", "영혼 에센스", "👻", "희귀", 10, regen=70, regen_turns=10,
               description="10턴 동안 턴이 지날 때마다 HP +70"),
    Consumable("witch_elixir", "마녀의 비약", "🧪", "희귀", 7, heal=400, description="HP +400"),
    Consumable("golden_soup", "황금 호박죽", "🥣", "전설", 3, full=True, description="HP를 전부 회복"),
)
MONSTERS = (
    Monster("giant_bat", "거대 박쥐", "🦇", 250, 75),
    Monster("slime", "슬라임", "🟢", 300, 50),
    Monster("witch", "마녀", "🧙", 150, 150),
    Monster("giant_rat", "거대 쥐", "🐀", 100, 50, dodge=75),       # 특수: 회피 확률이 75%로 변환
)
BOSSES = (
    Monster("skeleton_king", "해골 왕", "💀", 1000, 150, boss=True),
    Monster("giant_slime", "거대 슬라임", "🟩", 1250, 100, boss=True),
    Monster("goblin_king", "고블린 왕", "👺", 900, 200, boss=True),
)
GEAR_BY_KEY = {g.key: g for g in GEAR}
ITEM_BY_KEY = {c.key: c for c in CONSUMABLES}
MONSTER_BY_KEY = {m.key: m for m in MONSTERS + BOSSES}
for _table in (GEAR, CONSUMABLES):
    assert sum(entry.weight for entry in _table) == 100


def pick_weighted(rng, table):
    """weight(%)대로 하나 고른다."""
    draw, edge = rng.random() * 100, 0
    for entry in table:
        edge += entry.weight
        if draw < edge:
            return entry
    return table[-1]


def reduce_damage(damage: int, percent: int) -> int:
    """피해를 percent% 줄인다(반올림)."""
    return (damage * (100 - percent) + 50) // 100


@dataclass
class Run:
    """진행 중인 던전 하나. JSON으로 저장·복원된다."""
    hp: int = MAX_HP
    room: int = 0
    phase: str = "cleared"        # battle | guard | empty | chest | chest_done | cleared | dead | clear | quit
    kind: str = ""                # monster | boss | empty | chest
    enemy: dict | None = None
    weapon: str | None = None
    shield: str | None = None
    regen_amount: int = 0
    regen_turns: int = 0
    events: list = field(default_factory=list)      # 방금 있었던 일(화면에 보여준다)
    loot: list = field(default_factory=list)        # 이번 던전에서 얻은 물품 이름
    rev: int = 0                                    # 화면 번호. 행동마다 올라가서 지난 화면의 버튼을 가려낸다.
    items: dict = field(default_factory=dict)       # 가지고 있는 소모품 {key: 개수}
    pending_gear: str | None = None                 # 상자에서 나와 아직 장착/버림을 정하지 않은 장비

    def bump(self) -> None:
        self.rev += 1

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Run":
        data = json.loads(text)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def gear_weapon(self) -> Gear | None:
        return GEAR_BY_KEY.get(self.weapon)

    @property
    def gear_shield(self) -> Gear | None:
        return GEAR_BY_KEY.get(self.shield)

    @property
    def in_battle(self) -> bool:
        return self.phase in ("battle", "guard")

    @property
    def finished(self) -> bool:
        return self.phase in ("dead", "clear", "quit")

    @property
    def can_advance(self) -> bool:
        return self.phase in ("empty", "chest_done", "cleared") and not self.pending_gear

    @property
    def gear_pending(self) -> Gear | None:
        return GEAR_BY_KEY.get(self.pending_gear)


def new_run(weapon: str | None = None, shield: str | None = None) -> Run:
    return Run(weapon=weapon if weapon in GEAR_BY_KEY and GEAR_BY_KEY[weapon].slot == "weapon" else None,
               shield=shield if shield in GEAR_BY_KEY and GEAR_BY_KEY[shield].slot == "shield" else None)


def player_damage(run: Run) -> int:
    weapon = run.gear_weapon
    return BASE_DAMAGE + (weapon.attack if weapon else 0)


def guard_percent(run: Run) -> int:
    shield = run.gear_shield
    return shield.guard if shield and shield.guard is not None else GUARD_BASE


def enemy_dodge(run: Run) -> int:
    """몬스터의 회피 확률(%). 무기가 '변환'을 가지면 몬스터 자신의 값(거대 쥐 75% 포함)보다 우선한다."""
    weapon = run.gear_weapon
    if weapon and weapon.dodge is not None:
        return weapon.dodge
    return run.enemy["dodge"]


def hit_chance(run: Run) -> int:
    return 100 - enemy_dodge(run)


def roll_room(rng) -> str:
    """몬스터 30% · 빈 방 30% · 보물 상자 20% · 미니 보스 20%."""
    draw, edge = rng.random() * 100, 0
    for kind, percent in ROOM_TABLE:
        edge += percent
        if draw < edge:
            return kind
    return ROOM_TABLE[-1][0]


def _spawn(monster: Monster) -> dict:
    return {"key": monster.key, "name": monster.name, "emoji": monster.emoji, "hp": monster.hp, "max_hp": monster.hp,
            "damage": monster.damage, "dodge": monster.dodge, "boss": monster.boss}


def advance(run: Run, rng) -> list[str]:
    """다음 방으로 간다. 마지막 방(10번째)을 마쳤다면 던전이 끝난다."""
    if run.room >= ROOMS:
        run.phase, run.enemy, run.kind = "clear", None, ""
        run.events = ["🏆 던전의 끝에 도착했어요!"]
        return run.events
    run.room += 1
    run.kind, run.enemy = roll_room(rng), None
    if run.kind in ("monster", "boss"):
        table = BOSSES if run.kind == "boss" else MONSTERS
        monster = table[rng.randint(0, len(table) - 1)]
        run.enemy, run.phase = _spawn(monster), "battle"
        text = (f"👑 미니 보스의 방! **{monster.name}**이(가) 나타났어요!" if monster.boss
                else f"⚠️ 몬스터 조우! **{monster.name}**이(가) 나타났어요!")
    elif run.kind == "chest":
        run.phase, text = "chest", "🎁 보물 상자가 놓여 있는 방이에요."
    else:
        run.phase, text = "empty", "🚪 텅 빈 방이에요. 앞으로 나아갈 수 있어요."
    run.events = [text]
    return run.events


def _enemy_attack(run: Run, reduction: int) -> list[str]:
    damage = reduce_damage(run.enemy["damage"], reduction)
    run.hp = max(0, run.hp - damage)
    note = f" (가드로 {reduction}% 줄였어요)" if reduction else ""
    events = [f"{run.enemy['emoji']} {run.enemy['name']}의 공격! **{damage}** 피해를 입었어요.{note}"]
    if run.hp <= 0:
        run.phase = "dead"
        events.append("💀 쓰러졌어요…")
    return events


def _end_turn(run: Run) -> list[str]:
    """한 턴이 끝날 때: 영혼 에센스처럼 턴마다 회복하는 효과."""
    if run.phase == "dead" or run.regen_turns <= 0:
        return []
    before = run.hp
    run.hp = min(MAX_HP, run.hp + run.regen_amount)
    run.regen_turns -= 1
    if run.hp == before:
        return []
    return [f"👻 영혼 에센스: HP +{run.hp - before} (남은 {run.regen_turns}턴)"]


def _after_player_action(run: Run, reduction: int) -> list[str]:
    """플레이어 행동 뒤: 적이 살아 있으면 적이 공격하고, 턴이 끝난다."""
    events = []
    if run.enemy["hp"] <= 0:
        events.append(f"🎉 **{run.enemy['name']}**을(를) 쓰러뜨렸어요!")
        run.phase = "cleared"
    else:
        events += _enemy_attack(run, reduction)
    return events + _end_turn(run)


def player_attack(run: Run, rng) -> list[str]:
    """공격: 몬스터가 회피하지 못하면(기본 80%) 피해를 준다. 단검 같은 무기는 크리티컬(2배)이 터질 수 있다."""
    weapon = run.gear_weapon
    if rng.random() * 100 < enemy_dodge(run):
        events = [f"💨 {run.enemy['name']}이(가) 공격을 피했어요!"]
    else:
        damage, crit = player_damage(run), False
        if weapon and weapon.crit and rng.random() * 100 < weapon.crit:
            damage, crit = damage * 2, True
        run.enemy["hp"] = max(0, run.enemy["hp"] - damage)
        events = [f"{'💥 크리티컬! ' if crit else '⚔️ '}{run.enemy['name']}에게 **{damage}** 피해를 줬어요."]
    events += _after_player_action(run, 0)
    run.events = events
    return events


def player_defend(run: Run, guard_ok: bool) -> list[str]:
    """방어: 가드에 성공하면 몬스터의 피해를 줄인다(기본 60%, 방패는 더 많이). 실패하면 그대로 맞는다."""
    events = [("🛡️ 가드 성공!" if guard_ok else "❌ 가드 실패…")]
    events += _enemy_attack(run, guard_percent(run) if guard_ok else 0)
    events += _end_turn(run)
    run.events = events
    return events


def use_consumable(run: Run, key: str) -> list[str]:
    """아이템 사용. 전투 중이면 한 턴을 쓴 것으로 치고 몬스터가 공격한다."""
    item = ITEM_BY_KEY.get(key)
    if item is None:
        raise DungeonError("알 수 없는 아이템이에요.")
    if run.items.get(key, 0) < 1:
        raise DungeonError(f"{item.name}이(가) 없어요.")
    run.items[key] -= 1
    if run.items[key] <= 0:
        del run.items[key]
    if item.full:
        gained, run.hp = MAX_HP - run.hp, MAX_HP
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요. HP가 전부 회복됐어요! (+{gained})"]
    elif item.heal:
        gained = min(MAX_HP, run.hp + item.heal) - run.hp
        run.hp += gained
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요. HP +{gained}"]
    else:
        run.regen_amount, run.regen_turns = item.regen, item.regen_turns
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요. {item.regen_turns}턴 동안 턴마다 HP +{item.regen}!"]
    if run.in_battle:
        events += _enemy_attack(run, 0)
        events += _end_turn(run)
    run.events = events
    return events


def open_chest(run: Run, rng) -> tuple[Gear, Consumable, list[str]]:
    """보물 상자: 장비 한 개와 아이템 한 개. 좋은 것일수록 확률이 낮다."""
    gear, item = pick_weighted(rng, GEAR), pick_weighted(rng, CONSUMABLES)
    run.phase = "chest_done"
    run.pending_gear = gear.key                      # 장착할지 버릴지 정할 때까지 앞으로 갈 수 없다.
    run.items[item.key] = run.items.get(item.key, 0) + 1
    run.loot += [gear.name, item.name]
    held = run.gear_shield if gear.slot == "shield" else run.gear_weapon
    swap = f"\n┕ 지금 쓰는 **{held.name}**과(와) 바뀌어요." if held else ""
    run.events = ["🎁 상자를 열었어요!", f"{gear.emoji} **{gear.name}** ({gear.tier}) — {gear.description}{swap}",
                  f"{item.emoji} **{item.name}** ({item.tier}) — {item.description} (소지품에 넣었어요)"]
    return gear, item, run.events


def equip_pending(run: Run) -> list[str]:
    """상자에서 나온 장비를 장착한다. 같은 칸에 있던 장비는 사라진다."""
    gear = run.gear_pending
    if gear is None:
        raise DungeonError("장착할 장비가 없어요.")
    if gear.slot == "shield":
        run.shield = gear.key
    else:
        run.weapon = gear.key
    run.pending_gear = None
    run.events = [f"{gear.emoji} **{gear.name}**을(를) 장착했어요!"]
    return run.events


def discard_pending(run: Run) -> list[str]:
    gear = run.gear_pending
    if gear is None:
        raise DungeonError("버릴 장비가 없어요.")
    run.pending_gear = None
    run.events = [f"🗑️ {gear.emoji} **{gear.name}**을(를) 버렸어요."]
    return run.events


def roll_guard(rng) -> bool:
    """방어 버튼의 가드 성공 여부."""
    return rng.random() * 100 < GUARD_CHANCE


def roll_clear_reward(rng) -> tuple[int, bool]:
    """클리어 보상: (재화, 히든 칭호 획득권이 나왔는지)."""
    candy = rng.randint(*CLEAR_CANDY)
    return candy, rng.random() * 100 < TICKET_CHANCE
