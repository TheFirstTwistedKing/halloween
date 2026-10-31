"""거래 서비스 시험: 물품·역할 사고팔기, 환불, 연타 방지, 위험한 역할 차단."""
import asyncio
import unittest
from types import SimpleNamespace as NS

import discord

import services
from services import DANGEROUS_PERMISSIONS, role_problem
from store import CANDY_MAX


from tests.helpers import FakeRole


class World:
    """services 시험용 가짜 서버: 공용 World에 역할 물품 도우미만 더했다."""
    def __init__(self):
        from tests.helpers import World as Base
        self._base = Base()
        self.store, self.locks, self.guild, self.member, self.roles = (
            self._base.store, self._base.locks, self._base.guild, self._base.member, self._base.roles)
        self.me, self.new_member = self._base.me, self._base.new_member

    def role_item(self, role_id=50, name="VIP", **kw):
        self.roles[role_id] = FakeRole(role_id, **{k: kw.pop(k) for k in list(kw) if k in ("position", "permissions", "managed")})
        defaults = dict(kind="role", role_id=role_id, buy_price=100, sell_price=40, buyable=True, sellable=True, emoji="👑")
        defaults.update(kw)
        return self.store.create_item(100, name, **defaults)


class ItemTradeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World()
        self.item = self.w.store.create_item(100, "초", emoji="🕯️", buy_price=30, sell_price=10, buyable=True, sellable=True)
        self.w.store.add_candy(100, 7, 100)

    async def test_buy_and_sell_messages(self):
        result = await services.buy(self.w.store, self.w.locks, self.w.member, self.item, 2)
        self.assertEqual((result.ok, result.text), (True, "🍬 60을 내고 🕯️ **초** ×2을(를) 샀어요."))
        result = await services.sell(self.w.store, self.w.locks, self.w.member, self.item, 1)
        self.assertEqual((result.ok, result.text), (True, "🕯️ **초** ×1을(를) 팔고 🍬 10을 받았어요."))
        self.assertEqual((self.w.store.wallet(100, 7), self.w.store.qty(100, 7, self.item["id"])), (50, 1))

    async def test_failures_are_reported_not_raised(self):
        for coroutine, text in [(services.buy(self.w.store, self.w.locks, self.w.member, self.item, 99), "부족해요"),
                                (services.sell(self.w.store, self.w.locks, self.w.member, self.item, 1), "모자라요"),
                                (services.buy(self.w.store, self.w.locks, self.w.member, self.item, 0), "수량")]:
            result = await coroutine
            self.assertFalse(result.ok)
            self.assertIn(text, result.text)

    async def test_the_latest_price_is_used_and_a_deleted_item_is_refused(self):
        stale = self.item                                            # 상세 화면을 열어 둔 사이에
        self.w.store.update_item(100, self.item["id"], buy_price=50)   # 가격이 바뀌었다.
        result = await services.buy(self.w.store, self.w.locks, self.w.member, stale, 1)
        self.assertIn("🍬 50", result.text)
        self.w.store.delete_item(100, self.item["id"])
        for call in (services.buy, services.sell):
            result = await call(self.w.store, self.w.locks, self.w.member, stale, 1)
            self.assertEqual((result.ok, result.text), (False, "이제 상점에 없는 물품이에요."))

    async def test_double_clicking_never_charges_twice_beyond_the_balance(self):
        results = await asyncio.gather(*[services.buy(self.w.store, self.w.locks, self.w.member, self.item, 1) for _ in range(10)])
        self.assertEqual(sum(r.ok for r in results), 3)               # 100 / 30 = 3번만 성공
        self.assertEqual((self.w.store.wallet(100, 7), self.w.store.qty(100, 7, self.item["id"])), (10, 3))


class RoleTradeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World()
        self.item = self.w.role_item()
        self.w.store.add_candy(100, 7, 150)

    async def buy(self, member=None):
        return await services.buy(self.w.store, self.w.locks, member or self.w.member, self.item, 1)

    async def test_buying_a_role_gives_it_and_takes_candy(self):
        result = await self.buy()
        self.assertEqual((result.ok, result.text), (True, "🍬 100을 내고 👑 <@&50> 역할을 받았어요!"))
        self.assertEqual([r.id for r in self.w.member.roles], [50])
        self.assertEqual(self.w.store.wallet(100, 7), 50)

    async def test_not_enough_candy_gives_nothing(self):
        self.w.store.add_candy(100, 7, -100)
        result = await self.buy()
        self.assertFalse(result.ok)
        self.assertEqual((self.w.member.roles, self.w.store.wallet(100, 7)), ([], 50))
        self.w.member.add_roles.assert_not_awaited()

    async def test_a_failed_role_grant_refunds_the_candy(self):
        self.w.member.add_roles.side_effect = discord.Forbidden(NS(status=403, reason="x"), "Missing Permissions")
        result = await self.buy()
        self.assertEqual((result.ok, self.w.store.wallet(100, 7), self.w.member.roles), (False, 150, []))
        self.assertIn("돌려 드렸어요", result.text)

    async def test_already_owned_missing_and_unbuyable_roles(self):
        await self.buy()
        again = await self.buy()
        self.assertEqual((again.ok, again.text), (False, "이미 가지고 있는 역할이에요."))
        self.assertEqual(self.w.store.wallet(100, 7), 50)                  # 두 번째로는 재화가 빠지지 않는다.
        del self.w.roles[50]
        self.assertIn("사라졌어요", (await self.buy(self.w.new_member(8))).text)
        self.w.roles[50] = FakeRole(50)
        self.w.store.update_item(100, self.item["id"], buyable=False)
        self.assertIn("살 수 없는", (await self.buy(self.w.new_member(9))).text)

    async def test_selling_a_role_takes_it_back_and_pays(self):
        await self.buy()
        result = await services.sell(self.w.store, self.w.locks, self.w.member, self.item, 1)
        self.assertEqual((result.ok, self.w.member.roles, self.w.store.wallet(100, 7)), (True, [], 90))
        again = await services.sell(self.w.store, self.w.locks, self.w.member, self.item, 1)
        self.assertEqual((again.ok, again.text), (False, "가지고 있지 않은 역할이에요."))

    async def test_selling_a_role_when_the_wallet_would_overflow_keeps_the_role(self):
        await self.buy()
        self.w.store.add_candy(100, 7, CANDY_MAX)
        result = await services.sell(self.w.store, self.w.locks, self.w.member, self.item, 1)
        self.assertEqual((result.ok, [r.id for r in self.w.member.roles]), (False, [50]))
        self.assertEqual(self.w.store.wallet(100, 7), CANDY_MAX)

    async def test_a_failed_role_removal_pays_nothing(self):
        await self.buy()
        self.w.member.remove_roles.side_effect = discord.Forbidden(NS(status=403, reason="x"), "no")
        before = self.w.store.wallet(100, 7)
        result = await services.sell(self.w.store, self.w.locks, self.w.member, self.item, 1)
        self.assertEqual((result.ok, self.w.store.wallet(100, 7), len(self.w.member.roles)), (False, before, 1))

    async def test_unsellable_role(self):
        await self.buy()
        self.w.store.update_item(100, self.item["id"], sellable=False)
        self.assertIn("팔 수 없는", (await services.sell(self.w.store, self.w.locks, self.w.member, self.item, 1)).text)

    async def test_double_clicking_a_role_purchase_charges_once(self):
        self.w.store.add_candy(100, 7, 1000)           # 여러 번 살 수 있을 만큼 넉넉해야, 잠금이 없을 때의 이중 결제가 드러난다.
        results = await asyncio.gather(*[self.buy() for _ in range(6)])
        self.assertEqual(sum(r.ok for r in results), 1)
        self.assertEqual((self.w.store.wallet(100, 7), len(self.w.member.roles)), (1050, 1))

    async def test_a_role_that_became_unsafe_after_registration_is_not_given(self):
        self.w.roles[50].permissions = discord.Permissions(administrator=True)     # 등록 뒤에 관리자 권한이 붙었다면
        result = await self.buy()
        self.assertEqual((result.ok, self.w.member.roles, self.w.store.wallet(100, 7)), (False, [], 150))
        self.assertIn("관리 권한", result.text)


class RoleSafetyTests(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def test_safe_role_passes(self):
        self.assertIsNone(role_problem(self.w.guild, FakeRole(5, position=2)))

    def test_each_problem(self):
        guild = self.w.guild
        self.assertIn("@everyone", role_problem(guild, FakeRole(5, default=True)))
        self.assertIn("관리하는", role_problem(guild, FakeRole(5, managed=True)))
        self.assertIn("위에 있는", role_problem(guild, FakeRole(5, position=10)))          # 봇과 같은 위치도 안 된다.
        self.assertIn("위에 있는", role_problem(guild, FakeRole(5, position=11)))
        guild.me.guild_permissions = discord.Permissions.none()
        self.assertIn("역할 관리", role_problem(guild, FakeRole(5)))

    def test_every_dangerous_permission_is_refused(self):
        valid = {name for name in DANGEROUS_PERMISSIONS if name in discord.Permissions.VALID_FLAGS}
        self.assertGreaterEqual(len(valid), 14)
        for name in valid:
            role = FakeRole(5, permissions=discord.Permissions(**{name: True}))
            self.assertIn("관리 권한", role_problem(self.w.guild, role) or "", name)


class CurrencyInServicesTests(unittest.IsolatedAsyncioTestCase):
    async def test_trade_messages_use_the_servers_currency(self):
        w = World()
        w.store.set_currency(100, emoji="💎", name="젬", max=1000)
        item = w.store.create_item(100, "초", buyable=True, buy_price=30, sellable=True, sell_price=10)
        w.store.add_candy(100, 7, 100)
        result = await services.buy(w.store, w.locks, w.member, item, 2)
        self.assertEqual((result.ok, result.text), (True, "💎 60을 내고 🎃 **초** ×2을(를) 샀어요."))
        result = await services.sell(w.store, w.locks, w.member, item, 1)
        self.assertEqual((result.ok, result.text), (True, "🎃 **초** ×1을(를) 팔고 💎 10을 받았어요."))
        w.store.add_candy(100, 7, 5000)
        result = await services.sell(w.store, w.locks, w.member, item, 1)
        self.assertEqual((result.ok, "젬이 최대(1,000개)" in result.text), (False, True))


if __name__ == "__main__":
    unittest.main()
