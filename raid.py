"""레이드: 관리자가 연 거대 보스를 최대 50명이 **기지에 전시한 장비**로 함께 잡는다. 디스코드와 상관없이 저장소(store.db)만 다룬다.

흐름
- 관리자 `/레이드 열기` → 채널에 레이드 화면이 올라가고 [참가]로 모인다(모집). `/레이드 시작`으로 싸움이 시작된다(참가는 싸우는 중에도 된다).
- 참가하는 순간 그 사람의 기지 전시 장비(무기·방패·갑옷)를 그대로(능력치 전부) 들고 싸운다. 장비는 잃지 않는다.
- 각자 [공격]·[방어]·[스킬] 버튼을 누른다. 누른 뒤에는 쿨타임(기본 3초) 동안 기다린다.
  · 공격: 맨손 공격력 + 무기 공격력(무기 크리티컬 확률로 2배).
  · 방어: 다음 보스 공격을 방패 가드(%)만큼 줄이고, 맞은 공격의 반사(%)만큼 되돌려 준다.
  · 스킬: 전시한 무기의 스킬(강타·연타·흡혈·기절탄·관통). 쿨타임은 무기 스킬 쿨타임 × 행동 쿨타임 × 2초.
- 보스는 정한 간격(기본 8초)마다 살아 있는 참가자 몇 명(5명에 1명꼴, 최소 1명)을 공격한다. 갑옷 피해 감소가 적용된다.
  2페이즈와 스킬(강타·자가 수리·방어막·과열)도 붙일 수 있다(보스 공격 N번마다).
- 보스를 쓰러뜨리면 승리: 보상 재화(총액)를 **준 피해 비율대로** 나누고, 참가한 사람 모두에게 참가 보상을 더 준다.
  제한 시간이 지나거나 참가자가 모두 다운되면 실패(보상 없음, 잃는 것도 없음).
"""
from __future__ import annotations

import json
import math
import time

import balance
import guilds
from balance import BOSS_SKILLS, boss_skill_name, reduce_damage
from store import StoreError

MAX_MEMBERS = 50
HP_MAX = 1_000_000_000
REWARD_MAX = 1_000_000_000
LOG_KEEP = 8
OPEN = ("open", "active")


class RaidError(StoreError):
    pass


def _now(now):
    return time.time() if now is None else now


# ---------------------------------------------------------------- 저장
def get(store, raid_id: int):
    return store.db.execute("SELECT * FROM raids WHERE id=?", (raid_id,)).fetchone()


def current(store, guild: int):
    """이 서버에서 모집 중이거나 싸우는 중인 레이드(행). 없으면 None."""
    return store.db.execute("SELECT * FROM raids WHERE guild=? AND state IN ('open','active') ORDER BY id DESC", (guild,)).fetchone()


def by_message(store, message_id: int):
    return store.db.execute("SELECT * FROM raids WHERE message=?", (message_id,)).fetchone()


def running(store) -> list:
    return store.db.execute("SELECT * FROM raids WHERE state IN ('open','active') ORDER BY id").fetchall()


def boss(row) -> dict:
    return json.loads(row["data"])


def _save(store, raid_id: int, data: dict, **columns) -> None:
    data["dirty"] = True
    sets = ", ".join(f"{k}=?" for k in columns)
    with store.db:
        store.db.execute(f"UPDATE raids SET data=?{', ' + sets if sets else ''} WHERE id=?", (json.dumps(data, ensure_ascii=False), *columns.values(), raid_id))


def members(store, raid_id: int) -> list[dict]:
    """참가자 [{user, damage, ...능력치·상태}] (참가 순)."""
    out = []
    for row in store.db.execute("SELECT user, damage, data FROM raid_members WHERE raid=? ORDER BY rowid", (raid_id,)):
        entry = json.loads(row["data"])
        entry.update(user=row["user"], damage=row["damage"])
        out.append(entry)
    return out


def _save_member(store, raid_id: int, member: dict) -> None:
    data = {k: v for k, v in member.items() if k not in ("user", "damage")}
    with store.db:
        store.db.execute("UPDATE raid_members SET data=?, damage=? WHERE raid=? AND user=?",
                         (json.dumps(data, ensure_ascii=False), member["damage"], raid_id, member["user"]))


def _log(data: dict, text: str) -> None:
    data["log"] = (data.get("log", []) + [text])[-LOG_KEEP:]


# ---------------------------------------------------------------- 열기·시작·취소
def check_boss(*, name: str, emoji: str, hp: int, damage: int, phase2_hp: int = 0, phase2_damage: int = 0, skill: str | None = None,
               skill_every: int = 0, skill_power: int = 0, skill_turns: int = 0, skill_name: str | None = None) -> dict:
    """레이드 보스 설정을 검사한다. 잘못되면 RaidError."""
    name = (name or "").strip()
    if not 1 <= len(name) <= balance.NAME_MAX or any(c in name for c in "@#<>`*_~|"):
        raise RaidError(f"보스 이름은 1~{balance.NAME_MAX}자로, @ # < > ` * _ ~ | 없이 써 주세요.")
    if not 1 <= hp <= HP_MAX or not 0 <= damage <= 10_000_000 or not 0 <= phase2_hp <= HP_MAX or not 0 <= phase2_damage <= 10_000_000:
        raise RaidError("체력·공격 값이 범위를 벗어났어요.")
    pattern, problem = balance._check_boss_pattern(dict(skill=skill, skill_every=skill_every, skill_power=skill_power, skill_turns=skill_turns,
                                                        skill_name=skill_name))
    if problem:
        raise RaidError(problem)
    return {"name": name, "emoji": emoji, "hp": hp, "max_hp": hp, "damage": damage, "phase": 1, "phase2_hp": phase2_hp,
            "phase2_damage": phase2_damage if phase2_hp else 0, "skill": pattern["skill"], "skill_every": pattern["skill_every"],
            "skill_power": pattern["skill_power"], "skill_turns": pattern["skill_turns"], "skill_name": pattern["skill_name"]}


def open_raid(store, guild: int, channel: int, boss_data: dict, *, minutes: int = 10, reward: int = 0, min_reward: int = 0,
              attack_every: int = 8, cooldown: int = 3, now=None) -> int:
    """레이드를 연다(모집). 서버마다 한 번에 하나. 레이드 id."""
    if current(store, guild) is not None:
        raise RaidError("이미 모집 중이거나 진행 중인 레이드가 있어요. 먼저 끝내거나 `/레이드 취소`를 해 주세요.")
    if not 1 <= minutes <= 180 or not 0 <= reward <= REWARD_MAX or not 0 <= min_reward <= REWARD_MAX \
            or not 3 <= attack_every <= 120 or not 1 <= cooldown <= 60:
        raise RaidError("제한 시간·보상·간격 값이 범위를 벗어났어요.")
    data = dict(boss_data, minutes=minutes, reward=reward, min_reward=min_reward, attack_every=attack_every, cooldown=cooldown,
                turn=0, barrier=0, barrier_turns=0, burn=0, burn_turns=0, stunned=False, log=[], paid=False)
    with store.db:
        raid_id = store.db.execute("INSERT INTO raids(guild, channel, state, created, data) VALUES(?,?,'open',?,?)",
                                   (guild, channel, _now(now), json.dumps(data, ensure_ascii=False))).lastrowid
    return raid_id


def set_message(store, raid_id: int, message_id: int) -> None:
    with store.db:
        store.db.execute("UPDATE raids SET message=? WHERE id=?", (message_id, raid_id))


def start(store, guild: int, now=None):
    """모집 중인 레이드를 시작한다: 지금부터 제한 시간 동안 싸운다. 참가자가 한 명도 없으면 시작하지 않는다."""
    row = current(store, guild)
    if row is None or row["state"] != "open":
        raise RaidError("모집 중인 레이드가 없어요. 먼저 `/레이드 열기`를 해 주세요.")
    if not members(store, row["id"]):
        raise RaidError("아직 참가한 사람이 없어요. [참가] 버튼으로 모인 뒤 시작해 주세요.")
    data, t = boss(row), _now(now)
    data["next_attack"] = t + data["attack_every"]
    data["items"] = [dict(item) for item in store.settings(guild)["raid_items"]]      # 시작할 때의 회복 아이템(도중에 설정을 바꿔도 이 레이드는 그대로)
    for member in members(store, row["id"]):
        _give_items(data, member)
        _save_member(store, row["id"], member)
    _log(data, f"🔥 **레이드 시작!** {data['emoji']} **{data['name']}**을(를) {data['minutes']}분 안에 쓰러뜨려요!")
    if data["items"]:
        _log(data, "🧪 모두에게 회복 아이템을 나눠 줬어요: " + item_list(data["items"]))
    _save(store, row["id"], data, state="active", ends=t + data["minutes"] * 60)
    return get(store, row["id"])


def _give_items(data: dict, member: dict) -> None:
    """참가자에게 이 레이드의 회복 아이템을 준다(종류마다 정한 개수)."""
    member["items"] = {str(item["id"]): item["amount"] for item in data.get("items", [])}


def item_list(items) -> str:
    return " · ".join(f"{i['emoji']} {i['name']} ×{i['amount']}(HP +{i['heal']:,})" for i in items)


def cancel(store, guild: int):
    row = current(store, guild)
    if row is None:
        raise RaidError("모집 중이거나 진행 중인 레이드가 없어요.")
    data = boss(row)
    _log(data, "🚫 관리자가 레이드를 취소했어요.")
    _save(store, row["id"], data, state="cancelled")
    return get(store, row["id"])


# ---------------------------------------------------------------- 참가
def member_stats(store, guild: int, user: int) -> dict:
    """참가자의 능력치: 기지에 전시한 장비 그대로(능력치 전부). 길드 버프 '장비 강화'도 붙는다."""
    bal = store.balance(guild)
    shown = store.base(guild, user)
    weapon, shield, armor = (bal.gear_by_key.get(shown.get(slot)) for slot in ("weapon", "shield", "armor"))
    weapon = weapon if weapon is not None and weapon.slot == "weapon" else None
    shield = shield if shield is not None and shield.slot == "shield" else None
    armor = armor if armor is not None and armor.slot == "armor" else None
    gear_buff = guilds.buff_of(store, guild, user) == "gear"
    attack = bal.base_damage + (weapon.attack + (balance.GUILD_GEAR_ATTACK if gear_buff else 0) if weapon else 0)
    guard = (shield.guard if shield and shield.guard is not None else bal.guard_base) + (balance.GUILD_GEAR_GUARD if gear_buff and shield else 0)
    reflect = shield.reflect if shield and shield.reflect is not None else bal.reflect_base
    reduction = (armor.reduction + (balance.GUILD_GEAR_REDUCTION if gear_buff else 0)) if armor else 0
    return {"hp": bal.max_hp, "max_hp": bal.max_hp, "attack": attack, "crit": weapon.crit if weapon else 0,
            "guard": min(100, guard), "reflect": min(500, reflect), "reduction": min(balance.ARMOR_MAX, reduction),
            "skill": weapon.skill if weapon else None, "skill_power": weapon.skill_power if weapon else 0,
            "skill_cooldown": weapon.skill_cooldown if weapon else 0, "skill_name": weapon.skill_name if weapon else None,
            "ult": weapon.ult if weapon else None, "ult_power": weapon.ult_power if weapon else 0, "ult_name": weapon.ult_name if weapon else None,
            "gear": [f"{g.emoji} {g.name}" for g in (weapon, shield, armor) if g is not None],
            "next_act": 0.0, "next_skill": 0.0, "guarding": False, "down": False,
            "ult_gauge": 0, "awaken_gauge": 0, "awakened": False, "items": {}}


def join(store, guild: int, user: int, now=None) -> dict:
    """참가한다(모집 중이거나 싸우는 중일 때, 최대 50명). 참가자 정보."""
    row = current(store, guild)
    if row is None:
        raise RaidError("지금 열린 레이드가 없어요.")
    if store.db.execute("SELECT 1 FROM raid_members WHERE raid=? AND user=?", (row["id"], user)).fetchone():
        raise RaidError("이미 참가했어요.")
    if len(members(store, row["id"])) >= MAX_MEMBERS:
        raise RaidError(f"레이드 인원이 꽉 찼어요(최대 {MAX_MEMBERS}명).")
    stats = member_stats(store, guild, user)
    if row["state"] == "active":                            # 싸우는 중에 들어오면 회복 아이템도 바로 받는다
        _give_items(boss(row), stats)
    with store.db:
        store.db.execute("INSERT INTO raid_members VALUES(?,?,0,?)", (row["id"], user, json.dumps(stats, ensure_ascii=False)))
    data = boss(row)
    _log(data, f"🙋 <@{user}> 참가! ({' · '.join(stats['gear']) or '👊 맨손'})")
    _save(store, row["id"], data)
    return stats


# ---------------------------------------------------------------- 싸움
def _hit_boss(data: dict, member: dict, damage: int, *, pierce: bool = False) -> tuple[int, list[str]]:
    """보스에게 피해를 준다(방어막 적용, 관통이면 무시). (실제 피해, 일어난 일)."""
    if data["barrier_turns"] > 0 and not pierce:
        damage = reduce_damage(damage, data["barrier"])
    damage = max(0, min(damage, data["hp"]))
    data["hp"] -= damage
    member["damage"] += damage
    events = []
    if data["hp"] <= 0 and data["phase"] == 1 and data["phase2_hp"] > 0:
        data.update(phase=2, hp=data["phase2_hp"], max_hp=data["phase2_hp"], barrier_turns=0, stunned=False)
        if data["phase2_damage"]:
            data["damage"] = data["phase2_damage"]
        events.append(f"⚠️ {data['emoji']} **{data['name']}**의 1페이즈 체력 바가 다 깎였어요… 🔴 **2페이즈 돌입!** (HP {data['hp']:,})")
    return damage, events


def act(store, guild: int, user: int, action: str, rng, now=None) -> str:
    """참가자의 행동(attack | defend | skill | ult | special | item:<아이템 id>). 화면 기록에 남고, 누른 사람에게 보여 줄 한 줄을 돌려준다."""
    t = _now(now)
    row = current(store, guild)
    if row is None or row["state"] != "active":
        raise RaidError("싸우는 중인 레이드가 없어요." + (" 관리자가 `/레이드 시작`을 하면 싸울 수 있어요." if row is not None else ""))
    tick(store, guild, rng, t)                             # 밀린 보스 공격부터 처리한다
    row = get(store, row["id"])
    if row["state"] != "active":
        raise RaidError("레이드가 끝났어요.")
    mine = next((m for m in members(store, row["id"]) if m["user"] == user), None)
    if mine is None:
        raise RaidError("먼저 [참가]를 눌러 주세요.")
    if mine["down"]:
        raise RaidError("다운돼서 더 싸울 수 없어요. 동료들을 응원해 주세요!")
    if t < mine["next_act"]:
        raise RaidError(f"{math.ceil(mine['next_act'] - t)}초 뒤에 다시 행동할 수 있어요.")
    data = boss(row)
    bal = store.balance(guild)                               # 궁극기·각성 수치는 /각성설정을 따른다
    awake = bal.awaken_bonus if mine.get("awakened") else 0
    who = f"<@{user}>"
    if action == "attack":
        damage = mine["attack"] * (100 + awake) // 100
        crit = mine["crit"] and rng.random() * 100 < mine["crit"]
        dealt, extra = _hit_boss(data, mine, damage * 2 if crit else damage)
        line = f"{'💥 크리티컬! ' if crit else '⚔️ '}{who} → **{dealt:,}** 피해"
    elif action == "defend":
        mine["guarding"] = True
        dealt, extra = 0, []
        line = f"🛡️ {who} 방어 태세 (다음 공격 {mine['guard']}% 감소 · 반사 {mine['reflect']}%)"
    elif action == "skill":
        if not mine["skill"]:
            raise RaidError("기지에 전시한 무기에 스킬이 없어요.")
        if t < mine["next_skill"]:
            raise RaidError(f"스킬은 {math.ceil(mine['next_skill'] - t)}초 뒤에 쓸 수 있어요.")
        extra, line = _technique(data, mine, mine["skill"], mine["skill_power"], mine.get("skill_name"), f"✨ {who}의", awake)
        mine["next_skill"] = t + max(1, mine["skill_cooldown"]) * data["cooldown"] * 2
    elif action == "ult":
        if not mine.get("ult"):
            raise RaidError("기지에 전시한 무기에 궁극기가 없어요.")
        if mine.get("ult_gauge", 0) < 100:
            raise RaidError(f"궁극기 게이지가 {mine.get('ult_gauge', 0)}%예요. 공격·방어를 하면 차요.")
        extra, line = _technique(data, mine, mine["ult"], mine["ult_power"], mine.get("ult_name"), f"🟡 {who}의 궁극기", awake)
        mine["ult_gauge"] = 0
        mine["awaken_gauge"] = min(100, mine.get("awaken_gauge", 0) + bal.awaken_charge)
        if mine["awaken_gauge"] >= 100 and not mine.get("awakened"):
            mine["awakened"] = True
            line += " · ⚡ **각성!**"
    elif action == "special":
        if not mine.get("awakened"):
            raise RaidError(f"각성 중에만 쓸 수 있어요. 각성 게이지 {mine.get('awaken_gauge', 0)}% (궁극기를 쓰면 차요).")
        extra, line = _technique(data, mine, bal.special, bal.special_power, bal.special_name, f"⚡ {who}의 각성 특수 스킬", awake)
        if bal.special_ends:
            mine["awakened"], mine["awaken_gauge"] = False, 0
            line += " · 각성 끝"
    elif action.startswith("item:"):
        item = next((i for i in data.get("items", []) if str(i["id"]) == action[5:]), None)
        if item is None:
            raise RaidError("이 레이드에 없는 아이템이에요.")
        left = mine.get("items", {}).get(action[5:], 0)
        if left <= 0:
            raise RaidError(f"{item['emoji']} {item['name']}이(가) 남아 있지 않아요.")
        if mine["hp"] >= mine["max_hp"]:
            raise RaidError("HP가 이미 가득 차 있어요.")
        healed = min(mine["max_hp"], mine["hp"] + item["heal"]) - mine["hp"]
        mine["hp"] += healed
        mine["items"][action[5:]] = left - 1
        extra, line = [], f"🧪 {who} {item['emoji']} {item['name']} → HP +{healed:,} (남은 {left - 1}개)"
    else:
        raise RaidError("알 수 없는 행동이에요.")
    if action in ("attack", "defend") and mine.get("ult") and mine.get("ult_gauge", 0) < 100:     # 궁극기 게이지: 공격·방어 한 번마다
        mine["ult_gauge"] = min(100, mine.get("ult_gauge", 0) + bal.ult_charge)
        if mine["ult_gauge"] >= 100:
            line += " · 🟡 궁극기 준비!"
    mine["next_act"] = t + data["cooldown"]
    _log(data, line)
    for e in extra:
        _log(data, e)
    _save_member(store, row["id"], mine)
    _save(store, row["id"], data)
    _check_end(store, guild, row["id"], t)
    return line


def _technique(data: dict, mine: dict, kind: str, power: int, custom: str | None, title: str, awake: int) -> tuple[list[str], str]:
    """스킬·궁극기·각성 특수 스킬 한 번(종류는 무기 스킬과 같다). 각성 중이면 피해 보너스. (일어난 일, 기록 한 줄)."""
    base = mine["attack"] * (100 + awake) // 100
    damage = {"power": base * power // 100, "multi": base * power, "drain": base, "stun": base * power // 100, "pierce": base * power // 100}[kind]
    dealt, extra = _hit_boss(data, mine, damage, pierce=kind == "pierce")
    line = f"{title} **[{balance.weapon_skill_name(kind, custom)}]** → **{dealt:,}** 피해"
    if kind == "drain":
        healed = min(mine["max_hp"], mine["hp"] + dealt * power // 100) - mine["hp"]
        mine["hp"] += healed
        line += f" · 🩸 HP +{healed:,}"
    if kind == "stun" and data["hp"] > 0:
        data["stunned"] = True
        line += " · 💫 보스 기절(다음 공격을 못 해요)"
    return extra, line


def _boss_attack(store, raid_id: int, data: dict, rng) -> None:
    """보스 공격 한 번: 스킬 차례면 스킬, 아니면 살아 있는 참가자 몇 명을 때린다."""
    alive = [m for m in members(store, raid_id) if not m["down"]]
    if data["barrier_turns"] > 0:
        data["barrier_turns"] -= 1
    if data["burn_turns"] > 0:                              # 과열: 살아 있는 모두가 맞는다(갑옷 적용)
        for m in alive:
            m["hp"] = max(0, m["hp"] - reduce_damage(data["burn"], m["reduction"]))
        data["burn_turns"] -= 1
        _log(data, f"🔥 과열! 모두 {data['burn']:,} 피해 (남은 {data['burn_turns']}번)")
    if data["stunned"]:
        data["stunned"] = False
        _log(data, f"💫 {data['emoji']} {data['name']}은(는) 기절해서 공격하지 못했어요!")
        _finish_hits(store, raid_id, alive, data)
        return
    data["turn"] += 1
    raw, label = data["damage"], ""
    skill = data["skill"]
    if skill and data["skill_every"] and data["turn"] % data["skill_every"] == 0:
        name = boss_skill_name(skill, data.get("skill_name"))
        if skill == "heal":
            gained = min(data["max_hp"], data["hp"] + data["max_hp"] * data["skill_power"] // 100) - data["hp"]
            data["hp"] += gained
            _log(data, f"🌀 보스의 스킬 **[{name}]**! HP +{gained:,}")
            _finish_hits(store, raid_id, alive, data)
            return
        if skill == "barrier":
            data["barrier"], data["barrier_turns"] = data["skill_power"], data["skill_turns"]
            _log(data, f"🌀 보스의 스킬 **[{name}]**! {data['skill_turns']}번 동안 받는 피해 {data['skill_power']}% 감소")
            _finish_hits(store, raid_id, alive, data)
            return
        if skill == "burn":
            data["burn"], data["burn_turns"] = data["skill_power"], data["skill_turns"]
            _log(data, f"🌀 보스의 스킬 **[{name}]**! 과열이 시작돼요({data['skill_turns']}번)")
            _finish_hits(store, raid_id, alive, data)
            return
        raw, label = raw * data["skill_power"] // 100, f"스킬 **[{name}]**! "
    if alive:
        count = min(len(alive), max(1, math.ceil(len(alive) / 5)))
        targets = rng.sample(alive, count)
        hits = []
        for m in targets:
            if m["guarding"]:
                taken = reduce_damage(raw, m["guard"])
                reflected = raw * m["reflect"] // 100
                if reflected and data["hp"] > 0:
                    _dealt, extra = _hit_boss(data, m, reflected, pierce=True)
                    for e in extra:
                        _log(data, e)
            else:
                taken = reduce_damage(raw, m["reduction"])
            m["hp"] = max(0, m["hp"] - taken)
            hits.append(f"<@{m['user']}> -{taken:,}" + (" 🛡️" if m["guarding"] else ""))
        _log(data, f"👹 {data['emoji']} {label}공격 → " + ", ".join(hits))
    _finish_hits(store, raid_id, alive, data)


def _finish_hits(store, raid_id: int, alive: list[dict], data: dict) -> None:
    """보스 공격이 끝나면: 방어 태세는 풀리고, 체력이 0이 된 사람은 다운된다."""
    for m in alive:
        m["guarding"] = False
        if m["hp"] <= 0 and not m["down"]:
            m["down"] = True
            _log(data, f"💀 <@{m['user']}> 다운!")
        _save_member(store, raid_id, m)


def tick(store, guild: int, rng, now=None) -> str | None:
    """밀린 보스 공격을 처리하고 끝났는지 본다. 끝났다면 결과 상태('won' | 'lost'), 아니면 None."""
    t = _now(now)
    row = current(store, guild)
    if row is None or row["state"] != "active":
        return None
    data = boss(row)
    rounds = 0
    while data.get("next_attack", t + 1) <= t and data["hp"] > 0 and rounds < 50:
        attack_at = data["next_attack"]
        if attack_at > row["ends"]:
            break
        _boss_attack(store, row["id"], data, rng)
        data["next_attack"] = attack_at + data["attack_every"]
        rounds += 1
        if all(m["down"] for m in members(store, row["id"])):
            break
    if rounds:
        _save(store, row["id"], data)
    return _check_end(store, guild, row["id"], t)


def _check_end(store, guild: int, raid_id: int, now: float) -> str | None:
    row = get(store, raid_id)
    if row["state"] != "active":
        return None
    data = boss(row)
    everyone = members(store, raid_id)
    if data["hp"] <= 0:
        state, text = "won", f"🏆 **{data['name']}**을(를) 쓰러뜨렸어요! 레이드 성공!"
    elif everyone and all(m["down"] for m in everyone):
        state, text = "lost", "💀 참가자가 모두 다운됐어요… 레이드 실패."
    elif now >= row["ends"]:
        state, text = "lost", "⏰ 제한 시간이 끝났어요… 레이드 실패."
    else:
        return None
    _log(data, text)
    _save(store, raid_id, data, state=state)
    if state == "won":
        pay(store, guild, raid_id)
    return state


def shares(data: dict, everyone: list[dict]) -> dict[int, int]:
    """승리 보상: 총액을 준 피해 비율대로(내림) + 참가 보상. {사람: 받을 양}."""
    total = sum(m["damage"] for m in everyone)
    out = {}
    for m in everyone:
        part = data["reward"] * m["damage"] // total if total else 0
        out[m["user"]] = part + data["min_reward"]
    return out


def pay(store, guild: int, raid_id: int) -> dict[int, int]:
    """이긴 레이드의 보상을 한 번만 준다(최대 보유량을 넘는 만큼은 버려진다). {사람: 실제로 받은 양}."""
    row = get(store, raid_id)
    data = boss(row)
    if row["state"] != "won" or data.get("paid"):
        return {}
    plan = shares(data, members(store, raid_id))
    cap = store.currency(guild).max
    paid = {}
    with store.db:
        for user, amount in plan.items():
            store._ensure_wallet(guild, user)
            old = store.wallet(guild, user)
            new = min(cap, old + amount)
            store.db.execute("UPDATE wallets SET candy=? WHERE guild=? AND user=?", (new, guild, user))
            paid[user] = new - old
    data["paid"], data["payout"] = True, {str(u): n for u, n in paid.items()}
    _save(store, raid_id, data)
    return paid


def mark_clean(store, raid_id: int) -> None:
    row = get(store, raid_id)
    data = boss(row)
    data["dirty"] = False
    with store.db:
        store.db.execute("UPDATE raids SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), raid_id))


def status_text(store, guild: int, row, user: int, now=None) -> str:
    """참가자 본인의 상태(누른 사람에게만 보여 준다): HP·쿨타임·궁극기·각성 게이지·회복 아이템."""
    mine = next((m for m in members(store, row["id"]) if m["user"] == user), None)
    if mine is None:
        raise RaidError("아직 참가하지 않았어요. [참가]를 눌러 주세요.")
    t, data = _now(now), boss(row)
    lines = [f"❤️ HP {mine['hp']:,}/{mine['max_hp']:,}" + (" 💀 다운" if mine["down"] else ""),
             f"🎒 {' · '.join(mine['gear']) or '👊 맨손'} · 공격 {mine['attack']:,}",
             "⏱️ 지금 행동할 수 있어요" if t >= mine["next_act"] else f"⏱️ {math.ceil(mine['next_act'] - t)}초 뒤에 행동"]
    if mine["skill"]:
        name = balance.weapon_skill_name(mine["skill"], mine.get("skill_name"))
        lines.append(f"✨ [{name}] " + ("준비됨" if t >= mine["next_skill"] else f"{math.ceil(mine['next_skill'] - t)}초 뒤"))
    if mine.get("ult"):
        lines.append(f"🟡 궁극기 [{balance.weapon_skill_name(mine['ult'], mine.get('ult_name'))}] 게이지 {mine.get('ult_gauge', 0)}%")
    lines.append("⚡ **각성 중!** [⚡ 각성] 버튼을 쓸 수 있어요" if mine.get("awakened") else f"⚡ 각성 게이지 {mine.get('awaken_gauge', 0)}%")
    items = [f"{i['emoji']} {i['name']} ×{mine.get('items', {}).get(str(i['id']), 0)}" for i in data.get("items", [])]
    lines.append("🧪 " + (" · ".join(items) if items else ("시작하면 받아요" if row["state"] == "open" else "없음")))
    return "\n".join(lines)


def time_left(row, now=None) -> str:
    seconds = max(0, int(row["ends"] - _now(now))) if row["ends"] else 0
    return f"{seconds // 60}분 {seconds % 60}초"


def skill_line(data: dict) -> str:
    return balance.skill_text(data["skill"], data["skill_every"], data["skill_power"], data["skill_turns"], data.get("skill_name")) if data["skill"] else ""

