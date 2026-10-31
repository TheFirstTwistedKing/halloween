"""던전 콘텐츠(몬스터·보스·장비·소모품) 편집 규칙. 디스코드에 접속하지 않는 순수 로직이라 그대로 시험할 수 있다.

서버 설정의 `dungeon_content`에는 관리자가 손댄 구역만 들어 있고({구역: [개체 사전, ...]}), 구역이 있으면 기본 표 대신 그것을 쓴다.
처음 손댈 때 기본 표(관리자가 /던전설정으로 바꾼 숫자 포함)를 통째로 복사해 와서 그 위에서 더하고 빼고 바꾼다.
"""
from __future__ import annotations

import secrets

import balance
from balance import SECTION_LABELS, SECTION_LIMITS, SECTIONS, Balance, check_entity, entity_dict
from store import Store, valid_emoji

# 구역별로 /던전설정 숫자 항목의 이름표(구역을 직접 편집하면 그 숫자는 쓰이지 않으므로 같이 정리한다)
LEGACY_PREFIX = {"monsters": "몬스터.", "bosses": "보스.", "gear": "장비.", "consumables": "아이템."}
# 같은 이름이 겹치면 안 되는 범위(몬스터와 보스는 같은 이름을 쓰면 헷갈린다)
NAME_SCOPE = {"monsters": ("monsters", "bosses"), "bosses": ("monsters", "bosses"), "gear": ("gear",), "consumables": ("consumables",)}


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
    _commit(store, guild, section, items + [entity_dict(entity)])
    return getattr(store.balance(guild), section)[-1]


def edit(store: Store, guild: int, sections, name: str, changes: dict):
    """이름으로 찾은 개체의 값을 바꾼다. 바꿀 값만 담은 changes를 덮어쓴다. (구역, 바뀐 개체)를 돌려준다."""
    hit = find(store, guild, sections, name)
    if hit is None:
        raise ContentError("그런 항목이 없어요. 이름은 자동완성에서 골라 주세요.")
    section, data = hit
    changes = {k: v for k, v in changes.items() if v is not None or k in ("dodge", "guard", "reflect")}
    if not changes:
        raise ContentError("바꿀 내용을 하나 이상 적어 주세요.")
    new = {**data, **changes}
    if "name" in changes:
        new["name"] = str(new["name"]).strip()
        _unique_name(store, guild, section, new["name"], ignore_key=data["key"])
    entity = _check(section, new)
    items = [entity_dict(entity) if d["key"] == data["key"] else d for d in current(store, guild, section)]
    _commit(store, guild, section, items)
    return section, next(e for e in getattr(store.balance(guild), section) if e.key == data["key"])


def delete(store: Store, guild: int, sections, name: str):
    """이름으로 찾은 개체를 지운다. 이미 던전 안에 있는 사람에게는 영향이 없다(몬스터는 이미 나왔고, 장비·소모품은 쓸 수 없게 된다)."""
    hit = find(store, guild, sections, name)
    if hit is None:
        raise ContentError("그런 항목이 없어요. 이름은 자동완성에서 골라 주세요.")
    section, data = hit
    _commit(store, guild, section, [d for d in current(store, guild, section) if d["key"] != data["key"]])
    return section, data


def reset(store: Store, guild: int, sections=SECTIONS) -> int:
    """구역을 기본 표로 되돌린다(바꾼 구역 수를 돌려준다). 기본 표는 /던전설정으로 바꾼 숫자와 합쳐지므로 그 숫자도 함께 지운다."""
    cfg = store.settings(guild)
    content = {k: v for k, v in cfg["dungeon_content"].items() if k not in sections}
    done = sum(1 for s in sections if s in cfg["dungeon_content"])
    overrides = {k: v for k, v in cfg["dungeon"].items() if not any(k.startswith(LEGACY_PREFIX[s]) for s in sections)}
    store.update_settings(guild, dungeon_content=content, dungeon=overrides)
    return done


def describe(section: str, data: dict) -> str:
    """목록에 보여줄 한 줄."""
    entity, _ = check_entity(section, data)
    head = f"{entity.emoji} **{entity.name}**"
    if section in ("monsters", "bosses"):
        dodge = "" if section == "bosses" else f" · 회피 {entity.dodge}%"
        return f"{head} — HP {entity.hp:,} · 공격 {entity.damage:,}{dodge}"
    return f"{head} ({entity.tier}) — {entity.description} · 등장 {entity.weight}"
