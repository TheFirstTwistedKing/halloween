"""던전 규칙: 방 구성, 턴제 전투, 장비·아이템·몬스터. 디스코드에 접속하지 않는 순수 로직이다.

확률은 모두 퍼센트이고, 난수 생성기를 받아서 시험하기 쉽다. 수치(기본값과 관리자가 바꿀 수 있는 값)는 balance.py에 있고,
이 파일의 함수들은 `bal`(Balance)을 받아 그 값을 쓴다. 생략하면 기본값이다.
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field

from balance import (BASE_DAMAGE, BASE_DODGE, BOSSES, CLEAR_CANDY, CONSUMABLES, DEFAULT, FLOOR_HEAL, FLOOR_SCALE, FLOORS,  # noqa: F401  (다시 내보냄)
                     GEAR, GUARD_BASE, GUARD_CHANCE, GUARD_HEAL, MAX_HP, MONSTERS, REFLECT_BASE, ROOM_PLAN, ROOMS,
                     TICKET_CHANCE, TIER_ORDER, Balance, Consumable, EVENT_TAG, Gear, Monster, SecretBoss, condition_text, candidates_for_floor, floor_scaled,
                     monsters_for_floor, reduce_damage, shop_price, GEAR_SLOTS, SLOT_NAMES, SHOP_SLOTS)
from josa import eul_reul, i_ga

BARE = {"weapon": "맨손", "shield": "맨몸 가드", "armor": "맨몸"}       # 그 칸이 비었을 때 부르는 말
GEAR_BY_KEY = {g.key: g for g in GEAR}          # 기본값 표(이름·이모지 조회용). 실제 수치는 bal에서 읽는다.
ITEM_BY_KEY = {c.key: c for c in CONSUMABLES}
MONSTER_BY_KEY = {m.key: m for m in MONSTERS + BOSSES}


class DungeonError(Exception):
    """사용자에게 그대로 보여줘도 되는 규칙 위반 문장."""


def pick_weighted(rng, table):
    """weight(비율)대로 하나 고른다. 가중치의 합이 100일 필요는 없다."""
    total = sum(entry.weight for entry in table)
    draw, edge = rng.random() * total, 0
    for entry in table:
        edge += entry.weight
        if draw < edge:
            return entry
    return next(entry for entry in reversed(table) if entry.weight > 0)


@dataclass
class Run:
    """진행 중인 던전 하나. JSON으로 저장·복원된다."""
    hp: int = MAX_HP
    floor: int = 1                # 지금 층(1부터). 예전에 저장된 던전에는 없어서 1층으로 읽는다.
    room: int = 0                 # 이 층에서 몇 번째 방인지(1~ROOMS)
    phase: str = "cleared"        # battle | guard | empty | chest | chest_done | cleared | dead | clear | quit
    kind: str = ""                # monster | boss | empty | chest
    enemy: dict | None = None
    weapon: str | None = None
    shield: str | None = None
    spare_weapon: str | None = None                 # 예비 칸(이번 던전 동안만): 장착하지 않고 가지고 있는 무기 1개
    spare_shield: str | None = None                 # 예비 방패 1개. 예비 칸은 무기 1·방패 1·갑옷 1이 전부이고 던전이 끝나면 사라진다.
    armor: str | None = None                        # 입고 있는 갑옷(받는 피해를 줄인다)
    spare_armor: str | None = None                  # 예비 갑옷 1개
    regen_amount: int = 0
    regen_turns: int = 0
    drain_amount: int = 0                           # 턴마다 내 HP가 줄어드는 양(자해 소모품)과 남은 턴. HP는 1 밑으로 내려가지 않는다.
    drain_turns: int = 0
    invuln_turns: int = 0                           # 남은 무적 턴: 이 동안 몬스터의 공격 피해를 받지 않는다.
    reduce_percent: int = 0                         # 받는 피해를 줄이는 %와 남은 턴(소모품 효과)
    reduce_turns: int = 0
    events: list = field(default_factory=list)      # 방금 있었던 일(화면에 보여준다)
    loot: list = field(default_factory=list)        # 이번 던전에서 얻은 물품 이름
    rev: int = 0                                    # 화면 번호. 행동마다 올라가서 지난 화면의 버튼을 가려낸다.
    items: dict = field(default_factory=dict)       # 가지고 있는 소모품 {key: 개수}
    pending_gear: str | None = None                 # 상자에서 나와 아직 장착/버림을 정하지 않은 장비
    auto: bool = False                              # 빠른진행 중(컴퓨터가 알아서 진행). 켜 둔 채 봇이 꺼지면 다음에 /던전을 열 때 꺼진다.
    auto_paid: bool = False                         # 이 던전에서 빠른진행 비용(입장 물품 한 번 더)을 이미 냈다: 껐다 켜도 다시 내지 않는다.
    infinite: bool = False                          # 무한모드 중: 마지막 층을 깬 뒤 클리어하지 않고 계속 올라가고 있다.
    bonus: int = 0                                  # 방금 올라간 무한 층의 보너스 재화(서비스가 지급하고 0으로 되돌린다)
    shop: list = field(default_factory=list)        # 지금 상점의 상품 [{kind: gear|item, key, price, sold}]
    shop_floor: int = 0                             # 마지막으로 상점이 열린 층(같은 층에서 또 열리지 않게)
    room_candy: int = 0                             # 방금 들어선 커스텀 방이 준 재화(서비스가 지급하고 0으로 되돌린다)
    last: list = field(default_factory=list)        # 바로 직전 행동에서 일어난 일(소모품 사용 조건에 쓴다): guard_success·guard_fail·attack_hit·attack_miss·item_gain:키·item_use:키

    def bump(self) -> None:
        self.rev += 1

    @property
    def stage(self) -> int:
        """처음부터 세어 몇 번째 방인지(시뮬레이터 통계용). 1층 3번째 방이면 3, 2층 1번째 방이면 ROOMS + 1."""
        return (max(self.floor, 1) - 1) * ROOMS + min(self.room, ROOMS)         # 11번째(시크릿) 방은 그 층의 마지막 방으로 센다

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Run":
        data = json.loads(text)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def gear_weapon(self) -> Gear | None:           # 이름·이모지 용도(수치는 bal.gear_by_key에서)
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


def new_run(weapon: str | None = None, shield: str | None = None, bal: Balance | None = None) -> Run:
    bal = bal or DEFAULT
    return Run(hp=bal.max_hp,
               weapon=weapon if weapon in bal.gear_by_key and bal.gear_by_key[weapon].slot == "weapon" else None,
               shield=shield if shield in bal.gear_by_key and bal.gear_by_key[shield].slot == "shield" else None)


def _weapon(run: Run, bal: Balance) -> Gear | None:
    return bal.gear_by_key.get(run.weapon)


def _shield(run: Run, bal: Balance) -> Gear | None:
    return bal.gear_by_key.get(run.shield)


def _armor(run: Run, bal: Balance) -> Gear | None:
    gear = bal.gear_by_key.get(run.armor)
    return gear if gear is not None and gear.slot == "armor" else None


def armor_percent(run: Run, bal: Balance | None = None) -> int:
    """입고 있는 갑옷이 줄여 주는 피해(%). 갑옷이 없으면 0."""
    armor = _armor(run, bal or DEFAULT)
    return armor.reduction if armor else 0


def player_damage(run: Run, bal: Balance | None = None) -> int:
    bal = bal or DEFAULT
    weapon = _weapon(run, bal)
    return bal.base_damage + (weapon.attack if weapon else 0)


def guard_percent(run: Run, bal: Balance | None = None) -> int:
    bal = bal or DEFAULT
    shield = _shield(run, bal)
    return shield.guard if shield and shield.guard is not None else bal.guard_base


def reflect_percent(run: Run, bal: Balance | None = None) -> int:
    bal = bal or DEFAULT
    shield = _shield(run, bal)
    return shield.reflect if shield and shield.reflect is not None else bal.reflect_base


def enemy_dodge(run: Run, bal: Balance | None = None) -> int:
    """몬스터의 회피 확률(%). 보스는 항상 0(회피 불가). 잡몹은 무기가 '변환'을 가지면 몬스터 자신의 값(거대 쥐 포함)보다 우선한다."""
    bal = bal or DEFAULT
    if run.enemy.get("boss"):
        return 0
    weapon = _weapon(run, bal)
    if weapon and weapon.dodge is not None:
        return weapon.dodge
    return run.enemy["dodge"]


def hit_chance(run: Run, bal: Balance | None = None) -> int:
    return 100 - enemy_dodge(run, bal)


def custom_rooms_for(bal: Balance, room: int, floor: int) -> list:
    """이 방 번호·층에 나올 수 있는 커스텀 방들(가중치가 0보다 큰 것). 보스 방(마지막 방)에는 나오지 않는다."""
    if room >= ROOMS:
        return []
    return [c for c in bal.rooms if c.weight > 0 and (not c.rooms or room in c.rooms) and (not c.floors or floor in c.floors)]


def roll_room(rng, room: int, bal: Balance | None = None, floor: int = 1) -> str:
    """room번째 방의 종류. 방 배치의 칸에 이 방·층에 나올 수 있는 커스텀 방(종류는 'custom:키')을 더해서,
    칸이 하나뿐이면 그대로, 여러 칸이면 가중치대로 고른다."""
    bal = bal or DEFAULT
    table = bal.room_plan[min(max(room, 1), len(bal.room_plan)) - 1]
    extra = [(f"custom:{c.key}", c.weight) for c in custom_rooms_for(bal, room, floor)]
    if extra:
        table = (*table, *extra)
    if len(table) == 1:
        return table[0][0]
    total = sum(weight for _, weight in table)
    draw, edge = rng.random() * total, 0
    for kind, weight in table:
        edge += weight
        if draw < edge:
            return kind
    return table[-1][0]


def _spawn(monster: Monster, floor: int = 1, bal: Balance | None = None) -> dict:
    """몬스터를 floor층에 맞게 만든다. 체력과 공격은 층마다 floor_scale%씩 늘어난다(회피는 그대로)."""
    percent = (bal or DEFAULT).floor_scale
    hp = max(1, floor_scaled(monster.hp, floor, percent))
    enemy = {"key": monster.key, "name": monster.name, "emoji": monster.emoji, "hp": hp, "max_hp": hp,
             "damage": floor_scaled(monster.damage, floor, percent), "dodge": monster.dodge, "boss": monster.boss, "stunned": False}
    if isinstance(monster, SecretBoss):                  # 시크릿 보스: 드롭·연출 정보를 같이 담는다(싸우는 도중 설정이 바뀌어도 이 싸움은 그대로).
        enemy.update(secret=True, intro=monster.intro, image=monster.image,
                     drops={"gear": monster.drop_gear, "gear_chance": monster.drop_gear_chance, "item": monster.drop_item,
                            "item_chance": monster.drop_item_chance, "amount": monster.drop_item_amount, "nothing": monster.nothing_chance})
    return enemy


def advance(run: Run, rng, bal: Balance | None = None) -> list[str]:
    """다음 방으로 간다. 한 층의 마지막 방(10번째, 보스)을 마쳤다면 위 층의 1번째 방으로 올라가고(잃은 HP를 일부 회복),
    마지막 층이라면 던전이 끝난다."""
    bal = bal or DEFAULT
    climb: list[str] = []
    eligible = [s for s in bal.secrets if s.weight > 0 and (not s.floors or run.floor in s.floors)]    # 이 층에 나올 수 있는 시크릿 보스
    if run.room == ROOMS and run.kind == "boss" and bal.secret_chance > 0 and eligible \
            and rng.random() * 100 < bal.secret_chance:
        boss = pick_weighted(rng, eligible)            # 보스를 이기고 올라가려는 순간 10%(기본) 확률로 11번째 방이 열린다.
        run.room, run.kind = ROOMS + 1, "secret"
        run.enemy, run.phase = _spawn(boss, run.floor, bal), "battle"
        run.last.append("secret_intro")                   # 화면이 특별한 등장 연출을 보여 주라는 표시
        run.events = [f"🌌 **시크릿 방**이 열렸어요! **{boss.name}**이(가) 가동됐어요!"]
        return run.events
    if run.room >= ROOMS:
        if shop_due(run, bal):                            # 보스(시크릿 보스 포함)를 이기고 올라가기 직전에 상점이 열린다.
            opened = open_shop(run, rng, bal)
            if opened:
                return opened
        if run.floor >= bal.floors and not run.infinite:
            if bal.infinite:                              # 꼭대기: 클리어하고 끝낼지, 무한모드로 계속할지 묻는다(고를 때까지 이 방에서 안 움직인다).
                run.phase, run.enemy, run.kind = "summit", None, "summit"
                run.events = [f"🏆 **{bal.floors}층 꼭대기**에 도착했어요!", "**클리어하고 끝낼지**, **♾️ 무한모드**로 계속 올라갈지 정해 주세요.",
                              "무한모드를 고르면 클리어 보상을 지금 받고, 다운돼도 잃지 않아요. 층마다 보너스 재화가 나오지만 몬스터는 계속 강해져요."]
                return run.events
            run.phase, run.enemy, run.kind = "clear", None, ""
            run.events = [f"🏆 {bal.floors}층 꼭대기에 도착했어요! 던전을 모두 돌파했어요!"]
            return run.events
        run.floor += 1
        if run.infinite and run.floor > bal.floors:       # 무한 층에 오를 때마다 보너스(층이 높을수록 커진다)
            run.bonus = bal.infinite_bonus + bal.infinite_step * (run.floor - bal.floors - 1)
        run.room = 0
        healed = (bal.max_hp - run.hp) * bal.floor_heal // 100
        run.hp = min(bal.max_hp, run.hp + max(0, healed))
        climb = [f"⬆️ **{run.floor}층**으로 올라왔어요!" + (f" 정비로 HP +{healed}" if healed > 0 else "")
                 + (f" 몬스터는 1층의 {floor_scaled(100, run.floor, bal.floor_scale)}% 수준이에요." if bal.floor_scale else "")]
    run.room += 1
    run.kind, run.enemy = roll_room(rng, run.room, bal, run.floor), None
    custom = None
    if run.kind.startswith("custom:"):                   # 커스텀 방: 종류는 'custom'으로 두고 방 정보는 여기서 꺼낸다(관리자가 지운 방이면 빈 방).
        custom = next((c for c in bal.rooms if c.key == run.kind[7:]), None)
        run.kind = "custom" if custom else "empty"
    if run.kind in ("monster", "boss") and not (bal.bosses if run.kind == "boss" else bal.monsters):
        run.kind = "empty"                               # 관리자가 몬스터를 모두 지웠다면(검사를 통과한 설정이면 없는 일) 빈 방으로 둔다.
    if run.kind in ("monster", "boss"):
        table = bal.bosses if run.kind == "boss" else bal.monsters
        monster = pick_weighted(rng, monsters_for_floor(table, run.floor))      # 이 층에 나올 수 있는 것 중에서 '등장' 비율대로(기본은 모두 같은 확률)
        run.enemy, run.phase = _spawn(monster, run.floor, bal), "battle"
        text = (f"👑 관문 노드! **{monster.name}**이(가) 가동됐어요!" if monster.boss
                else f"⚠️ 적성 개체 감지! **{monster.name}**이(가) 접근해요!")
    elif run.kind == "chest":
        run.phase, text = "chest", "🎁 보급 상자가 놓여 있는 방이에요."
    elif run.kind == "trap":
        text = _trap(run, bal)
        run.phase = "dead" if run.hp <= 0 else "empty"          # 함정에 다운됐다면 던전은 끝난다(부활권은 서비스가 쓴다).
    elif run.kind == "custom":
        text = _custom_room(run, custom, bal)
        run.phase = "dead" if run.hp <= 0 else "empty"
    else:
        run.phase, text = "empty", "🚪 텅 빈 방이에요. 앞으로 나아갈 수 있어요."
    run.events = [*climb, *([text] if isinstance(text, str) else text)]
    return run.events


def shop_due(run: Run, bal: Balance) -> bool:
    """이 층을 마치고 올라가기 전에 상점이 열려야 하나: 간격(층)의 배수이고, 이 층에서 아직 열리지 않았고,
    클리어로 끝나는 마지막 층이 아닐 때(무한모드가 켜져 있으면 마지막 층에서도 열린다)."""
    if bal.shop_interval <= 0 or run.floor % bal.shop_interval != 0 or run.shop_floor == run.floor:
        return False
    return run.floor < bal.floors or run.infinite or bool(bal.infinite)


def open_shop(run: Run, rng, bal: Balance) -> list[str]:
    """상점을 연다: 이 층에 나올 수 있는 장비·소모품(등장 비율 0보다 큰 것) 중에서 서로 다른 상품 SHOP_SLOTS개를 고른다.
    상품마다 장비/소모품은 반반, 그 안에서는 상자와 같은 '등장' 비율대로 고른다(소수 가능). 팔 것이 하나도 없으면 상점은 열리지 않는다(빈 목록)."""
    run.shop_floor = run.floor
    pools = {"gear": [g for g in candidates_for_floor(bal.gear, run.floor) if g.weight > 0],
             "item": [c for c in candidates_for_floor(bal.consumables, run.floor) if c.weight > 0]}
    offers = []
    for _ in range(SHOP_SLOTS):
        kinds = [k for k, pool in pools.items() if pool]
        if not kinds:
            break
        kind = kinds[0] if len(kinds) == 1 else ("gear" if rng.random() < 0.5 else "item")
        pool = pools[kind]
        pick = pick_weighted(rng, pool)                  # 상자와 같은 '등장' 비율대로(이미 놓인 상품은 빼고)
        pool.remove(pick)
        offers.append({"kind": kind, "key": pick.key, "price": shop_price(pick, bal.shop_markup), "sold": False})
    if not offers:                                      # 팔 것이 없다: 상점 없이 그냥 올라간다.
        return []
    run.shop = offers
    run.phase, run.kind, run.enemy = "shop", "shop", None
    run.events = [f"🛒 **{run.floor}층 상점**이 열렸어요! 보스를 이긴 보상으로 재화를 써서 장비·소모품을 살 수 있어요.",
                  "다 봤으면 [상점 나가기]로 위 층에 올라가요. 이 상점은 한 번만 열려요."]
    return run.events


def buy_offer(run: Run, arg, bal: Balance) -> int:
    """상점 상품 하나를 산다(arg: 0부터 센 번호). run에 반영하고 낼 재화를 돌려준다 — 재화를 빼는 건 부르는 쪽(저장소)이다.
    산 장비는 상자에서 나온 것처럼 '정하지 않은 장비'가 되고, 소모품은 소지품에 들어간다."""
    if run.phase != "shop":
        raise DungeonError("지금은 상점이 아니에요.")
    try:
        number = int(arg)
    except (TypeError, ValueError):
        raise DungeonError("알 수 없는 상품이에요.")
    if not 0 <= number < len(run.shop):
        raise DungeonError("알 수 없는 상품이에요.")
    offer = run.shop[number]
    if offer.get("sold"):
        raise DungeonError("이미 산 상품이에요.")
    if offer["kind"] == "gear":
        gear = bal.gear_by_key.get(offer["key"])
        if gear is None:
            raise DungeonError("그 상품은 사라졌어요.")
        if run.pending_gear:
            raise DungeonError("먼저 상자나 상점에서 받은 장비를 장착하거나 예비에 넣거나 버려 주세요.")
        run.pending_gear = gear.key
        run.loot.append(gear.name)
        run.events = [f"🛒 {gear.emoji} **{gear.name}** ({gear.tier})을(를) 샀어요! — {gear.description}", "장착할지, 예비에 넣을지, 버릴지 정해 주세요."]
    else:
        item = bal.item_by_key.get(offer["key"])
        if item is None:
            raise DungeonError("그 상품은 사라졌어요.")
        run.items[item.key] = run.items.get(item.key, 0) + 1
        run.last.append(f"item_gain:{item.key}")
        run.loot.append(item.name)
        run.events = [f"🛒 {item.emoji} **{item.name}** ({item.tier})을(를) 샀어요! 소지품에 넣었어요. — {item.description}"]
    offer["sold"] = True
    return offer["price"]


TRAP_TEXTS = ("바닥 패널이 꺼지며 합선이 일어나요!", "벽면 터릿에서 볼트가 쏟아져요!", "천장 크레인이 떨어져요!", "냉각 가스가 누출돼요!")


def _hurt(run: Run, raw: int, bal: Balance) -> tuple[int, str]:
    """방이 주는 피해를 적용한다(가드는 못 쓰지만 갑옷·소모품 피해 감소·무적은 적용). (실제 피해, 덧붙일 설명)."""
    damage = incoming_damage(run, raw, 0, bal)
    run.hp = max(0, run.hp - damage)
    if run.hp <= 0:
        run.phase = "dead"
    if raw and run.invuln_turns > 0:
        return damage, " ✨ 무적이라 피해를 받지 않았어요."
    armor = armor_percent(run, bal)
    notes = ([f"갑옷 {armor}%"] if armor else []) + ([f"피해 감소 {run.reduce_percent}%"] if run.reduce_turns > 0 and run.reduce_percent else [])
    return damage, f" ({' · '.join(notes)}로 줄였어요)" if notes and raw else ""


def _trap(run: Run, bal: Balance) -> list[str]:
    """함정 방: 들어서자마자 피해를 입는다(막을 수 없다). 피해는 몬스터처럼 층마다 늘어난다."""
    raw = floor_scaled(bal.trap_damage, run.floor, bal.floor_scale)
    flavor = TRAP_TEXTS[(run.floor + run.room) % len(TRAP_TEXTS)]
    if raw <= 0:
        return [f"⚡ 함정 방이에요. {flavor} 다행히 아무 일도 없었어요."]
    damage, note = _hurt(run, raw, bal)
    events = [f"⚡ **함정 방!** {flavor} **{damage}** 피해를 입었어요.{note}"]
    if run.phase == "dead":
        events.append("💀 시스템이 다운됐어요…")
    return events


def _custom_room(run: Run, room, bal: Balance) -> list[str]:
    """커스텀 방: 문구를 보여 주고 피해 → 회복 → 재화 → 소모품 순으로 효과를 준다."""
    events = [f"{room.emoji} **{room.name}**" + (f"\n{room.text}" if room.text else "")]
    if room.damage:
        damage, note = _hurt(run, room.damage, bal)
        events.append(f"💢 **{damage}** 피해를 입었어요.{note}")
        if run.phase == "dead":
            events.append("💀 시스템이 다운됐어요…")
            return events
    if room.heal:
        gained = max(0, min(bal.max_hp, run.hp + room.heal) - run.hp)
        run.hp += gained
        events.append(f"💚 HP +{gained}")
    if room.candy:
        run.room_candy += room.candy
        events.append("🪙 재화를 얻었어요! (아래에서 지급돼요)")
    item = bal.item_by_key.get(room.item) if room.item else None
    if item is not None:
        run.items[item.key] = run.items.get(item.key, 0) + room.item_amount
        run.last.append(f"item_gain:{item.key}")
        run.loot.append(item.name)
        events.append(f"{item.emoji} **{item.name}** ×{room.item_amount}을(를) 소지품에 넣었어요.")
    return events


def take_clear(run: Run) -> list[str]:
    """꼭대기에서 [클리어하고 끝내기]: 던전을 클리어로 끝낸다(보상은 서비스가 준다)."""
    if run.phase != "summit":
        raise DungeonError("지금은 고를 수 없어요. 꼭대기에 도착했을 때만 고를 수 있어요.")
    run.phase, run.enemy, run.kind = "clear", None, ""
    run.events = [f"🏆 던전을 모두 돌파했어요!"]
    return run.events


def go_infinite(run: Run, rng, bal: Balance | None = None) -> list[str]:
    """꼭대기에서 [무한모드 계속]: 무한모드를 켜고 다음 층으로 올라간다(클리어 보상과 층 보너스는 서비스가 준다)."""
    if run.phase != "summit":
        raise DungeonError("지금은 고를 수 없어요. 꼭대기에 도착했을 때만 고를 수 있어요.")
    run.infinite = True
    events = advance(run, rng, bal)
    return ["♾️ **무한모드**를 시작해요!", *events]


def incoming_damage(run: Run, raw: int, reduction: int = 0, bal: Balance | None = None, guarded: bool = False) -> int:
    """몬스터의 공격 raw가 지금 실제로 주는 피해: 무적이면 0. 가드에 성공했다면(guarded) **방패(가드)의 감소만** 적용한다.
    아니면 가드 감소(reduction%) → 갑옷 → 소모품의 피해 감소를 차례로 적용한다."""
    if run.invuln_turns > 0:
        return 0
    damage = reduce_damage(raw, reduction)
    if guarded:
        return damage
    armor = armor_percent(run, bal)
    if armor:
        damage = reduce_damage(damage, armor)
    if run.reduce_turns > 0 and run.reduce_percent:
        damage = reduce_damage(damage, run.reduce_percent)
    return damage


def _enemy_attack(run: Run, reduction: int, bal: Balance | None = None, guarded: bool = False) -> list[str]:
    if run.enemy.get("stunned"):                         # 가드 성공의 충격으로 이번 공격은 건너뛴다.
        run.enemy["stunned"] = False
        return [f"💫 {run.enemy['name']}은(는) 아직 휘청거려서 공격하지 못했어요!"]
    damage = incoming_damage(run, run.enemy["damage"], reduction, bal, guarded)
    run.hp = max(0, run.hp - damage)
    if run.invuln_turns > 0:
        events = [f"{run.enemy['emoji']} {run.enemy['name']}의 공격! ✨ **무적**이라 피해를 받지 않았어요."]
    else:
        armor = 0 if guarded else armor_percent(run, bal)           # 가드 중에는 방패의 감소만 적용된다
        buff = run.reduce_turns > 0 and run.reduce_percent and not guarded
        notes = ([f"가드로 {reduction}% 줄였어요"] if reduction else []) + ([f"갑옷으로 {armor}% 줄였어요"] if armor else []) \
            + ([f"피해 감소 {run.reduce_percent}%"] if buff else [])
        events = [f"{run.enemy['emoji']} {run.enemy['name']}의 공격! **{damage}** 피해를 입었어요." + (f" ({', '.join(notes)})" if notes else "")]
    if run.hp <= 0:
        run.phase = "dead"
        events.append("💀 시스템이 다운됐어요…")
    return events


def _end_turn(run: Run, bal: Balance, rng=None) -> list[str]:
    """한 턴이 끝날 때: 턴마다 회복(나노봇 주입제), 턴마다 줄어드는 HP(자해), 무적·피해 감소 턴 줄이기, 몬스터에게 걸린 지속 피해."""
    if run.phase == "dead":
        return []
    events = []
    if run.regen_turns > 0:
        before = run.hp
        run.hp = min(bal.max_hp, run.hp + run.regen_amount)
        run.regen_turns -= 1
        if run.hp != before:
            events.append(f"🧬 나노봇 주입제: HP +{run.hp - before} (남은 {run.regen_turns}턴)")
    if run.drain_turns > 0:
        before = run.hp
        run.hp = max(1, run.hp - run.drain_amount)
        run.drain_turns -= 1
        if run.hp != before:
            events.append(f"🔥 HP -{before - run.hp} (남은 {run.drain_turns}턴)")
    if run.invuln_turns > 0:
        run.invuln_turns -= 1
        if run.invuln_turns == 0:
            events.append("✨ 무적이 끝났어요.")
    if run.reduce_turns > 0:
        run.reduce_turns -= 1
        if run.reduce_turns == 0:
            run.reduce_percent = 0
            events.append("🛡️ 피해 감소가 끝났어요.")
    enemy = run.enemy
    if run.in_battle and enemy and enemy.get("dot_turns", 0) > 0 and enemy["hp"] > 0:
        enemy["hp"] = max(0, enemy["hp"] - enemy["dot"])
        enemy["dot_turns"] -= 1
        events.append(f"🌡️ {enemy['name']}에게 지속 피해 **{enemy['dot']}**! (남은 {enemy['dot_turns']}턴)")
        if enemy["hp"] <= 0:
            events.append(f"🎉 **{enemy['name']}**을(를) 무력화했어요!")
            run.phase = "cleared"
            if enemy.get("secret"):
                events += secret_drops(run, rng or random, bal)
    return events


def secret_drops(run: Run, rng, bal: Balance) -> list[str]:
    """무력화한 시크릿 보스의 드롭을 준다: 먼저 '빈 드롭 확률'로 아무것도 안 주는지 굴리고(정해 둔 경우만), 아니면 장비와 아이템을 각자 확률로 굴리고,
    둘 다 안 나오면 **반드시 하나는** 준다(확률이 더 높은 쪽, 같으면 장비).
    장비는 상자처럼 장착/예비/버리기를 정해야 하고, 아이템은 소지품에 들어간다. 없어진 장비·아이템은 건너뛴다. 줄 수 있는 게 하나도 없으면 빈 목록."""
    spec = run.enemy.get("drops") or {}
    nothing = spec.get("nothing") or 0
    if nothing > 0 and rng.random() * 100 < nothing:       # 빈 드롭: 이 확률로는 아무것도 떨어뜨리지 않는다(0이면 난수를 뽑지 않는다)
        return ["🎁 **시크릿 보스의 드롭…** 📭 아무것도 떨어뜨리지 않았어요."]
    gear, item = bal.gear_by_key.get(spec.get("gear")), bal.item_by_key.get(spec.get("item"))
    got_gear = gear is not None and rng.random() * 100 < spec.get("gear_chance", 100)
    got_item = item is not None and rng.random() * 100 < spec.get("item_chance", 100)
    if not (got_gear or got_item):
        if gear is not None and (item is None or spec.get("gear_chance", 100) >= spec.get("item_chance", 100)):
            got_gear = True
        elif item is not None:
            got_item = True
    events = []
    if got_gear and not run.pending_gear:
        run.pending_gear = gear.key
        run.loot.append(gear.name)
        events.append(f"{gear.emoji} **{gear.name}** ({gear.tier}) — {gear.description}\n┕ 장착할지, 예비에 넣을지, 버릴지 정해 주세요.")
    if got_item:
        amount = max(1, int(spec.get("amount", 1)))
        run.items[item.key] = run.items.get(item.key, 0) + amount
        run.loot.append(item.name)
        run.last.append(f"item_gain:{item.key}")
        events.append(f"{item.emoji} **{item.name}** ×{amount} ({item.tier}) — {item.description} (소지품에 넣었어요)")
    return ([f"🎁 **시크릿 보스의 드롭!**"] + events) if events else []


def _after_player_action(run: Run, reduction: int, bal: Balance, rng=None) -> list[str]:
    """플레이어 행동 뒤: 적이 살아 있으면 적이 공격하고, 턴이 끝난다."""
    events = []
    if run.enemy["hp"] <= 0:
        events.append(f"🎉 **{run.enemy['name']}**을(를) 무력화했어요!")
        run.phase = "cleared"
        if run.enemy.get("secret"):
            events += secret_drops(run, rng or random, bal)
    else:
        events += _enemy_attack(run, reduction, bal)
    return events + _end_turn(run, bal, rng)


def player_attack(run: Run, rng, bal: Balance | None = None) -> list[str]:
    """공격: 몬스터가 회피하지 못하면 피해를 준다. 단검 같은 무기는 크리티컬(2배)이 터질 수 있다."""
    bal = bal or DEFAULT
    weapon = _weapon(run, bal)
    if rng.random() * 100 < enemy_dodge(run, bal):
        events = [f"💨 {run.enemy['name']}이(가) 공격을 피했어요!"]
        run.last.append("attack_miss")
    else:
        run.last.append("attack_hit")
        damage, crit = player_damage(run, bal), False
        if weapon and weapon.crit and rng.random() * 100 < weapon.crit:
            damage, crit = damage * 2, True
        run.enemy["hp"] = max(0, run.enemy["hp"] - damage)
        events = [f"{'💥 크리티컬! ' if crit else '⚔️ '}{run.enemy['name']}에게 **{damage}** 피해를 줬어요."]
    events += _after_player_action(run, 0, bal, rng)
    run.events = events
    return events


def player_defend(run: Run, guard_ok: bool, bal: Balance | None = None, rng=None) -> list[str]:
    """방어: 가드에 성공하면 이번 피해를 줄이고, 맞은 공격의 일부만큼 HP를 회복하고, 그 공격의 일부를 몬스터에게 반사하고,
    몬스터의 다음 공격을 막는다. 실패하면 그대로 맞는다. 이미 기절한 몬스터 앞에서는 가드를 굴리지 않는다(어차피 공격하지 못한다)."""
    bal = bal or DEFAULT
    enemy = run.enemy
    if enemy.get("stunned"):
        events = _enemy_attack(run, 0, bal)
    else:
        raw = enemy["damage"]
        events = [("🛡️ 가드 성공!" if guard_ok else "❌ 가드 실패…")]
        run.last.append("guard_success" if guard_ok else "guard_fail")
        events += _enemy_attack(run, guard_percent(run, bal) if guard_ok else 0, bal, guarded=bool(guard_ok))
        if guard_ok and run.phase != "dead":
            healed = min(bal.max_hp, run.hp + raw * bal.guard_heal // 100) - run.hp
            run.hp += max(0, healed)
            reflected = raw * reflect_percent(run, bal) // 100
            enemy["hp"] = max(0, enemy["hp"] - reflected)
            events.append(f"💚 막아 낸 힘으로 HP +{max(0, healed)} · 🔁 **{reflected}** 피해를 되돌려 줬어요.")
            if enemy["hp"] <= 0:
                events.append(f"🎉 **{enemy['name']}**을(를) 무력화했어요!")
                run.phase = "cleared"
                if enemy.get("secret"):
                    events += secret_drops(run, rng or random, bal)
            else:
                enemy["stunned"] = True
                events.append(f"💫 {enemy['name']}이(가) 충격으로 다음 공격을 하지 못해요!")
    events += _end_turn(run, bal, rng)
    run.events = events
    return events


def condition_met(run: Run, cond, bal: Balance) -> bool:
    """소모품 사용 조건 하나를 지금 만족하는가. HP 조건은 지금 상태, 나머지는 바로 직전 행동에서 일어난 일을 본다."""
    kind, value = cond
    if kind in ("hp_le", "hp_ge"):
        return run.hp * 100 <= value * bal.max_hp if kind == "hp_le" else run.hp * 100 >= value * bal.max_hp
    if kind in ("enemy_hp_le", "enemy_hp_ge"):
        if not run.in_battle or not run.enemy:
            return False                                               # 싸우는 상대가 없으면 만족할 수 없다.
        enemy_max = run.enemy.get("max_hp") or run.enemy["hp"]
        return run.enemy["hp"] * 100 <= value * enemy_max if kind == "enemy_hp_le" else run.enemy["hp"] * 100 >= value * enemy_max
    if kind in ("item_gain", "item_use"):
        return f"{kind}:{value}" in run.last
    return EVENT_TAG[kind] in run.last


def unmet_conditions(run: Run, item: Consumable, bal: Balance | None = None) -> list:
    """이 소모품의 사용 조건 중 지금 만족하지 못하는 것들. 비어 있으면 쓸 수 있다."""
    bal = bal or DEFAULT
    return [cond for cond in item.conditions if not condition_met(run, cond, bal)]


def can_use_now(run: Run, item: Consumable, bal: Balance | None = None) -> bool:
    """지금 이 소모품을 쓸 수 있는가: 사용 조건을 전부 만족하고, 개조 재료라면 든 장비(무기·방패)에 맞는 레시피가 있다."""
    bal = bal or DEFAULT
    if unmet_conditions(run, item, bal):
        return False
    if (item.damage or item.dot) and not _can_hit(run):
        return False
    return not item.material or mod_target(run, item.key, bal)[1] is not None


def _can_hit(run: Run) -> bool:
    """몬스터에게 피해를 주는 소모품은 살아 있는 몬스터와 싸우는 중에만 쓸 수 있다."""
    return run.in_battle and bool(run.enemy) and run.enemy["hp"] > 0


def mod_target(run: Run, item_key: str, bal: Balance) -> tuple[Gear | None, Gear | None]:
    """개조 재료를 쓰면 (지금 들고 있는 장비, 바뀔 장비). 든 무기·방패·갑옷 모두 이 재료의 레시피가 있으면 **무기 → 방패 → 갑옷 순**으로 먼저 맞는 것을 개조한다.
    맞는 레시피가 없으면 (None, None)."""
    for held_key in (run.weapon, run.shield, run.armor):
        result = bal.mod_result(held_key, item_key) if held_key else None
        if result is not None:
            return bal.gear_by_key[held_key], bal.gear_by_key[result]
    return None, None


def _material_hint(item: Consumable, bal: Balance) -> str:
    recipes = [bal.gear_by_key[w].name for w, i, _ in bal.mods if i == item.key]
    return ("이 재료로 개조할 수 있는 장비: " + ", ".join(recipes[:5]) + (" 외" if len(recipes) > 5 else "")) if recipes else "아직 이 재료로 개조할 수 있는 장비가 없어요."


def use_consumable(run: Run, key: str, bal: Balance | None = None, rng=None) -> list[str]:
    """아이템 사용. 전투 중이면 한 턴을 쓴 것으로 치고 몬스터가 공격한다. 사용 조건이 있으면 전부 만족해야 쓸 수 있고(아니면 소모되지 않는다),
    개조 소모품은 들고 있는 무기를 지정한 무기로 바꾸고, 개조 재료는 든 무기에 맞는 레시피(/장비개조)가 있을 때 그 결과 무기로 바꾼다."""
    bal = bal or DEFAULT
    item = bal.item_by_key.get(key)
    if item is None:
        raise DungeonError("알 수 없는 아이템이에요.")
    if run.items.get(key, 0) < 1:
        raise DungeonError(f"{item.name}이(가) 없어요.")
    unmet = unmet_conditions(run, item, bal)
    if unmet:
        names = {c.key: c.name for c in bal.consumables}
        raise DungeonError(f"지금은 {item.name}을(를) 쓸 수 없어요. 조건: " + " & ".join(condition_text(c, names) for c in unmet))
    if (item.damage or item.dot) and not _can_hit(run):
        raise DungeonError(f"{item.name}은(는) 몬스터와 싸우는 중에만 쓸 수 있어요.")
    target = None
    before = None
    if item.material:                                   # 개조 재료: 든 무기나 방패에 맞는 레시피가 있어야 한다.
        before, target = mod_target(run, key, bal)
        if target is None:
            weapon, shield, armor = bal.gear_by_key.get(run.weapon), bal.gear_by_key.get(run.shield), bal.gear_by_key.get(run.armor)
            raise DungeonError(f"{item.name}은(는) 지금 든 장비(무기: {weapon.name if weapon else '맨손'} · 방패: {shield.name if shield else '없음'} · 갑옷: {armor.name if armor else '없음'})에는 "
                               f"쓸 수 없어요. {_material_hint(item, bal)}")
    elif item.modify:
        target = bal.gear_by_key.get(item.modify)
        if target is None or target.slot != "weapon":
            raise DungeonError(f"{item.name}(으)로 개조할 무기가 사라졌어요.")
        if run.weapon == target.key:
            raise DungeonError(f"이미 {target.emoji} {target.name}을(를) 들고 있어서 개조할 수 없어요.")
    run.items[key] -= 1
    if run.items[key] <= 0:
        del run.items[key]
    run.last = [f"item_use:{key}"]                  # 조건 판정은 끝났다: 이제 직전 행동은 이 소모품을 쓴 일이다.
    if target is not None:
        if target.slot == "weapon":
            before = before or _weapon(run, bal)
            run.weapon = target.key
            nothing = "맨손"
        elif target.slot == "armor":
            before = before or _armor(run, bal)
            run.armor = target.key
            nothing = "맨몸"
        else:
            before = before or _shield(run, bal)
            run.shield = target.key
            nothing = "맨몸 가드"
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요! 🔧 {before.name if before else nothing}이(가) "
                  f"{target.emoji} **{target.name}**(으)로 개조됐어요. ({target.description})"]
    else:
        events = []
        if item.full:
            gained, run.hp = max(0, bal.max_hp - run.hp), max(run.hp, bal.max_hp)
            events.append(f"{item.emoji} **{item.name}**을(를) 사용했어요. HP가 전부 회복됐어요! (+{gained})")
        elif item.heal:
            gained = max(0, min(bal.max_hp, run.hp + item.heal) - run.hp)
            run.hp += gained
            events.append(f"{item.emoji} **{item.name}**을(를) 사용했어요. HP +{gained}")
        elif item.regen:
            run.regen_amount, run.regen_turns = item.regen, item.regen_turns
            events.append(f"{item.emoji} **{item.name}**을(를) 사용했어요. {item.regen_turns}턴 동안 턴마다 HP +{item.regen}!")
        else:
            events.append(f"{item.emoji} **{item.name}**을(를) 사용했어요!")
        if item.self_damage:                             # 비용: 내 HP를 깎는다(1 밑으로는 안 내려간다). 여러 턴이면 첫 번째는 바로, 나머지는 턴마다.
            lost = run.hp - max(1, run.hp - item.self_damage)
            run.hp -= lost
            events.append(f"🔥 내 HP가 **{lost}** 줄었어요." + (f" (이후 {item.self_turns - 1}턴 동안 턴마다 -{item.self_damage})" if item.self_turns > 1 else ""))
            if item.self_turns > 1:
                run.drain_amount, run.drain_turns = item.self_damage, item.self_turns - 1
        if item.invuln_turns:
            run.invuln_turns = max(run.invuln_turns, item.invuln_turns)
            events.append(f"✨ {item.invuln_turns}턴 동안 몬스터의 공격을 받지 않아요!")
        if item.reduce:
            run.reduce_percent, run.reduce_turns = item.reduce, item.reduce_turns
            events.append(f"🛡️ {item.reduce_turns}턴 동안 받는 피해가 {item.reduce}% 줄어요!")
        if item.damage:
            run.enemy["hp"] = max(0, run.enemy["hp"] - item.damage)
            events.append(f"💥 {run.enemy['name']}에게 **{item.damage}** 피해를 줬어요.")
        if item.dot:
            run.enemy["dot"], run.enemy["dot_turns"] = item.dot, item.dot_turns
            events.append(f"🌡️ {run.enemy['name']}이(가) {item.dot_turns}턴 동안 턴마다 **{item.dot}** 피해를 입어요.")
    if run.in_battle:
        events += _after_player_action(run, 0, bal, rng)          # 몬스터가 다운됐다면 공격하지 않고, 아니면 반격한다. 턴 끝 효과도 여기서 처리한다.
    run.events = events
    return events


def open_chest(run: Run, rng, bal: Balance | None = None) -> tuple[Gear, Consumable, list[str]]:
    """보급 상자: 장비 한 개와 아이템 한 개. 좋은 것일수록 확률이 낮다."""
    bal = bal or DEFAULT
    gear = pick_weighted(rng, candidates_for_floor(bal.gear, run.floor))         # 이 층 상자에서 나올 수 있는 것 중에서 '등장' 비율대로
    item = pick_weighted(rng, candidates_for_floor(bal.consumables, run.floor))
    run.phase = "chest_done"
    run.pending_gear = gear.key                      # 장착할지 버릴지 정할 때까지 앞으로 갈 수 없다.
    run.items[item.key] = run.items.get(item.key, 0) + 1
    run.last.append(f"item_gain:{item.key}")
    run.loot += [gear.name, item.name]
    held = {"shield": _shield, "armor": _armor}.get(gear.slot, _weapon)(run, bal)
    swap = f"\n┕ 지금 쓰는 **{held.name}**과(와) 바뀌어요." if held else ""
    run.events = ["🎁 상자를 열었어요!", f"{gear.emoji} **{gear.name}** ({gear.tier}) — {gear.description}{swap}",
                  f"{item.emoji} **{item.name}** ({item.tier}) — {item.description} (소지품에 넣었어요)"]
    return gear, item, run.events


def _pending(run: Run, bal: Balance) -> Gear | None:
    """상자에서 나와 정하지 못한 장비. 그 사이 관리자가 장비를 지웠다면 대기만 풀고(앞으로 못 가는 일이 없도록) None."""
    if not run.pending_gear:
        return None
    gear = bal.gear_by_key.get(run.pending_gear)
    if gear is None:
        run.pending_gear = None
    return gear


def _label(gear: Gear) -> str:
    return f"{gear.emoji} **{gear.name}**"


def _valid(key: str | None, slot: str, bal: Balance) -> str | None:
    """그 칸에 놓을 수 있는 장비의 key. 관리자가 지웠거나 종류가 다르면 없는 것으로 본다."""
    gear = bal.gear_by_key.get(key) if key else None
    return key if gear is not None and gear.slot == slot else None


def equip_pending(run: Run, bal: Balance | None = None) -> list[str]:
    """상자에서 나온 장비를 장착한다. 같은 칸에 장착하고 있던 장비는 예비 칸이 비어 있으면 거기로 옮기고, 차 있으면 사라진다."""
    bal = bal or DEFAULT
    had = run.pending_gear
    gear = _pending(run, bal)
    if gear is None:
        if had:                                          # 그 사이 지워진 장비: 오류로 막지 않고 대기만 풀어 앞으로 갈 수 있게 한다.
            run.events = ["❔ 그 장비는 사라졌어요. 앞으로 나아갈 수 있어요."]
            return run.events
        raise DungeonError("장착할 장비가 없어요.")
    slot = gear.slot
    old = _valid(getattr(run, slot), slot, bal)
    setattr(run, slot, gear.key)
    run.pending_gear = None
    run.events = [f"{_label(gear)}을(를) 장착했어요!"]
    if old:
        spare_free = _valid(getattr(run, f"spare_{slot}"), slot, bal) is None
        if spare_free:
            setattr(run, f"spare_{slot}", old)
        run.events.append(f"┕ 기존 {_label(bal.gear_by_key[old])}은(는) " + ("예비 칸으로 옮겼어요." if spare_free else "예비 칸이 차 있어서 사라졌어요."))
    return run.events


def stash_pending(run: Run, bal: Balance | None = None) -> list[str]:
    """상자에서 나온 장비를 장착하지 않고 예비 칸에 넣는다. 그 종류의 예비 칸이 비어 있어야 한다(무기·방패·갑옷 1개씩만 넣을 수 있다)."""
    bal = bal or DEFAULT
    had = run.pending_gear
    gear = _pending(run, bal)
    if gear is None:
        if had:
            run.events = ["❔ 그 장비는 사라졌어요. 앞으로 나아갈 수 있어요."]
            return run.events
        raise DungeonError("예비 칸에 넣을 장비가 없어요.")
    slot = gear.slot
    if _valid(getattr(run, f"spare_{slot}"), slot, bal) is not None:
        held = bal.gear_by_key[getattr(run, f"spare_{slot}")]
        raise DungeonError(f"예비 {SLOT_NAMES[slot]} 칸이 이미 차 있어요 ({held.emoji} {held.name}). "
                           "먼저 [🎒 장비]에서 바꾸거나 팔아 주세요. 또는 [장착]으로 바로 쓰세요.")
    setattr(run, f"spare_{slot}", gear.key)
    run.pending_gear = None
    run.events = [f"📥 {_label(gear)}을(를) 예비 {SLOT_NAMES[slot]} 칸에 넣었어요."]
    return run.events


def swap_spare(run: Run, slot: str, bal: Balance | None = None) -> list[str]:
    """장착한 장비와 예비 칸의 장비를 서로 바꾼다(slot: weapon | shield). 한쪽이 비어 있으면 넣기(장착한 것을 예비로)나
    꺼내기(예비를 장착)가 된다. 싸우는 중에는 바꿀 수 없다."""
    bal = bal or DEFAULT
    if slot not in GEAR_SLOTS:
        raise DungeonError("알 수 없는 장비 칸이에요.")
    if run.finished:
        raise DungeonError("끝난 던전에서는 바꿀 수 없어요.")
    if run.in_battle:
        raise DungeonError("싸우는 중에는 장비를 바꿀 수 없어요. 싸움이 끝난 뒤에 바꿔 주세요.")
    name = SLOT_NAMES[slot]
    equipped, spare = _valid(getattr(run, slot), slot, bal), _valid(getattr(run, f"spare_{slot}"), slot, bal)
    if equipped is None and spare is None:
        setattr(run, slot, None)
        setattr(run, f"spare_{slot}", None)
        raise DungeonError(f"바꿀 {i_ga(name)} 없어요. 장착한 것도 예비도 비어 있어요.")
    setattr(run, slot, spare)
    setattr(run, f"spare_{slot}", equipped)
    if equipped and spare:
        run.events = [f"🔄 {eul_reul(name)} 교체했어요: {_label(bal.gear_by_key[equipped])} → {_label(bal.gear_by_key[spare])}",
                      f"┕ {_label(bal.gear_by_key[equipped])}은(는) 예비 칸으로 갔어요."]
    elif equipped:
        run.events = [f"📥 {_label(bal.gear_by_key[equipped])}을(를) 예비 칸에 넣었어요. 지금은 {BARE[slot]}이에요."]
    else:
        run.events = [f"📤 예비 칸의 {_label(bal.gear_by_key[spare])}을(를) 꺼내 장착했어요!"]
    return run.events


def discard_pending(run: Run, bal: Balance | None = None) -> list[str]:
    had = run.pending_gear
    gear = _pending(run, bal or DEFAULT)
    if gear is None:
        if had:
            run.events = ["❔ 그 장비는 이미 사라졌어요. 앞으로 나아갈 수 있어요."]
            return run.events
        raise DungeonError("버릴 장비가 없어요.")
    run.pending_gear = None
    run.events = [f"🗑️ {gear.emoji} **{gear.name}**을(를) 버렸어요."]
    return run.events


SELL_MODES = ("all", "one", "items")      # all: 고른 것 전부 · one: 고른 소모품을 1개씩 · items: 팔 수 있는 소모품 전부


def sellable(run: Run, bal: Balance | None = None) -> list[dict]:
    """지금 팔 수 있는 것(판매가 1 이상). 상자에서 나와 아직 정하지 않은 장비, 장착 중인 장비, 소지품의 소모품 순서다.
    id: pending / weapon / shield / item:<key>. 관리자가 지웠거나 판매가를 0으로 한 것은 나오지 않는다."""
    bal = bal or DEFAULT
    out = []
    for slot, key, tag in (("pending", run.pending_gear, "새 장비"), ("weapon", run.weapon, "장착 중"), ("shield", run.shield, "장착 중"),
                           ("armor", run.armor, "장착 중"), ("spare_weapon", run.spare_weapon, "예비"), ("spare_shield", run.spare_shield, "예비"),
                           ("spare_armor", run.spare_armor, "예비")):
        gear = bal.gear_by_key.get(key) if key else None
        if gear and gear.sell_price > 0:
            out.append({"id": slot, "kind": "gear", "name": gear.name, "emoji": gear.emoji, "qty": 1, "price": gear.sell_price, "tag": tag})
    for key, qty in run.items.items():
        item = bal.item_by_key.get(key)
        if item and item.sell_price > 0 and qty > 0:
            out.append({"id": f"item:{key}", "kind": "item", "name": item.name, "emoji": item.emoji, "qty": qty,
                        "price": item.sell_price, "tag": item.tier})
    return out


def sell(run: Run, picks, mode: str = "all", bal: Balance | None = None) -> tuple[int, list[tuple[dict, int]]]:
    """던전 안에서 장비·소모품을 판다. 고른 것(picks: sellable의 id 목록)을 run에서 빼고 (받을 재화 합계, [(항목, 판 개수)])를 돌려준다.
    싸우는 중이거나 끝난 던전에서는 팔 수 없다. 재화를 실제로 주는 건 호출하는 쪽(저장소)이다."""
    bal = bal or DEFAULT
    if run.finished:
        raise DungeonError("끝난 던전에서는 팔 수 없어요.")
    if run.in_battle:
        raise DungeonError("싸우는 중에는 팔 수 없어요. 싸움이 끝난 뒤에 팔아 주세요.")
    if mode not in SELL_MODES:
        raise DungeonError("알 수 없는 판매 방식이에요.")
    entries = {e["id"]: e for e in sellable(run, bal)}
    if mode == "items":
        picks = [i for i, e in entries.items() if e["kind"] == "item"]
    chosen = [entries[i] for i in dict.fromkeys(picks) if i in entries]
    if not chosen:
        raise DungeonError("팔 수 있는 걸 골라 주세요. (판매가가 0인 것은 팔 수 없어요)")
    total, sold = 0, []
    for entry in chosen:
        qty = entry["qty"] if entry["kind"] == "item" and mode != "one" else 1
        total += entry["price"] * qty
        sold.append((entry, qty))
        if entry["id"] == "pending":
            run.pending_gear = None
        elif entry["id"] == "weapon":
            run.weapon = None
        elif entry["id"] == "shield":
            run.shield = None
        elif entry["id"] == "armor":
            run.armor = None
        elif entry["id"] in ("spare_weapon", "spare_shield", "spare_armor"):
            setattr(run, entry["id"], None)
        else:
            key = entry["id"].split(":", 1)[1]
            run.items[key] -= qty
            if run.items[key] <= 0:
                del run.items[key]
    return total, sold


def guard_success_percent(run: Run | None, bal: Balance | None = None) -> int:
    """지금 방어할 때 가드에 성공할 확률(%). 낀 방패에 성공률이 정해져 있으면 그것, 아니면 서버 기본(방어.성공확률)."""
    bal = bal or DEFAULT
    shield = _shield(run, bal) if run is not None else None
    return shield.guard_chance if shield and shield.guard_chance is not None else bal.guard_chance


def roll_guard(rng, bal: Balance | None = None, run: Run | None = None) -> bool:
    """방어 버튼의 가드 성공 여부. run을 주면 낀 방패의 가드 성공률을 쓴다."""
    return rng.random() * 100 < guard_success_percent(run, bal)


def roll_clear_reward(rng, bal: Balance | None = None) -> tuple[int, bool]:
    """클리어 보상: (재화, 히든 칭호 획득권이 나왔는지)."""
    bal = bal or DEFAULT
    candy = rng.randint(*bal.clear_candy)
    return candy, rng.random() * 100 < bal.ticket_chance
