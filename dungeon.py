"""던전 규칙: 방 구성, 턴제 전투, 장비·아이템·몬스터. 디스코드에 접속하지 않는 순수 로직이다.

확률은 모두 퍼센트이고, 난수 생성기를 받아서 시험하기 쉽다. 수치(기본값과 관리자가 바꿀 수 있는 값)는 balance.py에 있고,
이 파일의 함수들은 `bal`(Balance)을 받아 그 값을 쓴다. 생략하면 기본값이다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from balance import (BASE_DAMAGE, BASE_DODGE, BOSSES, CLEAR_CANDY, CONSUMABLES, DEFAULT, FLOOR_HEAL, FLOOR_SCALE, FLOORS,  # noqa: F401  (다시 내보냄)
                     GEAR, GUARD_BASE, GUARD_CHANCE, GUARD_HEAL, MAX_HP, MONSTERS, REFLECT_BASE, ROOM_PLAN, ROOMS,
                     TICKET_CHANCE, TIER_ORDER, Balance, Consumable, EVENT_TAG, Gear, Monster, condition_text, floor_scaled, reduce_damage)

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
    spare_shield: str | None = None                 # 예비 방패 1개. 예비 칸은 무기 1·방패 1이 전부이고 던전이 끝나면 사라진다.
    regen_amount: int = 0
    regen_turns: int = 0
    events: list = field(default_factory=list)      # 방금 있었던 일(화면에 보여준다)
    loot: list = field(default_factory=list)        # 이번 던전에서 얻은 물품 이름
    rev: int = 0                                    # 화면 번호. 행동마다 올라가서 지난 화면의 버튼을 가려낸다.
    items: dict = field(default_factory=dict)       # 가지고 있는 소모품 {key: 개수}
    pending_gear: str | None = None                 # 상자에서 나와 아직 장착/버림을 정하지 않은 장비
    last: list = field(default_factory=list)        # 바로 직전 행동에서 일어난 일(소모품 사용 조건에 쓴다): guard_success·guard_fail·attack_hit·attack_miss·item_gain:키·item_use:키

    def bump(self) -> None:
        self.rev += 1

    @property
    def stage(self) -> int:
        """처음부터 세어 몇 번째 방인지(시뮬레이터 통계용). 1층 3번째 방이면 3, 2층 1번째 방이면 ROOMS + 1."""
        return (max(self.floor, 1) - 1) * ROOMS + self.room

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


def roll_room(rng, room: int, bal: Balance | None = None) -> str:
    """room번째 방의 종류. 방 배치에서 칸이 하나뿐이면 그대로, 여러 칸이면 가중치대로 고른다."""
    bal = bal or DEFAULT
    table = bal.room_plan[min(max(room, 1), len(bal.room_plan)) - 1]
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
    return {"key": monster.key, "name": monster.name, "emoji": monster.emoji, "hp": hp, "max_hp": hp,
            "damage": floor_scaled(monster.damage, floor, percent), "dodge": monster.dodge, "boss": monster.boss, "stunned": False}


def advance(run: Run, rng, bal: Balance | None = None) -> list[str]:
    """다음 방으로 간다. 한 층의 마지막 방(10번째, 보스)을 마쳤다면 위 층의 1번째 방으로 올라가고(잃은 HP를 일부 회복),
    마지막 층이라면 던전이 끝난다."""
    bal = bal or DEFAULT
    climb: list[str] = []
    if run.room >= ROOMS:
        if run.floor >= bal.floors:
            run.phase, run.enemy, run.kind = "clear", None, ""
            run.events = [f"🏆 {bal.floors}층 꼭대기에 도착했어요! 던전을 모두 돌파했어요!"]
            return run.events
        run.floor += 1
        run.room = 0
        healed = (bal.max_hp - run.hp) * bal.floor_heal // 100
        run.hp = min(bal.max_hp, run.hp + max(0, healed))
        climb = [f"⬆️ **{run.floor}층**으로 올라왔어요!" + (f" 숨을 돌려 HP +{healed}" if healed > 0 else "")
                 + (f" 몬스터는 1층의 {floor_scaled(100, run.floor, bal.floor_scale)}% 수준이에요." if bal.floor_scale else "")]
    run.room += 1
    run.kind, run.enemy = roll_room(rng, run.room, bal), None
    if run.kind in ("monster", "boss") and not (bal.bosses if run.kind == "boss" else bal.monsters):
        run.kind = "empty"                               # 관리자가 몬스터를 모두 지웠다면(검사를 통과한 설정이면 없는 일) 빈 방으로 둔다.
    if run.kind in ("monster", "boss"):
        table = bal.bosses if run.kind == "boss" else bal.monsters
        monster = pick_weighted(rng, table)               # 몬스터마다 정한 '등장' 비율대로(기본은 모두 같은 확률)
        run.enemy, run.phase = _spawn(monster, run.floor, bal), "battle"
        text = (f"👑 미니 보스의 방! **{monster.name}**이(가) 나타났어요!" if monster.boss
                else f"⚠️ 몬스터 조우! **{monster.name}**이(가) 나타났어요!")
    elif run.kind == "chest":
        run.phase, text = "chest", "🎁 보물 상자가 놓여 있는 방이에요."
    else:
        run.phase, text = "empty", "🚪 텅 빈 방이에요. 앞으로 나아갈 수 있어요."
    run.events = [*climb, text]
    return run.events


def _enemy_attack(run: Run, reduction: int) -> list[str]:
    if run.enemy.get("stunned"):                         # 가드 성공의 충격으로 이번 공격은 건너뛴다.
        run.enemy["stunned"] = False
        return [f"💫 {run.enemy['name']}은(는) 아직 휘청거려서 공격하지 못했어요!"]
    damage = reduce_damage(run.enemy["damage"], reduction)
    run.hp = max(0, run.hp - damage)
    note = f" (가드로 {reduction}% 줄였어요)" if reduction else ""
    events = [f"{run.enemy['emoji']} {run.enemy['name']}의 공격! **{damage}** 피해를 입었어요.{note}"]
    if run.hp <= 0:
        run.phase = "dead"
        events.append("💀 쓰러졌어요…")
    return events


def _end_turn(run: Run, bal: Balance) -> list[str]:
    """한 턴이 끝날 때: 영혼 에센스처럼 턴마다 회복하는 효과."""
    if run.phase == "dead" or run.regen_turns <= 0:
        return []
    before = run.hp
    run.hp = min(bal.max_hp, run.hp + run.regen_amount)
    run.regen_turns -= 1
    if run.hp == before:
        return []
    return [f"👻 영혼 에센스: HP +{run.hp - before} (남은 {run.regen_turns}턴)"]


def _after_player_action(run: Run, reduction: int, bal: Balance) -> list[str]:
    """플레이어 행동 뒤: 적이 살아 있으면 적이 공격하고, 턴이 끝난다."""
    events = []
    if run.enemy["hp"] <= 0:
        events.append(f"🎉 **{run.enemy['name']}**을(를) 쓰러뜨렸어요!")
        run.phase = "cleared"
    else:
        events += _enemy_attack(run, reduction)
    return events + _end_turn(run, bal)


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
    events += _after_player_action(run, 0, bal)
    run.events = events
    return events


def player_defend(run: Run, guard_ok: bool, bal: Balance | None = None) -> list[str]:
    """방어: 가드에 성공하면 이번 피해를 줄이고, 맞은 공격의 일부만큼 HP를 회복하고, 그 공격의 일부를 몬스터에게 반사하고,
    몬스터의 다음 공격을 막는다. 실패하면 그대로 맞는다. 이미 기절한 몬스터 앞에서는 가드를 굴리지 않는다(어차피 공격하지 못한다)."""
    bal = bal or DEFAULT
    enemy = run.enemy
    if enemy.get("stunned"):
        events = _enemy_attack(run, 0)
    else:
        raw = enemy["damage"]
        events = [("🛡️ 가드 성공!" if guard_ok else "❌ 가드 실패…")]
        run.last.append("guard_success" if guard_ok else "guard_fail")
        events += _enemy_attack(run, guard_percent(run, bal) if guard_ok else 0)
        if guard_ok and run.phase != "dead":
            healed = min(bal.max_hp, run.hp + raw * bal.guard_heal // 100) - run.hp
            run.hp += max(0, healed)
            reflected = raw * reflect_percent(run, bal) // 100
            enemy["hp"] = max(0, enemy["hp"] - reflected)
            events.append(f"💚 막아 낸 힘으로 HP +{max(0, healed)} · 🔁 **{reflected}** 피해를 되돌려 줬어요.")
            if enemy["hp"] <= 0:
                events.append(f"🎉 **{enemy['name']}**을(를) 쓰러뜨렸어요!")
                run.phase = "cleared"
            else:
                enemy["stunned"] = True
                events.append(f"💫 {enemy['name']}이(가) 충격으로 다음 공격을 하지 못해요!")
    events += _end_turn(run, bal)
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


def use_consumable(run: Run, key: str, bal: Balance | None = None) -> list[str]:
    """아이템 사용. 전투 중이면 한 턴을 쓴 것으로 치고 몬스터가 공격한다. 사용 조건이 있으면 전부 만족해야 쓸 수 있고(아니면 소모되지 않는다),
    개조 소모품은 들고 있는 무기를 지정한 무기로 바꾼다."""
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
    target = None
    if item.modify:
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
        before = _weapon(run, bal)
        run.weapon = target.key
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요! 🔧 {before.name if before else '맨손'}이(가) "
                  f"{target.emoji} **{target.name}**(으)로 개조됐어요. ({target.description})"]
    elif item.full:
        gained, run.hp = max(0, bal.max_hp - run.hp), max(run.hp, bal.max_hp)
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요. HP가 전부 회복됐어요! (+{gained})"]
    elif item.heal:
        gained = max(0, min(bal.max_hp, run.hp + item.heal) - run.hp)
        run.hp += gained
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요. HP +{gained}"]
    else:
        run.regen_amount, run.regen_turns = item.regen, item.regen_turns
        events = [f"{item.emoji} **{item.name}**을(를) 사용했어요. {item.regen_turns}턴 동안 턴마다 HP +{item.regen}!"]
    if run.in_battle:
        events += _enemy_attack(run, 0)
        events += _end_turn(run, bal)
    run.events = events
    return events


def open_chest(run: Run, rng, bal: Balance | None = None) -> tuple[Gear, Consumable, list[str]]:
    """보물 상자: 장비 한 개와 아이템 한 개. 좋은 것일수록 확률이 낮다."""
    bal = bal or DEFAULT
    gear, item = pick_weighted(rng, bal.gear), pick_weighted(rng, bal.consumables)
    run.phase = "chest_done"
    run.pending_gear = gear.key                      # 장착할지 버릴지 정할 때까지 앞으로 갈 수 없다.
    run.items[item.key] = run.items.get(item.key, 0) + 1
    run.last.append(f"item_gain:{item.key}")
    run.loot += [gear.name, item.name]
    held = _shield(run, bal) if gear.slot == "shield" else _weapon(run, bal)
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
    slot = "shield" if gear.slot == "shield" else "weapon"
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
    """상자에서 나온 장비를 장착하지 않고 예비 칸에 넣는다. 그 종류의 예비 칸이 비어 있어야 한다(무기 1개·방패 1개만 넣을 수 있다)."""
    bal = bal or DEFAULT
    had = run.pending_gear
    gear = _pending(run, bal)
    if gear is None:
        if had:
            run.events = ["❔ 그 장비는 사라졌어요. 앞으로 나아갈 수 있어요."]
            return run.events
        raise DungeonError("예비 칸에 넣을 장비가 없어요.")
    slot = "shield" if gear.slot == "shield" else "weapon"
    if _valid(getattr(run, f"spare_{slot}"), slot, bal) is not None:
        held = bal.gear_by_key[getattr(run, f"spare_{slot}")]
        raise DungeonError(f"예비 {'방패' if slot == 'shield' else '무기'} 칸이 이미 차 있어요 ({held.emoji} {held.name}). "
                           "먼저 [🎒 장비]에서 바꾸거나 팔아 주세요. 또는 [장착]으로 바로 쓰세요.")
    setattr(run, f"spare_{slot}", gear.key)
    run.pending_gear = None
    run.events = [f"📥 {_label(gear)}을(를) 예비 {'방패' if slot == 'shield' else '무기'} 칸에 넣었어요."]
    return run.events


def swap_spare(run: Run, slot: str, bal: Balance | None = None) -> list[str]:
    """장착한 장비와 예비 칸의 장비를 서로 바꾼다(slot: weapon | shield). 한쪽이 비어 있으면 넣기(장착한 것을 예비로)나
    꺼내기(예비를 장착)가 된다. 싸우는 중에는 바꿀 수 없다."""
    bal = bal or DEFAULT
    if slot not in ("weapon", "shield"):
        raise DungeonError("알 수 없는 장비 칸이에요.")
    if run.finished:
        raise DungeonError("끝난 던전에서는 바꿀 수 없어요.")
    if run.in_battle:
        raise DungeonError("싸우는 중에는 장비를 바꿀 수 없어요. 싸움이 끝난 뒤에 바꿔 주세요.")
    name = "방패" if slot == "shield" else "무기"
    equipped, spare = _valid(getattr(run, slot), slot, bal), _valid(getattr(run, f"spare_{slot}"), slot, bal)
    if equipped is None and spare is None:
        setattr(run, slot, None)
        setattr(run, f"spare_{slot}", None)
        raise DungeonError(f"바꿀 {name}가 없어요. 장착한 것도 예비도 비어 있어요.")        # 무기·방패는 받침이 없어서 '가'다.
    setattr(run, slot, spare)
    setattr(run, f"spare_{slot}", equipped)
    if equipped and spare:
        run.events = [f"🔄 {name}를 교체했어요: {_label(bal.gear_by_key[equipped])} → {_label(bal.gear_by_key[spare])}",
                      f"┕ {_label(bal.gear_by_key[equipped])}은(는) 예비 칸으로 갔어요."]
    elif equipped:
        run.events = [f"📥 {_label(bal.gear_by_key[equipped])}을(를) 예비 칸에 넣었어요. 지금은 {'맨몸 가드' if slot == 'shield' else '맨손'}이에요."]
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
                           ("spare_weapon", run.spare_weapon, "예비"), ("spare_shield", run.spare_shield, "예비")):
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
        elif entry["id"] in ("spare_weapon", "spare_shield"):
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
