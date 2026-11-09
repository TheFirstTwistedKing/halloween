"""/상점진열: 이미 만든 물품(열쇠 같은 것)을 상점에 올린다."""
import unittest
from unittest import mock
from unittest.mock import AsyncMock

import cogs.shop as shop_module
from cogs.shop import Shop
from store import MAX_SHOP_ITEMS, StoreError
from tests.helpers import World, sent_embed

G = 100


class ShelveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Shop(self.w.bot)
        self.w.channel(400)
        patcher = mock.patch.object(shop_module, "refresh_panels", new=AsyncMock(return_value=1))
        self.refresh = patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def shelve(self, item, *args, **kw):
        i = self.ask(**{k: kw.pop(k) for k in ("admin",) if k in kw})
        await Shop.shelve_item.callback(self.cog, i, item, *args, **kw)
        return i

    def text(self, i):
        return sent_embed(i)[0].description

    def row(self, name):
        return self.store.item_by_name(G, name)

    async def test_the_default_key_can_be_put_on_the_shelf_with_both_prices(self):
        self.store.ensure_defaults(G)
        self.assertEqual(self.row("열쇠")["buyable"] + self.row("열쇠")["sellable"], 0)       # 처음엔 상점에 없다
        i = await self.shelve("열쇠", 50, 20)
        key = self.row("열쇠")
        self.assertEqual((key["buyable"], key["buy_price"], key["sellable"], key["sell_price"], key["sell_max"]), (1, 50, 1, 20, None))
        self.assertIn("🗝️ **열쇠**을(를) 상점에 올렸어요", self.text(i))
        self.assertIn("구입 ✅ 💾 50", self.text(i))
        self.assertIn("판매 ✅ 💾 20", self.text(i))
        self.assertIn("열쇠", [r["name"] for r in self.store.items(G, shop_only=True)])
        self.refresh.assert_awaited_once()                                               # 상점 메시지가 새 목록으로 바뀐다

    async def test_only_the_side_you_give_a_price_for_is_turned_on(self):
        self.store.create_item(G, "부적")
        await self.shelve("부적", 30)
        charm = self.row("부적")
        self.assertEqual((charm["buyable"], charm["buy_price"], charm["sellable"]), (1, 30, 0))
        await self.shelve("부적", None, 12)                                              # 판매도 더하면 구입은 그대로 남는다
        charm = self.row("부적")
        self.assertEqual((charm["buyable"], charm["buy_price"], charm["sellable"], charm["sell_price"]), (1, 30, 1, 12))

    async def test_negative_and_range_selling_prices(self):
        self.store.create_item(G, "유리병")
        self.store.create_item(G, "복불복")
        i = await self.shelve("유리병", None, -40)
        self.assertIn("판매 ✅ 💾 -40", self.text(i))
        self.assertEqual(self.row("유리병")["sell_price"], -40)
        i = await self.shelve("복불복", None, -10, 30)
        self.assertIn("💾 -10 ~ 30", self.text(i))
        bottle = self.row("복불복")
        self.assertEqual((bottle["sell_price"], bottle["sell_max"]), (-10, 30))

    async def test_putting_it_on_again_sets_the_new_prices_and_a_missing_max_makes_it_fixed(self):
        self.store.create_item(G, "복불복", sell_price=-10, sell_max=30, sellable=True)
        await self.shelve("복불복", None, 7)
        bottle = self.row("복불복")
        self.assertEqual((bottle["sell_price"], bottle["sell_max"]), (7, None))

    async def test_a_price_is_required_and_a_max_needs_a_selling_price(self):
        self.store.create_item(G, "부적")
        for args, needle in (((), "하나는 적어"), ((None, None, 5), "하나는 적어"), ((30, None, 5), "판매가와 함께")):
            i = await self.shelve("부적", *args)
            self.assertIn(needle, self.text(i))
        charm = self.row("부적")
        self.assertEqual((charm["buyable"], charm["sellable"]), (0, 0))
        self.refresh.assert_not_awaited()

    async def test_unknown_items_and_roles_are_refused_with_a_hint(self):
        self.w.add_role(50, name="유령 손님")
        self.store.create_item(G, "VIP", kind="role", role_id=50, buy_price=9, buyable=True)
        self.assertIn("그런 물품이 없어요", self.text(await self.shelve("없는 것", 5)))
        self.assertIn("/역할등록", self.text(await self.shelve("VIP", 5)))

    async def test_the_25_item_limit_is_reported_and_nothing_changes(self):
        self.store.ensure_defaults(G)
        extra = self.store.create_item(G, "남는 물품")
        listed = len(self.store.items(G, shop_only=True))
        with self.store.db:                                                              # 상점 25칸을 채운다.
            for n in range(MAX_SHOP_ITEMS - listed):
                self.store.db.execute("INSERT INTO items(guild,name,emoji,kind,buyable,buy_price) VALUES(?,?,?,?,?,?)", (G, f"자리{n}", "⚙️", "item", 1, 1))
        self.assertEqual(len(self.store.items(G, shop_only=True)), MAX_SHOP_ITEMS)
        i = await self.shelve("남는 물품", 5)
        self.assertIn("최대 25개", self.text(i))
        self.assertEqual(self.row("남는 물품")["buyable"], 0)
        self.assertEqual(extra["id"], self.row("남는 물품")["id"])

    async def test_a_note_appears_when_no_shop_is_open_yet(self):
        self.store.create_item(G, "부적")
        self.assertIn("/상점등록", self.text(await self.shelve("부적", 5)))
        self.store.set_shop(G, 400, 9001)
        self.store.create_item(G, "부적2")
        self.assertNotIn("/상점등록", self.text(await self.shelve("부적2", 5)))

    async def test_only_admins_and_the_answer_is_public(self):
        self.store.create_item(G, "부적")
        i = await self.shelve("부적", 5, admin=False)
        self.assertIn("관리자만", self.text(i))
        self.assertEqual(self.row("부적")["buyable"], 0)
        i = await self.shelve("부적", 5)
        self.assertFalse(i.response.send_message.await_args.kwargs["ephemeral"])

    async def test_autocomplete_puts_items_not_in_the_shop_first_and_marks_the_listed_ones(self):
        self.store.ensure_defaults(G)
        self.store.create_item(G, "진열품", buy_price=5, buyable=True)
        self.store.create_item(G, "창고품")
        choices = await self.cog.shelve_autocomplete(self.ask(), "")
        names = [c.name for c in choices]
        self.assertLess(names.index("⚙️ 창고품"), names.index("⚙️ 진열품 · 진열 중"))        # 상점에 없는 것이 먼저
        self.assertIn("🗝️ 열쇠", names)
        self.assertEqual([c.value for c in await self.cog.shelve_autocomplete(self.ask(), "창고")], ["창고품"])
        roles = [r["name"] for r in self.store.items(G) if r["kind"] == "role"]
        self.assertTrue(not roles or all(c.value not in roles for c in choices))
        from types import SimpleNamespace as NS
        self.assertEqual(await self.cog.shelve_autocomplete(NS(guild_id=None), ""), [])

    async def test_autocomplete_labels_fit_discords_100_character_limit_even_at_the_longest_name_and_emoji(self):
        """이름은 30자, 서버 이모지는 길어야 62자라서 라벨(이모지+이름+진열 표시)은 100자를 넘을 수 없다. 그 최대 길이로 확인한다."""
        longest_emoji = "<a:" + "e" * 32 + ":" + "1" * 25 + ">"
        self.store.create_item(G, "가" * 30, emoji=longest_emoji, buy_price=5, buyable=True)
        labels = [c.name for c in await self.cog.shelve_autocomplete(self.ask(), "가가가")]
        self.assertEqual(len(labels), 1)
        self.assertLessEqual(len(labels[0]), 100)

    async def test_the_new_items_are_all_reachable(self):
        """패키지 물품 7종 같은 새 물품도 자동완성에 나온다."""
        names = [c.value for c in await self.cog.shelve_autocomplete(self.ask(), "")]
        for needle in ("구형 CPU", "쓰레기", "열쇠"):
            self.assertIn(needle, names)


if __name__ == "__main__":
    unittest.main()
