"""던전 규칙: 방 구성, 턴제 전투, 장비·아이템·몬스터. 디스코드에 접속하지 않는 순수 로직이다.

확률은 모두 퍼센트이고, 난수 생성기를 받아서 시험하기 쉽다. 수치(기본값과 관리자가 바꿀 수 있는 값)는 balance.py에 있고,
이 파일의 함수들은 `bal`(Balance)을 받아 그 값을 쓴다. 생략하면 기본값이다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from balance import (BASE_DAMAGE, BASE_DODGE, BOSSES, CLEAR_CANDY, CONSUMABLES, DEFAULT, GEAR, GUARD_BASE,  # noqa: F401  (다시 내보냄)
                     GUARD_CHANCE, GUARD_HEAL, MAX_HP, MONSTERS, REFLECT_BASE, ROOM_PLAN, ROOMS, TICKET_CHANCE,
                     TIER_ORDER, Balance, Consumable, Gear, Monster, reduce_damage)

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


def _spawn(monster: Monster) -> dict:
    return {"key": monster.key, "name": monster.name, "emoji": monster.emoji, "hp": monster.hp, "max_hp": monster.hp,
            "damage": monster.damage, "dodge": monster.dodge, "boss": monster.boss, "stunned": False}


def advance(run: Run, rng, bal: Balance | None = None) -> list[str]:
    """다음 방으로 간다. 마지막 방(10번째)을 마쳤다면 던전이 끝난다."""
    bal = bal or DEFAULT
    if run.room >= ROOMS:
        run.phase, run.enemy, run.kind = "clear", None, ""
        run.events = ["🏆 던전의 끝에 도착했어요!"]
        return run.events
    run.room += 1
    run.kind, run.enemy = roll_room(rng, run.room, bal), None
    if run.kind in ("monster", "boss") and not (bal.bosses if run.kind == "boss" else bal.monsters):
        run.kind = "empty"                               # 관리자가 몬스터를 모두 지웠다면(검사를 통과한 설정이면 없는 일) 빈 방으로 둔다.
    if run.kind in ("monster", "boss"):
        table = bal.bosses if run.kind == "boss" else bal.monsters
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
    else:
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


def use_consumable(run: Run, key: str, bal: Balance | None = None) -> list[str]:
    """아이템 사용. 전투 중이면 한 턴을 쓴 것으로 치고 몬스터가 공격한다."""
    bal = bal or DEFAULT
    item = bal.item_by_key.get(key)
    if item is None:
        raise DungeonError("알 수 없는 아이템이에요.")
    if run.items.get(key, 0) < 1:
        raise DungeonError(f"{item.name}이(가) 없어요.")
    run.items[key] -= 1
    if run.items[key] <= 0:
        del run.items[key]
    if item.full:
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


def equip_pending(run: Run, bal: Balance | None = None) -> list[str]:
    """상자에서 나온 장비를 장착한다. 같은 칸에 있던 장비는 사라진다."""
    had = run.pending_gear
    gear = _pending(run, bal or DEFAULT)
    if gear is None:
        if had:                                          # 그 사이 지워진 장비: 오류로 막지 않고 대기만 풀어 앞으로 갈 수 있게 한다.
            run.events = ["❔ 그 장비는 사라졌어요. 앞으로 나아갈 수 있어요."]
            return run.events
        raise DungeonError("장착할 장비가 없어요.")
    if gear.slot == "shield":
        run.shield = gear.key
    else:
        run.weapon = gear.key
    run.pending_gear = None
    run.events = [f"{gear.emoji} **{gear.name}**을(를) 장착했어요!"]
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


def roll_guard(rng, bal: Balance | None = None) -> bool:
    """방어 버튼의 가드 성공 여부."""
    return rng.random() * 100 < (bal or DEFAULT).guard_chance


def roll_clear_reward(rng, bal: Balance | None = None) -> tuple[int, bool]:
    """클리어 보상: (재화, 히든 칭호 획득권이 나왔는지)."""
    bal = bal or DEFAULT
    candy = rng.randint(*bal.clear_candy)
    return candy, rng.random() * 100 < bal.ticket_chance
