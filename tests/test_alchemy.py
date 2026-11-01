"""연금술: 열리는 시간(한국 시간 18:00~24:00), 합성 가격(2개 ×0.8 · 3개 ×0.6), 관리자 레시피, 합성 화면."""
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock

import alchemy
import cogs.alchemy as alchemy_module
import services
from cogs.alchemy import Alchemy, AlchemyView, alchemy_embed
from store import MAX_RECIPES, MAX_SHOP_ITEMS, Store, StoreError
from tests.helpers import World, sent_embed

G, U, OTHER = 100, 7, 8


def at(hour, minute=0, second=0):
    """한국 시간으로 2026-11-02 hour:minute:second 의 타임스탬프."""
    return datetime(2026, 11, 2, hour, minute, second, tzinfo=alchemy.KST).timestamp()


class TimeAndPriceTests(unittest.TestCase):
    def test_open_from_6pm_until_midnight_korean_time(self):
        for hour, minute, second, expected in ((17, 59, 59, False), (18, 0, 0, True), (18, 0, 1, True), (21, 30, 0, True),
                                               (23, 59, 59, True), (0, 0, 0, False), (0, 0, 1, False), (3, 0, 0, False), (12, 0, 0, False)):
            self.assertEqual(alchemy.is_open(at(hour, minute, second)), expected, f"{hour}:{minute:02}:{second:02}")

    def test_the_clock_is_korean_time_not_the_servers(self):
        utc_9am = datetime(2026, 11, 2, 9, 0, 0, tzinfo=alchemy.timezone.utc).timestamp()      # = 한국 18:00
        self.assertTrue(alchemy.is_open(utc_9am))
        self.assertFalse(alchemy.is_open(utc_9am - 1))
        utc_3pm = datetime(2026, 11, 2, 15, 0, 0, tzinfo=alchemy.timezone.utc).timestamp()     # = 한국 다음 날 00:00
        self.assertFalse(alchemy.is_open(utc_3pm))
        self.assertTrue(alchemy.is_open(utc_3pm - 1))

    def test_next_open_text(self):
        self.assertEqual(alchemy.next_open_text(at(19)), "")
        self.assertIn("6시간 0분 뒤", alchemy.next_open_text(at(12)))
        self.assertIn("17시간 30분 뒤", alchemy.next_open_text(at(0, 30)))
        self.assertIn("1시간 1분 뒤", alchemy.next_open_text(at(16, 59)))
        self.assertIn("1분 미만", alchemy.next_open_text(at(17, 59, 30)))
        self.assertIn("오후 6시", alchemy.next_open_text(at(5)))

    def test_two_items_are_80_percent_and_three_are_60_percent_of_the_sum(self):
        self.assertEqual(alchemy.combined_price([100, 100]), 160)
        self.assertEqual(alchemy.combined_price([100, 300]), 320)
        self.assertEqual(alchemy.combined_price([100, 100, 100]), 180)
        self.assertEqual(alchemy.combined_price([50, 100, 250]), 240)
        self.assertEqual(alchemy.combined_price([300, 500]), 640)

    def test_rounding_to_the_nearest_whole_number(self):
        self.assertEqual([alchemy.combined_price(p) for p in ([1, 1], [5, 5], [7, 8], [3, 3, 3], [5, 5, 5], [1, 1, 1])], [2, 8, 12, 5, 9, 2])

    def test_only_two_or_three_ingredients(self):
        for bad in ([], [100], [1, 1, 1, 1]):
            with self.assertRaises(ValueError):
                alchemy.combined_price(bad)


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.ensure_defaults(G)
        self.a = self.store.create_item(G, "마나 가루", emoji="✨", sell_price=100, sellable=True)
        self.b = self.store.create_item(G, "달빛 이슬", emoji="💧", sell_price=100, sellable=True)
        self.c = self.store.create_item(G, "별 조각", emoji="⭐", sell_price=300, sellable=True)
        self.out = self.store.create_item(G, "마력 약", emoji="🧪")
        self.out2 = self.store.create_item(G, "별빛 약", emoji="🔮")

    def tearDown(self):
        self.store.close()

    def add(self, result, *ingredients):
        return self.store.add_recipe(G, result["id"], [i["id"] for i in ingredients])

    def give(self, item, qty, user=U):
        self.store.add_item(G, user, item["id"], qty)

    def item(self, item):
        return self.store.item(G, item["id"])


class RecipeRuleTests(StoreFixture):
    def test_no_recipe_comes_with_the_bot(self):
        self.assertEqual(self.store.recipes(G), [])
        self.assertEqual(Store(":memory:").recipes(G), [])

    def test_adding_sets_the_result_price_from_the_ingredients_and_puts_it_on_sale(self):
        done = self.add(self.out, self.a, self.b)
        self.assertEqual((done["price"], done["listed"]), (160, True))
        out = self.item(self.out)
        self.assertEqual((out["sell_price"], out["sell_max"], out["sellable"]), (160, None, 1))
        done = self.add(self.out2, self.a, self.b, self.c)
        self.assertEqual(done["price"], (100 + 100 + 300) * 6 // 10)                       # 300
        self.assertEqual(self.item(self.out2)["sell_price"], 300)

    def test_an_ingredient_can_be_used_twice(self):
        done = self.add(self.out, self.a, self.a)
        self.assertEqual(done["price"], 160)
        self.assertEqual(done["recipe"]["ingredients"], sorted([self.a["id"]] * 2))

    def test_the_same_ingredients_in_any_order_are_one_recipe(self):
        self.add(self.out, self.a, self.b)
        with self.assertRaisesRegex(StoreError, "같은 재료의 레시피가 이미 있어요"):
            self.add(self.out2, self.b, self.a)
        self.add(self.out2, self.a, self.b, self.c)                                          # 재료가 하나 더 들어가면 다른 레시피

    def test_rules(self):
        cases = [
            ((self.out, self.a), "재료는 2~3개"),
            ((self.out, self.a, self.b, self.c, self.out2), "재료는 2~3개"),
            ((self.out, self.a, self.out), "결과물이 재료로"),
        ]
        for (result, *ingredients), message in cases:
            with self.assertRaisesRegex(StoreError, message):
                self.add(result, *ingredients)
        self.assertEqual(self.store.recipes(G), [])

    def test_a_role_cannot_be_used(self):
        self.store.create_item(G, "손님", kind="role", role_id=77, sell_price=50, sellable=True)
        role = self.store.item_by_name(G, "손님")
        with self.assertRaisesRegex(StoreError, "역할은 안 돼요"):
            self.add(self.out, self.a, role)
        with self.assertRaisesRegex(StoreError, "역할은 안 돼요"):
            self.add(role, self.a, self.b)

    def test_ingredients_need_a_fixed_price_of_at_least_one(self):
        free = self.store.create_item(G, "공짜", emoji="🎈")
        negative = self.store.create_item(G, "유리", emoji="🥃", sell_price=-50, sellable=True)
        ranged = self.store.create_item(G, "복불복", emoji="🎲", sell_price=-10, sell_max=30, sellable=True)
        for bad in (free, negative, ranged):
            with self.assertRaisesRegex(StoreError, "판매가가 1 이상의 고정값"):
                self.add(self.out, self.a, bad)
        self.assertEqual(self.store.recipes(G), [])

    def test_results_and_ingredients_cannot_swap_roles(self):
        self.add(self.out, self.a, self.b)
        with self.assertRaisesRegex(StoreError, "이미 다른 레시피의 결과물"):
            self.add(self.out, self.a, self.c)                                              # 한 물품에 레시피 하나
        with self.assertRaisesRegex(StoreError, "다른 레시피의 재료"):
            self.add(self.a, self.b, self.c)                                                # 재료는 결과물이 될 수 없다
        with self.assertRaisesRegex(StoreError, "연금술 결과물이라 재료로 쓸 수 없어요"):
            self.add(self.out2, self.out, self.c)                                           # 합성물을 다시 합칠 수는 없다

    def test_unknown_items_and_a_price_above_the_currency_max(self):
        with self.assertRaisesRegex(StoreError, "그런 물품이 없어요"):
            self.store.add_recipe(G, self.out["id"], [self.a["id"], 99999])
        self.store.set_currency(G, max=500)
        rich = self.store.create_item(G, "큰 보석", emoji="💎", sell_price=450, sellable=True)
        with self.assertRaisesRegex(StoreError, "재화 최대"):
            self.add(self.out, rich, self.c)                                                # (450+300)×0.8 = 600 > 500

    def test_the_recipe_limit(self):
        with self.store.db:
            for n in range(MAX_RECIPES):
                self.store.db.execute("INSERT INTO recipes(guild,result,ingredients) VALUES(?,?,?)", (G, 10_000 + n, f"[{n},{n + 1}]"))
        with self.assertRaisesRegex(StoreError, f"최대 {MAX_RECIPES}개"):
            self.add(self.out, self.a, self.b)

    def test_a_full_shop_still_gets_the_recipe_but_not_the_listing(self):
        with self.store.db:
            listed = len(self.store.items(G, shop_only=True))
            for n in range(MAX_SHOP_ITEMS - listed):
                self.store.db.execute("INSERT INTO items(guild,name,emoji,kind,buyable,buy_price) VALUES(?,?,?,?,?,?)", (G, f"자리{n}", "🎃", "item", 1, 1))
        done = self.add(self.out, self.a, self.b)
        self.assertFalse(done["listed"])
        out = self.item(self.out)
        self.assertEqual((out["sell_price"], out["sellable"]), (160, 0))                  # 가격은 정해지고 판매만 꺼져 있다.

    def test_deleting_a_recipe_keeps_the_item_and_its_price_and_frees_the_price(self):
        self.add(self.out, self.a, self.b)
        self.assertTrue(self.store.delete_recipe(G, self.out["id"]))
        self.assertFalse(self.store.delete_recipe(G, self.out["id"]))
        self.assertEqual(self.item(self.out)["sell_price"], 160)
        self.store.update_item(G, self.out["id"], sell_price=999)                          # 이제 직접 바꿀 수 있다.
        self.assertEqual(self.item(self.out)["sell_price"], 999)


class PriceFollowsIngredientsTests(StoreFixture):
    def test_changing_an_ingredient_price_recalculates_the_result(self):
        self.add(self.out, self.a, self.b)
        self.add(self.out2, self.b, self.c)
        self.store.update_item(G, self.b["id"], sell_price=200)
        self.assertEqual(self.item(self.out)["sell_price"], (100 + 200) * 8 // 10)         # 240
        self.assertEqual(self.item(self.out2)["sell_price"], (200 + 300) * 8 // 10)        # 400
        self.store.update_item(G, self.a["id"], name="마나 가루+")                           # 가격이 아닌 수정은 그대로
        self.assertEqual(self.item(self.out)["sell_price"], 240)

    def test_an_ingredient_cannot_be_given_a_price_that_has_no_value(self):
        self.add(self.out, self.a, self.b)
        for kwargs in (dict(sell_price=-5), dict(sell_price=10, sell_max=30), dict(sell_price=0, sellable=False)):
            with self.assertRaisesRegex(StoreError, "연금술 재료"):
                self.store.update_item(G, self.a["id"], **kwargs)
        self.assertEqual(self.item(self.a)["sell_price"], 100)

    def test_the_result_price_cannot_be_edited_by_hand_while_the_recipe_exists(self):
        self.add(self.out, self.a, self.b)
        with self.assertRaisesRegex(StoreError, "자동 계산"):
            self.store.update_item(G, self.out["id"], sell_price=1)
        self.store.update_item(G, self.out["id"], name="마력 영약", description="바뀐 설명", sellable=False)   # 가격 외 수정은 된다.
        self.assertEqual(self.item(self.out)["name"], "마력 영약")

    def test_items_used_in_a_recipe_cannot_be_deleted_until_the_recipe_is(self):
        self.add(self.out, self.a, self.b)
        for item in (self.a, self.out):
            with self.assertRaisesRegex(StoreError, "연금술 레시피"):
                self.store.delete_item(G, item["id"])
        self.store.delete_recipe(G, self.out["id"])
        self.assertEqual(self.store.delete_item(G, self.a["id"]), 0)


class AlchemizeTests(StoreFixture):
    def setUp(self):
        super().setUp()
        self.add(self.out, self.a, self.b)
        self.add(self.out2, self.a, self.b, self.c)

    def qty(self, item, user=U):
        return self.store.qty(G, user, item["id"])

    def test_two_ingredients_become_the_result(self):
        self.give(self.a, 2)
        self.give(self.b, 1)
        done = self.store.alchemize(G, U, [self.b["id"], self.a["id"]])                     # 순서는 상관없다.
        self.assertEqual((done["result"]["name"], done["price"]), ("마력 약", 160))
        self.assertEqual((self.qty(self.a), self.qty(self.b), self.qty(self.out)), (1, 0, 1))

    def test_three_ingredients(self):
        for item in (self.a, self.b, self.c):
            self.give(item, 1)
        done = self.store.alchemize(G, U, [self.c["id"], self.a["id"], self.b["id"]])
        self.assertEqual((done["result"]["name"], done["price"]), ("별빛 약", 300))
        self.assertEqual([self.qty(i) for i in (self.a, self.b, self.c, self.out2)], [0, 0, 0, 1])

    def test_the_result_stacks_and_everyone_has_their_own_bag(self):
        self.give(self.a, 2)
        self.give(self.b, 2)
        for _ in range(2):
            self.store.alchemize(G, U, [self.a["id"], self.b["id"]])
        self.assertEqual((self.qty(self.out), self.qty(self.out, OTHER)), (2, 0))

    def test_the_same_ingredient_twice_needs_two_of_them(self):
        double = self.store.create_item(G, "별 부스러기", emoji="🌟", sell_price=40, sellable=True)
        target = self.store.create_item(G, "별 결정", emoji="💠")
        self.add(target, double, double)
        self.give(double, 1)
        with self.assertRaisesRegex(StoreError, "2개 필요해요"):
            self.store.alchemize(G, U, [double["id"], double["id"]])
        self.assertEqual(self.qty(double), 1)
        self.give(double, 1)
        self.store.alchemize(G, U, [double["id"], double["id"]])
        self.assertEqual((self.qty(double), self.qty(target)), (0, 1))

    def test_a_missing_ingredient_or_recipe_changes_nothing(self):
        self.give(self.a, 1)
        before = lambda: [self.qty(i) for i in (self.a, self.b, self.c, self.out, self.out2)]
        for ids, message in (([self.a["id"], self.b["id"]], "필요해요"),                       # 달빛 이슬이 없다
                             ([self.a["id"], self.c["id"]], "레시피는 없어요"),                 # 그런 조합은 없다
                             ([self.a["id"]], "2~3개"), ([self.a["id"]] * 4, "2~3개")):
            with self.assertRaisesRegex(StoreError, message):
                self.store.alchemize(G, U, ids)
            self.assertEqual(before(), [1, 0, 0, 0, 0])

    def test_one_ingredient_you_do_not_have_does_not_eat_the_others(self):
        self.give(self.a, 1)
        self.give(self.c, 1)
        with self.assertRaises(StoreError):
            self.store.alchemize(G, U, [self.a["id"], self.b["id"], self.c["id"]])
        self.assertEqual((self.qty(self.a), self.qty(self.c), self.qty(self.out2)), (1, 1, 0))

    def test_a_deleted_recipe_no_longer_works(self):
        self.give(self.a, 1)
        self.give(self.b, 1)
        self.store.delete_recipe(G, self.out["id"])
        with self.assertRaisesRegex(StoreError, "레시피는 없어요"):
            self.store.alchemize(G, U, [self.a["id"], self.b["id"]])

    def test_stock_lists_only_recipe_ingredients_the_user_owns(self):
        self.give(self.a, 3)
        self.give(self.out, 1)                                       # 결과물은 재료가 아니다
        self.give(self.store.create_item(G, "잡동사니", emoji="🗑"), 5)
        self.assertEqual([(r["name"], r["qty"]) for r in self.store.alchemy_stock(G, U)], [("마나 가루", 3)])
        self.assertEqual(self.store.alchemy_stock(G, OTHER), [])


class OldDatabaseTests(unittest.TestCase):
    def test_a_database_from_before_alchemy_gets_the_table_and_keeps_its_data(self):
        from tests.test_seed import _old_database
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            _old_database(path)
            store = Store(path)
            self.assertEqual(store.recipes(100), [])
            self.assertEqual((store.wallet(100, 7), store.qty(100, 7, 1)), (777, 4))
            store.close()


class AdminCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Alchemy(self.w.bot)
        self.w.channel(400)
        patcher = mock.patch.object(alchemy_module, "refresh_panels", new=AsyncMock(return_value=1))
        self.refresh = patcher.start()
        self.addCleanup(patcher.stop)
        self.store.ensure_defaults(G)
        for name, price in (("마나 가루", 100), ("달빛 이슬", 100), ("별 조각", 300)):
            self.store.create_item(G, name, emoji="✨", sell_price=price, sellable=True)
        self.store.create_item(G, "마력 약", emoji="🧪")

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.ask(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        return i

    def text(self, i):
        embed, _ = sent_embed(i)
        return (embed.title or "") + "\n" + (embed.description or "")

    async def test_add_shows_the_price_formula_and_refreshes_the_shop(self):
        i = await self.run_cmd(Alchemy.recipe_add, "마력 약", "마나 가루", "달빛 이슬")
        text = self.text(i)
        self.assertIn("레시피를 더했어요", text)
        self.assertIn("✨ 마나 가루 + ✨ 달빛 이슬 → 🧪 마력 약", text)
        self.assertIn("🍬 160 = (100 + 100) ×0.8", text)
        self.assertIn("상점에 올라가 있어요", text)
        self.assertFalse(i.response.send_message.await_args.kwargs["ephemeral"])
        self.refresh.assert_awaited_once()
        self.assertEqual(self.store.item_by_name(G, "마력 약")["sell_price"], 160)

    async def test_three_ingredients_use_the_60_percent_rule(self):
        i = await self.run_cmd(Alchemy.recipe_add, "마력 약", "마나 가루", "달빛 이슬", "별 조각")
        self.assertIn("🍬 300 = (100 + 100 + 300) ×0.6", self.text(i))

    async def test_a_warning_when_the_result_is_not_worth_more_than_the_best_ingredient(self):
        self.store.create_item(G, "황금", emoji="🥇", sell_price=1000, sellable=True)
        self.store.create_item(G, "먼지", emoji="🌫", sell_price=1, sellable=True)
        i = await self.run_cmd(Alchemy.recipe_add, "마력 약", "황금", "먼지")               # (1000+1)×0.8 = 801 < 1000
        self.assertIn("⚠️ 합성물의 판매가가 가장 비싼 재료보다", self.text(i))
        i = await self.run_cmd(Alchemy.recipe_add, "마력 약", "마나 가루", "달빛 이슬")      # 이미 결과물
        self.assertIn("이미 다른 레시피의 결과물", self.text(i))

    async def test_errors_are_public_and_nothing_changes(self):
        for args, needle in ((("마력 약", "없는 물품", "달빛 이슬"), "없는 물품"), (("마력 약", "마나 가루", "마력 약"), "결과물이 재료로"),
                             (("없는 결과", "마나 가루", "달빛 이슬"), "없는 결과")):
            i = await self.run_cmd(Alchemy.recipe_add, *args)
            self.assertIn(needle, self.text(i))
            self.assertFalse(i.response.send_message.await_args.kwargs["ephemeral"])
        self.assertEqual(self.store.recipes(G), [])
        self.refresh.assert_not_awaited()

    async def test_only_admins(self):
        for command, args in ((Alchemy.recipe_add, ("마력 약", "마나 가루", "달빛 이슬")), (Alchemy.recipe_list, ()), (Alchemy.recipe_delete, ("마력 약",))):
            i = await self.run_cmd(command, *args, admin=False)
            self.assertIn("관리자만", self.text(i))
        self.assertEqual(self.store.recipes(G), [])

    async def test_list_and_delete(self):
        i = await self.run_cmd(Alchemy.recipe_list)
        self.assertIn("아직 레시피가 없어요", self.text(i))
        await self.run_cmd(Alchemy.recipe_add, "마력 약", "마나 가루", "달빛 이슬")
        i = await self.run_cmd(Alchemy.recipe_list)
        self.assertIn("레시피 1개", self.text(i))
        self.assertIn("→ 🧪 마력 약 (판매가 🍬 160)", self.text(i))
        self.assertIn("오후 6시부터 자정", self.text(i))
        i = await self.run_cmd(Alchemy.recipe_delete, "마력 약")
        self.assertIn("레시피를 지웠어요", self.text(i))
        self.assertEqual(self.store.recipes(G), [])
        i = await self.run_cmd(Alchemy.recipe_delete, "마력 약")
        self.assertIn("레시피가 없어요", self.text(i))

    async def test_autocompletes_offer_only_items_that_fit(self):
        await self.run_cmd(Alchemy.recipe_add, "마력 약", "마나 가루", "달빛 이슬")
        self.store.create_item(G, "유리병", emoji="🥃", sell_price=-5, sellable=True)
        self.store.create_item(G, "새 약", emoji="💊")
        ingredient = [c.value for c in await self.cog.ingredient_autocomplete(self.ask(), "")]
        self.assertIn("별 조각", ingredient)
        for hidden in ("마력 약", "유리병", "새 약", "열쇠"):                              # 결과물·음수·가격 없는 물품은 재료 후보가 아니다
            self.assertNotIn(hidden, ingredient)
        result = [c.value for c in await self.cog.result_autocomplete(self.ask(), "")]
        self.assertIn("새 약", result)
        for hidden in ("마력 약", "마나 가루", "달빛 이슬"):                               # 이미 결과물이거나 재료인 물품
            self.assertNotIn(hidden, result)
        self.assertEqual([c.value for c in await self.cog.delete_autocomplete(self.ask(), "")], ["마력 약"])


class PlayerScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.bot = self.w.bot
        self.cog = Alchemy(self.bot)
        self.w.channel(400)
        self.w.new_member(OTHER)
        self.store.ensure_defaults(G)
        self.a = self.store.create_item(G, "마나 가루", emoji="✨", sell_price=100, sellable=True)
        self.b = self.store.create_item(G, "달빛 이슬", emoji="💧", sell_price=100, sellable=True)
        self.out = self.store.create_item(G, "마력 약", emoji="🧪")
        self.store.add_recipe(G, self.out["id"], [self.a["id"], self.b["id"]])
        self.clock = mock.patch.object(alchemy_module.time, "time", return_value=at(20))
        self.time = self.clock.start()
        self.addCleanup(self.clock.stop)

    async def asyncTearDown(self):
        self.clock.stop()
        await self.w.close()

    def set_time(self, hour, minute=0, second=0):
        self.time.return_value = at(hour, minute, second)

    async def open(self):
        i = self.w.interaction()
        await Alchemy.alchemy.callback(self.cog, i)
        return i, i.response.send_message.await_args.kwargs

    def selects(self, view):
        return [c for c in view.children if hasattr(c, "options")]

    def craft_button(self, view):
        return next(c for c in view.children if getattr(c, "label", "") == "합성")

    async def pick(self, view, slot, item):
        i = self.w.interaction(data={"values": [str(item["id"])] if item else []})
        await self.selects(view)[slot].callback(i)
        return i

    async def craft(self, view):
        i = self.w.interaction()
        await view.craft(i)
        return i

    def give(self, item, qty, user=U):
        self.store.add_item(G, user, item["id"], qty)

    def qty(self, item, user=U):
        return self.store.qty(G, user, item["id"])

    async def test_the_command_is_public_and_shows_the_hours_and_rules(self):
        self.give(self.a, 2)
        _, kwargs = await self.open()
        self.assertFalse(kwargs["ephemeral"])
        text = "\n".join(f.name + f.value for f in kwargs["embed"].fields)
        self.assertIn("지금 열려 있어요", text)
        self.assertIn("오후 6시부터 자정(오전 12시)까지", text)
        self.assertIn("×0.8", text)
        self.assertIn("×0.6", text)
        self.assertIn("✨ 마나 가루 ×2", text)
        self.assertNotIn("마력 약", text)                                      # 레시피 내용은 알려 주지 않는다(관리자 목록에서만)

    async def test_three_selectors_and_a_craft_button_only_with_recipe_items_from_my_bag(self):
        self.give(self.a, 2)
        self.give(self.store.create_item(G, "잡동사니", emoji="🗑"), 4)
        _, kwargs = await self.open()
        view = kwargs["view"]
        self.assertEqual(len(self.selects(view)), 3)
        self.assertEqual([[o.label for o in s.options] for s in self.selects(view)], [["마나 가루 ×2"]] * 3)
        self.assertEqual([s.min_values for s in self.selects(view)], [0, 0, 0])
        self.assertFalse(self.craft_button(view).disabled)
        view.to_components()

    async def test_nothing_to_select_without_ingredients(self):
        _, kwargs = await self.open()
        self.assertEqual(self.selects(kwargs["view"]), [])
        self.assertTrue(self.craft_button(kwargs["view"]).disabled)
        self.assertIn("재료로 쓰이는 물품이 없어요", "".join(f.value for f in kwargs["embed"].fields))

    async def test_a_successful_craft_edits_the_same_message_and_swaps_the_items(self):
        self.give(self.a, 1)
        self.give(self.b, 1)
        view = (await self.open())[1]["view"]
        await self.pick(view, 0, self.a)
        await self.pick(view, 1, self.b)
        i = await self.craft(view)
        self.assertEqual((self.qty(self.a), self.qty(self.b), self.qty(self.out)), (0, 0, 1))
        i.response.send_message.assert_not_awaited()
        kwargs = i.response.edit_message.await_args.kwargs
        self.assertEqual(kwargs["embed"].title, "⚗️ 연금술 성공!")
        self.assertIn("✨ 마나 가루 + 💧 달빛 이슬", kwargs["embed"].description)
        self.assertIn("🧪 마력 약 **×1**", kwargs["embed"].description)
        self.assertIn("🍬 160", kwargs["embed"].description)
        self.assertEqual(self.selects(kwargs["view"]), [])                      # 재료가 떨어졌으니 선택창도 없다

    async def test_three_ingredients_through_the_screen(self):
        c = self.store.create_item(G, "별 조각", emoji="⭐", sell_price=300, sellable=True)
        star = self.store.create_item(G, "별빛 약", emoji="🔮")
        self.store.add_recipe(G, star["id"], [self.a["id"], self.b["id"], c["id"]])
        for item in (self.a, self.b, c):
            self.give(item, 1)
        view = (await self.open())[1]["view"]
        for slot, item in enumerate((c, self.a, self.b)):
            await self.pick(view, slot, item)
        await self.craft(view)
        self.assertEqual((self.qty(star), self.qty(c)), (1, 0))
        self.assertEqual(self.store.item(G, star["id"])["sell_price"], 300)

    async def test_it_refuses_outside_the_hours_even_if_the_screen_was_opened_earlier(self):
        self.give(self.a, 1)
        self.give(self.b, 1)
        view = (await self.open())[1]["view"]
        await self.pick(view, 0, self.a)
        await self.pick(view, 1, self.b)
        for hour, minute, second in ((17, 59, 59), (0, 0, 0), (3, 0, 0)):
            self.set_time(hour, minute, second)
            i = await self.craft(view)
            self.assertIn("오후 6시부터 자정", sent_embed(i)[0].description)
            self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"])
            self.assertEqual((self.qty(self.a), self.qty(self.out)), (1, 0))
        self.set_time(18, 0, 0)
        await self.craft(view)
        self.assertEqual(self.qty(self.out), 1)

    async def test_the_closed_screen_has_a_disabled_button_and_says_when_it_opens(self):
        self.give(self.a, 1)
        self.set_time(12)
        _, kwargs = await self.open()
        self.assertTrue(self.craft_button(kwargs["view"]).disabled)
        text = "".join(f.value for f in kwargs["embed"].fields)
        self.assertIn("지금은 닫혀 있어요", text)
        self.assertIn("6시간 0분 뒤", text)

    async def test_wrong_picks_explain_and_change_nothing(self):
        self.give(self.a, 1)
        self.give(self.b, 1)
        view = (await self.open())[1]["view"]
        i = await self.craft(view)                                              # 아무것도 안 고름
        self.assertIn("2개 이상", sent_embed(i)[0].description)
        await self.pick(view, 0, self.a)
        i = await self.craft(view)                                              # 하나만 고름
        self.assertIn("2개 이상", sent_embed(i)[0].description)
        await self.pick(view, 1, self.a)                                        # 같은 재료를 두 번(1개뿐)
        i = await self.craft(view)
        self.assertIn("레시피는 없어요", sent_embed(i)[0].description)
        await self.pick(view, 1, None)                                          # 선택을 지우면 빠진다
        self.assertEqual(view.picks, [self.a["id"], None, None])
        self.assertEqual((self.qty(self.a), self.qty(self.b), self.qty(self.out)), (1, 1, 0))

    async def test_a_second_craft_needs_a_second_set_of_ingredients(self):
        self.give(self.a, 1)
        self.give(self.b, 1)
        view = (await self.open())[1]["view"]
        await self.pick(view, 0, self.a)
        await self.pick(view, 1, self.b)
        await self.craft(view)
        i = await self.craft(view)                                              # 같은 화면에서 또 누름: 재료가 이미 없다
        self.assertIn("필요해요", sent_embed(i)[0].description)
        self.assertEqual(self.qty(self.out), 1)

    async def test_two_quick_presses_make_only_one(self):
        import asyncio
        self.give(self.a, 1)
        self.give(self.b, 1)
        view = (await self.open())[1]["view"]
        await self.pick(view, 0, self.a)
        await self.pick(view, 1, self.b)
        await asyncio.gather(self.craft(view), self.craft(view), self.craft(view))
        self.assertEqual((self.qty(self.out), self.qty(self.a), self.qty(self.b)), (1, 0, 0))

    async def test_only_the_person_who_opened_it_can_use_it(self):
        self.give(self.a, 1)
        view = (await self.open())[1]["view"]
        stranger = self.w.interaction(uid=OTHER)
        self.assertFalse(await view.interaction_check(stranger))
        self.assertIn("연 사람만", sent_embed(stranger)[0].description)
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)))

    async def test_the_crafted_item_sells_for_the_computed_price(self):
        self.give(self.a, 1)
        self.give(self.b, 1)
        view = (await self.open())[1]["view"]
        await self.pick(view, 0, self.a)
        await self.pick(view, 1, self.b)
        await self.craft(view)
        result = await services.sell(self.store, self.w.locks, self.w.member, self.store.item(G, self.out["id"]), 1)
        self.assertTrue(result.ok)
        self.assertEqual(self.store.wallet(G, U), 160)                           # (100 + 100) × 0.8

    async def test_the_embed_helper_and_the_timeout_state(self):
        self.give(self.a, 1)
        now = at(19)
        self.assertEqual(alchemy_embed(self.store, G, U, now, "메모", ok=False).title, "⚗️ 연금술 실패")
        self.assertEqual(alchemy_embed(self.store, G, U, now).title, "⚗️ 연금술")


if __name__ == "__main__":
    unittest.main()
