"""던전 콘텐츠(몬스터·보스·장비·소모품) 편집 규칙. 디스코드에 접속하지 않는 순수 로직이라 그대로 시험할 수 있다.

서버 설정의 `dungeon_content`에는 관리자가 손댄 구역만 들어 있고({구역: [개체 사전, ...]}), 구역이 있으면 기본 표 대신 그것을 쓴다.
처음 손댈 때 기본 표(관리자가 /던전설정으로 바꾼 숫자 포함)를 통째로 복사해 와서 그 위에서 더하고 빼고 바꾼다.
"""
from __future__ import annotations

import secrets

import balance
from balance import SECTION_LABELS, SECTION_LIMITS, SECTIONS, TIER_ORDER, Balance, check_entity, entity_dict
from store import Store, valid_emoji

# 구역별로 /던전설정 숫자 항목의 이름표(구역을 직접 편집하면 그 숫자는 쓰이지 않으므로 같이 정리한다)
LEGACY_PREFIX = {"monsters": "몬스터.", "bosses": "보스.", "gear": "장비.", "consumables": "아이템.", "secrets": "시크릿보스."}
# 같은 이름이 겹치면 안 되는 범위(몬스터와 보스는 같은 이름을 쓰면 헷갈린다)
NAME_SCOPE = {"monsters": ("monsters", "bosses", "secrets"), "bosses": ("monsters", "bosses", "secrets"), "secrets": ("monsters", "bosses", "secrets"),
              "gear": ("gear",), "consumables": ("consumables",)}


class ContentError(Exception):
    """사용자에게 그대로 보여줘도 되는 오류 문장."""


def current(store: Store, guild: int, section: str) -> list[dict]:
    """지금 이 서버가 쓰는 구역의 개체 목록(사전). 손댄 적이 없으면 기본 표(+숫자 덮어쓰기)다."""
    bal = store.balance(guild)
    return [entity_dict(e) for e in getattr(bal, section)]


def find(store: Store, guild: int, sections, name: str):
    """이름(대소문자 무시)으로 개체를 찾는다. (구역, 사전) 또는 None."""
    needle = name.strip().lower()
    for section in sections:
        for data in current(store, guild, section):
            if data["name"].lower() == needle:
                return section, data
    return None


def _commit(store: Store, guild: int, section: str, items: list[dict]) -> None:
    """검사한 뒤 저장한다. 게임이 깨지는 설정이면(나올 몬스터가 없음 등) 저장하지 않는다."""
    cfg = store.settings(guild)
    content = {**cfg["dungeon_content"], section: items}
    overrides = {k: v for k, v in cfg["dungeon"].items() if not k.startswith(LEGACY_PREFIX[section])}
    problem = balance.validate(balance.clean(overrides), content)
    if problem:
        raise ContentError(problem)
    store.update_settings(guild, dungeon_content=content, dungeon=overrides)


def _check_secret_refs(store: Store, guild: int, section: str, entity) -> None:
    """시크릿 보스가 떨어뜨릴 장비·아이템이 지금 실제로 있는지 본다."""
    if section != "secrets":
        return
    bal = store.balance(guild)
    if entity.drop_gear and entity.drop_gear not in bal.gear_by_key:
        raise ContentError("드롭 장비가 없어요. 이름은 자동완성에서 골라 주세요.")
    if entity.drop_item and entity.drop_item not in bal.item_by_key:
        raise ContentError("드롭 아이템이 없어요. 이름은 자동완성에서 골라 주세요.")


def _check(section: str, data: dict):
    entity, problem = check_entity(section, data)
    if problem:
        raise ContentError(problem)
    if not valid_emoji(entity.emoji):
        raise ContentError("이모지는 😀 같은 이모지 하나이거나 서버 이모지여야 해요.")
    return entity


def _unique_name(store: Store, guild: int, section: str, name: str, ignore_key: str | None = None) -> None:
    for sec in NAME_SCOPE[section]:
        for data in current(store, guild, sec):
            if data["name"].lower() == name.strip().lower() and data["key"] != ignore_key:
                raise ContentError(f"같은 이름의 {SECTION_LABELS[sec]}이(가) 이미 있어요.")


def add(store: Store, guild: int, section: str, data: dict):
    """새 개체를 더한다. key는 자동으로 만든다. 더해진 개체를 돌려준다."""
    items = current(store, guild, section)
    if len(items) >= SECTION_LIMITS[section]:
        raise ContentError(f"{SECTION_LABELS[section]}은(는) 최대 {SECTION_LIMITS[section]}개까지예요.")
    data = {**data, "name": str(data.get("name", "")).strip()}
    _unique_name(store, guild, section, data["name"])
    keys = {d["key"] for sec in SECTIONS for d in current(store, guild, sec)}
    key = "x" + secrets.token_hex(3)
    while key in keys:
        key = "x" + secrets.token_hex(3)
    entity = _check(section, {**data, "key": key})
    _check_secret_refs(store, guild, section, entity)
    _commit(store, guild, section, items + [entity_dict(entity)])
    return getattr(store.balance(guild), section)[-1]


def edit(store: Store, guild: int, sections, name: str, changes: dict):
    """이름으로 찾은 개체의 값을 바꾼다. 바꿀 값만 담은 changes를 덮어쓴다. (구역, 바뀐 개체)를 돌려준다."""
    hit = find(store, guild, sections, name)
    if hit is None:
        raise ContentError("그런 항목이 없어요. 이름은 자동완성에서 골라 주세요.")
    section, data = hit
    changes = {k: v for k, v in changes.items() if v is not None or k in ("dodge", "guard", "reflect", "guard_chance", "modify", "drop_gear", "drop_item")}
    if not changes:
        raise ContentError("바꿀 내용을 하나 이상 적어 주세요.")
    new = {**data, **changes}
    if "name" in changes:
        new["name"] = str(new["name"]).strip()
        _unique_name(store, guild, section, new["name"], ignore_key=data["key"])
    entity = _check(section, new)
    _check_secret_refs(store, guild, section, entity)
    if section == "consumables" and not entity.material and any(m["item"] == data["key"] for m in store.settings(guild)["weapon_mods"]):
        raise ContentError(f"**{data['name']}**은(는) 장비 개조 레시피(`/장비개조 목록`)의 재료라서 개조 재료가 아니게 바꿀 수 없어요. 먼저 그 레시피를 지워 주세요.")
    items = [entity_dict(entity) if d["key"] == data["key"] else d for d in current(store, guild, section)]
    _commit(store, guild, section, items)
    return section, next(e for e in getattr(store.balance(guild), section) if e.key == data["key"])


def delete(store: Store, guild: int, sections, name: str):
    """이름으로 찾은 개체를 지운다. 이미 던전 안에 있는 사람에게는 영향이 없다(몬스터는 이미 나왔고, 장비·소모품은 쓸 수 없게 된다)."""
    hit = find(store, guild, sections, name)
    if hit is None:
        raise ContentError("그런 항목이 없어요. 이름은 자동완성에서 골라 주세요.")
    section, data = hit
    users = _used_by(store, guild, section, data)
    if users:
        raise ContentError(users)
    _commit(store, guild, section, [d for d in current(store, guild, section) if d["key"] != data["key"]])
    return section, data


def _used_by(store: Store, guild: int, section: str, data: dict) -> str:
    """이 개체를 다른 소모품이 쓰고 있다면 지울 수 없는 이유를, 아니면 빈 문자열을 돌려준다.
    (무기는 개조 소모품의 목표로, 소모품은 다른 소모품의 '획득/사용' 조건으로 쓰일 수 있다.)"""
    mods = store.settings(guild)["weapon_mods"]
    if section in ("gear", "consumables"):
        # 시크릿 보스는 드롭이 하나는 남아 있어야 한다: 이걸 지우면 그 보스의 마지막 드롭이 사라질 때만 막는다.
        have_gear = {d["key"] for d in current(store, guild, "gear")} - ({data["key"]} if section == "gear" else set())
        have_item = {d["key"] for d in current(store, guild, "consumables")} - ({data["key"]} if section == "consumables" else set())
        field_name = "drop_gear" if section == "gear" else "drop_item"
        boss_users = [d["name"] for d in current(store, guild, "secrets") if d.get(field_name) == data["key"]
                      and d.get("drop_gear") not in have_gear and d.get("drop_item") not in have_item]
        if boss_users:
            return f"**{data['name']}**은(는) 시크릿 보스 {', '.join(boss_users[:3])}의 마지막 드롭이라서 지울 수 없어요. 먼저 그 시크릿 보스의 다른 드롭을 정하거나 보스를 지워 주세요."
    if section == "gear":
        users = [d["name"] for d in current(store, guild, "consumables") if d.get("modify") == data["key"]]
        if users:
            return f"**{data['name']}**은(는) 소모품 {', '.join(users[:3])}의 개조 목표로 쓰이고 있어서 지울 수 없어요. 먼저 그 소모품을 바꾸거나 지워 주세요."
        if any(data["key"] in (m["weapon"], m["result"]) for m in mods):
            return f"**{data['name']}**은(는) 장비 개조 레시피(`/장비개조 목록`)에 쓰이고 있어서 지울 수 없어요. 먼저 그 레시피를 지워 주세요."
    elif section == "consumables":
        users = [d["name"] for d in current(store, guild, "consumables") if d["key"] != data["key"]
                 and any(c[0] in balance.COND_ITEM and c[1] == data["key"] for c in d.get("conditions") or ())]
        if users:
            return f"**{data['name']}**은(는) 소모품 {', '.join(users[:3])}의 사용 조건으로 쓰이고 있어서 지울 수 없어요. 먼저 그 소모품을 바꾸거나 지워 주세요."
        if any(m["item"] == data["key"] for m in mods):
            return f"**{data['name']}**은(는) 장비 개조 레시피(`/장비개조 목록`)의 재료라서 지울 수 없어요. 먼저 그 레시피를 지워 주세요."
    return ""


# ---------------------------------------------------------------- 장비 개조 레시피: (무기, 재료 소모품) → 개조된 무기
def mods(store: Store, guild: int) -> list[dict]:
    """저장된 개조 레시피 [{id, weapon, item, result}] (키로 저장돼 있다)."""
    return store.settings(guild)["weapon_mods"]


def mod_names(store: Store, guild: int, entry: dict) -> tuple[str, str, str]:
    """레시피의 (무기, 재료, 결과 무기) 이름. 지워진 것은 '(없어짐)'."""
    bal = store.balance(guild)
    weapon, result = bal.gear_by_key.get(entry["weapon"]), bal.gear_by_key.get(entry["result"])
    item = bal.item_by_key.get(entry["item"])
    return tuple(f"{x.emoji} {x.name}" if x else "(없어짐)" for x in (weapon, item, result))


def mod_text(store: Store, guild: int, entry: dict) -> str:
    weapon, item, result = mod_names(store, guild, entry)
    return f"{weapon} + {item} → {result}"


def add_mod(store: Store, guild: int, weapon: str, item: str, result: str) -> dict:
    """장비(무기나 방패)에 재료를 쓰면 결과 장비로 바뀌는 레시피를 더한다. 이름은 자동완성에서 고른 것. 무기는 무기로, 방패는 방패로만 바뀐다.
    잘못되면 ContentError."""
    saved = mods(store, guild)
    if len(saved) >= balance.MAX_MODS:
        raise ContentError(f"개조 레시피는 {balance.MAX_MODS}개까지예요.")
    found = {}
    for label, sections, name in (("장비", ("gear",), weapon), ("재료", ("consumables",), item), ("결과 장비", ("gear",), result)):
        hit = find(store, guild, sections, name)
        if hit is None:
            raise ContentError(f"{label} **{name}**이(가) 없어요. 이름은 자동완성에서 골라 주세요.")
        found[label] = hit[1]
    kinds = {"weapon": "무기", "shield": "방패"}
    if found["장비"]["slot"] != found["결과 장비"]["slot"]:
        raise ContentError(f"무기는 무기로, 방패는 방패로만 개조할 수 있어요. **{found['장비']['name']}**은(는) {kinds[found['장비']['slot']]}인데 "
                           f"**{found['결과 장비']['name']}**은(는) {kinds[found['결과 장비']['slot']]}예요.")
    if found["재료"].get("material") is not True:
        raise ContentError(f"**{found['재료']['name']}**은(는) 개조 재료가 아니에요. 재료는 `/소모품 추가`의 `재료`를 켜서 만들어요(회복 효과가 있는 소모품은 재료로 쓸 수 없어요).")
    if found["장비"]["key"] == found["결과 장비"]["key"]:
        raise ContentError("장비와 결과 장비가 같아요. 다른 장비로 바뀌어야 해요.")
    for m in saved:
        if m["weapon"] == found["장비"]["key"] and m["item"] == found["재료"]["key"]:
            raise ContentError(f"이미 같은 레시피가 있어요: {mod_text(store, guild, m)}. 먼저 `/장비개조 삭제`로 지워 주세요.")
    entry = {"id": max([m["id"] for m in saved], default=0) + 1, "weapon": found["장비"]["key"], "item": found["재료"]["key"],
             "result": found["결과 장비"]["key"]}
    store.update_settings(guild, weapon_mods=[*saved, entry])
    return entry


def delete_mod(store: Store, guild: int, ident) -> dict:
    """번호(id)로 레시피를 지운다. 지운 레시피를 돌려준다."""
    saved = mods(store, guild)
    text = str(ident).strip()
    entry = next((m for m in saved if str(m["id"]) == text), None)
    if entry is None:
        raise ContentError("그런 레시피가 없어요. 번호는 `/장비개조 목록`에서 확인하세요.")
    store.update_settings(guild, weapon_mods=[m for m in saved if m is not entry and m["id"] != entry["id"]])
    return entry


def set_sell_price(store: Store, guild: int, sections, price: int, tier: str | None = None) -> dict[str, int]:
    """장비·소모품의 판매가를 한꺼번에 바꾼다. tier를 주면 그 등급만, 없으면 전부. 0이면 팔 수 없다.
    구역마다 바뀐 개수를 {구역: 개수}로 돌려준다. 바꿀 대상이 하나도 없으면 ContentError."""
    if tier is not None and tier not in TIER_ORDER:
        raise ContentError("등급은 일반·희귀·전설 중 하나여야 해요.")
    changed: dict[str, int] = {}
    for section in sections:
        if section not in ("gear", "consumables"):
            raise ContentError("판매가는 장비와 소모품에만 있어요.")
        items = current(store, guild, section)
        hits = [d for d in items if tier is None or d.get("tier", "일반") == tier]
        if not hits:
            continue
        new = [entity_dict(_check(section, {**d, "sell_price": price})) if d in hits else d for d in items]
        _commit(store, guild, section, new)
        changed[section] = len(hits)
    if not changed:
        raise ContentError("바꿀 장비·소모품이 없어요." if tier is None else f"{tier} 등급인 장비·소모품이 없어요.")
    return changed


def reset(store: Store, guild: int, sections=SECTIONS) -> int:
    """구역을 기본 표로 되돌린다(바꾼 구역 수를 돌려준다). 기본 표는 /던전설정으로 바꾼 숫자와 합쳐지므로 그 숫자도 함께 지운다."""
    cfg = store.settings(guild)
    content = {k: v for k, v in cfg["dungeon_content"].items() if k not in sections}
    done = sum(1 for s in sections if s in cfg["dungeon_content"])
    overrides = {k: v for k, v in cfg["dungeon"].items() if not any(k.startswith(LEGACY_PREFIX[s]) for s in sections)}
    store.update_settings(guild, dungeon_content=content, dungeon=overrides)
    return done


def describe(section: str, data: dict, bal=None) -> str:
    """목록에 보여줄 한 줄. bal(밸런스)을 주면 개조 목표 무기·조건의 소모품 이름까지 보여 준다."""
    entity, _ = check_entity(section, data)
    head = f"{entity.emoji} **{entity.name}**"
    if section == "secrets":
        gear = bal.gear_by_key.get(entity.drop_gear) if bal is not None else None
        item = bal.item_by_key.get(entity.drop_item) if bal is not None else None
        drops = []
        if entity.drop_gear:
            drops.append(f"{gear.emoji} {gear.name}" if gear else (f"{entity.drop_gear}" if bal is None else "(없어진 장비)"))
            drops[-1] += f" {entity.drop_gear_chance:g}%"
        if entity.drop_item:
            drops.append((f"{item.emoji} {item.name}" if item else (f"{entity.drop_item}" if bal is None else "(없어진 아이템)"))
                          + (f" ×{entity.drop_item_amount}" if entity.drop_item_amount > 1 else "") + f" {entity.drop_item_chance:g}%")
        return (f"{head} — HP {entity.hp:,} · 공격 {entity.damage:,} · 등장 {entity.weight:g} · 나오는 층 {balance.floors_text(entity.floors)}"
                f" · 드롭(하나는 반드시): " + " / ".join(drops) + (" · 🖼️ 그림 있음" if entity.image else ""))
    if section in ("monsters", "bosses"):
        dodge = "" if section == "bosses" else f" · 회피 {entity.dodge}%"
        where = f" · 나오는 층 {balance.floors_text(entity.floors)}" if entity.floors else ""
        return f"{head} — HP {entity.hp:,} · 공격 {entity.damage:,}{dodge} · 등장 {entity.weight:g}{where}"
    sell = f"판매 {entity.sell_price:,}" if entity.sell_price else "판매 불가"
    text = balance.item_summary(entity, bal) if bal is not None and section == "consumables" else entity.description
    return f"{head} ({entity.tier}) — {text} · 등장 {entity.weight:g} · {sell}"
