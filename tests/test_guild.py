"""길드 기본: 만들기·가입 신청/수락·탈퇴·추방·위임·해체·금고·버프·랭킹, 던전 버프, 명령어."""
import json
import unittest

import dungeon
import dungeon_service as service
import guilds
from balance import ARMOR_MAX, DEFAULT
from cogs.dungeon import run_embed
from cogs.guild import Guild, guild_embed
from dungeon import ROOMS, Run
from guilds import GuildError
from services import Locks
from store import DEFAULT_SETTINGS, Store, validate_settings
from tests.helpers import Scripted, World, sent_embed

G = 100
A, B, C, D = 1, 2, 3, 4
B_ = DEFAULT.gear_by_key


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        for u in (A, B, C, D):
            self.store.add_candy(G, u, 5000)

    def tearDown(self):
        self.store.close()

    def make(self, leader=A, name="강철단"):
        return guilds.create(self.store, G, leader, name)

    def join(self, row, user):
        guilds.apply(self.store, G, user, row["id"])
        guilds.accept(self.store, G, row["leader"], user)


class CreateTests(Base):
    def test_create_costs_and_makes_a_leader(self):
        row = self.make()
        self.assertEqual((row["name"], row["leader"], row["vault"], row["buff"]), ("강철단", A, 0, None))
        self.assertEqual(self.store.wallet(G, A), 4000)
        self.assertEqual(guilds.members(self.store, G, row["id"]), [A])
        self.assertEqual(guilds.of_user(self.store, G, A)["id"], row["id"])

    def test_refusals(self):
        self.make()
        with self.assertRaisesRegex(GuildError, "이미 길드"):
            self.make(A, "두번째단")                                                     # 이미 길드에 있다
        with self.assertRaises(GuildError):
            self.make(B, "강철단")                                                      # 이름 겹침
        with self.assertRaises(GuildError):
            self.make(B, "  ")
        with self.assertRaises(GuildError):
            self.make(B, "@everyone")
        with self.assertRaises(GuildError):
            self.make(B, "가" * 21)
        self.store.update_settings(G, guild_cost=10 ** 6)
        with self.assertRaises(GuildError):
            self.make(B, "부자단")
        self.assertEqual(self.store.wallet(G, B), 5000)
        self.store.update_settings(G, guild_cost=0)
        self.make(B, "무료단")
        self.assertEqual(self.store.wallet(G, B), 5000)

    def test_settings_are_checked(self):
        for bad in (dict(guild_cost=-1), dict(guild_size=1), dict(guild_size=101), dict(guild_cost=True)):
            with self.assertRaises(ValueError, msg=bad):
                validate_settings(dict(DEFAULT_SETTINGS, **bad))


class MemberTests(Base):
    def test_apply_accept_decline(self):
        row = self.make()
        other = self.make(D, "다른단")
        guilds.apply(self.store, G, B, row["id"])
        guilds.apply(self.store, G, B, other["id"])
        with self.assertRaises(GuildError):
            guilds.apply(self.store, G, B, row["id"])                                   # 두 번 신청
        self.assertEqual(guilds.requests(self.store, G, row["id"]), [B])
        with self.assertRaises(GuildError):
            guilds.accept(self.store, G, B, B)                                          # 길드장만
        guilds.accept(self.store, G, A, B)
        self.assertEqual(guilds.members(self.store, G, row["id"]), [A, B])
        self.assertEqual(guilds.requests(self.store, G, other["id"]), [])                # 다른 신청은 지워진다
        with self.assertRaises(GuildError):
            guilds.apply(self.store, G, B, other["id"])                                 # 이미 길드에 있다
        guilds.apply(self.store, G, C, row["id"])
        guilds.decline(self.store, G, A, C)
        self.assertEqual(guilds.requests(self.store, G, row["id"]), [])
        with self.assertRaises(GuildError):
            guilds.decline(self.store, G, A, C)

    def test_size_limit(self):
        self.store.update_settings(G, guild_size=2)
        row = self.make()
        guilds.apply(self.store, G, B, row["id"])
        guilds.apply(self.store, G, C, row["id"])
        guilds.accept(self.store, G, A, B)
        with self.assertRaises(GuildError):
            guilds.accept(self.store, G, A, C)
        with self.assertRaises(GuildError):
            guilds.apply(self.store, G, D, row["id"])

    def test_someone_who_joined_elsewhere_cannot_be_accepted(self):
        row, other = self.make(), self.make(D, "다른단")
        guilds.apply(self.store, G, B, row["id"])
        guilds.apply(self.store, G, B, other["id"])
        with self.store.db:                                                             # 다른 길드에 이미 들어간 상황을 만든다
            self.store.db.execute("INSERT INTO guild_members VALUES(?,?,?)", (G, B, other["id"]))
        with self.assertRaisesRegex(GuildError, "다른 길드"):
            guilds.accept(self.store, G, A, B)
        self.assertEqual(guilds.requests(self.store, G, row["id"]), [])

    def test_leave_kick_transfer(self):
        row = self.make()
        self.join(row, B)
        self.join(row, C)
        with self.assertRaisesRegex(GuildError, "위임"):
            guilds.leave(self.store, G, A)
        guilds.leave(self.store, G, C)
        self.assertEqual(guilds.members(self.store, G, row["id"]), [A, B])
        with self.assertRaises(GuildError):
            guilds.kick(self.store, G, B, A)                                            # 길드원은 못 한다
        with self.assertRaises(GuildError):
            guilds.kick(self.store, G, A, A)
        with self.assertRaises(GuildError):
            guilds.transfer(self.store, G, A, C)                                        # 길드원이 아니다
        guilds.transfer(self.store, G, A, B)
        self.assertEqual(guilds.members(self.store, G, row["id"]), [B, A])              # 길드장이 맨 앞
        guilds.kick(self.store, G, B, A)
        with self.assertRaises(GuildError):
            guilds.kick(self.store, G, B, A)
        _row, gone = guilds.leave(self.store, G, B)                                     # 혼자 남은 길드장이 나가면 해체
        self.assertTrue(gone)
        self.assertIsNone(guilds.get(self.store, G, row["id"]))
        with self.assertRaises(GuildError):
            guilds.leave(self.store, G, B)


class VaultTests(Base):
    def test_deposit_pay_and_disband_split(self):
        row = self.make()
        self.join(row, B)
        self.join(row, C)
        self.assertEqual(guilds.deposit(self.store, G, B, 1000), 1000)
        self.assertEqual(self.store.wallet(G, B), 4000)
        with self.assertRaises(GuildError):
            guilds.deposit(self.store, G, B, 10 ** 6)
        with self.assertRaises(GuildError):
            guilds.deposit(self.store, G, D, 10)                                        # 길드에 없다
        with self.assertRaises(GuildError):
            guilds.pay(self.store, G, B, B, 10)                                         # 길드장만
        with self.assertRaises(GuildError):
            guilds.pay(self.store, G, A, D, 10)                                         # 길드원이 아니다
        with self.assertRaises(GuildError):
            guilds.pay(self.store, G, A, C, 1001)
        self.assertEqual(guilds.pay(self.store, G, A, C, 1), 999)
        self.assertEqual(self.store.wallet(G, C), 5001)
        paid = guilds.disband(self.store, G, A)
        self.assertEqual(paid, {A: 333, B: 333, C: 333})
        self.assertEqual((self.store.wallet(G, A), self.store.wallet(G, B)), (4333, 4333))
        self.assertIsNone(guilds.of_user(self.store, G, B))

    def test_paying_past_the_wallet_limit_is_refused(self):
        row = self.make()
        self.join(row, B)
        guilds.deposit(self.store, G, A, 100)
        self.store.update_settings(G, currency_max=5000)
        with self.assertRaises(GuildError):
            guilds.pay(self.store, G, A, B, 1)
        self.assertEqual(guilds.get(self.store, G, row["id"])["vault"], 100)

    def test_disband_remainder_goes_to_the_leader(self):
        row = self.make()
        self.join(row, B)
        guilds.deposit(self.store, G, A, 5)
        self.assertEqual(guilds.disband(self.store, G, A), {A: 3, B: 2})


class BuffAndRankTests(Base):
    def test_buff_and_ranking(self):
        row = self.make()
        self.join(row, B)
        with self.assertRaises(GuildError):
            guilds.set_buff(self.store, G, B, "gear")
        with self.assertRaises(GuildError):
            guilds.set_buff(self.store, G, A, "fly")
        guilds.set_buff(self.store, G, A, "ram")
        self.assertEqual((guilds.buff_of(self.store, G, A), guilds.buff_of(self.store, G, B), guilds.buff_of(self.store, G, C)), ("ram", "ram", None))
        rich = self.make(C, "부자단")
        self.store.add_candy(G, C, 10 ** 6)
        names = [e["row"]["name"] for e in guilds.ranking(self.store, G)]
        self.assertEqual(names, ["부자단", "강철단"])                                    # 승리가 같으면 재화 합계
        with self.store.db:
            self.store.db.execute("UPDATE guilds SET wins=1 WHERE id=?", (row["id"],))
        self.assertEqual([e["row"]["name"] for e in guilds.ranking(self.store, G)], ["강철단", "부자단"])
        self.assertIsNotNone(rich)


class DungeonBuffTests(unittest.TestCase):
    def test_gear_buff_only_with_that_gear_equipped(self):
        run = dungeon.new_run()
        plain = (dungeon.player_damage(run), dungeon.guard_percent(run), dungeon.armor_percent(run))
        run.guild_buff = "gear"
        self.assertEqual((dungeon.player_damage(run), dungeon.guard_percent(run), dungeon.armor_percent(run)), plain)      # 맨손·맨몸이면 없다
        run.weapon, run.shield, run.armor = "longsword", "wooden_shield", "chain_armor"
        self.assertEqual(dungeon.player_damage(run), DEFAULT.base_damage + B_["longsword"].attack + 80)
        self.assertEqual(dungeon.guard_percent(run), min(100, B_["wooden_shield"].guard + 10))
        self.assertEqual(dungeon.armor_percent(run), min(ARMOR_MAX, B_["chain_armor"].reduction + 2))
        run.guild_buff = "ram"
        self.assertEqual(dungeon.player_damage(run), DEFAULT.base_damage + B_["longsword"].attack)

    def test_ram_gain(self):
        run = dungeon.new_run()
        self.assertEqual(dungeon.ram_gain(run, 1000), 1000)
        run.guild_buff = "gear"
        self.assertEqual(dungeon.ram_gain(run, 1000), 1000)                              # 장비 강화 버프는 재화를 늘리지 않는다
        run.guild_buff = "ram"
        self.assertEqual((dungeon.ram_gain(run, 1000), dungeon.ram_gain(run, 30), dungeon.ram_gain(run, 0)), (1020, 31, 0))

    def test_old_saves(self):
        data = json.loads(dungeon.new_run().to_json())
        data.pop("guild_buff")
        self.assertIsNone(Run.from_json(json.dumps(data)).guild_buff)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, A, self.store.ensure_system_items(G)["key"], 5)
        self.store.update_settings(G, guild_cost=0)
        self.row = guilds.create(self.store, G, A, "강철단")

    def tearDown(self):
        self.store.close()

    async def test_the_buff_is_set_on_entry_and_boosts_the_clear_reward(self):
        guilds.set_buff(self.store, G, A, "ram")
        out = await service.enter(self.store, self.locks, G, A)
        self.assertEqual(out.run.guild_buff, "ram")
        self.assertTrue(any("길드 버프" in e for e in out.run.events))
        self.assertIn("🛡️ 길드 버프", [f.name for f in run_embed(out.run, None, DEFAULT).fields])
        run = out.run
        run.floor, run.room, run.phase, run.kind = self.store.balance(G).floors, ROOMS, "summit", "summit"
        self.store.save_dungeon(G, A, run.to_json())
        done = await service.act(self.store, self.locks, Scripted(randoms=[0.99] * 5, ints=[100]), G, A, run.rev, "finish")
        self.assertEqual(done.reward.candy, 102)
        self.assertEqual(self.store.wallet(G, A), 102)

    async def test_infinite_bonus_is_boosted_too(self):
        guilds.set_buff(self.store, G, A, "ram")
        self.store.update_settings(G, dungeon={"상점.간격": 0, "일반.시크릿확률": 0})        # 상점 없이 바로 11층으로
        out = await service.enter(self.store, self.locks, G, A)
        run = out.run
        run.floor, run.room, run.phase, run.kind = self.store.balance(G).floors, ROOMS, "summit", "summit"
        self.store.save_dungeon(G, A, run.to_json())
        rng = Scripted(randoms=[0.99] * 40, ints=[100])
        done = await service.act(self.store, self.locks, rng, G, A, run.rev, "infinite")
        bal = self.store.balance(G)
        self.assertTrue(done.ok, done.text)
        self.assertEqual(self.store.wallet(G, A), 102 + dungeon.ram_gain(done.run, bal.infinite_bonus))

    async def test_no_guild_no_buff(self):
        guilds.leave(self.store, G, A)
        out = await service.enter(self.store, self.locks, G, A)
        self.assertIsNone(out.run.guild_buff)
        self.assertFalse(any("길드 버프" in e for e in out.run.events))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Guild(self.bot)
        for u in (A, B):
            self.store.add_candy(G, u, 5000)
            self.w.new_member(u)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, uid=A, admin=False, **kwargs):
        i = self.w.interaction(uid=uid, admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.title or "") + (embed.description or "") + "".join(f.value for f in embed.fields)

    async def test_full_flow(self):
        self.assertIn("만들었어요", await self.run_cmd(Guild.create, "강철단"))
        self.assertIn("신청했어요", await self.run_cmd(Guild.join, "강철단", uid=B))
        info = await self.run_cmd(Guild.info, uid=A)
        self.assertIn(f"<@{B}>", info)                                                  # 길드장에게는 신청자가 보인다
        self.assertNotIn(f"<@{B}>", await self.run_cmd(Guild.info, "강철단", uid=B))      # 다른 사람에게는 안 보인다
        self.assertIn("들어왔어요", await self.run_cmd(Guild.accept, self.w.members[B]))
        self.assertIn("넣었어요", await self.run_cmd(Guild.deposit, 300, uid=B))
        self.assertIn("줬어요", await self.run_cmd(Guild.pay, self.w.members[B], 100))
        self.assertIn("RAM 증가", await self.run_cmd(Guild.buff, type("C", (), {"value": "ram"})()))
        self.assertIn("강철단", await self.run_cmd(Guild.ranking))
        self.assertIn("길드장만", await self.run_cmd(Guild.kick, self.w.members[A], uid=B))
        self.assertIn("위임", await self.run_cmd(Guild.leave, uid=A))
        self.assertIn("나왔어요", await self.run_cmd(Guild.leave, uid=B))
        self.assertIn("해체하지 않았어요", await self.run_cmd(Guild.disband, False))
        self.assertIn("해체했어요", await self.run_cmd(Guild.disband, True))
        self.assertIsNone(guilds.of_user(self.store, G, A))

    async def test_info_errors_autocomplete_and_settings(self):
        self.assertIn("들어가 있지 않아요", await self.run_cmd(Guild.info))
        self.assertIn("길드가 없어요", await self.run_cmd(Guild.info, "없는단"))
        await self.run_cmd(Guild.create, "강철단")
        names = [c.value for c in await self.cog.guild_names(self.w.interaction(uid=B), "강")]
        self.assertEqual(names, ["강철단"])
        self.assertIn("관리자만", await self.run_cmd(Guild.settings, 10, uid=B))
        self.assertIn("바꿨어요", await self.run_cmd(Guild.settings, 10, 5, admin=True))
        self.assertEqual((self.store.settings(G)["guild_cost"], self.store.settings(G)["guild_size"]), (10, 5))
        self.assertIn("지금 길드 설정", await self.run_cmd(Guild.settings, admin=True))

    def test_embed(self):
        row = guilds.create(self.store, G, A, "강철단")
        fields = {f.name: f.value for f in guild_embed(self.store, G, row, A).fields}
        self.assertIn("0승 0패", fields["⚔️ 길드전"])
        self.assertIn("없음", fields["✨ 길드 버프"])


if __name__ == "__main__":
    unittest.main()
