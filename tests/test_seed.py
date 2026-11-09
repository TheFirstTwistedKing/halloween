"""패키지 물품 7종·음수/범위 판매가·기본 확률표, 운영 중인 서버의 이전, 옛 슬래시 명령어 정리 시험."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, PropertyMock, patch

import discord

import services
import theme
from cogs.shop import Shop
from main import EventBot
from store import (MAX_SHOP_ITEMS, NEW_ITEMS, SEED_VERSION, TRASH_SELL, Store, StoreError, first_package_table)
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
PRICES = {"witch_hat": (100, None), "ghost_cloth": (100, None), "broken_glass": (-150, None), "jack_o_lantern": (250, None),
          "moon_shard": (300, None), "vampire_wine": (500, None), "mystery_bottle": (-300, 300)}


class Scripted2(Scripted):
    """randint가 (low, high)를 기록하고 정해 둔 값을 차례로 돌려준다."""

    def __init__(self, ints):
        super().__init__(ints=ints)
        self.asked = []

    def randint(self, low, high):
        self.asked.append((low, high))
        return super().randint(low, high)


class DefaultsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.ids = self.store.ensure_system_items(G)

    def tearDown(self):
        self.store.close()

    def test_the_seven_items_exist_with_the_requested_prices_and_can_be_sold(self):
        for system, name, *_ in NEW_ITEMS:
            row = self.store.item(G, self.ids[system])
            low, high = PRICES[system]
            self.assertEqual((row["name"], row["sell_price"], row["sell_max"], row["sellable"], row["buyable"]), (name, low, high, 1, 0), name)
        self.assertEqual([item[1] for item in NEW_ITEMS], ["구형 CPU", "방열 패드", "깨진 디스플레이 파편", "무정전 전원 장치", "실리콘 웨이퍼", "액체 냉각제", "출처 불명 부품 키트"])

    def test_trash_is_sellable_between_minus_20_and_20(self):
        trash = self.store.item(G, self.ids["trash"])
        self.assertEqual((trash["sell_price"], trash["sell_max"], trash["sellable"]), (*TRASH_SELL, 1))
        self.assertEqual(TRASH_SELL, (-20, 20))

    def test_all_are_in_the_package_table_and_the_weights_are_percentages(self):
        table = self.store.package_table(G)
        self.assertEqual(sum(e["weight"] for e in table), 100)
        by_id = {e["id"]: e for e in table}
        for system, *_ in NEW_ITEMS:
            self.assertEqual(by_id[system]["item"], self.ids[system])
        self.assertEqual(self.store.settings(G)["seed_version"], SEED_VERSION)

    def test_better_items_are_rarer_and_bad_ones_are_common(self):
        w = {e["id"]: e["weight"] for e in self.store.package_table(G)}
        good = ["witch_hat", "jack_o_lantern", "moon_shard", "vampire_wine"]          # 값이 오를수록 확률이 내려간다.
        self.assertEqual(sorted(good, key=lambda k: PRICES[k][0]), good)
        self.assertEqual([w[k] for k in good], sorted((w[k] for k in good), reverse=True))
        self.assertEqual(w["witch_hat"], w["ghost_cloth"])                           # 같은 값(100)은 같은 확률
        self.assertEqual(w["vampire_wine"], min(v for k, v in w.items() if k != "revive"))   # 가장 비싼 물품이 가장 드물다(부활권은 따로 0.1%)
        self.assertEqual(w["revive"], 0.1)
        for bad in ("broken_glass", "trash"):                                        # 손해/쓸모없는 물품은 값나가는 물품보다 흔하다
            self.assertTrue(all(w[bad] > w[k] for k in ("jack_o_lantern", "moon_shard", "vampire_wine")), bad)
        self.assertGreater(w["broken_glass"], w["witch_hat"])
        self.assertLess(w["mystery_bottle"], w["broken_glass"])                      # 도박성 물품은 아주 흔하지는 않다
        self.assertTrue(w["none"] == max(w.values()))

    def test_expected_sale_value_of_a_package_is_modest(self):
        """안 팔면 그만인 손해 물품(깨진 디스플레이 파편)은 빼고, 팔면 받는 평균을 센다."""
        table, total = self.store.package_table(G), 0.0
        for e in table:
            row = self.store.item(G, e["item"]) if e["kind"] == "item" else None
            if row and row["sellable"]:
                high = row["sell_price"] if row["sell_max"] is None else row["sell_max"]
                value = (row["sell_price"] + high) / 2
                total += e["weight"] / 100 * max(0, value)
        self.assertTrue(20 <= total <= 40, total)


class SellingTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.ids = self.store.ensure_system_items(G)

    def tearDown(self):
        self.store.close()

    def give(self, system, qty, candy=0):
        self.store.add_item(G, U, self.ids[system], qty)
        if candy:
            self.store.add_candy(G, U, candy)

    def test_a_positive_fixed_price_still_pays_per_item(self):
        self.give("moon_shard", 3)
        self.assertEqual(self.store.sell_item(G, U, self.ids["moon_shard"], 3), 900)
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, self.ids["moon_shard"])), (900, 0))

    def test_a_negative_price_charges_the_seller(self):
        self.give("broken_glass", 2, candy=500)
        self.assertEqual(self.store.sell_item(G, U, self.ids["broken_glass"], 2), -300)
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, self.ids["broken_glass"])), (200, 0))

    def test_cannot_sell_a_negative_item_without_enough_currency_and_nothing_changes(self):
        self.give("broken_glass", 2, candy=299)
        with self.assertRaises(StoreError) as caught:
            self.store.sell_item(G, U, self.ids["broken_glass"], 2)
        self.assertIn("모자라서", str(caught.exception))
        self.assertIn("300", str(caught.exception))
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, self.ids["broken_glass"])), (299, 2))
        self.assertEqual(self.store.sell_item(G, U, self.ids["broken_glass"], 1), -150)          # 하나씩은 된다.

    def test_the_price_range_is_rolled_for_every_single_item(self):
        self.give("mystery_bottle", 3, candy=900)
        rng = Scripted2(ints=[-300, 300, 25])
        self.assertEqual(self.store.sell_item(G, U, self.ids["mystery_bottle"], 3, rng), 25)
        self.assertEqual(rng.asked, [(-300, 300)] * 3)
        self.assertEqual(self.store.wallet(G, U), 925)

    def test_a_range_needs_enough_currency_for_the_worst_case_so_nobody_can_reroll_for_free(self):
        self.give("mystery_bottle", 2, candy=599)
        with self.assertRaises(StoreError):
            self.store.sell_item(G, U, self.ids["mystery_bottle"], 2, Scripted2(ints=[1, 1]))
        self.assertEqual(self.store.qty(G, U, self.ids["mystery_bottle"]), 2)
        rng = Scripted2(ints=[])
        self.assertEqual(self.store.sell_item(G, U, self.ids["mystery_bottle"], 1, rng), -300)   # 값을 안 정하면 최소값(-300)
        self.assertEqual(self.store.wallet(G, U), 299)

    def test_a_range_that_could_pass_the_cap_is_refused(self):
        top = self.store.currency(G).max
        self.give("mystery_bottle", 1, candy=top - 299)                      # 최대 +300이 나오면 넘칠 수 있다
        with self.assertRaises(StoreError) as caught:
            self.store.sell_item(G, U, self.ids["mystery_bottle"], 1, Scripted2(ints=[-5]))
        self.assertIn("최대", str(caught.exception))
        self.assertEqual(self.store.qty(G, U, self.ids["mystery_bottle"]), 1)

    def test_trash_between_minus_20_and_20(self):
        self.give("trash", 2, candy=40)
        total = self.store.sell_item(G, U, self.ids["trash"], 2, Scripted2(ints=[-20, 20]))
        self.assertEqual((total, self.store.wallet(G, U)), (0, 40))

    def test_the_wallet_never_goes_below_zero_even_with_bad_luck(self):
        self.give("mystery_bottle", 4, candy=1200)
        self.store.sell_item(G, U, self.ids["mystery_bottle"], 4, Scripted2(ints=[-300] * 4))
        self.assertEqual(self.store.wallet(G, U), 0)


class ItemRuleTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_prices_validate(self):
        top = self.store.currency(1).max
        bad = [dict(sell_price=-top - 1, sellable=True), dict(sell_price=5, sell_max=4, sellable=True), dict(sell_price=0, sellable=True),
               dict(sell_price=1, sell_max=top + 1, sellable=True), dict(sell_price=1.5, sellable=True), dict(sell_price=True, sellable=True),
               dict(kind="role", role_id=5, sell_price=-5, sellable=True), dict(kind="role", role_id=6, sell_price=1, sell_max=9, sellable=True)]
        for kwargs in bad:
            with self.assertRaises(StoreError, msg=str(kwargs)):
                self.store.create_item(1, "물건", **kwargs)
        self.assertEqual(self.store.items(1), [])
        self.store.create_item(1, "음수", sell_price=-5, sellable=True)
        self.store.create_item(1, "범위", sell_price=-5, sell_max=0, sellable=True)
        self.store.create_item(1, "영을 지나는 범위", sell_price=0, sell_max=9, sellable=True)

    def test_min_equal_to_max_is_a_fixed_price(self):
        item = self.store.create_item(1, "물건", sell_price=7, sell_max=7, sellable=True)
        self.assertIsNone(item["sell_max"])
        ranged = self.store.update_item(1, item["id"], sell_price=-3, sell_max=3)
        self.assertEqual((ranged["sell_price"], ranged["sell_max"]), (-3, 3))
        fixed = self.store.update_item(1, item["id"], sell_price=4, sell_max=4)
        self.assertEqual((fixed["sell_price"], fixed["sell_max"]), (4, None))

    def test_raising_the_min_above_an_existing_max_asks_for_the_max_too(self):
        item = self.store.create_item(1, "물건", sell_price=-3, sell_max=3, sellable=True)
        with self.assertRaisesRegex(StoreError, "판매최대"):
            self.store.update_item(1, item["id"], sell_price=10)
        self.assertEqual(self.store.update_item(1, item["id"], sell_price=10, sell_max=20)["sell_max"], 20)


class DisplayTests(unittest.TestCase):
    def row(self, low, high=None):
        return {"sell_price": low, "sell_max": high}

    def test_sell_text(self):
        self.assertEqual(theme.sell_text(self.row(100)), "💾 100")
        self.assertEqual(theme.sell_text(self.row(-150)), "💾 -150")
        self.assertEqual(theme.sell_text(self.row(-300, 300)), "💾 -300 ~ 300")
        self.assertEqual(theme.sell_text({"sell_price": 5}), "💾 5")                    # 칸이 없는 옛 형식도 그대로
        gem = theme.Currency("<:gem:123456789012345678>", "젬", 999)
        self.assertEqual(theme.sell_text(self.row(-300, 300), gem, plain=True), "젬 -300 ~ 300")

    def test_notes_warn_only_when_the_price_is_unusual(self):
        self.assertEqual(theme.sell_note(self.row(100)), "")
        self.assertIn("재화를 내야", theme.sell_note(self.row(-150)))
        self.assertIn("손해", theme.sell_note(self.row(-300, 300)))
        self.assertNotIn("손해", theme.sell_note(self.row(5, 50)))
        self.assertIn("팔 때마다", theme.sell_note(self.row(5, 50)))


class ServiceMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.ids = self.store.ensure_system_items(G)

    async def asyncTearDown(self):
        await self.w.close()

    async def sell(self, system, qty, rng=None):
        item = self.store.item(G, self.ids[system])
        return await services.sell(self.store, self.w.locks, self.w.member, item, qty, rng)

    async def test_messages_say_whether_you_were_paid_charged_or_got_nothing(self):
        self.store.add_item(G, U, self.ids["witch_hat"], 1)
        paid = await self.sell("witch_hat", 1)
        self.assertTrue(paid.ok)
        self.assertIn("💾 100을 받았어요", paid.text)
        self.assertEqual(self.store.wallet(G, U), 100)
        self.store.add_candy(G, U, 100)                                                   # 150을 내려면 재화가 있어야 한다.
        self.store.add_item(G, U, self.ids["broken_glass"], 1)
        charged = await self.sell("broken_glass", 1)
        self.assertTrue(charged.ok)
        self.assertIn("💾 150을 냈어요", charged.text)
        self.assertEqual(self.store.wallet(G, U), 50)
        self.store.add_item(G, U, self.ids["trash"], 1)
        nothing = await self.sell("trash", 1, Scripted2(ints=[0]))
        self.assertIn("받은 재화가 없어요", nothing.text)
        self.assertEqual(self.store.wallet(G, U), 50)

    async def test_a_refusal_is_reported_not_raised(self):
        self.store.add_item(G, U, self.ids["broken_glass"], 1)
        result = await self.sell("broken_glass", 1)                                       # 지갑이 비어 있다
        self.assertFalse(result.ok)
        self.assertIn("모자라서", result.text)


class ShopCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Shop(self.w.bot)
        self.w.channel(400)
        self.w.bot.get_channel = lambda cid: self.w.channels.get(cid)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self):
        return self.w.interaction(admin=True)

    async def test_register_a_negative_and_a_range_item_with_the_commands(self):
        i = self.ask()
        await Shop.add_item.callback(self.cog, i, "함정", False, True, 0, -50, "", "⚡", None)
        self.assertIn("판매 ✅ 💾 -50", sent_embed(i)[0].description)
        i = self.ask()
        await Shop.add_item.callback(self.cog, i, "복불복", False, True, 0, -10, "", "🎲", 30)
        self.assertIn("💾 -10 ~ 30", sent_embed(i)[0].description)
        row = self.store.item_by_name(G, "복불복")
        self.assertEqual((row["sell_price"], row["sell_max"]), (-10, 30))

    async def test_edit_turns_a_fixed_price_into_a_range_and_back(self):
        self.store.create_item(G, "물건", sell_price=5, sellable=True)
        i = self.ask()
        await Shop.edit_item.callback(self.cog, i, "물건", sell_price=-20, sell_max=20)
        self.assertIn("💾 -20 ~ 20", sent_embed(i)[0].description)
        i = self.ask()
        await Shop.edit_item.callback(self.cog, i, "물건", sell_price=9, sell_max=9)
        row = self.store.item_by_name(G, "물건")
        self.assertEqual((row["sell_price"], row["sell_max"]), (9, None))
        i = self.ask()
        await Shop.edit_item.callback(self.cog, i, "물건", sell_price=-1, sell_max=-5)
        self.assertIn("판매 최대가", sent_embed(i)[0].description)

    async def test_trash_can_be_edited_like_any_item(self):
        self.store.ensure_defaults(G)
        i = self.ask()
        await Shop.edit_item.callback(self.cog, i, "쓰레기", sell_price=-5, sell_max=5)
        trash = self.store.item_by_name(G, "쓰레기")
        self.assertEqual((trash["sell_price"], trash["sell_max"]), (-5, 5))


def _old_database(path, *, shop_items=0, custom_table=None, trash_sellable=False, extra_names=()):
    """판매가 범위(sell_max)와 새 물품이 생기기 전 구조의 DB를 직접 만든다. 운영 중이던 서버처럼 가방·지갑 데이터가 들어 있다."""
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE settings(guild INTEGER PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE wallets(guild INTEGER, user INTEGER, candy INTEGER NOT NULL DEFAULT 0 CHECK(candy >= 0),
            exp INTEGER NOT NULL DEFAULT 0 CHECK(exp >= 0), PRIMARY KEY(guild, user));
        CREATE TABLE items(id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL CHECK(kind IN ('item','role')), role_id INTEGER,
            buy_price INTEGER NOT NULL DEFAULT 0, sell_price INTEGER NOT NULL DEFAULT 0,
            buyable INTEGER NOT NULL DEFAULT 0, sellable INTEGER NOT NULL DEFAULT 0, system TEXT);
        CREATE UNIQUE INDEX items_name ON items(guild, lower(name));
        CREATE UNIQUE INDEX items_system ON items(guild, system) WHERE system IS NOT NULL;
        CREATE TABLE inventory(guild INTEGER, user INTEGER, item INTEGER, qty INTEGER NOT NULL CHECK(qty > 0), PRIMARY KEY(guild, user, item));
    """)
    db.execute("INSERT INTO items(id,guild,name,emoji,description,kind,system) VALUES(1,100,'열쇠','🗝️','옛 설명','item','key')")
    db.execute("INSERT INTO items(id,guild,name,emoji,description,kind,system,sellable,sell_price) VALUES(2,100,'쓰레기','🗑️','','item','trash',?,?)",
               (int(trash_sellable), 3 if trash_sellable else 0))
    for n in range(shop_items):
        db.execute("INSERT INTO items(guild,name,emoji,kind,buyable,buy_price) VALUES(100,?,'⚙️','item',1,5)", (f"진열{n}",))
    for name in extra_names:
        db.execute("INSERT INTO items(guild,name,emoji,description,kind) VALUES(100,?,'⚙️','내가 만든 것','item')", (name,))
    db.execute("INSERT INTO wallets(guild,user,candy) VALUES(100,7,777)")
    db.execute("INSERT INTO inventory VALUES(100,7,1,4)")
    import json
    table = custom_table or [
        {"id": "none", "kind": "none", "weight": 60, "amount": 1, "emoji": "👻", "title": "꽝"},
        {"id": "key", "kind": "item", "item": 1, "weight": 20, "amount": 1},
        {"id": "candy", "kind": "candy", "weight": 10, "amount": 50},
        {"id": "trash", "kind": "item", "item": 2, "weight": 10, "amount": 1}]
    db.execute("INSERT INTO settings VALUES(100, ?)", (json.dumps({"seeded": True, "entry_item": 1, "entry_cost": 1, "package_table": table,
                                                                 "channels": [400]}, ensure_ascii=False),))
    db.commit()
    db.close()


class MigrationTests(unittest.TestCase):
    """이미 운영 중이던 서버(예전 구조의 DB)가 업데이트 뒤 처음 열릴 때."""

    def open(self, **kwargs):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        path = Path(self.dir.name) / "old.sqlite3"
        _old_database(path, **kwargs)
        store = Store(path)
        self.addCleanup(store.close)
        return store

    def test_the_old_database_gets_the_new_column_and_keeps_all_its_data(self):
        store = self.open()
        columns = {r["name"] for r in store.db.execute("PRAGMA table_info(items)")}
        self.assertIn("sell_max", columns)
        self.assertEqual((store.wallet(100, 7), store.qty(100, 7, 1)), (777, 4))
        self.assertEqual(store.settings(100)["channels"], [400])
        store.ensure_defaults(100)
        self.assertEqual((store.wallet(100, 7), store.qty(100, 7, 1), store.settings(100)["entry_item"]), (777, 4, 1))
        self.assertEqual(store.item(100, 1)["description"], "옛 설명")

    def test_the_upgrade_adds_the_items_the_trash_range_and_the_new_table_once(self):
        store = self.open()
        store.ensure_defaults(100)
        names = [r["name"] for r in store.items(100)]
        self.assertEqual(names, ["열쇠", "쓰레기", *(item[1] for item in NEW_ITEMS), "부활권"])
        trash = store.item_by_name(100, "쓰레기")
        self.assertEqual((trash["sellable"], trash["sell_price"], trash["sell_max"]), (1, -20, 20))
        table = store.settings(100)["package_table"]
        self.assertEqual((round(sum(e["weight"] for e in table), 6), len(table)), (100, 12))
        self.assertEqual(next(e for e in table if e["id"] == "key")["item"], 1)                # 기존 열쇠를 이어받는다
        self.assertEqual(next(e for e in table if e["id"] == "trash")["item"], 2)
        snapshot = (store.items(100), store.settings(100)["package_table"])
        store.ensure_defaults(100)
        store.ensure_defaults(100)
        self.assertEqual(len(store.items(100)), 10)                                         # 여러 번 열어도 한 번만
        self.assertEqual(store.settings(100)["package_table"], snapshot[1])
        reopened = Store(Path(self.dir.name) / "old.sqlite3")                                # 다시 켜도 그대로
        self.addCleanup(reopened.close)
        reopened.ensure_defaults(100)
        self.assertEqual(len(reopened.items(100)), 10)
        self.assertEqual(reopened.settings(100)["seed_version"], SEED_VERSION)

    def test_a_table_the_admin_changed_is_left_alone_but_the_items_are_still_made(self):
        custom = [{"id": "none", "kind": "none", "weight": 90, "amount": 1}, {"id": "key", "kind": "item", "item": 1, "weight": 10, "amount": 1}]
        store = self.open(custom_table=custom)
        store.ensure_defaults(100)
        table = store.settings(100)["package_table"]
        self.assertEqual(table[:2], custom)                                                 # 관리자가 정한 칸과 비율은 그대로 두고
        revive = store.revive_item(100)
        self.assertEqual([(e["id"], e["item"]) for e in table[2:]], [("revive", revive["id"])])   # 부활권만 덧붙인다
        self.assertAlmostEqual(table[2]["weight"] / sum(e["weight"] for e in table), 0.001, places=6)   # 몫이 0.1%
        self.assertEqual(len(store.items(100)), 10)                                         # 물품은 만들어 둬서 /패키지결과 추가로 넣을 수 있다

    def test_a_trash_the_admin_already_made_sellable_keeps_its_price(self):
        store = self.open(trash_sellable=True)
        store.ensure_defaults(100)
        trash = store.item_by_name(100, "쓰레기")
        self.assertEqual((trash["sell_price"], trash["sell_max"]), (3, None))

    def test_an_item_the_admin_made_with_the_same_name_is_reused_not_overwritten(self):
        store = self.open(extra_names=("구형 CPU",))
        store.ensure_defaults(100)
        hats = [r for r in store.items(100) if r["name"] == "구형 CPU"]
        self.assertEqual(len(hats), 1)
        self.assertEqual((hats[0]["description"], hats[0]["sell_price"], hats[0]["sellable"]), ("내가 만든 것", 0, 0))
        table = store.settings(100)["package_table"]
        self.assertEqual(next(e for e in table if e["id"] == "witch_hat")["item"], hats[0]["id"])    # 결과표는 그 물품을 쓴다

    def test_a_full_shop_does_not_overflow_the_25_item_limit(self):
        store = self.open(shop_items=MAX_SHOP_ITEMS - 2)
        store.ensure_defaults(100)
        self.assertEqual(len(store.items(100, shop_only=True)), MAX_SHOP_ITEMS)                # 상점은 25칸이 꽉 찼다
        self.assertEqual(len(store.items(100)), 2 + (MAX_SHOP_ITEMS - 2) + 7 + 1)              # 물품은 7종과 부활권을 모두 만들었다(부활권은 상점에 올리지 않는다)
        new_names = [item[1] for item in NEW_ITEMS]
        sellable = [r["name"] for r in store.items(100) if r["name"] in new_names and r["sellable"]]
        self.assertEqual(sellable, new_names[:2])                                              # 남은 2칸에 앞의 두 물품만 판매가 켜졌고
        self.assertEqual(len([r for r in store.items(100) if r["name"] in new_names and not r["sellable"]]), 5)   # 나머지 5종은 꺼진 채다.
        self.assertEqual(store.item_by_name(100, "쓰레기")["sellable"], 0)                      # 자리가 없어 쓰레기도 그대로
        table = store.settings(100)["package_table"]
        self.assertEqual(len(table), 12)                                                       # 결과표에는 모두 들어가 뜯으면 가방에 들어온다

    def test_a_brand_new_server_gets_the_new_defaults_directly(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.ensure_defaults(5)
        self.assertEqual(len(store.items(5)), 10)
        self.assertAlmostEqual(sum(e["weight"] for e in store.settings(5)["package_table"]), 100)
        self.assertEqual(first_package_table({"key": 1, "trash": 2})[0]["weight"], 60)         # 처음 표는 그대로 보관(이전 판정용)


class ApplyDefaultTableTests(unittest.IsolatedAsyncioTestCase):
    """옛 서버의 관리자가 손댄 결과표는 자동 이전이 건드리지 않는다. 그럴 때 /패키지결과 기본값으로 직접 새 표를 적용한다."""

    async def asyncSetUp(self):
        from cogs.rules_admin import RulesAdmin
        from unittest import mock
        import cogs.rules_admin as rules_module
        self.w = World()
        self.store = self.w.store
        self.cog = RulesAdmin(self.w.bot)
        self.w.channel(400)
        patcher = mock.patch.object(rules_module, "refresh_panels", new=AsyncMock(return_value=1))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ids = self.store.ensure_system_items(G)
        custom = [{"id": "none", "kind": "none", "weight": 90, "amount": 1}, {"id": "key", "kind": "item", "item": self.ids["key"], "weight": 10, "amount": 1}]
        self.store.update_settings(G, package_table=custom)                 # 관리자가 손댄 표(자동 이전은 이걸 건드리지 않았다)
        self.custom = custom

    async def asyncTearDown(self):
        await self.w.close()

    async def ask_default(self, admin=True):
        from cogs.rules_admin import RulesAdmin
        i = self.w.interaction(admin=admin)
        await RulesAdmin.package_default.callback(self.cog, i)
        return i

    async def press(self, i):
        view = i.response.send_message.await_args.kwargs["view"]
        press = self.w.interaction(admin=True)
        await view.confirm(press)
        return press

    def text(self, i):
        embed = i.response.send_message.await_args.kwargs["embed"]
        return (embed.title or "") + (embed.description or "")

    async def test_the_preview_shows_the_new_table_and_changes_nothing_until_confirmed(self):
        i = await self.ask_default()
        text = self.text(i)
        for needle in ("지금 결과표 **2칸**", "빈 패킷 39.9%", "열쇠 14%", "구형 CPU 5.5%", "액체 냉각제 0.5%", "출처 불명 부품 키트 4%", "부활권 0.1%", "가방·재화·상점·던전 설정은 바뀌지 않아요"):
            self.assertIn(needle, text)
        self.assertEqual(self.store.settings(G)["package_table"], self.custom)

    async def test_confirming_replaces_a_customized_table_with_the_default(self):
        press = await self.press(await self.ask_default())
        table = self.store.settings(G)["package_table"]
        self.assertEqual((len(table), round(sum(e["weight"] for e in table), 6)), (12, 100))
        self.assertIn("12칸", press.response.edit_message.await_args.kwargs["embed"].description)
        by_id = {e["id"]: e for e in table}
        for system, name, *_ in NEW_ITEMS:
            self.assertEqual(self.store.item(G, by_id[system]["item"])["name"], name)

    async def test_it_makes_only_the_missing_items_and_keeps_existing_ones_untouched(self):
        mine = self.store.item_by_name(G, "구형 CPU")
        self.store.update_item(G, mine["id"], sell_price=999)                  # 관리자가 값을 바꿔 둔 물품
        moon = self.store.item_by_name(G, "실리콘 웨이퍼")
        with self.store.db:
            self.store.db.execute("DELETE FROM items WHERE id=?", (moon["id"],))      # 지워진 물품
        before = {r["name"]: r["id"] for r in self.store.items(G)}
        await self.press(await self.ask_default())
        after = {r["name"]: r["id"] for r in self.store.items(G)}
        self.assertEqual(self.store.item_by_name(G, "구형 CPU")["sell_price"], 999)       # 기존 물품은 그대로
        self.assertEqual(after["구형 CPU"], before["구형 CPU"])
        self.assertIn("실리콘 웨이퍼", after)                                                   # 없던 것만 새로 만든다
        self.assertEqual(self.store.item_by_name(G, "실리콘 웨이퍼")["sell_price"], 300)
        self.assertEqual(len(after), len(before) + 1)

    async def test_wallets_bags_and_other_settings_are_not_touched(self):
        self.store.add_candy(G, U, 321)
        self.store.add_item(G, U, self.ids["key"], 4)
        self.store.update_settings(G, entry_cost=3, channels=[400])
        await self.press(await self.ask_default())
        cfg = self.store.settings(G)
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, self.ids["key"])), (321, 4))
        self.assertEqual((cfg["entry_cost"], cfg["channels"], cfg["entry_item"]), (3, [400], self.ids["key"]))

    async def test_it_recreates_the_key_and_trash_if_the_admin_had_deleted_them(self):
        self.store.update_settings(G, entry_item=None, package_table=[{"id": "none", "kind": "none", "weight": 1, "amount": 1}])
        with self.store.db:
            self.store.db.execute("DELETE FROM items WHERE system IN ('key','trash') AND guild=?", (G,))
        await self.press(await self.ask_default())
        names = [r["name"] for r in self.store.items(G)]
        self.assertEqual((names.count("열쇠"), names.count("쓰레기")), (1, 1))
        self.assertEqual(len(self.store.settings(G)["package_table"]), 12)

    async def test_pressing_twice_is_the_same_as_once(self):
        await self.press(await self.ask_default())
        first = (self.store.settings(G)["package_table"], len(self.store.items(G)))
        await self.press(await self.ask_default())
        self.assertEqual((self.store.settings(G)["package_table"], len(self.store.items(G))), first)

    async def test_only_admins_and_only_the_admin_who_asked_can_confirm(self):
        i = await self.ask_default(admin=False)
        self.assertIn("관리자만", self.text(i))
        self.assertEqual(self.store.settings(G)["package_table"], self.custom)
        i = await self.ask_default()
        view = i.response.send_message.await_args.kwargs["view"]
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=7, admin=True)))
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=8, admin=True)))

    async def test_the_new_table_really_drops_the_new_items_in_a_package(self):
        from packages import open_package
        await self.press(await self.ask_default())
        self.store.create_package(G, 400, 9001, time.time() + 600, "all")
        self.w.bot.rng.randoms.append(0.9985)                                             # 부활권 바로 앞 = 출처 불명 부품 키트
        i = self.w.interaction(uid=5, message_id=9001)
        await open_package(i)
        self.assertIn("출처 불명 부품 키트", i.response.send_message.await_args.kwargs["embed"].title)
        bottle = self.store.item_by_name(G, "출처 불명 부품 키트")
        self.assertEqual(self.store.qty(G, 5, bottle["id"]), 1)


class PurgeTests(unittest.IsolatedAsyncioTestCase):
    """디스코드에 남은 코드에 없는 옛 명령어를 시작할 때 지운다."""

    def bot(self, guild_ids=None, guilds=(), existing=None):
        bot = EventBot(Store(":memory:"), guild_ids)
        existing = existing or {}
        bot.tree.fetch_commands = AsyncMock(side_effect=lambda guild=None: [NS(name=n) for n in existing.get(getattr(guild, "id", None), [])])
        bot.tree.sync = AsyncMock(return_value=[])
        patcher = patch.object(EventBot, "guilds", new_callable=PropertyMock, return_value=[NS(id=g) for g in guilds])
        patcher.start()
        self.addCleanup(patcher.stop)
        return bot

    async def asyncTearDown(self):
        pass

    async def test_with_guild_ids_the_old_global_commands_are_removed(self):
        bot = self.bot([111], guilds=[111], existing={None: ["옛명령", "또옛명령"], 111: ["가방"]})
        with self.assertLogs("halloween", level="INFO") as logs:
            removed = await bot.purge_stale_commands()
        self.assertEqual(removed, ["옛명령", "또옛명령"])
        bot.tree.sync.assert_awaited_once_with(guild=None)
        self.assertIn("/옛명령", "\n".join(logs.output))
        await bot.close()

    async def test_the_servers_in_guild_ids_are_never_touched(self):
        bot = self.bot([111], guilds=[111], existing={111: ["가방", "던전"]})
        self.assertEqual(await bot.purge_stale_commands(), [])
        bot.tree.sync.assert_not_awaited()                                                  # 지금 쓰는 서버용 명령어는 그대로
        await bot.close()

    async def test_other_servers_old_server_commands_are_removed_too(self):
        bot = self.bot([111], guilds=[111, 222], existing={222: ["옛명령"]})
        self.assertEqual(await bot.purge_stale_commands(), ["옛명령"])
        self.assertEqual([call.kwargs["guild"].id for call in bot.tree.sync.await_args_list], [222])
        await bot.close()

    async def test_without_guild_ids_old_server_commands_are_removed_and_global_ones_are_kept(self):
        bot = self.bot(None, guilds=[111, 222], existing={None: ["가방"], 111: ["옛명령"], 222: []})
        self.assertEqual(await bot.purge_stale_commands(), ["옛명령"])
        self.assertEqual([call.kwargs["guild"].id for call in bot.tree.sync.await_args_list], [111])
        bot.tree.fetch_commands.assert_any_await(guild=bot.tree.fetch_commands.await_args_list[0].kwargs["guild"])
        self.assertNotIn(None, [call.kwargs["guild"] for call in bot.tree.fetch_commands.await_args_list])   # 전체 서버용은 들여다보지도 않는다
        await bot.close()

    async def test_nothing_to_remove_means_no_sync_calls(self):
        bot = self.bot(None, guilds=[111], existing={})
        self.assertEqual(await bot.purge_stale_commands(), [])
        bot.tree.sync.assert_not_awaited()
        await bot.close()

    async def test_a_failing_server_does_not_stop_the_others(self):
        bot = self.bot(None, guilds=[111, 222], existing={111: ["a"], 222: ["b"]})
        calls = []

        async def flaky_sync(guild=None):
            calls.append(guild.id)
            if guild.id == 111:
                raise discord.HTTPException(NS(status=403, reason="Forbidden"), "missing access")
            return []
        bot.tree.sync = AsyncMock(side_effect=flaky_sync)
        with self.assertLogs("halloween", level="WARNING") as logs:
            removed = await bot.purge_stale_commands()
        self.assertEqual((calls, removed), ([111, 222], ["b"]))
        self.assertIn("서버 ID 111", "\n".join(logs.output))
        await bot.close()

    async def test_it_runs_once_even_if_the_connection_is_restored(self):
        bot = self.bot(None, guilds=[111], existing={111: ["옛명령"]})
        with patch.object(EventBot, "user", new_callable=PropertyMock, return_value="봇"):
            await bot.on_ready()
            await bot.on_ready()
        self.assertEqual(bot.tree.sync.await_count, 1)
        await bot.close()


if __name__ == "__main__":
    unittest.main()
