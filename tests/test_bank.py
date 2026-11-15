"""개인 COM 금고: 넣기·빼기(최대 100,000,000 COM), 경험치·레벨은 그대로, 예전 DB 자동 이전, 명령어."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import rp
from cogs.rp import Roleplay
from rp import RpError
from store import Store
from tests.helpers import World, sent_embed

G, A = 100, 7


class BankTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        rp.give(self.store, G, A, 1000)

    def tearDown(self):
        self.store.close()

    def test_deposit_and_withdraw_keep_exp(self):
        self.assertEqual(rp.deposit(self.store, G, A, 300), (300, 700, 300))
        self.assertEqual(rp.withdraw(self.store, G, A, 100), (100, 800, 200))
        me = rp.row(self.store, G, A)
        self.assertEqual((me["com"], me["bank"], me["exp"]), (800, 200, 1000))           # 경험치는 그대로
        with self.assertRaisesRegex(RpError, "모자라요"):
            rp.deposit(self.store, G, A, 801)
        with self.assertRaisesRegex(RpError, "그만큼 없어요"):
            rp.withdraw(self.store, G, A, 201)
        self.assertEqual((rp.row(self.store, G, A)["com"], rp.row(self.store, G, A)["bank"]), (800, 200))

    def test_all_when_amount_is_empty(self):
        self.assertEqual(rp.deposit(self.store, G, A)[0], 1000)
        with self.assertRaisesRegex(RpError, "넣을 COM이 없거나"):
            rp.deposit(self.store, G, A)
        self.assertEqual(rp.withdraw(self.store, G, A), (1000, 1000, 0))
        with self.assertRaisesRegex(RpError, "비어 있어요"):
            rp.withdraw(self.store, G, A)

    def test_bank_limit_is_100_million(self):
        self.assertEqual(rp.BANK_MAX, 100_000_000)
        rp.give(self.store, G, A, rp.BANK_MAX)
        rp.deposit(self.store, G, A, rp.BANK_MAX - 10)
        with self.assertRaisesRegex(RpError, "100,000,000"):
            rp.deposit(self.store, G, A, 11)
        self.assertEqual(rp.deposit(self.store, G, A)[0], 10)                              # 비우면 남은 자리만큼
        self.assertEqual(rp.row(self.store, G, A)["bank"], rp.BANK_MAX)
        with self.assertRaisesRegex(RpError, "가득"):
            rp.deposit(self.store, G, A)

    def test_withdraw_respects_the_wallet_limit(self):
        rp.deposit(self.store, G, A, 500)
        with self.store.db:
            self.store.db.execute("UPDATE rp SET com=? WHERE guild=? AND user=?", (rp.COM_MAX - 100, G, A))
        with self.assertRaisesRegex(RpError, "최대"):
            rp.withdraw(self.store, G, A, 101)
        self.assertEqual(rp.withdraw(self.store, G, A)[0], 100)                            # 비우면 들어갈 만큼만
        self.assertEqual(rp.row(self.store, G, A)["bank"], 400)

    def test_an_old_database_gets_the_bank_column(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE rp(guild INTEGER, user INTEGER, com INTEGER NOT NULL DEFAULT 0, exp INTEGER NOT NULL DEFAULT 0, "
                       "credit INTEGER NOT NULL DEFAULT 0, job INTEGER, last_work REAL NOT NULL DEFAULT 0, last_spin REAL NOT NULL DEFAULT 0, "
                       "last_attend TEXT NOT NULL DEFAULT '', PRIMARY KEY(guild, user))")
            db.execute("INSERT INTO rp(guild, user, com, exp) VALUES(100, 7, 500, 1234)")
            db.commit()
            db.close()
            store = Store(path)
            try:
                me = rp.row(store, G, A)
                self.assertEqual((me["com"], me["exp"], me["bank"]), (500, 1234, 0))
                rp.deposit(store, G, A, 200)
                self.assertEqual(rp.row(store, G, A)["bank"], 200)
            finally:
                store.close()


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Roleplay(self.bot)
        self.w.new_member(A)
        rp.give(self.store, G, A, 1000)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, **kwargs):
        i = self.w.interaction(uid=A)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.title or "") + (embed.description or "") + "".join(f.name + f.value for f in embed.fields)

    async def test_commands(self):
        self.assertIn("300", await self.run_cmd(Roleplay.bank_deposit, 300))
        shown = await self.run_cmd(Roleplay.bank_show)
        self.assertIn("금고 **300**", shown)
        self.assertIn("지갑 **700**", shown)
        self.assertIn("꺼냈어요", await self.run_cmd(Roleplay.bank_withdraw, 100))
        self.assertIn("모자라요", await self.run_cmd(Roleplay.bank_deposit, 10 ** 6))
        self.assertIn("🏦 금고200", await self.run_cmd(Roleplay.profile))
        self.assertIn("꺼냈어요", await self.run_cmd(Roleplay.bank_withdraw))                # 비우면 전부
        self.assertEqual(rp.row(self.store, G, A)["bank"], 0)


if __name__ == "__main__":
    unittest.main()
