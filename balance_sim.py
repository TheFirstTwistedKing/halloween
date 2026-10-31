"""밸런스 시뮬레이터: 던전을 컴퓨터가 대신 여러 번 돌려 클리어율을 어림잡는다. 디스코드에 접속하지 않는다.

세 가지 '플레이 방식'으로 돌린다. 모두 장비는 기대 공격력이 높은 쪽으로 고르고, 전투 밖에서는 회복 아이템을 알뜰히 쓰고,
위험하면 전투 중에도 쓴다. 실제 유저의 실력과 선택에 따라 결과는 달라질 수 있다(어림값이다).
"""
from __future__ import annotations

import random
from collections import Counter

import dungeon
from balance import Balance, DEFAULT

POLICIES = (("attack", "공격만"), ("mixed", "체력이 60% 아래일 때만 방어"), ("defend", "기절 아닐 땐 항상 방어"))


def _dps(bal: Balance, weapon, dodge: int, boss: bool = False) -> float:
    attack = bal.base_damage + (weapon.attack if weapon else 0)
    crit = (weapon.crit if weapon else 0) / 100
    miss = 0 if boss else (weapon.dodge if weapon and weapon.dodge is not None else dodge)
    return (1 - miss / 100) * attack * (1 + crit)


def _best_heal(run, bal):
    heals = [k for k in run.items if not bal.item_by_key[k].regen]
    return max(heals, key=lambda k: (bal.item_by_key[k].full, bal.item_by_key[k].heal)) if heals else None


def play(rng, policy: str, bal: Balance) -> tuple[bool, int]:
    """던전 한 판. (클리어했는지, 마지막으로 도달한 방)."""
    run = dungeon.new_run(bal=bal)
    dungeon.advance(run, rng, bal)
    for _ in range(5000):                                   # 무한 루프 안전장치
        if run.phase == "dead":
            return False, run.room
        if run.phase == "clear":
            return True, run.room
        if run.pending_gear:
            gear = bal.gear_by_key[run.pending_gear]
            if gear.slot == "weapon":
                held = bal.gear_by_key.get(run.weapon)
                better = _dps(bal, gear, 10) > _dps(bal, held, 10)
            else:
                held = bal.gear_by_key.get(run.shield)
                better = held is None or (gear.guard or 0) > (held.guard or 0)
            (dungeon.equip_pending if better else dungeon.discard_pending)(run)
        elif run.phase == "chest":
            dungeon.open_chest(run, rng, bal)
        elif run.in_battle:
            enemy = run.enemy
            stunned = enemy.get("stunned")
            heal = _best_heal(run, bal)
            if heal and not stunned and run.hp <= enemy["damage"] * 2:
                dungeon.use_consumable(run, heal, bal)
            elif policy == "attack" or stunned:
                dungeon.player_attack(run, rng, bal)
            elif policy == "defend" or run.hp < bal.max_hp * 0.6:
                dungeon.player_defend(run, dungeon.roll_guard(rng, bal), bal)
            else:
                dungeon.player_attack(run, rng, bal)
        elif run.can_advance:
            missing, used = bal.max_hp - run.hp, False
            for key in sorted(run.items, key=lambda k: -bal.item_by_key[k].heal):
                item = bal.item_by_key[key]
                if (item.full and missing >= bal.max_hp * 0.6) or (item.heal and not item.full and item.heal <= missing) \
                        or (item.regen and run.regen_turns == 0 and run.hp <= bal.max_hp * 0.9):
                    dungeon.use_consumable(run, key, bal)
                    used = True
                    break
            if not used:
                dungeon.advance(run, rng, bal)
        else:
            break
    return False, run.room


def simulate(bal: Balance = DEFAULT, runs: int = 1000, seed: int | None = None) -> dict:
    """세 가지 플레이 방식으로 각각 runs판씩 돌린다. {방식: {clear, avg_room, deaths}} (clear는 0~1, deaths는 방 번호별 비율)."""
    out = {}
    for key, label in POLICIES:
        rng = random.Random(None if seed is None else seed + len(out))
        results = [play(rng, key, bal) for _ in range(runs)]
        deaths = Counter(room for won, room in results if not won)
        out[key] = {"label": label, "clear": sum(won for won, _ in results) / runs,
                    "avg_room": sum(room for _, room in results) / runs,
                    "deaths": {room: count / runs for room, count in sorted(deaths.items())}}
    return out
