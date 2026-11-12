"""던전 진행: 규칙(dungeon.py)과 저장소(store.py)를 이어 준다. 디스코드에 접속하지 않아서 그대로 시험할 수 있다.

한 사람의 던전은 한 번에 한 가지 행동만 처리하고(잠금), 행동마다 화면 번호(rev)를 올려서
지난 화면에 남은 버튼을 누르면 무시한다. 클리어 보상은 한 번만 나간다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import dungeon
import dungeon_auto
from balance import VAULT_GEAR_MAX, VAULT_ITEM_MAX
from dungeon import DungeonError, Run
from store import Store, StoreError

GEAR_SLOTS = ("weapon", "shield", "armor", "spare_weapon", "spare_shield", "spare_armor")

AUTO_REFUSE = "⏩ 빠른진행 중이에요. ⏹ 멈추기를 누르면 직접 진행할 수 있어요."
ACTIONS = ("advance", "attack", "defend", "item", "chest", "equip", "stash", "swap_weapon", "swap_shield", "swap_armor", "discard", "quit", "finish", "infinite", "buy", "skill", "ult", "special")


@dataclass
class Reward:
    candy: int = 0                # 실제로 받은 재화
    ticket: bool = False          # 이번에 히든 칭호 획득권이 나왔는지
    tickets_total: int = 0        # 지금 가진 획득권 수
    items: list = field(default_factory=list)   # 받은 물품 [(이모지, 이름, 개수)]
    final: bool = False           # 무한모드 최종보스를 쓰러뜨린 완전 클리어인지
    final_candy: int = 0          # 그중 최종보스 보상 재화(정한 값. 최대 보유량에 막히면 실제로는 덜 받는다)
    final_tickets: int = 0        # 최종보스 보상 획득권
    vault: list = field(default_factory=list)       # 보관함에 넣은 것 [(이모지, 이름, 개수)]
    vault_lost: list = field(default_factory=list)  # 보관함이 가득 차서 못 넣은 것 [(이모지, 이름, 개수)]


@dataclass
class Outcome:
    ok: bool
    text: str = ""                # 실패했을 때 보여줄 문장
    run: Run | None = None
    ended: str | None = None      # dead | clear | quit — 던전이 끝났다면
    reward: Reward | None = None
    stale: bool = False           # 지난 화면의 버튼이었다
    started: bool = False         # 이번에 새로 들어갔다(입장 물품을 냈다)
    notes: list = field(default_factory=list)


def entry_label(store: Store, guild_id: int) -> str:
    """던전 입장에 내는 것(예: '🗝️ 열쇠 1개'). 무료 입장이면 빈 문자열."""
    cfg = store.settings(guild_id)
    item = store.item(guild_id, cfg["entry_item"]) if cfg["entry_item"] is not None else None
    return f"{item['emoji']} {item['name']} {cfg['entry_cost']}개" if item else ""


def loadout_plan(store: Store, guild_id: int, user_id: int, bal):
    """다음 입장 때 보관함에서 가져갈 것(/보관함에서 고른 것 중 지금 보관함에 있고, 던전에 있는 장비·소모품만).
    (장비 {칸: key}, 소모품 {key: 1}, 보관함에서 뺄 것 [(kind, key, 개수)], 이름 목록)."""
    vault, plan = store.vault(guild_id, user_id), store.loadout(guild_id, user_id)
    gear, items, take, names = {}, {}, [], []
    for slot in ("weapon", "shield", "armor"):
        found = bal.gear_by_key.get(plan[slot])
        if found and found.slot == slot and vault["gear"].get(found.key, 0) > 0:
            gear[slot] = found.key
            take.append(("gear", found.key, 1))
            names.append(f"{found.emoji} {found.name}")
    for key in plan["items"]:
        found = bal.item_by_key.get(key)
        if len(items) < bal.vault_items and found and key not in items and vault["item"].get(key, 0) > 0:
            items[key] = 1
            take.append(("item", key, 1))
            names.append(f"{found.emoji} {found.name}")
    return gear, items, take, names


def vault_fit(store: Store, guild_id: int, user_id: int, run: Run, bal):
    """클리어한 던전에서 들고 있던 장비(장착·예비)와 소모품을 보관함에 넣을 계획. 장비는 모두 합쳐 VAULT_GEAR_MAX개,
    소모품은 종류마다 VAULT_ITEM_MAX개까지(넘는 것은 버려진다). 관리자가 지운 장비·소모품은 넣지 않는다.
    (넣을 것 [(kind, key, 개수)], 넣은 것 [(이모지, 이름, 개수)], 못 넣은 것 [(이모지, 이름, 개수)])."""
    vault = store.vault(guild_id, user_id)
    room = VAULT_GEAR_MAX - sum(vault["gear"].values())
    put: dict[tuple[str, str], int] = {}
    lost: dict[tuple[str, str], int] = {}
    for slot in GEAR_SLOTS:
        key = getattr(run, slot)
        if key in bal.gear_by_key:
            target = put if room > 0 else lost
            target[("gear", key)] = target.get(("gear", key), 0) + 1
            room -= 1
    for key, qty in run.items.items():
        if key in bal.item_by_key and qty > 0:
            fits = max(0, min(qty, VAULT_ITEM_MAX - vault["item"].get(key, 0)))
            if fits:
                put[("item", key)] = fits
            if qty > fits:
                lost[("item", key)] = qty - fits

    def label(pairs):
        out = []
        for (kind, key), qty in pairs.items():
            thing = (bal.gear_by_key if kind == "gear" else bal.item_by_key)[key]
            out.append((thing.emoji, thing.name, qty))
        return out
    return [(kind, key, qty) for (kind, key), qty in put.items()], label(put), label(lost)


async def enter(store: Store, locks, guild_id: int, user_id: int, auto_running: bool = False) -> Outcome:
    """던전에 들어간다. 진행 중인 던전이 있으면 입장 물품 없이 이어서 하고, 없으면 입장 물품을 낸다.
    빠른진행이 켜져 있는데 그걸 돌리는 작업이 없다면(auto_running=False: 봇이 다시 켜졌거나 끊겼다) 빠른진행을 끄고 이어서 한다."""
    async with locks.get(guild_id, user_id):
        bal = store.balance(guild_id)
        fresh = dungeon.new_run(bal=bal)
        gear, items, take, brought = loadout_plan(store, guild_id, user_id, bal)      # 새로 시작할 때만 실제로 꺼낸다
        for slot, key in gear.items():
            setattr(fresh, slot, key)
        fresh.items = dict(items)
        try:
            data, started = store.enter_dungeon(guild_id, user_id, fresh.to_json(), take)
        except StoreError as exc:
            return Outcome(False, str(exc))
        run = Run.from_json(data)
        run.hp = min(run.hp, bal.max_hp)             # 관리자가 최대 체력을 낮췄다면 맞춘다.
        was_auto = run.auto and not auto_running
        if was_auto:
            run.auto = False
        run.bump()                                   # 이전에 띄워 둔 화면은 이제 지난 화면이 된다.
        entry = entry_label(store, guild_id)
        run.events = ([f"🏭 {entry + '을(를) 내고 ' if entry else ''}던전에 들어왔어요! {bal.floors}층까지 올라가야 해요. (한 층에 방이 {dungeon.ROOMS}개)"] if started
                      else ["📖 진행 중이던 던전을 이어서 해요. (입장 물품은 쓰지 않아요)"])
        if started and brought:
            run.events.append("📦 보관함에서 가져왔어요: " + ", ".join(brought))
        if was_auto:
            run.events.append("⏸ 빠른진행이 멈춰 있어요(봇이 다시 켜졌거나 중간에 끊겼어요). 다시 켜도 이 던전에서는 입장 물품을 더 내지 않아요.")
        store.save_dungeon(guild_id, user_id, run.to_json())
        return Outcome(True, run=run, started=started)


async def act(store: Store, locks, rng, guild_id: int, user_id: int, rev: int, action: str,
              arg: str | None = None, guard_ok: bool | None = None, auto: bool = False) -> Outcome:
    """버튼 하나를 처리한다. rev는 그 버튼이 붙어 있던 화면의 번호. auto=True는 빠른진행(컴퓨터)이 하는 행동이다.
    빠른진행 중에는 사람이 누른 행동(나가기만 빼고)을 받지 않는다."""
    if action not in ACTIONS:
        return Outcome(False, "알 수 없는 행동이에요.")
    async with locks.get(guild_id, user_id):
        data = store.dungeon_run(guild_id, user_id)
        if data is None:
            return Outcome(False, "진행 중인 던전이 없어요. `/던전`으로 다시 들어와 주세요.", stale=True)
        bal = store.balance(guild_id)
        run = Run.from_json(data)
        run.hp = min(run.hp, bal.max_hp)
        if run.rev != rev:
            return Outcome(False, "지난 화면이에요. 가장 최근 화면에서 눌러 주세요. (안 보이면 `/던전`)", stale=True)
        if auto and not run.auto:
            return Outcome(False, "빠른진행이 멈췄어요.", run=run, stale=True)
        if run.auto and not auto and action != "quit":
            return Outcome(False, AUTO_REFUSE, run=run)
        spend = 0
        try:
            if action == "buy":                           # 상점: 재화는 저장할 때 같이 뺀다(모자라면 아무것도 바뀌지 않는다).
                spend = dungeon.buy_offer(run, arg, bal)
                problem = None
            else:
                problem = _apply(run, rng, action, arg, guard_ok, bal)
        except DungeonError as exc:
            problem = str(exc)
        if problem:
            return Outcome(False, problem, run=run)
        run.bump()
        if action == "buy":
            try:
                store.spend_in_dungeon(guild_id, user_id, run.to_json(), spend)
            except StoreError as exc:
                return Outcome(False, str(exc))
            return Outcome(True, run=run)
        if run.phase == "dead" and _revive(store, guild_id, user_id, run, bal):
            return Outcome(True, run=run, notes=["revived"])
        if not run.finished and run.phase != "dead":
            if action == "infinite" or run.bonus or run.room_candy:
                return _save_with_rewards(store, rng, guild_id, user_id, run, bal, clear_reward=action == "infinite")
            store.save_dungeon(guild_id, user_id, run.to_json())
            return Outcome(True, run=run)
        return _finish(store, rng, guild_id, user_id, run, bal)


def _roll_clear(store: Store, rng, guild_id: int, bal):
    """클리어 보상을 굴린다: (재화, 획득권이 나왔는지, [(물품 행, 개수)]). 추가 물품은 항목마다 따로 확률을 굴린다(보상이 없으면 난수를 더 뽑지 않는다)."""
    candy, ticket = dungeon.roll_clear_reward(rng, bal)
    given = []
    for entry in store.settings(guild_id)["clear_rewards"]:
        item = store.item(guild_id, entry["item"])
        if item is not None and rng.random() * 100 < entry["chance"]:
            given.append((item, entry["amount"]))
    return candy, ticket, given


def _save_with_rewards(store: Store, rng, guild_id: int, user_id: int, run: Run, bal, *, clear_reward: bool) -> Outcome:
    """무한모드: 던전은 끝내지 않고 저장하면서 보상을 한 번에 준다 — 꼭대기에서 무한모드를 고른 순간의 클리어 보상(clear_reward)과,
    무한 층에 오를 때의 층 보너스(run.bonus). 화면(run.events)에도 받은 내용을 적는다."""
    cur = store.currency(guild_id)
    room = max(0, cur.max - store.wallet(guild_id, user_id))          # 재화 최대 보유량 안에서 받을 수 있는 만큼(같은 계산을 저장소도 한다)
    candy_total, ticket, given, lines = 0, False, [], []
    if clear_reward:
        candy, ticket, given = _roll_clear(store, rng, guild_id, bal)
        got = min(candy, room)
        room -= got
        candy_total += candy
        lines.append(f"🏆 **클리어 보상**을 받았어요! {cur.money(got)}" + (" · 🎟️ 히든 칭호 획득권 +1" if ticket else "")
                     + "".join(f" · {item['emoji']} {item['name']} ×{amount:,}" for item, amount in given))
    bonus, run.bonus = run.bonus, 0
    if bonus:
        got = min(bonus, room)
        room -= got
        candy_total += bonus
        lines.append(f"♾️ 무한 **{run.floor}층** 보너스! {cur.money(got)}")
    gift, run.room_candy = run.room_candy, 0
    if gift:
        got = min(gift, room)
        candy_total += gift
        lines.append(f"🎁 방에서 얻은 재화! {cur.money(got)}")
    run.events = [*run.events, *lines]
    done = store.reward_in_dungeon(guild_id, user_id, run.to_json(), candy=candy_total, tickets=1 if ticket else 0,
                                   items=[(item["id"], amount) for item, amount in given])
    if done is None:
        return Outcome(False, "이미 끝난 던전이에요.", stale=True)
    return Outcome(True, run=run)


async def sell(store: Store, locks, guild_id: int, user_id: int, rev: int, picks, mode: str = "all") -> Outcome:
    """던전 안에서 장비·소모품을 판다. 판매가는 관리자가 정한 값이고, 재화는 바로 들어온다.
    싸우는 중에는 팔 수 없고, 재화가 최대 보유량을 넘게 되면 아무것도 팔리지 않는다."""
    async with locks.get(guild_id, user_id):
        data = store.dungeon_run(guild_id, user_id)
        if data is None:
            return Outcome(False, "진행 중인 던전이 없어요. `/던전`으로 다시 들어와 주세요.", stale=True)
        bal = store.balance(guild_id)
        run = Run.from_json(data)
        run.hp = min(run.hp, bal.max_hp)
        if run.rev != rev:
            return Outcome(False, "지난 화면이에요. 가장 최근 화면에서 눌러 주세요. (안 보이면 `/던전`)", stale=True)
        if run.auto:
            return Outcome(False, AUTO_REFUSE, run=run)
        try:
            total, sold = dungeon.sell(run, picks, mode, bal)
        except DungeonError as exc:
            return Outcome(False, str(exc), run=run)
        cur = store.currency(guild_id)
        run.events = [f"💰 {entry['emoji']} **{entry['name']}**" + (f" ×{qty:,}" if qty > 1 else "") + f" — {cur.money(entry['price'] * qty)}"
                      for entry, qty in sold]
        run.events.append(f"합계 {cur.money(total)}을(를) 받았어요!")
        run.last = []
        run.bump()
        try:
            store.sell_in_dungeon(guild_id, user_id, run.to_json(), total)
        except StoreError as exc:
            return Outcome(False, str(exc), run=run)
        return Outcome(True, run=run, notes=[total])


async def set_auto(store: Store, locks, guild_id: int, user_id: int, rev: int, on: bool) -> Outcome:
    """빠른진행을 켜거나 끈다. 켤 때는 이 던전에서 처음이면 입장 물품을 한 번 더 낸다(껐다 켜도 다시 내지 않는다).
    notes에 낸 입장 물품 문구가 들어온다(안 냈으면 비어 있다). 끌 때는 화면 번호를 따지지 않는다(지난 화면의 멈추기 버튼도 먹는다)."""
    async with locks.get(guild_id, user_id):
        data = store.dungeon_run(guild_id, user_id)
        if data is None:
            return Outcome(False, "진행 중인 던전이 없어요. `/던전`으로 다시 들어와 주세요.", stale=True)
        bal = store.balance(guild_id)
        run = Run.from_json(data)
        run.hp = min(run.hp, bal.max_hp)
        if on:
            if run.rev != rev:
                return Outcome(False, "지난 화면이에요. 가장 최근 화면에서 눌러 주세요. (안 보이면 `/던전`)", stale=True)
            if run.auto:
                return Outcome(True, run=run)
            charge = not run.auto_paid
            label = entry_label(store, guild_id) if charge else ""
            run.auto = run.auto_paid = True
            run.events = ["⏩ **빠른진행**을 켰어요! 이제 컴퓨터가 알아서 진행해요." + (f" ({label}을(를) 한 번 더 냈어요)" if label else "")]
            run.bump()
            try:
                store.start_auto(guild_id, user_id, run.to_json(), charge)
            except StoreError as exc:
                return Outcome(False, str(exc), run=run)
            return Outcome(True, run=run, notes=[label])
        if not run.auto:
            return Outcome(True, run=run)
        run.auto = False
        run.events = ["⏹ 빠른진행을 멈췄어요. 이제 직접 진행해요."]
        run.bump()
        store.save_dungeon(guild_id, user_id, run.to_json())
        return Outcome(True, run=run)


async def auto_step(store: Store, locks, rng, guild_id: int, user_id: int) -> Outcome:
    """빠른진행이 한 걸음 간다: 컴퓨터가 다음 행동을 골라(dungeon_auto) 사람이 누른 것과 똑같은 경로로 처리한다.
    던전이 끝났거나 빠른진행이 꺼졌으면 stale=True로 알린다."""
    run = await peek(store, guild_id, user_id)
    if run is None:
        return Outcome(False, "던전이 끝났어요.", stale=True)
    if not run.auto:
        return Outcome(False, "빠른진행이 멈췄어요.", run=run, stale=True)
    action, arg = dungeon_auto.choose(run, store.balance(guild_id))
    return await act(store, locks, rng, guild_id, user_id, run.rev, action, arg, auto=True)


async def grant_gear(store: Store, locks, guild_id: int, user_id: int, gear_key: str) -> Outcome:
    """관리자가 진행 중인 던전의 그 사람에게 장비를 준다. 상자에서 나온 것처럼 '정하지 않은 장비'로 올라가서, 그 사람이 장착 / 예비에 넣기 / 버리기를 고른다.
    던전에 없거나, 싸우는 중이거나, 상자를 열기 전이거나, 이미 정하지 않은 장비가 있으면 줄 수 없다(상자에서 나온 장비를 덮어쓰지 않게).
    화면 번호가 올라가서 그 사람의 예전 화면 버튼은 지난 화면이 되고, `/던전`을 열면 새 화면이 나온다. 빠른진행 중이면 컴퓨터가 알아서 정한다."""
    async with locks.get(guild_id, user_id):
        data = store.dungeon_run(guild_id, user_id)
        if data is None:
            return Outcome(False, "그 사람은 지금 던전에 들어가 있지 않아요. 던전 안에 있는 사람에게만 장비를 줄 수 있어요.")
        bal = store.balance(guild_id)
        gear = bal.gear_by_key.get(gear_key)
        if gear is None:
            return Outcome(False, "그런 장비가 없어요. 이름은 자동완성에서 골라 주세요.")
        run = Run.from_json(data)
        run.hp = min(run.hp, bal.max_hp)
        if run.in_battle:
            return Outcome(False, "그 사람이 지금 싸우는 중이에요. 싸움이 끝난 뒤에 다시 줘 주세요.", run=run)
        if run.phase == "chest":
            return Outcome(False, "그 사람 앞에 아직 열지 않은 상자가 있어요. 상자를 열어 장비를 정한 뒤에 다시 줘 주세요.", run=run)
        if run.pending_gear:
            return Outcome(False, "그 사람에게 아직 정하지 않은 장비가 있어요. 그 장비를 장착하거나 버린 뒤에 다시 줘 주세요.", run=run)
        run.pending_gear = gear.key
        run.loot.append(gear.name)
        run.events = [f"🎁 **관리자가 장비를 줬어요!**", f"{gear.emoji} **{gear.name}** ({gear.tier}) — {gear.description}",
                      "장착할지, 예비에 넣을지, 버릴지 정해 주세요."]
        run.bump()
        store.save_dungeon(guild_id, user_id, run.to_json())
        return Outcome(True, run=run)


async def peek(store: Store, guild_id: int, user_id: int) -> Run | None:
    """진행 중인 던전을 바꾸지 않고 읽는다(판매 화면에서 돌아올 때 쓴다). 없으면 None."""
    data = store.dungeon_run(guild_id, user_id)
    if data is None:
        return None
    bal = store.balance(guild_id)
    run = Run.from_json(data)
    run.hp = min(run.hp, bal.max_hp)
    return run


def _apply(run: Run, rng, action: str, arg: str | None, guard_ok: bool | None, bal) -> str | None:
    """행동을 규칙에 적용한다. 할 수 없는 행동이면 이유를 돌려준다."""
    previous, run.last = run.last, []                      # '바로 직전 행동'에서 일어난 일은 행동마다 새로 쓴다(소모품 사용 조건).
    if action == "item":
        run.last = previous                                # 소모품은 직전 행동을 기준으로 조건을 따진다. 쓰고 나면 use_consumable가 '방금 쓴 것'으로 새로 쓴다.
    if action == "quit":
        run.phase, run.enemy = "quit", None
        run.events = ["🚪 던전에서 나왔어요. 입장에 쓴 물품은 돌아오지 않아요."]
    elif action == "attack":
        if run.phase != "battle":
            return "지금은 싸울 상대가 없어요."
        dungeon.player_attack(run, rng, bal)
    elif action == "defend":
        if run.phase != "battle":
            return "지금은 싸울 상대가 없어요."
        ok = dungeon.roll_guard(rng, bal, run) if guard_ok is None else bool(guard_ok)       # 따로 정해 주지 않으면 낀 방패의 성공률로 굴린다
        dungeon.player_defend(run, ok, bal, rng)
    elif action == "item":
        dungeon.use_consumable(run, arg or "", bal, rng)
    elif action in ("skill", "ult", "special"):
        dungeon.use_technique(run, action, rng, bal)
    elif action == "chest":
        if run.phase != "chest":
            return "열 수 있는 상자가 없어요."
        dungeon.open_chest(run, rng, bal)
    elif action == "equip":
        dungeon.equip_pending(run, bal)
    elif action == "stash":
        dungeon.stash_pending(run, bal)
    elif action in ("swap_weapon", "swap_shield", "swap_armor"):
        dungeon.swap_spare(run, action.split("_", 1)[1], bal)
    elif action == "discard":
        dungeon.discard_pending(run, bal)
    elif action == "finish":
        dungeon.take_clear(run)
    elif action == "infinite":
        dungeon.go_infinite(run, rng, bal)
    elif action == "advance":
        if not (run.can_advance or run.phase == "shop"):          # 상점에서는 [상점 나가기]로 위 층에 올라간다
            return ("먼저 상자에서 나온 장비를 장착하거나 버려 주세요." if run.pending_gear
                    else "지금은 앞으로 갈 수 없어요.")
        dungeon.advance(run, rng, bal)
    return None


def _revive(store: Store, guild_id: int, user_id: int, run: Run, bal) -> bool:
    """다운됐을 때 가방에 부활권이 있으면 한 장 쓰고 그 자리에서 다시 일어난다(HP는 최대의 bal.revive_heal %).
    싸우던 상대는 그대로 남는다. 부활권이 없으면 아무것도 바꾸지 않고 False(그대로 던전이 끝난다)."""
    item = store.revive_item(guild_id)
    if item is None or store.qty(guild_id, user_id, item["id"]) < 1:
        return False
    saved = (run.hp, run.phase, list(run.events))
    run.hp = max(1, bal.max_hp * bal.revive_heal // 100)
    run.phase = "battle" if run.enemy else "cleared"
    left = store.qty(guild_id, user_id, item["id"]) - 1
    run.events = run.events + [f"{item['emoji']} **{item['name']}**을(를) 한 장 썼어요! HP {run.hp}(으)로 다시 일어났어요."
                               + (f" (남은 {item['name']} {left}장)" if left else "")]
    if store.use_revive(guild_id, user_id, run.to_json()):
        return True
    run.hp, run.phase, run.events = saved                 # 그 사이 부활권이 사라졌다면(겹친 요청 등) 다운된 채로 둔다.
    return False


def _finish(store: Store, rng, guild_id: int, user_id: int, run: Run, bal) -> Outcome:
    """던전이 끝났다. 클리어면 보상을 주고, 어느 쪽이든 진행 기록을 지운다."""
    ended = {"clear": "clear", "quit": "quit"}.get(run.phase, "dead")
    candy, ticket, given = _roll_clear(store, rng, guild_id, bal) if ended == "clear" else (0, False, [])
    final = ended == "clear" and run.final_clear          # 최종보스 완전 클리어: 클리어 보상에 최종보스 보상을 더한다
    extra_candy, extra_tickets = (bal.final_reward, bal.final_tickets) if final else (0, 0)
    put, saved, lost = vault_fit(store, guild_id, user_id, run, bal) if ended == "clear" else ([], [], [])   # 클리어하면 들고 있던 것은 보관함으로
    done = store.end_dungeon(guild_id, user_id, candy=candy + extra_candy, tickets=(1 if ticket else 0) + extra_tickets,
                             items=[(item["id"], amount) for item, amount in given], vault=put)
    if done is None:                                  # 이미 끝난 던전(겹친 요청): 보상은 이미 나갔다.
        return Outcome(False, "이미 끝난 던전이에요.", stale=True)
    reward = Reward(candy=done[0], ticket=ticket, tickets_total=done[1],
                    items=[(item["emoji"], item["name"], amount) for item, amount in given],
                    final=final, final_candy=extra_candy, final_tickets=extra_tickets, vault=saved, vault_lost=lost) if ended == "clear" else None
    return Outcome(True, run=run, ended=ended, reward=reward)
