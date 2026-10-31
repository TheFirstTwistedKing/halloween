"""규칙과 저장소 시험: 확률, 재화 상한, 물품·가방, 사고팔기, 패키지, 재화·입장 물품·결과표 설정."""
import random
import unittest

from store import (CANDY_MAX, DEFAULT_SETTINGS, MAX_SHOP_ITEMS, Store, StoreError, check_package_table, roll_candy,
                   roll_package_result, roll_package_spawn, valid_emoji, validate_settings)


class Scripted:
    """정해 둔 값을 차례로 돌려주는 가짜 난수 생성기."""
    def __init__(self, randoms=(), ints=()):
        self.randoms, self.ints = list(randoms), list(ints)

    def random(self):
        return self.randoms.pop(0)

    def randint(self, low, high):
        return self.ints.pop(0) if self.ints else low


class ProbabilityTests(unittest.TestCase):
    def test_candy_chance_boundaries(self):
        cfg = dict(DEFAULT_SETTINGS)
        self.assertEqual(roll_candy(cfg, Scripted([0.0], [3])), 3)
        self.assertEqual(roll_candy(cfg, Scripted([0.4999], [5])), 5)
        self.assertEqual(roll_candy(cfg, Scripted([0.5])), 0)               # 정확히 50%는 못 받는다.
        self.assertEqual(roll_candy(cfg, Scripted([0.9999])), 0)
        self.assertEqual(roll_candy({**cfg, "candy_chance": 0}, Scripted([0.0])), 0)
        self.assertEqual(roll_candy({**cfg, "candy_chance": 100}, Scripted([0.9999], [2])), 2)

    def test_candy_amount_is_1_to_5(self):
        rng = random.Random(1)
        amounts = {roll_candy({**DEFAULT_SETTINGS, "candy_chance": 100}, rng) for _ in range(2000)}
        self.assertEqual(amounts, {1, 2, 3, 4, 5})

    def test_package_spawn_boundaries(self):
        cfg = dict(DEFAULT_SETTINGS)
        self.assertTrue(roll_package_spawn(cfg, Scripted([0.0])))
        self.assertTrue(roll_package_spawn(cfg, Scripted([0.000999])))
        self.assertFalse(roll_package_spawn(cfg, Scripted([0.001])))        # 0.1% 경계
        self.assertFalse(roll_package_spawn({**cfg, "package_chance": 0}, Scripted([0.0])))

    def test_package_result_boundaries(self):
        table = Store(":memory:").package_table(1)
        for value, expected in [(0.0, "none"), (0.5999, "none"), (0.60, "key"), (0.7999, "key"), (0.80, "candy"),
                                (0.8999, "candy"), (0.90, "trash"), (0.9999, "trash")]:
            self.assertEqual(roll_package_result(table, Scripted([value]))["id"], expected, value)
        self.assertEqual(sum(e["weight"] for e in table), 100)

    def test_package_result_uses_ratios_not_percentages(self):
        table = [{"id": "a", "kind": "none", "weight": 1}, {"id": "b", "kind": "none", "weight": 3}]      # 합계 4
        for value, expected in [(0.0, "a"), (0.2499, "a"), (0.25, "b"), (0.9999, "b")]:
            self.assertEqual(roll_package_result(table, Scripted([value]))["id"], expected, value)
        zero_tail = [{"id": "a", "kind": "none", "weight": 1}, {"id": "z", "kind": "none", "weight": 0}]
        self.assertEqual(roll_package_result(zero_tail, Scripted([0.9999999]))["id"], "a")             # 확률 0인 칸은 나오지 않는다.
        self.assertEqual(roll_package_result(zero_tail, Scripted([1.0]))["id"], "a")                   # 부동소수점 끝값에서도 마찬가지

    def test_large_sample_matches_the_stated_rates(self):
        rng, n = random.Random(20241031), 400_000
        cfg = dict(DEFAULT_SETTINGS)
        gained = sum(1 for _ in range(n) if roll_candy(cfg, rng))
        self.assertAlmostEqual(gained / n, 0.50, delta=0.01)
        spawns = sum(1 for _ in range(1_000_000) if roll_package_spawn(cfg, rng))
        self.assertTrue(800 <= spawns <= 1200, spawns)                      # 기대 1000회(0.1%)
        table = Store(":memory:").package_table(1)
        counts = {e["id"]: 0 for e in table}
        for _ in range(n):
            counts[roll_package_result(table, rng)["id"]] += 1
        for e in table:
            self.assertAlmostEqual(counts[e["id"]] / n * 100, e["weight"], delta=1.0, msg=e["id"])

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_defaults_follow_the_requested_rules(self):
        cfg = self.store.settings(1)
        self.assertEqual((cfg["candy_chance"], cfg["candy_min"], cfg["candy_max"], cfg["package_chance"]), (50.0, 1, 5, 0.1))
        self.assertEqual((cfg["package_mode"], cfg["channels"]), ("all", []))

    def test_updates_validate_and_persist_and_are_isolated(self):
        self.store.update_settings(1, candy_chance=75.5, channels=[10, 20], package_mode="first")
        self.assertEqual(self.store.settings(1)["candy_chance"], 75.5)
        self.assertEqual(self.store.settings(2)["candy_chance"], 50.0)
        copy = self.store.settings(1)
        copy["channels"].append(99)
        self.assertEqual(self.store.settings(1)["channels"], [10, 20])        # 바깥에서 고쳐도 저장값은 그대로
        for bad in [dict(candy_chance=101), dict(candy_chance=-1), dict(candy_min=6, candy_max=5), dict(package_mode="x"),
                    dict(package_seconds=5), dict(candy_cooldown=True), dict(reaction="yes"), dict(channels=["a"]), dict(currency_name=""), dict(currency_emoji="abc"),
                    dict(currency_max=0), dict(entry_cost=0), dict(package_table=[{"id": "x"}])]:
            with self.assertRaises(ValueError, msg=str(bad)):
                self.store.update_settings(1, **bad)
        with self.assertRaises(ValueError):
            self.store.update_settings(1, 없는설정=1)
        self.assertEqual(self.store.settings(1)["candy_chance"], 75.5)         # 거절된 변경은 반영되지 않는다.
        validate_settings(dict(DEFAULT_SETTINGS))


class WalletTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_candy_is_capped_and_never_negative(self):
        self.assertEqual(self.store.add_candy(1, 7, 5), 5)
        self.assertEqual(self.store.add_candy(1, 7, CANDY_MAX), CANDY_MAX - 5)   # 넘치는 만큼은 버려진다.
        self.assertEqual(self.store.wallet(1, 7), CANDY_MAX)
        self.assertEqual(self.store.add_candy(1, 7, 3), 0)
        self.assertEqual(self.store.add_candy(1, 7, -CANDY_MAX - 500), -CANDY_MAX)
        self.assertEqual(self.store.wallet(1, 7), 0)

    def test_servers_and_users_are_separate(self):
        self.store.add_candy(1, 7, 10)
        self.assertEqual((self.store.wallet(2, 7), self.store.wallet(1, 8)), (0, 0))

    def test_there_is_no_exp_feature_any_more(self):
        for gone in ("add_exp", "exchange_exp"):
            self.assertFalse(hasattr(self.store, gone), gone)
        self.assertNotIn("exp_min", DEFAULT_SETTINGS)
        self.assertIsInstance(self.store.wallet(1, 7), int)

    def test_old_exp_data_and_settings_are_kept_but_ignored(self):
        """예전 버전의 DB: 지갑의 exp 열과 설정의 exp_* 키는 그대로 두되 쓰지 않는다."""
        import json
        self.store.add_candy(1, 7, 5)
        self.store.db.execute("UPDATE wallets SET exp=777 WHERE guild=1 AND user=7")
        self.store.db.execute("INSERT INTO settings VALUES(1, ?)", (json.dumps({"candy_min": 2, "exp_min": 15, "exp_max": 25, "exp_cooldown": 60}),))
        self.store.db.commit()
        self.store._settings.clear()
        cfg = self.store.settings(1)
        self.assertEqual((cfg["candy_min"], "exp_min" in cfg), (2, False))
        self.store.update_settings(1, candy_max=9)                                   # 저장해도 옛 키가 다시 살아나지 않는다.
        self.assertNotIn("exp_max", json.loads(self.store.db.execute("SELECT data FROM settings WHERE guild=1").fetchone()["data"]))
        self.assertEqual(self.store.db.execute("SELECT exp FROM wallets WHERE guild=1 AND user=7").fetchone()["exp"], 777)
        self.assertEqual(self.store.wallet(1, 7), 5)


class CurrencyTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_default_currency_and_changes_are_per_server(self):
        cur = self.store.currency(1)
        self.assertEqual((cur.emoji, cur.name, cur.max), ("🍬", "할로윈 재화", CANDY_MAX))
        self.store.set_currency(1, emoji="💎", name="젬", max=500)
        self.assertEqual(tuple(self.store.currency(1)), ("💎", "젬", 500))
        self.assertEqual(self.store.currency(2).name, "할로윈 재화")
        self.assertEqual(self.store.currency(1).money(1234), "💎 1,234")

    def test_the_max_is_used_everywhere(self):
        self.store.set_currency(1, max=100)
        self.assertEqual(self.store.add_candy(1, 7, 250), 100)
        self.assertEqual(self.store.add_candy_many(1, [7, 8], 60), (2, 60, 1))        # 7은 이미 가득이라 0, 8은 60
        self.assertEqual(self.store.wallet(1, 8), 60)
        item = self.store.create_item(1, "물건", sell_price=50, sellable=True)
        self.store.add_item(1, 8, item["id"], 1)
        with self.assertRaisesRegex(StoreError, "최대"):
            self.store.sell_item(1, 8, item["id"], 1)                                 # 60+50 > 100
        with self.assertRaisesRegex(StoreError, "가격"):
            self.store.create_item(1, "비싼 물건", buy_price=101, buyable=True)

    def test_lowering_the_max_trims_holders_and_raising_does_not(self):
        self.store.add_candy(1, 7, 900)
        self.store.add_candy(1, 8, 50)
        self.assertEqual(self.store.wallets_above(1, 100), 1)
        cur, trimmed = self.store.set_currency(1, max=100)
        self.assertEqual((cur.max, trimmed, self.store.wallet(1, 7), self.store.wallet(1, 8)), (100, 1, 100, 50))
        self.assertEqual(self.store.set_currency(1, max=5000)[1], 0)
        self.assertEqual(self.store.wallet(1, 7), 100)

    def test_bad_values_are_refused_and_change_nothing(self):
        self.store.add_candy(1, 7, 900)
        for bad in (dict(emoji="abc"), dict(emoji=""), dict(name=" 젬"), dict(name="가" * 21), dict(max=0), dict(max=-5), dict(zzz=1)):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.store.set_currency(1, **bad)
        self.assertEqual((self.store.wallet(1, 7), self.store.currency(1).name), (900, "할로윈 재화"))
        with self.assertRaises(ValueError):
            self.store.set_currency(1, max=10, emoji="abc")                           # 일부만 잘못돼도 지갑을 건드리지 않는다.
        self.assertEqual(self.store.wallet(1, 7), 900)
        self.store.set_currency(1, emoji="<:gem:123456789012345678>")                 # 서버 이모지는 허용
        self.assertEqual(self.store.currency(1).emoji, "<:gem:123456789012345678>")


class ItemTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_create_and_read(self):
        item = self.store.create_item(1, "호박 랜턴", emoji="🎃", description="빛나요", buy_price=30, sell_price=10,
                                      buyable=True, sellable=True)
        self.assertEqual((item["name"], item["buy_price"], item["sell_price"], item["buyable"], item["sellable"]), ("호박 랜턴", 30, 10, 1, 1))
        self.assertEqual(self.store.item_by_name(1, "  호박 랜턴 ")["id"], item["id"])
        self.assertIsNone(self.store.item(2, item["id"]))                      # 다른 서버에서는 안 보인다.

    def test_names_are_unique_per_server_ignoring_case(self):
        self.store.create_item(1, "Candy Bag", buyable=True, buy_price=5)
        with self.assertRaisesRegex(StoreError, "같은 이름"):
            self.store.create_item(1, "candy bag")
        self.store.create_item(2, "candy bag")                                 # 다른 서버는 가능

    def test_validation(self):
        for kwargs, message in [
            (dict(name=""), "이름"), (dict(name=" 앞공백"), "이름"), (dict(name="가" * 31), "이름"),
            (dict(name="a", emoji="abc"), "이모지"), (dict(name="a", emoji=""), "이모지"), (dict(name="a", description="x" * 201), "설명"),
            (dict(name="a", buyable=True, buy_price=0), "구매가"), (dict(name="a", sellable=True, sell_price=0), "판매가"),
            (dict(name="a", buy_price=CANDY_MAX + 1), "가격"), (dict(name="a", buy_price=-1), "가격"),
            (dict(name="a", kind="role"), "역할"),
        ]:
            name = kwargs.pop("name")
            with self.assertRaisesRegex(StoreError, message, msg=str(kwargs)):
                self.store.create_item(1, name, **kwargs)
        self.assertEqual(self.store.items(1), [])

    def test_emoji_rules(self):
        for ok in ["🎃", "🍬", "👻", "🕸️", "<:pumpkin:123456789012345678>", "<a:spin:123456789012345678>"]:
            self.assertTrue(valid_emoji(ok), ok)
        for bad in ["", "a", "1", "ab", " ", "호", "🎃🎃🎃🎃🎃🎃🎃🎃🎃", "<:x:1>", "<:pumpkin:abc>"]:
            self.assertFalse(valid_emoji(bad), bad)

    def test_role_items_must_be_unique_by_role(self):
        self.store.create_item(1, "VIP", kind="role", role_id=55, buyable=True, buy_price=100)
        with self.assertRaisesRegex(StoreError, "이미 상점 물품으로 등록된 역할"):
            self.store.create_item(1, "VIP2", kind="role", role_id=55)

    def test_shop_holds_at_most_25_listed_items(self):
        for n in range(MAX_SHOP_ITEMS):
            self.store.create_item(1, f"물품{n}", buyable=True, buy_price=1)
        with self.assertRaisesRegex(StoreError, "최대 25개"):
            self.store.create_item(1, "하나 더", buyable=True, buy_price=1)
        self.store.create_item(1, "숨은 물품")                                  # 구매·판매가 모두 꺼진 물품은 상점에 안 올라가니 가능
        hidden = self.store.item_by_name(1, "숨은 물품")
        with self.assertRaisesRegex(StoreError, "최대 25개"):
            self.store.update_item(1, hidden["id"], buyable=True, buy_price=3)
        self.assertEqual(len(self.store.items(1, shop_only=True)), 25)

    def test_system_items(self):
        ids = self.store.ensure_system_items(1)
        self.assertEqual(set(ids), {"key", "trash"})
        self.assertEqual(self.store.ensure_system_items(1), ids)               # 여러 번 불러도 한 번만 만들어진다.
        key = self.store.item(1, ids["key"])
        self.assertEqual((key["name"], key["emoji"], key["buyable"], key["sellable"]), ("열쇠", "🗝️", 0, 0))
        self.assertEqual(self.store.items(1, shop_only=True), [])              # 기본은 상점에 안 나온다.
        renamed = self.store.update_item(1, ids["key"], name="황금 열쇠", emoji="🔑")       # 기본 물품도 이름·이모지를 바꿀 수 있다.
        self.assertEqual((renamed["name"], renamed["emoji"]), ("황금 열쇠", "🔑"))
        self.assertEqual(self.store.ensure_system_items(1), ids)                 # 바꿔도 다시 만들지 않는다.
        with self.assertRaisesRegex(StoreError, "쓰는 설정"):
            self.store.delete_item(1, ids["key"])                                # 던전 입장 물품이라 못 지운다.
        with self.assertRaisesRegex(StoreError, "패키지 결과표"):
            self.store.delete_item(1, ids["trash"])                              # 패키지 결과표가 쓰고 있다.
        self.store.update_item(1, ids["trash"], sellable=True, sell_price=2)   # 쓰레기를 팔 수 있게 하는 것은 가능
        self.assertEqual([r["name"] for r in self.store.items(1, shop_only=True)], ["쓰레기"])
        with self.assertRaisesRegex(StoreError, "같은 이름"):
            self.store.create_item(1, "황금 열쇠")

    def test_default_items_can_be_deleted_once_nothing_uses_them_and_are_not_recreated(self):
        ids = self.store.ensure_system_items(1)
        self.store.update_settings(1, entry_item=None, package_table=[e for e in self.store.package_table(1) if e["id"] not in ("key", "trash")])
        self.store.add_item(1, 7, ids["key"], 3)
        self.assertEqual(self.store.delete_item(1, ids["key"]), 3)               # 가방에서도 사라진다.
        self.store.delete_item(1, ids["trash"])
        self.assertEqual(self.store.ensure_system_items(1), {})                  # 한 번 만든 기본 물품은 지워도 되살아나지 않는다.
        self.assertEqual(self.store.items(1), [])
        self.assertEqual(self.store.item_users(1, 12345), [])

    def test_an_old_servers_existing_key_and_trash_are_reused_with_their_bags_intact(self):
        """업데이트 전에 이미 열쇠·쓰레기가 만들어져 있고 가방에 들어 있는 서버: 같은 물품을 입장 물품·결과표로 이어받는다."""
        with self.store.db:
            cursor = self.store.db.execute("INSERT INTO items(guild, name, emoji, description, kind, system) VALUES(1,'열쇠','🗝️','옛 설명','item','key')")
            self.store.db.execute("INSERT INTO items(guild, name, emoji, description, kind, system) VALUES(1,'쓰레기','🗑️','','item','trash')")
        key_id = cursor.lastrowid
        self.store.add_item(1, 7, key_id, 4)
        self.assertEqual(self.store.settings(1)["seeded"], False)
        self.assertEqual(self.store.ensure_system_items(1)["key"], key_id)
        cfg = self.store.settings(1)
        self.assertEqual((cfg["seeded"], cfg["entry_item"], cfg["entry_cost"]), (True, key_id, 1))
        self.assertEqual(next(e for e in cfg["package_table"] if e["id"] == "key")["item"], key_id)
        self.assertEqual(self.store.qty(1, 7, key_id), 4)
        self.assertEqual(len(self.store.items(1)), 2)                          # 새로 만들지 않았다.

    def test_update(self):
        item = self.store.create_item(1, "초", buy_price=5, buyable=True)
        updated = self.store.update_item(1, item["id"], name="긴 초", sell_price=2, sellable=True, emoji="🕯️")
        self.assertEqual((updated["name"], updated["sell_price"], updated["sellable"], updated["emoji"]), ("긴 초", 2, 1, "🕯️"))
        with self.assertRaisesRegex(StoreError, "판매가"):
            self.store.update_item(1, item["id"], sell_price=0)               # 판매가 켜져 있는데 가격이 0
        with self.assertRaisesRegex(StoreError, "그런 물품"):
            self.store.update_item(1, 9999, name="x")
        self.store.create_item(1, "다른 초")
        with self.assertRaisesRegex(StoreError, "같은 이름"):
            self.store.update_item(1, item["id"], name="다른 초")
        self.assertEqual(self.store.item(1, item["id"])["name"], "긴 초")

    def test_delete_removes_it_from_bags(self):
        item = self.store.create_item(1, "초", buy_price=5, buyable=True)
        self.store.add_item(1, 7, item["id"], 3)
        self.store.add_item(1, 8, item["id"], 4)
        self.assertEqual(self.store.delete_item(1, item["id"]), 7)
        self.assertEqual((self.store.qty(1, 7, item["id"]), self.store.inventory(1, 8)), (0, []))
        with self.assertRaises(StoreError):
            self.store.delete_item(1, item["id"])


class TradeTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.item = self.store.create_item(1, "초", buy_price=30, sell_price=10, buyable=True, sellable=True)["id"]
        self.store.add_candy(1, 7, 100)

    def test_buy(self):
        self.assertEqual(self.store.buy_item(1, 7, self.item, 2), 60)
        self.assertEqual((self.store.wallet(1, 7), self.store.qty(1, 7, self.item)), (40, 2))

    def test_buy_failures_change_nothing(self):
        with self.assertRaisesRegex(StoreError, "부족해요. 필요 🍬120"):
            self.store.buy_item(1, 7, self.item, 4)
        for qty in (0, -1, 1000, True, 1.5):
            with self.assertRaisesRegex(StoreError, "수량"):
                self.store.buy_item(1, 7, self.item, qty)
        with self.assertRaises(StoreError):
            self.store.buy_item(1, 7, 9999, 1)
        self.store.update_item(1, self.item, buyable=False)
        with self.assertRaisesRegex(StoreError, "살 수 없는"):
            self.store.buy_item(1, 7, self.item, 1)
        self.assertEqual((self.store.wallet(1, 7), self.store.qty(1, 7, self.item)), (100, 0))

    def test_sell(self):
        self.store.add_item(1, 7, self.item, 5)
        self.assertEqual(self.store.sell_item(1, 7, self.item, 3), 30)
        self.assertEqual((self.store.wallet(1, 7), self.store.qty(1, 7, self.item)), (130, 2))

    def test_sell_failures(self):
        with self.assertRaisesRegex(StoreError, "가진 수량이 모자라요"):
            self.store.sell_item(1, 7, self.item, 1)
        self.store.add_item(1, 7, self.item, 2)
        with self.assertRaisesRegex(StoreError, "가진 수량이 모자라요"):
            self.store.sell_item(1, 7, self.item, 3)
        self.store.update_item(1, self.item, sellable=False)
        with self.assertRaisesRegex(StoreError, "팔 수 없는"):
            self.store.sell_item(1, 7, self.item, 1)
        self.assertEqual(self.store.qty(1, 7, self.item), 2)

    def test_sale_that_would_pass_the_cap_is_refused_without_losing_the_item(self):
        self.store.add_item(1, 7, self.item, 5)
        self.store.add_candy(1, 7, CANDY_MAX)                                   # 가득 참
        with self.assertRaisesRegex(StoreError, "넘어서 팔 수 없어요"):
            self.store.sell_item(1, 7, self.item, 1)
        self.assertEqual((self.store.wallet(1, 7), self.store.qty(1, 7, self.item)), (CANDY_MAX, 5))
        self.store.add_candy(1, 7, -10)                                         # 정확히 상한이 되는 판매는 가능
        self.assertEqual(self.store.sell_item(1, 7, self.item, 1), 10)
        self.assertEqual(self.store.wallet(1, 7), CANDY_MAX)

    def test_role_items_are_not_traded_through_the_inventory(self):
        role = self.store.create_item(1, "VIP", kind="role", role_id=5, buy_price=10, buyable=True)["id"]
        with self.assertRaisesRegex(StoreError, "거래할 수 없는"):
            self.store.buy_item(1, 7, role, 1)

    def test_inventory_listing_puts_system_items_first(self):
        ids = self.store.ensure_system_items(1)
        self.store.add_item(1, 7, self.item, 2)
        self.store.add_item(1, 7, ids["trash"], 1)
        self.store.add_item(1, 7, ids["key"], 3)
        self.assertEqual([(r["name"], r["qty"]) for r in self.store.inventory(1, 7)], [("열쇠", 3), ("쓰레기", 1), ("초", 2)])
        self.assertEqual(self.store.add_item(1, 7, self.item, -10), -2)          # 없는 만큼은 빼지 않는다.
        self.assertEqual(self.store.qty(1, 7, self.item), 0)

    def test_spend_candy(self):
        self.store.spend_candy(1, 7, 40)
        self.assertEqual(self.store.wallet(1, 7), 60)
        with self.assertRaises(StoreError):
            self.store.spend_candy(1, 7, 61)


class ShopAndPackageTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_shops(self):
        self.store.set_shop(1, 100, 555)
        self.store.set_shop(1, 100, 556)                                        # 같은 채널에 다시 등록하면 새 메시지로 교체
        self.store.set_shop(1, 101, 600)
        self.assertEqual(sorted((r["channel"], r["message"]) for r in self.store.shops(1)), [(100, 556), (101, 600)])
        self.assertEqual(self.store.shops(2), [])
        self.assertTrue(self.store.remove_shop(100))
        self.assertFalse(self.store.remove_shop(100))

    def test_everyone_can_open_once_each(self):
        self.store.create_package(1, 100, 900, expires=1000.0, mode="all")
        self.assertEqual(self.store.open_package(900, 7, "key", 10.0), "ok")
        self.assertEqual(self.store.open_package(900, 8, "none", 11.0), "ok")       # 다른 사람도 뜯을 수 있다.
        self.assertEqual(self.store.open_package(900, 7, "candy", 12.0), "again")   # 같은 사람은 한 번만
        self.assertEqual([(r["user"], r["result"]) for r in self.store.package_openers(900)], [(7, "key"), (8, "none")])

    def test_first_mode_closes_after_one_opener(self):
        self.store.create_package(1, 100, 900, expires=1000.0, mode="first")
        self.assertEqual(self.store.open_package(900, 7, "key", 10.0), "ok")
        self.assertEqual(self.store.open_package(900, 8, "key", 10.1), "closed")
        self.assertEqual(self.store.open_package(900, 7, "key", 10.2), "closed")
        self.assertEqual(len(self.store.package_openers(900)), 1)

    def test_expired_and_missing_packages(self):
        self.store.create_package(1, 100, 900, expires=50.0, mode="all")
        self.assertEqual(self.store.open_package(900, 7, "key", 50.0), "gone")      # 끝나는 시각 이후
        self.assertEqual(self.store.open_package(12345, 7, "key", 1.0), "gone")
        self.assertEqual(self.store.package_openers(900), [])

    def test_one_active_package_per_channel_and_cleanup(self):
        self.store.create_package(1, 100, 900, expires=50.0, mode="all")
        self.assertEqual(self.store.active_package(100, 10.0)["message"], 900)
        self.assertIsNone(self.store.active_package(100, 50.0))                    # 시간이 지나면 새 패키지가 나올 수 있다.
        self.assertIsNone(self.store.active_package(101, 10.0))
        self.store.create_package(1, 100, 901, expires=500.0, mode="first")
        self.store.open_package(901, 7, "none", 10.0)
        self.assertIsNone(self.store.active_package(100, 60.0))                    # 선착순으로 닫힌 것도 활성이 아니다.
        self.assertEqual(sorted(r["message"] for r in self.store.finished_packages(60.0)), [900, 901])
        self.store.mark_summarized(901)                                            # 요약을 마친 선착순 패키지는 만료 때까지 남는다.
        self.assertEqual(self.store.package(901)["closed"], 2)
        self.assertEqual(self.store.open_package(901, 8, "none", 61.0), "closed")
        self.assertEqual([r["message"] for r in self.store.finished_packages(60.0)], [900])
        self.assertEqual(sorted(r["message"] for r in self.store.finished_packages(501.0)), [900, 901])
        self.store.delete_package(901)
        self.assertEqual([r["message"] for r in self.store.finished_packages(60.0)], [900])
        self.assertEqual(self.store.package_openers(901), [])


if __name__ == "__main__":
    unittest.main()
