"""길드전: 선전포고 → 수락 → 기간 점수전 → 진 길드원 모두의 RAM 50%가 이긴 길드원들에게. 잠금, 점수, 정산, 명령어."""
import unittest
from unittest.mock import AsyncMock

import dungeon
import dungeon_service as service
import guild_war
import guilds
from cogs.dungeon import run_embed
from cogs.guild import Guild, result_embed, war_embed
from dungeon import ROOMS
from guilds import GuildError
from services import Locks
from store import DEFAULT_SETTINGS, Store, validate_settings
from tests.helpers import Scripted, World, sent_embed

G = 100
A1, A2, D1, D2, X = 1, 2, 3, 4, 5
T0 = 1_000_000.0
HOUR = 3600


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.update_settings(G, guild_cost=0)
        for u, money in ((A1, 1000), (A2, 3000), (D1, 801), (D2, 2000), (X, 100)):
            self.store.add_candy(G, u, money)
        self.att = guilds.create(self.store, G, A1, "공격단")
        self.dfn = guilds.create(self.store, G, D1, "수비단")
        self.join(self.att, A2)
        self.join(self.dfn, D2)

    def tearDown(self):
        self.store.close()

    def join(self, row, user):
        guilds.apply(self.store, G, user, row["id"])
        guilds.accept(self.store, G, row["leader"], user)

    def start(self, now=T0):
        guild_war.declare(self.store, G, A1, "수비단", now=now)
        return guild_war.accept(self.store, G, D1, channel=400, now=now)


class DeclareTests(Base):
    def test_declare_accept_flow(self):
        war = guild_war.declare(self.store, G, A1, "수비단", now=T0)
        self.assertEqual((war["state"], war["attacker_name"], war["defender_name"]), ("pending", "공격단", "수비단"))
        self.assertFalse(guilds.locked_by_war(self.store, G, self.att["id"]))           # 대기 중에는 잠그지 않는다
        war = guild_war.accept(self.store, G, D1, channel=400, now=T0 + 10)
        self.assertEqual((war["state"], war["started"], war["ends"], war["channel"]), ("active", T0 + 10, T0 + 10 + 24 * HOUR, 400))
        self.assertTrue(guilds.locked_by_war(self.store, G, self.att["id"]))
        self.assertTrue(guilds.locked_by_war(self.store, G, self.dfn["id"]))

    def test_refusals(self):
        with self.assertRaises(GuildError):
            guild_war.declare(self.store, G, A2, "수비단", now=T0)                    # 길드장만
        with self.assertRaises(GuildError):
            guild_war.declare(self.store, G, A1, "공격단", now=T0)                    # 우리 길드
        with self.assertRaises(GuildError):
            guild_war.declare(self.store, G, A1, "없는단", now=T0)
        guild_war.declare(self.store, G, A1, "수비단", now=T0)
        with self.assertRaises(GuildError):
            guild_war.declare(self.store, G, D1, "공격단", now=T0)                    # 둘 다 이미 걸려 있다
        third = guilds.create(self.store, G, X, "셋째단")
        with self.assertRaises(GuildError):
            guild_war.declare(self.store, G, X, "수비단", now=T0)
        with self.assertRaises(GuildError):
            guild_war.accept(self.store, G, A1, now=T0)                               # 선전포고한 쪽은 받을 수 없다
        with self.assertRaises(GuildError):
            guild_war.accept(self.store, G, D2, now=T0)                               # 길드장만
        self.assertIsNotNone(third)

    def test_decline_cancel_and_expire(self):
        guild_war.declare(self.store, G, A1, "수비단", now=T0)
        guild_war.decline(self.store, G, D1)
        self.assertIsNone(guild_war.open_war(self.store, G, self.att["id"]))
        guild_war.declare(self.store, G, A1, "수비단", now=T0)
        with self.assertRaises(GuildError):
            guild_war.cancel(self.store, G, D1)                                       # 받은 쪽은 거둘 수 없다
        guild_war.cancel(self.store, G, A1)
        with self.assertRaises(GuildError):
            guild_war.cancel(self.store, G, A1)
        guild_war.declare(self.store, G, A1, "수비단", now=T0)
        with self.assertRaises(GuildError):
            guild_war.accept(self.store, G, D1, now=T0 + 24 * HOUR)                   # 기간이 지나면 사라진다
        self.assertIsNone(guild_war.open_war(self.store, G, self.dfn["id"]))

    def test_accept_picks_by_name_when_several(self):
        third = guilds.create(self.store, G, X, "셋째단")
        guild_war.declare(self.store, G, X, "수비단", now=T0)
        with self.assertRaises(GuildError):
            guild_war.declare(self.store, G, A1, "수비단", now=T0)                    # 수비단은 이미 걸려 있다
        with self.assertRaises(GuildError):
            guild_war.accept(self.store, G, D1, "공격단", now=T0)                     # 공격단이 한 선전포고는 없다
        war = guild_war.accept(self.store, G, D1, "셋째단", now=T0)
        self.assertEqual(war["attacker"], third["id"])

    def test_war_hours_setting(self):
        self.store.update_settings(G, war_hours=2)
        self.assertEqual(self.start()["ends"], T0 + 2 * HOUR)
        for bad in (0, 169, True):
            with self.assertRaises(ValueError):
                validate_settings(dict(DEFAULT_SETTINGS, war_hours=bad))


class LockTests(Base):
    def test_membership_and_deposit_are_locked_during_the_war(self):
        self.start()
        with self.assertRaises(GuildError):
            guilds.leave(self.store, G, A2)
        with self.assertRaises(GuildError):
            guilds.kick(self.store, G, D1, D2)
        with self.assertRaises(GuildError):
            guilds.disband(self.store, G, A1)
        with self.assertRaises(GuildError):
            guilds.deposit(self.store, G, D2, 100)                                    # 금고로 RAM을 숨기지 못한다
        with self.assertRaises(GuildError):
            guilds.apply(self.store, G, X, self.att["id"])
        self.assertEqual(guilds.members(self.store, G, self.dfn["id"]), [D1, D2])


class ScoreTests(Base):
    def test_points_go_to_the_right_side_until_the_end(self):
        war = self.start()
        self.assertEqual(guild_war.add_points(self.store, G, A2, 12, now=T0 + 1), 12)
        self.assertEqual(guild_war.add_points(self.store, G, D1, 5, now=T0 + 2), 5)
        guild_war.add_points(self.store, G, A2, 3, now=T0 + 3)
        self.assertEqual(guild_war.add_points(self.store, G, X, 9, now=T0 + 4), 0)       # 길드가 없다
        self.assertEqual(guild_war.add_points(self.store, G, A1, 9, now=war["ends"]), 0)  # 끝난 뒤에는 안 쌓인다
        row = guild_war.get(self.store, G, war["id"])
        self.assertEqual((row["attacker_score"], row["defender_score"]), (15, 5))
        self.assertEqual(guild_war.top_scorers(self.store, war["id"], self.att["id"]), [(A2, 15)])

    def test_points_for_a_run(self):
        run = dungeon.new_run()
        run.floor, run.phase = 7, "dead"
        self.assertEqual(guild_war.points_for(run), 7)
        run.phase = "clear"
        self.assertEqual(guild_war.points_for(run), 17)
        run.final_clear, run.floor = True, 100
        self.assertEqual(guild_war.points_for(run), 160)
        run.floor, run.phase, run.final_clear = 0, "quit", False
        self.assertEqual(guild_war.points_for(run), 1)


class SettleTests(Base):
    def test_the_loser_pays_half_and_the_winners_split_it(self):
        war = self.start()
        guild_war.add_points(self.store, G, D2, 10, now=T0 + 1)
        guild_war.add_points(self.store, G, A1, 3, now=T0 + 1)
        self.assertEqual(guild_war.settle_due(self.store, now=T0 + 23 * HOUR), [])      # 아직 끝나지 않았다
        [(guild, result)] = guild_war.settle_due(self.store, now=war["ends"])
        self.assertEqual((guild, result["winner"]["id"], result["loser"]["id"]), (G, self.dfn["id"], self.att["id"]))
        self.assertEqual(result["taken"], {A1: 500, A2: 1500})
        self.assertEqual(result["total"], 2000)
        self.assertEqual(result["given"], {D1: 1000, D2: 1000})
        self.assertEqual([self.store.wallet(G, u) for u in (A1, A2, D1, D2)], [500, 1500, 1801, 3000])
        self.assertEqual((guilds.get(self.store, G, self.dfn["id"])["wins"], guilds.get(self.store, G, self.att["id"])["losses"]), (1, 1))
        self.assertIsNotNone(guilds.get(self.store, G, self.att["id"]))                 # 진 길드는 그대로 남는다
        self.assertFalse(guilds.locked_by_war(self.store, G, self.att["id"]))
        self.assertEqual(guild_war.settle_due(self.store, now=war["ends"] + 99), [])     # 한 번만
        self.assertEqual(self.store.wallet(G, A1), 500)

    def test_odd_amounts_round_down_and_remainder_goes_to_the_leader(self):
        self.start()
        guild_war.add_points(self.store, G, A1, 1, now=T0 + 1)
        [(_g, result)] = guild_war.settle_due(self.store, now=T0 + 24 * HOUR)
        self.assertEqual(result["taken"], {D1: 400, D2: 1000})                           # 801의 50%는 400
        self.assertEqual(result["given"], {A1: 700, A2: 700})
        self.store.close()
        self.setUp()
        self.join_three()

    def join_three(self):
        guilds.apply(self.store, G, X, self.att["id"])
        guilds.accept(self.store, G, A1, X)
        self.start()
        guild_war.add_points(self.store, G, A1, 1, now=T0 + 1)
        [(_g, result)] = guild_war.settle_due(self.store, now=T0 + 24 * HOUR)
        self.assertEqual(result["total"], 1400)
        self.assertEqual(result["given"], {A1: 468, A2: 466, X: 466})                   # 1400 = 466×3 + 2(길드장)

    def test_a_draw_moves_nothing(self):
        self.start()
        [(_g, result)] = guild_war.settle_due(self.store, now=T0 + 24 * HOUR)
        self.assertIsNone(result["winner"])
        self.assertEqual([self.store.wallet(G, u) for u in (A1, A2, D1, D2)], [1000, 3000, 801, 2000])
        self.assertEqual(guilds.get(self.store, G, self.att["id"])["wins"], 0)

    def test_winners_are_capped_at_the_wallet_limit(self):
        self.store.update_settings(G, currency_max=3000)
        self.start()
        guild_war.add_points(self.store, G, A1, 1, now=T0 + 1)
        [(_g, result)] = guild_war.settle_due(self.store, now=T0 + 24 * HOUR)
        self.assertEqual(result["given"], {A1: 700, A2: 0})                              # A2는 이미 3000(최대)
        self.assertEqual(self.store.wallet(G, A2), 3000)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.update_settings(G, guild_cost=0)
        self.store.add_item(G, A1, self.store.ensure_system_items(G)["key"], 5)
        self.att = guilds.create(self.store, G, A1, "공격단")
        self.dfn = guilds.create(self.store, G, D1, "수비단")

    def tearDown(self):
        self.store.close()

    async def test_finishing_a_dungeon_scores_for_the_guild(self):
        guild_war.declare(self.store, G, A1, "수비단")
        war = guild_war.accept(self.store, G, D1)
        out = await service.enter(self.store, self.locks, G, A1)
        run = out.run
        run.floor, run.room, run.phase, run.kind = self.store.balance(G).floors, ROOMS, "summit", "summit"
        self.store.save_dungeon(G, A1, run.to_json())
        done = await service.act(self.store, self.locks, Scripted(randoms=[0.99] * 5, ints=[100]), G, A1, run.rev, "finish")
        self.assertEqual(done.notes, [f"war:{self.store.balance(G).floors + 10}"])
        self.assertEqual(guild_war.get(self.store, G, war["id"])["attacker_score"], self.store.balance(G).floors + 10)
        shown = "".join(f.value for f in run_embed(done.run, done, self.store.balance(G), self.store.currency(G)).fields)
        self.assertIn("길드전", shown)

    async def test_no_war_no_points(self):
        out = await service.enter(self.store, self.locks, G, A1)
        done = await service.act(self.store, self.locks, Scripted(), G, A1, out.run.rev, "quit")
        self.assertEqual(done.notes, [])


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.store.update_settings(G, guild_cost=0)
        for u, money in ((A1, 1000), (D1, 600)):
            self.store.add_candy(G, u, money)
            self.w.new_member(u)
        self.att = guilds.create(self.store, G, A1, "공격단")
        self.dfn = guilds.create(self.store, G, D1, "수비단")
        self.cog = Guild(self.bot)
        self.channel = self.w.channel(400)                                              # 길드전을 받는 채널(결과가 여기에 올라간다)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, uid=A1, **kwargs):
        i = self.w.interaction(uid=uid)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.title or "") + (embed.description or "") + "".join(f.value for f in embed.fields)

    async def test_flow_and_result_post(self):
        self.assertIn("선전포고했어요", await self.run_cmd(Guild.declare, "수비단"))
        self.assertIn("공격단", await self.run_cmd(Guild.war_status, uid=D1))            # 받은 쪽에 선전포고가 보인다
        i = self.w.interaction(uid=D1)
        await Guild.war_status.callback(self.cog, i)
        self.assertTrue(any("우리에게 온 선전포고" in f.name for f in sent_embed(i)[0].fields))
        self.assertIn("길드전 시작", await self.run_cmd(Guild.war_accept, uid=D1))
        self.assertIn("남은 시간", await self.run_cmd(Guild.war_status))
        guild_war.add_points(self.store, G, A1, 4)
        war = guild_war.open_war(self.store, G, self.att["id"])
        channel = self.channel
        channel.send = AsyncMock()
        self.bot.get_channel = lambda cid: channel if cid == 400 else None
        done = await self.cog.settle_now(now=war["ends"])
        self.assertEqual(len(done), 1)
        posted = channel.send.call_args.kwargs["embed"]
        self.assertIn("공격단 승리", posted.title)
        self.assertEqual([self.store.wallet(G, u) for u in (A1, D1)], [1300, 300])
        self.assertIn("🏆 승리", await self.run_cmd(Guild.war_status))

    async def test_decline_cancel_and_errors(self):
        await self.run_cmd(Guild.declare, "수비단")
        self.assertIn("거뒀어요", await self.run_cmd(Guild.war_cancel))
        await self.run_cmd(Guild.declare, "수비단")
        self.assertIn("거절했어요", await self.run_cmd(Guild.war_decline, uid=D1))
        self.assertIn("우리 길드에는", await self.run_cmd(Guild.declare, "공격단"))
        self.assertIn("들어가 있지 않아요", await self.run_cmd(Guild.war_status, uid=X))
        self.assertIn("하는 길드전이 없어요", await self.run_cmd(Guild.war_status))

    def test_embeds(self):
        guild_war.declare(self.store, G, A1, "수비단", now=T0)
        pending = guild_war.open_war(self.store, G, self.att["id"])
        self.assertIn("대기", war_embed(self.store, G, pending).title)
        war = guild_war.accept(self.store, G, D1, now=T0)
        self.assertIn("24시간 0분", war_embed(self.store, G, war, now=T0).description)
        draw = guild_war.settle(self.store, G, war["id"])
        self.assertIn("무승부", result_embed(self.store, G, draw).title)


if __name__ == "__main__":
    unittest.main()
