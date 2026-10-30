"""규칙과 저장소 시험: 확률, 재화 상한, 교환, 물품·가방, 사고팔기, 패키지."""
import random
import unittest

from store import (CANDY_MAX, DEFAULT_SETTINGS, EXCHANGE_CANDY, EXCHANGE_EXP, MAX_SHOP_ITEMS, PACKAGE_TABLE, Store, StoreError,
                    roll_candy, roll_exp, roll_package_result, roll_package_spawn, valid_emoji, validate_settings)


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
        for value, expected in [(0.0, "none"), (0.5999, "none"), (0.60, "key"), (0.7999, "key"), (0.80, "candy"),
                                (0.8999, "candy"), (0.90, "trash"), (0.9999, "trash")]:
            self.assertEqual(roll_package_result(Scripted([value])), expected, value)
        self.assertEqual(sum(p for _, p in PACKAGE_TABLE), 100)

    def test_large_sample_matches_the_stated_rates(self):
        rng, n = random.Random(20241031), 400_000
        cfg = dict(DEFAULT_SETTINGS)
        gained = sum(1 for _ in range(n) if roll_candy(cfg, rng))
        self.assertAlmostEqual(gained / n, 0.50, delta=0.01)
        spawns = sum(1 for _ in range(1_000_000) if roll_package_spawn(cfg, rng))
        self.assertTrue(800 <= spawns <= 1200, spawns)                      # 기대 1000회(0.1%)
        counts = {name: 0 for name, _ in PACKAGE_TABLE}
        for _ in range(n):
            counts[roll_package_result(rng)] += 1
        for name, percent in PACKAGE_TABLE:
            self.assertAlmostEqual(counts[name] / n * 100, percent, delta=1.0, msg=name)

    def test_exp_roll(self):
        rng = random.Random(3)
        values = [roll_exp(DEFAULT_SETTINGS, rng) for _ in range(1000)]
        self.assertTrue(all(15 <= v <= 25 for v in values))


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
                    dict(package_seconds=5), dict(candy_cooldown=True), dict(reaction="yes"), dict(channels=["a"]), dict(exp_min=30)]:
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
        self.assertEqual(self.store.wallet(1, 7)[0], CANDY_MAX)
        self.assertEqual(self.store.add_candy(1, 7, 3), 0)
        self.assertEqual(self.store.add_candy(1, 7, -CANDY_MAX - 500), -CANDY_MAX)
        self.assertEqual(self.store.wallet(1, 7)[0], 0)

    def test_servers_and_users_are_separate(self):
        self.store.add_candy(1, 7, 10)
        self.assertEqual((self.store.wallet(2, 7), self.store.wallet(1, 8)), ((0, 0), (0, 0)))

    def test_exp(self):
        self.assertEqual(self.store.add_exp(1, 7, 20000), 20000)
        self.assertEqual(self.store.add_exp(1, 7, -999999), -20000)
        self.assertEqual(self.store.wallet(1, 7)[1], 0)

    def test_exchange_15000_exp_for_15_candy(self):
        self.store.add_exp(1, 7, 40000)
        self.assertEqual(self.store.exchange_exp(1, 7, 1), (EXCHANGE_EXP, EXCHANGE_CANDY))
        self.assertEqual(self.store.wallet(1, 7), (15, 25000))
        self.assertEqual(self.store.exchange_exp(1, 7, 1), (15000, 15))
        self.assertEqual(self.store.wallet(1, 7), (30, 10000))

    def test_exchange_failures_change_nothing(self):
        self.store.add_exp(1, 7, 14999)
        with self.assertRaisesRegex(StoreError, "EXP가 부족"):
            self.store.exchange_exp(1, 7, 1)
        for bad in (0, -1):
            with self.assertRaises(StoreError):
                self.store.exchange_exp(1, 7, bad)
        self.assertEqual(self.store.wallet(1, 7), (0, 14999))

    def test_exchange_respects_the_cap(self):
        self.store.add_exp(1, 7, 10 * EXCHANGE_EXP)
        self.store.add_candy(1, 7, CANDY_MAX - 20)                            # 여유 20 → 1번(15)은 되고 2번(30)은 안 된다.
        with self.assertRaisesRegex(StoreError, "1번까지만"):
            self.store.exchange_exp(1, 7, 2)
        self.assertEqual(self.store.exchange_exp(1, 7, 1), (15000, 15))
        with self.assertRaisesRegex(StoreError, "가득 차서"):
            self.store.exchange_exp(1, 7, 1)
        self.assertEqual(self.store.wallet(1, 7), (CANDY_MAX - 5, 9 * EXCHANGE_EXP))


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
        with self.assertRaisesRegex(StoreError, "기본 물품의 이름"):
            self.store.update_item(1, ids["key"], name="다른 이름")
        with self.assertRaisesRegex(StoreError, "기본 물품"):
            self.store.delete_item(1, ids["trash"])
        self.store.update_item(1, ids["trash"], sellable=True, sell_price=2)   # 쓰레기를 팔 수 있게 하는 것은 가능
        self.assertEqual([r["name"] for r in self.store.items(1, shop_only=True)], ["쓰레기"])
        with self.assertRaisesRegex(StoreError, "같은 이름"):
            self.store.create_item(1, "열쇠")

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
        self.assertEqual((self.store.wallet(1, 7)[0], self.store.qty(1, 7, self.item)), (40, 2))

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
        self.assertEqual((self.store.wallet(1, 7)[0], self.store.qty(1, 7, self.item)), (100, 0))

    def test_sell(self):
        self.store.add_item(1, 7, self.item, 5)
        self.assertEqual(self.store.sell_item(1, 7, self.item, 3), 30)
        self.assertEqual((self.store.wallet(1, 7)[0], self.store.qty(1, 7, self.item)), (130, 2))

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
        self.assertEqual((self.store.wallet(1, 7)[0], self.store.qty(1, 7, self.item)), (CANDY_MAX, 5))
        self.store.add_candy(1, 7, -10)                                         # 정확히 상한이 되는 판매는 가능
        self.assertEqual(self.store.sell_item(1, 7, self.item, 1), 10)
        self.assertEqual(self.store.wallet(1, 7)[0], CANDY_MAX)

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
        self.assertEqual(self.store.wallet(1, 7)[0], 60)
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
