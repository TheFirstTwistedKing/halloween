"""롤플레잉: COM(재화이자 EXP)·LV·직업·일하기·환전·룰렛·레벨 역할 보상. 디스코드와 상관없이 저장소(store.db)만 다룬다.

- COM: 롤플레잉 재화. 얻은 COM은 EXP로도 쌓이고(누적), 모은 EXP로 LV이 저절로 오른다. COM을 써도 LV은 내려가지 않는다.
- 환전: 10 RAM ⇄ 1 COM(양방향, 수수료 없음). COM을 RAM으로 바꾼 만큼은 '되산 몫'으로 기억해 두고,
  다시 RAM→COM으로 바꿀 때 그 몫만큼은 EXP로 치지 않는다(오가기만 해서 LV을 올리는 것을 막는다).
- 룰렛: COM을 건다. 꽝 x0 70% · 당첨 x2 25% · 초당첨 x5 5%(확률은 관리자가 바꾼다). 딴 만큼(돌려받은 것 - 건 것)만 EXP가 된다.
- 직업: 관리자가 만들고 임명한다. /일하기로 급여(COM)를 받는다(직업마다 쿨타임). 급여는 LV이 오를수록 늘어난다(LV마다 +pay_step%).
  직업에 '승급 직업'과 '승급 LV'을 정하면, 그 LV이 된 사람을 관리자가 승급시킬 수 있다.
- 레벨 보상: 관리자가 'LV N → 역할'을 정한다. LV이 그 레벨에 닿으면 역할을 준다(부르는 쪽이 디스코드에서 준다).
"""
from __future__ import annotations

import time

from josa import i_ga
from store import StoreError

COM = "🪙"
COM_NAME = "COM"
COM_MAX = 9_999_999_999
EXCHANGE_RATE = 10                 # 10 RAM = 1 COM
LV_MAX = 999
JOBS_MAX = 25
REWARDS_MAX = 25
NAME_MAX = 20
DEFAULT_ROULETTE = [{"name": "꽝", "times": 0, "chance": 70}, {"name": "당첨", "times": 2, "chance": 25}, {"name": "초당첨", "times": 5, "chance": 5}]
DEFAULT_FORMULA = {"mul": 1, "offset": 1, "div": 10}  # 누적 경험치 = ((나눗셈 × 레벨)² − 오프셋) ÷ 곱셈 + 오프셋
FORMULA_LIMITS = {"mul": (1, 1_000_000), "offset": (0, 1_000_000_000), "div": (1, 10_000)}
MUTE_MODES = ("off", "block", "reduce")           # 음성 마이크/헤드셋 음소거: 제한 없음 · 차단 · 감소
DEFAULT_CONFIG = {"pay_step": 5, "bet_max": 1000, "spin_cooldown": 10, "label": "LV.", "chat_xp": 30, "voice_xp": 0, "attend_xp": 1000,
                  "chat_cooldown": 1, "mute_mode": "block", "mute_percent": 50, "shop_channel": None,
                  "formula": DEFAULT_FORMULA, "roulette": DEFAULT_ROULETTE, "rewards": [], "jobs": [], "shop": []}
LIMITS = {"pay_step": (0, 1000), "bet_max": (1, COM_MAX), "spin_cooldown": (0, 86400), "chat_xp": (0, 1_000_000), "voice_xp": (0, 1_000_000),
          "attend_xp": (0, 1_000_000_000), "chat_cooldown": (0, 3600), "mute_percent": (0, 100)}
SHOP_MAX = 25
LIST_KEYS = ("formula", "roulette", "rewards", "jobs", "shop")


class RpError(StoreError):
    pass


def money(amount: int) -> str:
    return f"{COM} {amount:,} {COM_NAME}"


def _whole(value, low, high) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def check_config(raw) -> None:
    """저장 전 검사. 잘못되면 ValueError."""
    if not isinstance(raw, dict) or len(raw) > 20:
        raise ValueError("롤플레잉 설정이 올바르지 않아요.")


def config(raw) -> dict:
    """저장된 롤플레잉 설정을 쓸 수 있는 모양으로(잘못된 칸은 기본값)."""
    raw = raw if isinstance(raw, dict) else {}
    out = {k: v for k, v in DEFAULT_CONFIG.items() if k not in LIST_KEYS}
    for key, (low, high) in LIMITS.items():
        if _whole(raw.get(key), low, high):
            out[key] = raw[key]
    if isinstance(raw.get("label"), str) and 1 <= len(raw["label"].strip()) <= 10:
        out["label"] = raw["label"].strip()
    if raw.get("mute_mode") in MUTE_MODES:
        out["mute_mode"] = raw["mute_mode"]
    if _whole(raw.get("shop_channel"), 1, 10 ** 20):
        out["shop_channel"] = raw["shop_channel"]
    formula = raw.get("formula") if isinstance(raw.get("formula"), dict) else {}
    out["formula"] = {k: formula[k] if _whole(formula.get(k), low, high) else DEFAULT_FORMULA[k] for k, (low, high) in FORMULA_LIMITS.items()}
    shop = raw.get("shop") if isinstance(raw.get("shop"), list) else []
    out["shop"] = [dict(i) for i in shop if isinstance(i, dict) and _whole(i.get("id"), 1, 10 ** 9) and isinstance(i.get("name"), str)
                   and i.get("kind") in ("text", "role") and _whole(i.get("price"), 0, COM_MAX)][:SHOP_MAX]
    wheel = raw.get("roulette")
    out["roulette"] = [dict(s) for s in wheel] if isinstance(wheel, list) and wheel and all(
        isinstance(s, dict) and isinstance(s.get("name"), str) and _whole(s.get("times"), 0, 1000) and _whole(s.get("chance"), 0, 100) for s in wheel) \
        and sum(s["chance"] for s in wheel) == 100 else [dict(s) for s in DEFAULT_ROULETTE]
    rewards = raw.get("rewards") if isinstance(raw.get("rewards"), list) else []
    out["rewards"] = sorted([dict(r) for r in rewards if isinstance(r, dict) and _whole(r.get("level"), 1, LV_MAX) and _whole(r.get("role"), 1, 10 ** 20)],
                            key=lambda r: r["level"])[:REWARDS_MAX]
    jobs = raw.get("jobs") if isinstance(raw.get("jobs"), list) else []
    out["jobs"] = [dict(j) for j in jobs if isinstance(j, dict) and _whole(j.get("id"), 1, 10 ** 9) and isinstance(j.get("name"), str)][:JOBS_MAX]
    return out


# ---------------------------------------------------------------- 레벨
def total_for(level: int, cfg: dict) -> int:
    """LV level이 되는 데 필요한 누적 경험치 = ((나눗셈 × 레벨)² − 오프셋) ÷ 곱셈 + 오프셋(내림). LV 0은 0."""
    if level <= 0:
        return 0
    f = cfg["formula"]
    return max(0, ((f["div"] * level) ** 2 - f["offset"]) // f["mul"] + f["offset"])


def formula_text(cfg: dict) -> str:
    f = cfg["formula"]
    return f"누적 경험치 = (({f['div']} × 현재 레벨)² - {f['offset']}) ÷ {f['mul']} + {f['offset']}"


def level_of(exp: int, cfg: dict) -> tuple[int, int, int]:
    """누적 경험치로 (LV, 이번 LV에서 모은 양, 다음 LV까지 이번 LV에서 필요한 양). LV은 0부터. 최대 LV이면 필요한 양은 0."""
    low, high = 0, LV_MAX                                    # total_for는 레벨이 오를수록 커지므로 이분 탐색
    while low < high:
        mid = (low + high + 1) // 2
        if total_for(mid, cfg) <= exp:
            low = mid
        else:
            high = mid - 1
    if low >= LV_MAX:
        return LV_MAX, exp - total_for(LV_MAX, cfg), 0
    return low, exp - total_for(low, cfg), total_for(low + 1, cfg) - total_for(low, cfg)


# ---------------------------------------------------------------- 저장
def row(store, guild: int, user: int) -> dict:
    found = store.db.execute("SELECT * FROM rp WHERE guild=? AND user=?", (guild, user)).fetchone()
    if found is None:
        return {"com": 0, "exp": 0, "credit": 0, "job": None, "last_work": 0.0, "last_spin": 0.0, "last_attend": ""}
    return {k: found[k] for k in ("com", "exp", "credit", "job", "last_work", "last_spin", "last_attend")}


def _ensure(store, guild: int, user: int) -> None:
    store.db.execute("INSERT OR IGNORE INTO rp(guild, user) VALUES(?,?)", (guild, user))


def _gain(store, guild: int, user: int, amount: int, exp: int) -> tuple[int, int]:
    """(거래 안에서) COM을 더하고 EXP를 쌓는다. 최대 보유량을 넘으면 RpError. (지금 COM, 지금 EXP)."""
    _ensure(store, guild, user)
    now = row(store, guild, user)
    if now["com"] + amount > COM_MAX:
        raise RpError(f"{COM_NAME}은(는) 최대 {COM_MAX:,}개까지 가질 수 있어요.")
    store.db.execute("UPDATE rp SET com=com+?, exp=exp+? WHERE guild=? AND user=?", (amount, max(0, exp), guild, user))
    return now["com"] + amount, now["exp"] + max(0, exp)


def give(store, guild: int, user: int, amount: int, *, exp: bool = True) -> tuple[int, int]:
    """관리자 지급 등. exp면 EXP로도 쌓는다. (COM, EXP)."""
    with store.db:
        return _gain(store, guild, user, amount, amount if exp else 0)


# ---------------------------------------------------------------- 직업
def jobs(cfg: dict) -> list[dict]:
    return cfg["jobs"]


def job_by_name(cfg: dict, name: str) -> dict | None:
    needle = (name or "").strip().lower()
    return next((j for j in cfg["jobs"] if j["name"].lower() == needle), None)


def job_by_id(cfg: dict, job_id) -> dict | None:
    return next((j for j in cfg["jobs"] if j["id"] == job_id), None)


def clean_job_name(name: str) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= NAME_MAX or any(c in name for c in "@#<>`*_~|"):
        raise RpError(f"직업 이름은 1~{NAME_MAX}자로, @ # < > ` * _ ~ | 없이 써 주세요.")
    return name


def assign(store, guild: int, user: int, job_id: int | None) -> None:
    with store.db:
        _ensure(store, guild, user)
        store.db.execute("UPDATE rp SET job=?, last_work=0 WHERE guild=? AND user=?", (job_id, guild, user))


def pay_for(job: dict, level: int, cfg: dict) -> int:
    """LV에 따른 급여: 기본 급여 × (1 + LV × pay_step%). LV 0이면 기본 급여 그대로."""
    return job["pay"] * (100 + cfg["pay_step"] * max(0, level)) // 100


def work(store, guild: int, user: int, cfg: dict, now=None) -> dict:
    """일하기: 직업 급여(COM)를 받고 EXP가 쌓인다. 쿨타임이 안 끝났으면 RpError.
    {'job', 'pay', 'com', 'before', 'after'(LV)}."""
    t = time.time() if now is None else now
    me = row(store, guild, user)
    job = job_by_id(cfg, me["job"])
    if job is None:
        raise RpError("아직 직업이 없어요. 관리자에게 직업을 받아야 일할 수 있어요(`/직업목록`).")
    wait = me["last_work"] + job["cooldown"] * 60 - t
    if wait > 0:
        minutes, seconds = divmod(int(wait) + 1, 60)
        raise RpError(f"아직 쉬는 중이에요. {minutes}분 {seconds}초 뒤에 다시 일할 수 있어요.")
    before = level_of(me["exp"], cfg)[0]
    pay = pay_for(job, before, cfg)
    with store.db:
        com, exp = _gain(store, guild, user, pay, pay)
        store.db.execute("UPDATE rp SET last_work=? WHERE guild=? AND user=?", (t, guild, user))
    return {"job": job, "pay": pay, "com": com, "before": before, "after": level_of(exp, cfg)[0]}


def can_promote(store, guild: int, user: int, cfg: dict) -> tuple[dict | None, dict | None, int]:
    """(지금 직업, 승급할 직업, 필요한 LV). 승급 직업이 없으면 (직업, None, 0)."""
    me = row(store, guild, user)
    job = job_by_id(cfg, me["job"])
    if job is None or not job.get("next"):
        return job, None, 0
    return job, job_by_id(cfg, job["next"]), job.get("promote_level", 0)


def transfer(store, guild: int, giver: int, receiver: int, amount: int, cfg: dict) -> tuple[int, int]:
    """COM 주기(직업 능력): 'COM 주기' 능력이 있는 직업만 쓸 수 있다. COM만 옮기고 경험치는 늘리지 않는다
    (주고받기만 해서 레벨을 올리지 못하게). (준 사람의 남은 COM, 받은 사람의 COM)."""
    job = job_by_id(cfg, row(store, guild, giver)["job"])
    if job is None or not job.get("give"):
        raise RpError("COM 주기는 'COM 주기' 능력이 있는 직업만 쓸 수 있어요(`/직업목록`에서 확인해요).")
    if giver == receiver:
        raise RpError("나에게는 줄 수 없어요.")
    if amount < 1:
        raise RpError("1 이상 줘 주세요.")
    with store.db:
        me = row(store, guild, giver)
        if me["com"] < amount:
            raise RpError(f"{i_ga(COM_NAME)} 모자라요. (가진 것 {me['com']:,})")
        _ensure(store, guild, receiver)
        other = row(store, guild, receiver)
        if other["com"] + amount > COM_MAX:
            raise RpError(f"받는 사람의 {COM_NAME}이(가) 최대({COM_MAX:,})를 넘어서 줄 수 없어요.")
        store.db.execute("UPDATE rp SET com=com-? WHERE guild=? AND user=?", (amount, guild, giver))
        store.db.execute("UPDATE rp SET com=com+? WHERE guild=? AND user=?", (amount, guild, receiver))
    return me["com"] - amount, other["com"] + amount


# ---------------------------------------------------------------- 환전·룰렛
def exchange_to_com(store, guild: int, user: int, com: int, ram_max: int) -> dict:
    """RAM을 내고 COM을 받는다(10 RAM = 1 COM). '되산 몫'만큼은 EXP로 치지 않는다."""
    if com < 1:
        raise RpError("1 이상 바꿔 주세요.")
    cost = com * EXCHANGE_RATE
    with store.db:
        store._ensure_wallet(guild, user)
        have = store.wallet(guild, user)
        if have < cost:
            raise RpError(f"RAM이 모자라요. {com:,} {COM_NAME}에는 {cost:,} RAM이 필요해요(가진 것 {have:,}).")
        me = row(store, guild, user)
        refunded = min(me["credit"], com)
        store.db.execute("UPDATE wallets SET candy=candy-? WHERE guild=? AND user=?", (cost, guild, user))
        now_com, exp = _gain(store, guild, user, com, com - refunded)
        store.db.execute("UPDATE rp SET credit=credit-? WHERE guild=? AND user=?", (refunded, guild, user))
    return {"com": now_com, "exp_gain": com - refunded, "cost": cost, "exp": exp}


def exchange_to_ram(store, guild: int, user: int, com: int, ram_max: int) -> dict:
    """COM을 내고 RAM을 받는다(1 COM = 10 RAM). RAM 최대 보유량을 넘으면 거절. LV은 내려가지 않는다."""
    if com < 1:
        raise RpError("1 이상 바꿔 주세요.")
    gain = com * EXCHANGE_RATE
    with store.db:
        me = row(store, guild, user)
        if me["com"] < com:
            raise RpError(f"{i_ga(COM_NAME)} 모자라요. (가진 것 {me['com']:,})")
        store._ensure_wallet(guild, user)
        if store.wallet(guild, user) + gain > ram_max:
            raise RpError(f"RAM이 최대({ram_max:,})를 넘어서 바꿀 수 없어요.")
        store.db.execute("UPDATE rp SET com=com-?, credit=credit+? WHERE guild=? AND user=?", (com, com, guild, user))
        store.db.execute("UPDATE wallets SET candy=candy+? WHERE guild=? AND user=?", (gain, guild, user))
    return {"com": me["com"] - com, "gain": gain}


def spin(store, guild: int, user: int, bet: int, cfg: dict, rng, now=None) -> dict:
    """룰렛: COM을 걸고 칸을 뽑는다. 돌려받는 것 = 건 것 × 배수. 딴 만큼만 EXP. {'slot', 'payout', 'com', 'before', 'after'}."""
    t = time.time() if now is None else now
    if not 1 <= bet <= cfg["bet_max"]:
        raise RpError(f"한 번에 1~{cfg['bet_max']:,} {COM_NAME}까지 걸 수 있어요.")
    me = row(store, guild, user)
    wait = me["last_spin"] + cfg["spin_cooldown"] - t
    if wait > 0:
        raise RpError(f"{int(wait) + 1}초 뒤에 다시 돌릴 수 있어요.")
    if me["com"] < bet:
        raise RpError(f"{i_ga(COM_NAME)} 모자라요. (가진 것 {me['com']:,})")
    draw, edge, slot = rng.random() * 100, 0, cfg["roulette"][-1]
    for candidate in cfg["roulette"]:
        edge += candidate["chance"]
        if draw < edge:
            slot = candidate
            break
    payout = bet * slot["times"]
    before = level_of(me["exp"], cfg)[0]
    with store.db:
        store.db.execute("UPDATE rp SET com=com-?, last_spin=? WHERE guild=? AND user=?", (bet, t, guild, user))
        com, exp = _gain(store, guild, user, payout, payout - bet)
    return {"slot": slot, "payout": payout, "com": com, "before": before, "after": level_of(exp, cfg)[0]}


# ---------------------------------------------------------------- 활동 경험치(= COM): 채팅·음성·출석체크
def gain(store, guild: int, user: int, amount: int, cfg: dict) -> tuple[int, int] | None:
    """활동으로 COM(=경험치)을 준다. (LV 전, LV 후). 최대 보유량에 막히면 None(아무것도 안 준다)."""
    if amount <= 0:
        return None
    before = level_of(row(store, guild, user)["exp"], cfg)[0]
    try:
        with store.db:
            _com, exp = _gain(store, guild, user, amount, amount)
    except RpError:
        return None
    return before, level_of(exp, cfg)[0]


def attend(store, guild: int, user: int, cfg: dict, today: str) -> tuple[int, int, int]:
    """출석체크(하루 한 번, today는 'YYYY-MM-DD'). (받은 양, LV 전, LV 후). 이미 했으면 RpError."""
    me = row(store, guild, user)
    if me["last_attend"] == today:
        raise RpError("오늘은 이미 출석체크를 했어요. 내일(한국 시간 자정 이후) 다시 해 주세요.")
    before = level_of(me["exp"], cfg)[0]
    with store.db:
        _com, exp = _gain(store, guild, user, cfg["attend_xp"], cfg["attend_xp"])
        store.db.execute("UPDATE rp SET last_attend=? WHERE guild=? AND user=?", (today, guild, user))
    return cfg["attend_xp"], before, level_of(exp, cfg)[0]


def voice_amount(cfg: dict, self_mute: bool, self_deaf: bool) -> int:
    """음성 채널 5분마다 받을 양. 마이크·헤드셋을 스스로 끈 사람은 설정대로 막거나 줄인다(서버 뮤트는 상관없다)."""
    amount = cfg["voice_xp"]
    if not (self_mute or self_deaf) or cfg["mute_mode"] == "off":
        return amount
    if cfg["mute_mode"] == "block":
        return 0
    return amount * (100 - cfg["mute_percent"]) // 100


# ---------------------------------------------------------------- COM 상점
def shop_item(cfg: dict, name: str) -> dict | None:
    needle = (name or "").strip().lower()
    return next((i for i in cfg["shop"] if i["name"].lower() == needle), None)


def spend(store, guild: int, user: int, price: int) -> int:
    """COM을 낸다(경험치·LV은 그대로). 모자라면 RpError. 남은 COM."""
    with store.db:
        me = row(store, guild, user)
        if me["com"] < price:
            raise RpError(f"{i_ga(COM_NAME)} 모자라요. (가진 것 {me['com']:,}, 필요한 것 {price:,})")
        _ensure(store, guild, user)
        store.db.execute("UPDATE rp SET com=com-? WHERE guild=? AND user=?", (price, guild, user))
    return me["com"] - price


def refund(store, guild: int, user: int, price: int) -> None:
    """산 것을 주지 못했을 때 COM을 돌려준다(경험치는 늘리지 않는다)."""
    with store.db:
        _ensure(store, guild, user)
        store.db.execute("UPDATE rp SET com=MIN(com+?, ?) WHERE guild=? AND user=?", (price, COM_MAX, guild, user))


def rewards_between(cfg: dict, before: int, after: int) -> list[dict]:
    """LV before → after로 오르며 닿은 레벨 보상."""
    return [r for r in cfg["rewards"] if before < r["level"] <= after]


def rewards_upto(cfg: dict, level: int) -> list[dict]:
    return [r for r in cfg["rewards"] if r["level"] <= level]
