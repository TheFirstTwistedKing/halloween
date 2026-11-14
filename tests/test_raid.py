"""레이드: 관리자가 열고, 최대 50명이 기지 장비로 함께 싸우고, 이기면 준 피해 비율대로 보상(지면 보상 없음)."""
import unittest
from unittest.mock import AsyncMock

import guilds
import raid
from balance import DEFAULT
from cogs.raid import Raid, RaidView, raid_embed
from raid import RaidError
from store import Store
from tests.helpers import World, sent_embed

G, CH = 100, 400
A, B, C = 1, 2, 3
T0 = 1_000_000.0
B_ = DEFAULT.gear_by_key


class Dice:
    """정해 둔 값을 돌려주는 주사위. sample은 앞에서부터 고른다(누가 맞을지 정해 둔다)."""

    def __init__(self, randoms=None):
        self.randoms = list(randoms or [])

    def random(self):
        return self.randoms.pop(0) if self.randoms else 0.99

    def sample(self, population, k):
        return list(population)[:k]


def boss(**kw):
    data = dict(name="거대 코어", emoji="🐉", hp=10000, damage=100)
    data.update(kw)
    return raid.check_boss(**data)


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def display(self, user, **slots):
        keys = list(slots.values())
        self.store.vault_put(G, user, [("gear", k, 1) for k in keys])
        for slot, key in slots.items():
            self.store.set_display(G, user, slot, key)

    def opened(self, **kw):
        options = dict(minutes=10, reward=1000, min_reward=10, attack_every=8, cooldown=3)
        options.update({k: kw.pop(k) for k in list(kw) if k in options})
        return raid.open_raid(self.store, G, CH, boss(**kw), now=T0, **options)

    def started(self, users=(A,), **kw):
        raid_id = self.opened(**kw)
        for u in users:
            raid.join(self.store, G, u, now=T0)
        raid.start(self.store, G, now=T0)
        return raid_id

    def data(self, raid_id):
        return raid.boss(raid.get(self.store, raid_id))

    def member(self, raid_id, user):
        return next(m for m in raid.members(self.store, raid_id) if m["user"] == user)


class OpenTests(Base):
    def test_boss_checks(self):
        for bad in (dict(name=""), dict(name="@everyone"), dict(hp=0), dict(hp=raid.HP_MAX + 1), dict(damage=-1),
                    dict(skill="fly", skill_every=1, skill_power=1), dict(skill="strike", skill_every=0, skill_power=200)):
            with self.assertRaises(RaidError, msg=bad):
                boss(**bad)
        self.assertEqual(boss(skill="burn", skill_every=2, skill_power=30, skill_turns=2, skill_name="불바다")["skill_name"], "불바다")

    def test_only_one_raid_at_a_time_and_ranges(self):
        self.opened()
        with self.assertRaises(RaidError):
            self.opened()
        raid.cancel(self.store, G)
        self.assertIsNone(raid.current(self.store, G))
        for bad in (dict(minutes=0), dict(attack_every=2), dict(cooldown=61), dict(reward=-1)):
            with self.assertRaises(RaidError, msg=bad):
                self.opened(**bad)

    def test_start_needs_someone(self):
        self.opened()
        with self.assertRaisesRegex(RaidError, "참가한 사람"):
            raid.start(self.store, G, now=T0)
        raid.join(self.store, G, A, now=T0)
        row = raid.start(self.store, G, now=T0)
        self.assertEqual((row["state"], row["ends"]), ("active", T0 + 600))
        with self.assertRaises(RaidError):
            raid.start(self.store, G, now=T0)


class JoinTests(Base):
    def test_stats_come_from_the_base_display_in_full(self):
        self.display(A, weapon="longsword", shield="shield", armor="chain_armor")
        self.opened()
        stats = raid.join(self.store, G, A, now=T0)
        self.assertEqual(stats["attack"], DEFAULT.base_damage + B_["longsword"].attack)
        self.assertEqual((stats["guard"], stats["reflect"], stats["reduction"]), (B_["shield"].guard, B_["shield"].reflect, B_["chain_armor"].reduction))
        self.assertEqual(stats["hp"], DEFAULT.max_hp)
        bare = raid.join(self.store, G, B, now=T0)                                      # 전시한 게 없으면 맨손·맨몸
        self.assertEqual((bare["attack"], bare["reduction"], bare["skill"]), (DEFAULT.base_damage, 0, None))
        self.assertEqual(self.store.base(G, A)["weapon"], "longsword")                  # 장비는 그대로 남는다

    def test_guild_gear_buff_applies(self):
        self.display(A, weapon="longsword")
        self.store.update_settings(G, guild_cost=0)
        guilds.create(self.store, G, A, "강철단")
        guilds.set_buff(self.store, G, A, "gear")
        self.opened()
        self.assertEqual(raid.join(self.store, G, A, now=T0)["attack"], DEFAULT.base_damage + B_["longsword"].attack + 80)

    def test_limits(self):
        with self.assertRaises(RaidError):
            raid.join(self.store, G, A, now=T0)                                         # 열린 레이드가 없다
        raid_id = self.opened()
        raid.join(self.store, G, A, now=T0)
        with self.assertRaises(RaidError):
            raid.join(self.store, G, A, now=T0)
        for u in range(10, 10 + raid.MAX_MEMBERS - 1):
            raid.join(self.store, G, u, now=T0)
        self.assertEqual(len(raid.members(self.store, raid_id)), 50)
        with self.assertRaisesRegex(RaidError, "꽉 찼어요"):
            raid.join(self.store, G, 999, now=T0)

    def test_joining_during_the_fight(self):
        self.started()
        raid.join(self.store, G, B, now=T0 + 5)
        self.assertEqual(len(raid.members(self.store, raid.current(self.store, G)["id"])), 2)


class ActTests(Base):
    def test_attack_crit_and_cooldown(self):
        self.display(A, weapon="dagger")
        raid_id = self.started()
        base = DEFAULT.base_damage + B_["dagger"].attack
        raid.act(self.store, G, A, "attack", Dice([0.99]), now=T0 + 1)
        self.assertEqual(self.data(raid_id)["hp"], 10000 - base)
        with self.assertRaisesRegex(RaidError, "초 뒤"):
            raid.act(self.store, G, A, "attack", Dice(), now=T0 + 2)
        line = raid.act(self.store, G, A, "attack", Dice([0.0]), now=T0 + 4)              # 레이저 커터 크리티컬
        self.assertIn("크리티컬", line)
        self.assertEqual(self.data(raid_id)["hp"], 10000 - base * 3)
        self.assertEqual(self.member(raid_id, A)["damage"], base * 3)

    def test_refusals(self):
        self.opened()
        raid.join(self.store, G, A, now=T0)
        with self.assertRaisesRegex(RaidError, "시작"):
            raid.act(self.store, G, A, "attack", Dice(), now=T0)                         # 아직 모집 중
        raid.start(self.store, G, now=T0)
        with self.assertRaisesRegex(RaidError, "참가"):
            raid.act(self.store, G, B, "attack", Dice(), now=T0)
        with self.assertRaisesRegex(RaidError, "스킬이 없어요"):
            raid.act(self.store, G, A, "skill", Dice(), now=T0)
        with self.assertRaises(RaidError):
            raid.act(self.store, G, A, "dance", Dice(), now=T0)

    def test_skills(self):
        content_changes = {"power": 300, "multi": 4, "drain": 50, "stun": 100, "pierce": 200}
        for kind, power in content_changes.items():
            self.store.close()
            self.setUp()
            import content
            content.edit(self.store, G, ("gear",), "플라즈마 블레이드", {"skill": kind, "skill_power": power, "skill_cooldown": 2})
            self.display(A, weapon="longsword")
            raid_id = self.started()
            data = self.data(raid_id)
            data.update(barrier=50, barrier_turns=1)
            raid._save(self.store, raid_id, data)
            with self.store.db:                                                          # 흡혈을 보려고 체력을 깎아 둔다
                m = self.member(raid_id, A)
                m["hp"] = 500
                raid._save_member(self.store, raid_id, m)
            base = DEFAULT.base_damage + B_["longsword"].attack
            raid.act(self.store, G, A, "skill", Dice(), now=T0 + 1)
            dealt = 10000 - self.data(raid_id)["hp"]
            expected = {"power": base * 3, "multi": base * 4, "drain": base, "stun": base, "pierce": base * 2}[kind]
            self.assertEqual(dealt, expected if kind == "pierce" else expected // 2, kind)     # 관통만 방어막을 무시한다
            if kind == "drain":
                self.assertEqual(self.member(raid_id, A)["hp"], 500 + dealt // 2)
            if kind == "stun":
                self.assertTrue(self.data(raid_id)["stunned"])
            with self.assertRaisesRegex(RaidError, "스킬은"):
                raid.act(self.store, G, A, "skill", Dice(), now=T0 + 5)                  # 스킬 쿨타임 2 × 3초 × 2 = 12초
            raid.act(self.store, G, A, "skill", Dice(), now=T0 + 13.5)


class BossTests(Base):
    def test_boss_attacks_on_schedule_with_armor(self):
        self.display(A, armor="chain_armor")
        raid_id = self.started(users=(A, B))
        raid.tick(self.store, G, Dice(), now=T0 + 7)
        self.assertEqual(self.member(raid_id, A)["hp"], 1000)
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        self.assertEqual(self.member(raid_id, A)["hp"], 1000 - (100 - 100 * B_["chain_armor"].reduction // 100))
        self.assertEqual(self.member(raid_id, B)["hp"], 1000)                            # 2명이면 한 명만 맞는다
        raid.tick(self.store, G, Dice(), now=T0 + 24)                                    # 밀린 두 번
        self.assertEqual(self.data(raid_id)["turn"], 3)

    def test_targets_scale_with_alive_members(self):
        raid_id = self.started(users=tuple(range(10, 21)))                               # 11명 → 3명
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        self.assertEqual(sum(1 for m in raid.members(self.store, raid_id) if m["hp"] < 1000), 3)

    def test_defend_reduces_and_reflects_then_ends(self):
        self.display(A, shield="shield")
        raid_id = self.started()
        raid.act(self.store, G, A, "defend", Dice(), now=T0 + 1)
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        shield = B_["shield"]
        self.assertEqual(self.member(raid_id, A)["hp"], 1000 - (100 - 100 * shield.guard // 100))
        self.assertEqual(self.data(raid_id)["hp"], 10000 - 100 * shield.reflect // 100)
        self.assertFalse(self.member(raid_id, A)["guarding"])
        raid.tick(self.store, G, Dice(), now=T0 + 16)
        self.assertEqual(self.member(raid_id, A)["hp"], 1000 - (100 - 100 * shield.guard // 100) - 100)

    def test_down_and_all_down_loses(self):
        raid_id = self.started(users=(A,), damage=600)
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        self.assertEqual(raid.get(self.store, raid_id)["state"], "active")
        self.assertEqual(raid.tick(self.store, G, Dice(), now=T0 + 16), "lost")
        self.assertTrue(self.member(raid_id, A)["down"])
        self.assertEqual(raid.get(self.store, raid_id)["state"], "lost")
        self.assertEqual(self.store.wallet(G, A), 0)                                     # 지면 보상 없음

    def test_time_up_loses_and_down_cannot_act(self):
        raid_id = self.started(users=(A, B), damage=1000, attack_every=120, minutes=1)
        raid.tick(self.store, G, Dice(), now=T0 + 59)
        self.assertEqual(raid.tick(self.store, G, Dice(), now=T0 + 60), "lost")
        self.assertEqual(raid.get(self.store, raid_id)["state"], "lost")
        self.store.close()
        self.setUp()
        raid_id = self.started(users=(A, B), damage=1000)
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        with self.assertRaisesRegex(RaidError, "다운"):
            raid.act(self.store, G, A, "attack", Dice(), now=T0 + 9)

    def test_stun_skips_an_attack(self):
        raid_id = self.started()
        data = self.data(raid_id)
        data["stunned"] = True
        raid._save(self.store, raid_id, data)
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        self.assertEqual(self.member(raid_id, A)["hp"], 1000)
        self.assertFalse(self.data(raid_id)["stunned"])

    def test_boss_skills(self):
        raid_id = self.started(skill="strike", skill_every=2, skill_power=300)
        raid.tick(self.store, G, Dice(), now=T0 + 16)
        self.assertEqual(self.member(raid_id, A)["hp"], 1000 - 100 - 300)
        self.store.close()
        self.setUp()
        raid_id = self.started(skill="heal", skill_every=1, skill_power=10)
        raid.act(self.store, G, A, "attack", Dice(), now=T0 + 1)
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        self.assertEqual(self.data(raid_id)["hp"], 10000)
        self.store.close()
        self.setUp()
        raid_id = self.started(skill="barrier", skill_every=1, skill_power=50, skill_turns=2)
        raid.tick(self.store, G, Dice(), now=T0 + 8)
        raid.act(self.store, G, A, "attack", Dice(), now=T0 + 9)
        self.assertEqual(self.data(raid_id)["hp"], 10000 - DEFAULT.base_damage // 2)
        self.store.close()
        self.setUp()
        raid_id = self.started(users=(A, B), skill="burn", skill_every=1, skill_power=40, skill_turns=2)
        raid.tick(self.store, G, Dice(), now=T0 + 16)
        self.assertEqual((self.member(raid_id, A)["hp"], self.member(raid_id, B)["hp"]), (960, 960))

    def test_phase_two(self):
        raid_id = self.started(hp=10, phase2_hp=500, phase2_damage=300)
        raid.act(self.store, G, A, "attack", Dice(), now=T0 + 1)
        data = self.data(raid_id)
        self.assertEqual((data["phase"], data["hp"], data["damage"]), (2, 500, 300))
        self.assertEqual(raid.get(self.store, raid_id)["state"], "active")


class RewardTests(Base):
    def test_win_splits_by_damage_plus_participation_once(self):
        self.display(A, weapon="longsword")
        raid_id = self.started(users=(A, B, C), hp=360, reward=1000, min_reward=10)
        raid.act(self.store, G, A, "attack", Dice(), now=T0 + 1)                          # 120
        raid.act(self.store, G, B, "attack", Dice(), now=T0 + 1)                          # 20
        data = self.data(raid_id)
        data["hp"] = 1
        raid._save(self.store, raid_id, data)
        raid.act(self.store, G, B, "attack", Dice(), now=T0 + 4)                          # 1 (남은 만큼만)
        row = raid.get(self.store, raid_id)
        self.assertEqual(row["state"], "won")
        damage = {m["user"]: m["damage"] for m in raid.members(self.store, raid_id)}
        self.assertEqual((damage[B], self.data(raid_id)["hp"]), (DEFAULT.base_damage + 1, 0))      # 남은 체력만큼만 기여도로 친다
        total = sum(damage.values())
        for u in (A, B, C):
            self.assertEqual(self.store.wallet(G, u), 1000 * damage[u] // total + 10)
        self.assertEqual(raid.pay(self.store, G, raid_id), {})                          # 한 번만
        self.assertEqual(self.store.wallet(G, C), 10)
        self.assertIn("받은 보상", "".join(f.name for f in raid_embed(self.store, row).fields))

    def test_wallet_cap(self):
        self.store.update_settings(G, currency_max=50)
        raid_id = self.started(hp=1, reward=1000)
        raid.act(self.store, G, A, "attack", Dice(), now=T0 + 1)
        self.assertEqual(self.store.wallet(G, A), 50)
        self.assertEqual(raid.boss(raid.get(self.store, raid_id))["payout"], {str(A): 50})


class CogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.channel = self.w.channel(CH)
        self.cog = Raid(self.bot, rng=Dice())
        self.message = type("M", (), {"id": 777})()

    async def asyncTearDown(self):
        await self.w.close()

    async def admin(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        i.original_response = AsyncMock(return_value=self.message)
        await command.callback(self.cog, i, *args, **kwargs)
        return i

    def press(self, uid, action):
        i = self.w.interaction(uid=uid)
        i.message = self.message
        return i

    async def test_open_join_start_attack_and_refresh(self):
        i = await self.admin(Raid.open, "거대 코어", 5000, 50, reward=100)
        embed, kwargs = sent_embed(i)
        self.assertIn("레이드", embed.title)
        self.assertIsInstance(i.response.send_message.call_args.kwargs["view"], RaidView)
        row = raid.current(self.store, G)
        self.assertEqual((row["message"], row["channel"]), (777, CH))
        join = self.press(A, "join")
        await self.cog.press(join, "join")
        join.response.edit_message.assert_awaited()                                     # 화면을 바로 고친다
        again = self.press(A, "join")
        await self.cog.press(again, "join")
        self.assertTrue(again.response.send_message.call_args.kwargs["ephemeral"])      # 안 되는 행동은 본인에게만
        await self.admin(Raid.begin)
        self.assertEqual(raid.current(self.store, G)["state"], "active")
        hit = self.press(A, "attack")
        self.cog.last_edit[row["id"]] = 10 ** 12                                        # 방금 고쳤다면
        await self.cog.press(hit, "attack")
        hit.response.defer.assert_awaited()                                             # 다음 정리 때 모아서 고친다
        partial = type("P", (), {"edit": AsyncMock()})()
        self.channel.get_partial_message = lambda mid: partial
        self.bot.get_channel = lambda cid: self.channel if cid == CH else None
        self.assertEqual(await self.cog.refresh_now(), [row["id"]])
        partial.edit.assert_awaited()
        self.assertEqual(await self.cog.refresh_now(), [])                               # 바뀐 게 없으면 고치지 않는다

    async def test_admin_only_errors_and_cancel(self):
        i = await self.admin(Raid.open, "코어", 100, 1, admin=False)
        self.assertIn("관리자만", sent_embed(i)[0].description)
        i = await self.admin(Raid.begin)
        self.assertIn("모집 중인 레이드가 없어요", sent_embed(i)[0].description)
        i = await self.admin(Raid.open, "코어", 100, 1, emoji="abc")
        self.assertIn("이모지", sent_embed(i)[0].description)
        await self.admin(Raid.open, "코어", 100, 1)
        i = await self.admin(Raid.open, "코어2", 100, 1)
        self.assertIn("이미", sent_embed(i)[0].description)
        await self.admin(Raid.cancel)
        self.assertIsNone(raid.current(self.store, G))
        ended = self.press(A, "join")
        await self.cog.press(ended, "join")
        self.assertIn("끝났어요", ended.response.send_message.call_args.kwargs["embed"].description)


if __name__ == "__main__":
    unittest.main()
