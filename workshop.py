"""기지의 요리(소모품)·제작(장비) 레시피. 관리자가 만들고(/기지레시피), 플레이어는 /기지 화면에서만 만든다.

레시피 하나: 가방 물품(패키지 물품 등) 1~3종 × 개수 + 재화(RAM) → 던전 소모품(요리) 또는 장비(제작). 결과물은 📦 보관함으로 간다.
저장은 서버 설정의 workshop 목록이다(store.check_workshop이 모양을 검사한다).
"""
from __future__ import annotations

from store import WORKSHOP_COST_MAX, WORKSHOP_INGREDIENTS, WORKSHOP_MAX, StoreError

KINDS = {"cook": ("🍳", "요리", "소모품"), "craft": ("🔨", "제작", "장비")}
AMOUNT_MAX = 10                  # 요리 한 번에 나오는 소모품 개수 한도(제작은 늘 1개)


def recipes(store, guild: int, kind: str | None = None) -> list[dict]:
    return [r for r in store.settings(guild)["workshop"] if kind is None or r["kind"] == kind]


def result_of(bal, recipe: dict):
    """레시피의 결과물(던전 소모품·장비). 관리자가 지웠다면 None."""
    return (bal.item_by_key if recipe["kind"] == "cook" else bal.gear_by_key).get(recipe["result"])


def add_recipe(store, guild: int, kind: str, result_name: str, ingredients, cost: int, amount: int = 1) -> dict:
    """레시피를 더한다. ingredients는 [(물품 이름, 개수), ...]. 결과물 하나에 레시피는 하나. 잘못되면 StoreError."""
    if kind not in KINDS:
        raise StoreError("알 수 없는 레시피 종류예요.")
    store.ensure_defaults(guild)
    bal = store.balance(guild)
    pool = bal.consumables if kind == "cook" else bal.gear
    needle = (result_name or "").strip().lower()
    result = next((x for x in pool if x.name.lower() == needle), None)
    if result is None:
        raise StoreError(f"그런 {KINDS[kind][2]}이 없어요. 결과물 이름은 자동완성에서 골라 주세요(던전의 {KINDS[kind][2]}).")
    saved = store.settings(guild)["workshop"]
    if len(saved) >= WORKSHOP_MAX:
        raise StoreError(f"레시피는 서버마다 {WORKSHOP_MAX}개까지예요.")
    if any(r["kind"] == kind and r["result"] == result.key for r in saved):
        raise StoreError(f"{result.emoji} **{result.name}**에는 이미 {KINDS[kind][1]} 레시피가 있어요. 먼저 지워 주세요.")
    parts: dict[int, int] = {}
    for name, qty in ingredients:
        row = store.item_by_name(guild, name)
        if row is None or row["kind"] != "item":
            raise StoreError(f"**{name}** 물품이 없어요. 재료는 가방 물품이고, 이름은 자동완성에서 골라 주세요.")
        parts[row["id"]] = parts.get(row["id"], 0) + qty
    if not parts:
        raise StoreError("재료를 하나 이상 정해 주세요.")
    if len(parts) > WORKSHOP_INGREDIENTS or any(q > 999 for q in parts.values()):
        raise StoreError(f"재료는 {WORKSHOP_INGREDIENTS}종까지, 종류마다 999개까지예요.")
    if not 0 <= cost <= WORKSHOP_COST_MAX:
        raise StoreError("비용이 올바르지 않아요.")
    amount = amount if kind == "cook" else 1
    if not 1 <= amount <= AMOUNT_MAX:
        raise StoreError(f"한 번에 만드는 개수는 1~{AMOUNT_MAX}개예요.")
    recipe = {"id": max((r["id"] for r in saved), default=0) + 1, "kind": kind, "result": result.key, "amount": amount,
              "ingredients": [[item_id, qty] for item_id, qty in parts.items()], "cost": cost}
    store.update_settings(guild, workshop=[*saved, recipe])
    return recipe


def delete_recipe(store, guild: int, recipe_id: int) -> dict:
    saved = store.settings(guild)["workshop"]
    found = next((r for r in saved if r["id"] == recipe_id), None)
    if found is None:
        raise StoreError("그런 레시피가 없어요. 번호는 `/기지레시피 목록`에서 확인해 주세요.")
    store.update_settings(guild, workshop=[r for r in saved if r["id"] != recipe_id])
    return found


def ingredient_text(store, guild: int, recipe: dict, user: int | None = None) -> str:
    """재료 설명('🔩 구리선 ×2 · 💾 100'). user가 있으면 가진 개수도('(3/2)')."""
    cur = store.currency(guild)
    parts = []
    for item_id, qty in recipe["ingredients"]:
        row = store.item(guild, item_id)
        label = f"{row['emoji']} {row['name']}" if row else "❔ 지워진 물품"
        parts.append(f"{label} ×{qty}" + (f" ({store.qty(guild, user, item_id)}/{qty})" if user is not None else ""))
    if recipe["cost"]:
        parts.append(cur.money(recipe["cost"]) + (f" ({cur.money(store.wallet(guild, user))} 보유)" if user is not None else ""))
    return " · ".join(parts)


def recipe_line(store, guild: int, recipe: dict, user: int | None = None) -> str:
    emoji, verb, _what = KINDS[recipe["kind"]]
    result = result_of(store.balance(guild), recipe)
    name = f"{result.emoji} **{result.name}**" if result else "❔ 지워진 결과물"
    amount = f" ×{recipe['amount']}" if recipe["amount"] > 1 else ""
    return f"{emoji} #{recipe['id']} {name}{amount} ← {ingredient_text(store, guild, recipe, user)}"


def can_make(store, guild: int, user: int, recipe: dict) -> bool:
    """재료·재화가 다 있고 결과물·재료가 지금도 있는지(보관함 자리는 만들 때 따로 본다)."""
    if result_of(store.balance(guild), recipe) is None:
        return False
    for item_id, qty in recipe["ingredients"]:
        if store.item(guild, item_id) is None or store.qty(guild, user, item_id) < qty:
            return False
    return store.wallet(guild, user) >= recipe["cost"]


def make(store, guild: int, user: int, recipe_id: int) -> tuple[dict, object]:
    """레시피대로 만든다(부르는 쪽이 그 사람의 잠금을 잡는다). (레시피, 결과물). 못 만들면 StoreError이고 아무것도 바뀌지 않는다."""
    recipe = next((r for r in recipes(store, guild) if r["id"] == recipe_id), None)
    if recipe is None:
        raise StoreError("그 레시피는 이제 없어요. 화면을 다시 열어 주세요.")
    result = result_of(store.balance(guild), recipe)
    if result is None:
        raise StoreError("이 레시피의 결과물이 던전에서 지워졌어요. 관리자에게 알려 주세요.")
    if any(store.item(guild, item_id) is None for item_id, _q in recipe["ingredients"]):
        raise StoreError("이 레시피의 재료 물품이 지워졌어요. 관리자에게 알려 주세요.")
    store.workshop_make(guild, user, [tuple(p) for p in recipe["ingredients"]], recipe["cost"],
                        "item" if recipe["kind"] == "cook" else "gear", recipe["result"], recipe["amount"])
    return recipe, result
