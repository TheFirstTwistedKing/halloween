"""던전 진행: 규칙(dungeon.py)과 저장소(store.py)를 이어 준다. 디스코드에 접속하지 않아서 그대로 시험할 수 있다.

한 사람의 던전은 한 번에 한 가지 행동만 처리하고(잠금), 행동마다 화면 번호(rev)를 올려서
지난 화면에 남은 버튼을 누르면 무시한다. 클리어 보상은 한 번만 나간다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import dungeon
from dungeon import DungeonError, Run
from store import Store, StoreError

ACTIONS = ("advance", "attack", "defend", "item", "chest", "equip", "discard", "quit")


@dataclass
class Reward:
    candy: int = 0                # 실제로 받은 재화
    ticket: bool = False          # 이번에 히든 칭호 획득권이 나왔는지
    tickets_total: int = 0        # 지금 가진 획득권 수


@dataclass
class Outcome:
    ok: bool
    text: str = ""                # 실패했을 때 보여줄 문장
    run: Run | None = None
    ended: str | None = None      # dead | clear | quit — 던전이 끝났다면
    reward: Reward | None = None
    stale: bool = False           # 지난 화면의 버튼이었다
    started: bool = False         # 이번에 새로 들어갔다(열쇠를 썼다)
    notes: list = field(default_factory=list)


async def enter(store: Store, locks, guild_id: int, user_id: int) -> Outcome:
    """던전에 들어간다. 진행 중인 던전이 있으면 열쇠 없이 이어서 하고, 없으면 열쇠 1개를 쓴다."""
    async with locks.get(guild_id, user_id):
        bal = store.balance(guild_id)
        try:
            data, started = store.enter_dungeon(guild_id, user_id, dungeon.new_run(bal=bal).to_json())
        except StoreError as exc:
            return Outcome(False, str(exc))
        run = Run.from_json(data)
        run.hp = min(run.hp, bal.max_hp)             # 관리자가 최대 체력을 낮췄다면 맞춘다.
        run.bump()                                   # 이전에 띄워 둔 화면은 이제 지난 화면이 된다.
        run.events = (["🗝️ 열쇠를 써서 던전에 들어왔어요! 앞으로 방이 10번 나와요."] if started
                      else ["📖 진행 중이던 던전을 이어서 해요. (열쇠는 쓰지 않아요)"])
        store.save_dungeon(guild_id, user_id, run.to_json())
        return Outcome(True, run=run, started=started)


async def act(store: Store, locks, rng, guild_id: int, user_id: int, rev: int, action: str,
              arg: str | None = None, guard_ok: bool = False) -> Outcome:
    """버튼 하나를 처리한다. rev는 그 버튼이 붙어 있던 화면의 번호."""
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
        try:
            problem = _apply(run, rng, action, arg, guard_ok, bal)
        except DungeonError as exc:
            problem = str(exc)
        if problem:
            return Outcome(False, problem, run=run)
        run.bump()
        if not run.finished and run.phase != "dead":
            store.save_dungeon(guild_id, user_id, run.to_json())
            return Outcome(True, run=run)
        return _finish(store, rng, guild_id, user_id, run, bal)


def _apply(run: Run, rng, action: str, arg: str | None, guard_ok: bool, bal) -> str | None:
    """행동을 규칙에 적용한다. 할 수 없는 행동이면 이유를 돌려준다."""
    if action == "quit":
        run.phase, run.enemy = "quit", None
        run.events = ["🚪 던전에서 나왔어요. 쓴 열쇠는 돌아오지 않아요."]
    elif action == "attack":
        if run.phase != "battle":
            return "지금은 싸울 상대가 없어요."
        dungeon.player_attack(run, rng, bal)
    elif action == "defend":
        if run.phase != "battle":
            return "지금은 싸울 상대가 없어요."
        dungeon.player_defend(run, bool(guard_ok), bal)
    elif action == "item":
        dungeon.use_consumable(run, arg or "", bal)
    elif action == "chest":
        if run.phase != "chest":
            return "열 수 있는 상자가 없어요."
        dungeon.open_chest(run, rng, bal)
    elif action == "equip":
        dungeon.equip_pending(run)
    elif action == "discard":
        dungeon.discard_pending(run)
    elif action == "advance":
        if not run.can_advance:
            return ("먼저 상자에서 나온 장비를 장착하거나 버려 주세요." if run.pending_gear
                    else "지금은 앞으로 갈 수 없어요.")
        dungeon.advance(run, rng, bal)
    return None


def _finish(store: Store, rng, guild_id: int, user_id: int, run: Run, bal) -> Outcome:
    """던전이 끝났다. 클리어면 보상을 주고, 어느 쪽이든 진행 기록을 지운다."""
    ended = {"clear": "clear", "quit": "quit"}.get(run.phase, "dead")
    candy, ticket = dungeon.roll_clear_reward(rng, bal) if ended == "clear" else (0, False)
    done = store.end_dungeon(guild_id, user_id, candy=candy, tickets=1 if ticket else 0)
    if done is None:                                  # 이미 끝난 던전(겹친 요청): 보상은 이미 나갔다.
        return Outcome(False, "이미 끝난 던전이에요.", stale=True)
    reward = Reward(candy=done[0], ticket=ticket, tickets_total=done[1]) if ended == "clear" else None
    return Outcome(True, run=run, ended=ended, reward=reward)
