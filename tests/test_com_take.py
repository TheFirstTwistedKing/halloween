"""관리자 COM 회수(/롤플관리 com회수): 지갑·금고, 수량 비우면 전부, EXP는 고를 때만 뺀다.
누구나 쓰는 COM 선물(/com선물): 직업 없이 지갑 COM만 옮기고 경험치는 늘지 않는다."""
import unittest

import discord

import rp
from cogs.rp import Roleplay
from rp import RpError
from store import Store
from tests.helpers import World, sent_embed

G, A = 100, 7
Choice = discord.app_commands.Choice


class TakeTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        rp.give(self.store, G, A, 1000)
        rp.deposit(self.store, G, A, 400)                                               # 지갑 600 · 금고 400 · EXP 1000

    def tearDown(self):
        self.store.close()

    def me(self):
        r = rp.row(self.store, G, A)
        return r["com"], r["bank"], r["exp"]

    def test_wallet_take_keeps_exp_by_default(self):
        self.assertEqual(rp.take(self.store, G, A, 100), (100, 500, 1000))
        self.assertEqual(self.me(), (500, 400, 1000))

    def test_take_with_exp_lowers_exp_but_never_below_zero(self):
        self.assertEqual(rp.take(self.store, G, A, 300, exp=True), (300, 300, 700))
        rp.take(self.store, G, A, 300, where="bank", exp=True)
        rp.take(self.store, G, A, where="bank", exp=True)                               # 금고 남은 100, EXP 400 → 300
        self.assertEqual(self.me(), (300, 0, 300))
        with self.store.db:
            self.store.db.execute("UPDATE rp SET exp=10 WHERE guild=? AND user=?", (G, A))
        rp.take(self.store, G, A, 50, exp=True)
        self.assertEqual(self.me(), (250, 0, 0))

    def test_bank_and_take_all(self):
        self.assertEqual(rp.take(self.store, G, A, where="bank"), (400, 0, 1000))
        self.assertEqual(self.me(), (600, 0, 1000))                                     # 지갑은 그대로
        with self.assertRaisesRegex(RpError, "금고에 가져갈"):
            rp.take(self.store, G, A, where="bank")
        self.assertEqual(rp.take(self.store, G, A), (600, 0, 1000))
        with self.assertRaisesRegex(RpError, "지갑에 가져갈"):
            rp.take(self.store, G, A)

    def test_not_enough_changes_nothing(self):
        with self.assertRaisesRegex(RpError, "지갑에 그만큼 없어요"):
            rp.take(self.store, G, A, 601)
        with self.assertRaisesRegex(RpError, "금고에 그만큼 없어요"):
            rp.take(self.store, G, A, 401, where="bank", exp=True)
        with self.assertRaisesRegex(RpError, "1 이상"):
            rp.take(self.store, G, A, 0)
        self.assertEqual(self.me(), (600, 400, 1000))

    def test_someone_with_nothing(self):
        with self.assertRaisesRegex(RpError, "가져갈"):
            rp.take(self.store, G, 999)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Roleplay(self.bot)
        self.member = self.w.new_member(A)
        rp.give(self.store, G, A, 1000)

    async def asyncTearDown(self):
        await self.w.close()

    async def call(self, *args, admin=True, **kwargs):
        i = self.w.interaction(uid=A, admin=admin)
        await Roleplay.take_com.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return embed.description or ""

    async def test_command(self):
        self.assertIn("관리자만", await self.call(self.member, 10, admin=False))
        self.assertEqual(rp.row(self.store, G, A)["com"], 1000)
        text = await self.call(self.member, 100)
        self.assertIn("지갑에서 🪙 100 COM", text)
        self.assertIn("900 COM 남음", text)
        self.assertNotIn("📉", text)
        text = await self.call(self.member, 500, with_exp=True)                          # EXP 1000 → 500: LV 3 → 2
        self.assertIn("LV. 3 → LV. 2", text)
        self.assertIn("자동으로 빼지 않아요", text)
        self.assertIn("그만큼 없어요", await self.call(self.member, 401))
        rp.deposit(self.store, G, A, 300)
        self.assertIn("금고에서 🪙 300 COM", await self.call(self.member, where=Choice(name="금고", value="bank")))
        self.assertEqual((rp.row(self.store, G, A)["com"], rp.row(self.store, G, A)["bank"], rp.row(self.store, G, A)["exp"]), (100, 0, 500))
        self.assertIn("EXP도 뺐어요", await self.call(self.member, 1, with_exp=True))



class GiftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Roleplay(self.bot)
        self.a, self.b = self.w.new_member(A), self.w.new_member(8)
        rp.give(self.store, G, A, 1000)
        rp.deposit(self.store, G, A, 500)                                               # 지갑 500 · 금고 500

    async def asyncTearDown(self):
        await self.w.close()

    async def call(self, member, amount, uid=A):
        i = self.w.interaction(uid=uid, admin=False)
        await Roleplay.gift.callback(self.cog, i, member, amount)
        embed, _ = sent_embed(i)
        return embed.description or ""

    def com_exp(self, uid):
        r = rp.row(self.store, G, uid)
        return r["com"], r["bank"], r["exp"]

    async def test_anyone_without_a_job_can_gift_and_no_exp_moves(self):
        self.assertIsNone(rp.row(self.store, G, A)["job"])
        text = await self.call(self.b, 200)
        self.assertIn("선물했어요", text)
        self.assertIn("남은 COM 300", text)
        self.assertEqual(self.com_exp(A), (300, 500, 1000))                              # 준 사람 EXP·금고 그대로
        self.assertEqual(self.com_exp(8), (200, 0, 0))                                   # 받은 사람 EXP는 늘지 않는다
        self.assertIn("선물했어요", await self.call(self.a, 50, uid=8))                   # 받은 사람도 다시 선물할 수 있다
        self.assertEqual(self.com_exp(A), (350, 500, 1000))

    async def test_refusals_change_nothing(self):
        self.assertIn("모자라요", await self.call(self.b, 501))                           # 금고 COM은 선물에 쓰지 않는다
        self.assertIn("나에게는", await self.call(self.a, 10))
        robot = self.w.new_member(9)
        robot.bot = True
        self.assertIn("봇에게는", await self.call(robot, 10))
        with self.store.db:
            self.store.db.execute("INSERT INTO rp(guild, user, com) VALUES(?,?,?)", (G, 8, rp.COM_MAX - 5))
        self.assertIn("최대", await self.call(self.b, 6))
        self.assertEqual(self.com_exp(A), (500, 500, 1000))
        self.assertEqual(rp.row(self.store, G, 9)["com"], 0)


if __name__ == "__main__":
    unittest.main()
