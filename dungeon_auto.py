"""빠른진행: 컴퓨터가 던전을 대신 진행할 때 다음에 할 행동을 고른다. 디스코드에 접속하지 않는 순수 규칙이라 그대로 시험할 수 있다.

balance_sim의 '체력이 60% 아래일 때만 방어' 방식을 따른다: 기본은 공격하고, HP가 60% 아래면 방어하고, 위험하면 회복 아이템을 쓴다.
더 좋은 장비만 장착하고(아니면 버린다), 싸움 밖에서 HP가 많이 빠졌으면 회복 아이템을 알뜰하게 쓴다. 지금 쓸 수 있는 개조 소모품·재료는
무기가 더 강해질 때만 쓴다. 장비·소모품을 파는 일은 하지 않는다.
"""
from __future__ import annotations

from balance import Balance, Consumable, Gear
from dungeon import Run, can_use_now, mod_target, skill_ready

DEFEND_BELOW = 60          # HP가 최대의 이 %보다 낮으면 방어한다
ASSUMED_DODGE = 10         # 장비 비교에 쓰는 잡몹 회피(%)


def _dps(bal: Balance, weapon: Gear | None) -> float:
    attack = bal.base_damage + (weapon.attack if weapon else 0)
    crit = (weapon.crit if weapon else 0) / 100
    miss = weapon.dodge if weapon and weapon.dodge is not None else ASSUMED_DODGE
    return (1 - miss / 100) * attack * (1 + crit)


def _shield_score(bal: Balance, shield: Gear | None) -> float:
    guard = shield.guard if shield and shield.guard is not None else bal.guard_base
    chance = shield.guard_chance if shield and shield.guard_chance is not None else bal.guard_chance
    return guard * chance / 100


def _plain_heals(run: Run, bal: Balance) -> list[Consumable]:
    out = []
    for key in run.items:
        item = bal.item_by_key.get(key)
        if item and not item.material and not item.modify and (item.full or item.heal or item.regen) and not item.self_damage and can_use_now(run, item, bal):
            out.append(item)
    return out


def _stronger(bal: Balance, held: Gear | None, target: Gear) -> bool:
    """target이 held보다 강한가: 무기는 기대 공격력, 방패는 가드 점수로 비교한다."""
    if target.slot == "weapon":
        return _dps(bal, target) > _dps(bal, held)
    if target.slot == "armor":
        return target.reduction > (held.reduction if held else 0)
    return _shield_score(bal, target) > _shield_score(bal, held)


def _upgrade_item(run: Run, bal: Balance) -> str | None:
    """지금 써서 들고 있는 장비(무기·방패)가 더 강해지는 개조 소모품(고정 개조·재료 레시피)이 있으면 그 key."""
    for key in run.items:
        item = bal.item_by_key.get(key)
        if item is None or not can_use_now(run, item, bal):
            continue
        if item.material:
            held, target = mod_target(run, key, bal)
        elif item.modify:
            held, target = bal.gear_by_key.get(run.weapon), bal.gear_by_key.get(item.modify)
        else:
            continue
        if target is not None and (held is None or target.key != held.key) and target.slot in ("weapon", "shield", "armor") and _stronger(bal, held, target):
            if item.modify and target.slot != "weapon":
                continue
            return key
    return None


def choose(run: Run, bal: Balance) -> tuple[str, str | None]:
    """지금 상태에서 컴퓨터가 할 행동 (행동 이름, 인자). 행동 이름은 dungeon_service.ACTIONS의 것이다."""
    if run.phase == "summit":                            # 꼭대기: 컴퓨터는 위험을 늘리지 않고 클리어하고 끝낸다(무한모드는 직접 고른다).
        return "finish", None
    if run.pending_gear:
        gear = bal.gear_by_key.get(run.pending_gear)
        if gear is None:
            return "discard", None
        if gear.slot == "weapon":
            better = _dps(bal, gear) > _dps(bal, bal.gear_by_key.get(run.weapon))
        elif gear.slot == "armor":
            better = _stronger(bal, bal.gear_by_key.get(run.armor), gear)
        else:
            better = _shield_score(bal, gear) > _shield_score(bal, bal.gear_by_key.get(run.shield))
        return ("equip" if better else "discard"), None
    if run.phase == "chest":
        return "chest", None
    heals = _plain_heals(run, bal)
    if run.in_battle:
        enemy = run.enemy
        stunned = enemy.get("stunned")
        healers = [i for i in heals if not i.regen]
        if healers and not stunned and run.hp <= enemy["damage"] * 2:
            return "item", max(healers, key=lambda i: (i.full, i.heal)).key
        ready = skill_ready(run, bal)
        for which in ("special", "ult", "skill"):                    # 쓸 수 있는 기술은 아끼지 않고 쓴다(센 것부터)
            if ready[which]:
                return which, None
        if stunned:
            return "attack", None
        return ("defend" if run.hp * 100 < bal.max_hp * DEFEND_BELOW else "attack"), None
    upgrade = _upgrade_item(run, bal)
    if upgrade:
        return "item", upgrade
    missing = bal.max_hp - run.hp
    for item in sorted(heals, key=lambda i: -i.heal):
        if (item.full and missing >= bal.max_hp * 0.6) or (item.heal and not item.full and item.heal <= missing) \
                or (item.regen and run.regen_turns == 0 and run.hp <= bal.max_hp * 0.9):
            return "item", item.key
    return "advance", None
