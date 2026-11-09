"""5층마다 열리는 상점: 보스를 이기고 올라가기 직전에 장비·소모품 3개를 재화로 산다. 규칙·가격·저장소·서비스·화면."""
import json
import random
import unittest

import balance
import dungeon
import dungeon_auto
import dungeon_service as service
from balance import Balance, check_entity, check_value, entity_dict, shop_price
from cogs.dungeon import DungeonView, respond, run_embed, shop_text
from dungeon import DungeonError, Run
from services import Locks
from store import Store, StoreError
from tests.helpers import Scripted, World

G, U = 100, 7
NO_SECRET = 0.99


def cleared_boss(bal, floor=5, **kw):
    run = dungeon.new_run(bal=bal)
    run.floor, run.room, run.kind, run.phase = floor, dungeon.ROOMS, "boss", "cleared"
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class PriceTests(unittest.TestCase):
    def test_price_is_the_sell_price_times_the_markup(self):
        common = balance.CONSUMABLES[0]                                                  # 일반 사탕: 판매가 5
        self.assertEqual((common.sell_price, shop_price(common, 400)), (5, 20))
        self.assertEqual(shop_price(common, 100), 5)
        self.assertEqual(shop_price(common, 150), 8)                                     # 7.5 → 반올림
        self.assertEqual(shop_price(common, 1), 1)                                       # 최소 1

    def test_an_unsellable_item_is_priced_by_its_tier(self):
        item = check_entity("consumables", dict(key="x", name="안 팔림", emoji="💾", tier="전설", weight=1, heal=5, sell_price=0))[0]
        self.assertEqual((item.sell_price, shop_price(item, 400)), (0, 200))
        gear = check_entity("gear", dict(key="g", name="안 팔림", emoji="⚔️", slot="weapon", tier="희귀", weight=1, attack=5, sell_price=0))[0]
        self.assertEqual(shop_price(gear, 400), 60)

    def test_the_settings_exist_with_sensible_ranges(self):
        bal = Balance.build({})
        self.assertEqual((bal.shop_interval, bal.shop_markup), (5, 400))
        self.assertEqual(check_value("상점.간격", 0), (0, None))
        self.assertEqual(check_value("상점.간격", 3), (3, None))
        self.assertIsNotNone(check_value("상점.간격", 101)[1])
        self.assertIsNotNone(check_value("상점.간격", 2.5)[1])
        self.assertEqual(check_value("상점.가격배율", 250), (250, None))
        self.assertIsNotNone(check_value("상점.가격배율", 0)[1])
        self.assertEqual(Balance.build({"상점.간격": 3, "상점.가격배율": 100}).shop_markup, 100)


class WhenTests(unittest.TestCase):
    def due(self, floor, bal=None, **kw):
        bal = bal or Balance.build({})
        run = cleared_boss(bal, floor, **kw)
        return dungeon.shop_due(run, bal)

    def test_every_fifth_floor_but_not_others(self):
        self.assertEqual([f for f in range(1, 10) if self.due(f)], [5])
        self.assertEqual([f for f in range(1, 21) if self.due(f, infinite=True)], [5, 10, 15, 20])

    def test_the_interval_is_adjustable_and_zero_turns_the_shop_off(self):
        three = Balance.build({"상점.간격": 3})
        self.assertEqual([f for f in range(1, 10) if self.due(f, three)], [3, 6, 9])
        off = Balance.build({"상점.간격": 0})
        self.assertFalse(any(self.due(f, off, infinite=True) for f in range(1, 30)))

    def test_the_last_floor_has_a_shop_only_when_the_run_can_go_on(self):
        self.assertTrue(self.due(10))                                                    # 무한모드를 고를 수 있다
        self.assertFalse(self.due(10, Balance.build({"일반.무한모드": 0})))              # 바로 클리어: 사 봐야 쓸 곳이 없다
        self.assertTrue(self.due(10, Balance.build({"일반.무한모드": 0}), infinite=True))
        self.assertTrue(self.due(5, Balance.build({"일반.무한모드": 0})))

    def test_it_opens_once_per_floor(self):
        bal = Balance.build({})
        self.assertFalse(self.due(5, bal, shop_floor=5))
        self.assertTrue(self.due(5, bal, shop_floor=0))


class OpenTests(unittest.TestCase):
    def test_a_shop_opens_after_the_boss_with_three_different_offers(self):
        bal = Balance.build({})
        for seed in range(30):
            run = cleared_boss(bal)
            events = dungeon.advance(run, random.Random(seed), bal)
            if run.kind == "secret":                                                    # 10% 확률로 시크릿 방이 먼저 열린다
                continue
            self.assertEqual((run.phase, run.kind, run.floor, run.enemy, run.shop_floor), ("shop", "shop", 5, None, 5))
            self.assertEqual(len(run.shop), 3)
            self.assertEqual(len({(o["kind"], o["key"]) for o in run.shop}), 3)
            self.assertTrue(all(o["price"] >= 1 and not o["sold"] for o in run.shop))
            self.assertIn("5층 상점", events[0])
            self.assertFalse(run.can_advance)

    def test_kinds_are_half_and_half_and_picks_follow_the_drop_weights(self):
        bal = Balance.build({})
        gear = item = 0
        count = {}
        for seed in range(600):
            run = cleared_boss(bal)
            dungeon.open_shop(run, random.Random(seed), bal)
            for offer in run.shop:
                gear += offer["kind"] == "gear"
                item += offer["kind"] == "item"
                count[offer["key"]] = count.get(offer["key"], 0) + 1
        self.assertAlmostEqual(gear / (gear + item), 0.5, delta=0.06)
        self.assertGreater(count["club"], count["pumpkin_blade"] * 3)                    # 등장 25 대 3: 흔한 것이 훨씬 자주 나온다
        self.assertGreater(count["candy"], count["golden_soup"] * 3)                     # 38 대 3
        self.assertTrue(count.get("gun") and count.get("golden_soup"))                   # 그래도 전설도 가끔 팔린다

    def test_fractional_weights_work_and_a_tiny_one_is_almost_never_sold(self):
        raw = {"gear": [entity_dict(check_entity("gear", dict(key=k, name=k, emoji="⚔️", slot="weapon", tier="일반", weight=w, attack=5))[0])
                        for k, w in (("common", 1), ("rare", 0.0001))]}
        bal = Balance.build({}, raw)
        rare = 0
        for seed in range(300):
            run = cleared_boss(bal)
            dungeon.open_shop(run, Scripted(randoms=[0.0, random.Random(seed).random(), 0.0, 0.0, 0.0, 0.0]), bal)
            rare += run.shop[0]["key"] == "rare"
        self.assertLessEqual(rare, 1)
        run = cleared_boss(bal)
        dungeon.open_shop(run, Scripted(randoms=[0.0, 0.99995, 0.0, 0.0, 0.0, 0.0]), bal)           # 0.9999 이후 구간이면 희귀가 나온다
        self.assertEqual(run.shop[0]["key"], "rare")

    def test_scripted_picks(self):
        bal = Balance.build({})
        run = cleared_boss(bal)
        # 장비(0.0 < 0.5) 중 첫째, 소모품(0.9) 중 마지막, 장비 중 둘째(남은 목록의 0.0)
        dungeon.open_shop(run, Scripted(randoms=[0.0, 0.0, 0.9, 0.999, 0.0, 0.0]), bal)
        self.assertEqual([(o["kind"], o["key"]) for o in run.shop], [("gear", bal.gear[0].key), ("item", bal.consumables[-1].key), ("gear", bal.gear[1].key)])

    def test_weight_zero_and_other_floors_are_never_sold(self):
        raw = {"gear": [entity_dict(check_entity("gear", dict(key=k, name=k, emoji="⚔️", slot="weapon", tier="일반", weight=w, attack=5, floors=f))[0])
                        for k, w, f in (("a", 1, [5]), ("b", 0, []), ("c", 1, [6]), ("d", 1, []))]}
        bal = Balance.build({}, raw)
        for seed in range(50):
            run = cleared_boss(bal)
            dungeon.open_shop(run, random.Random(seed), bal)
            self.assertTrue({o["key"] for o in run.shop if o["kind"] == "gear"} <= {"a", "d"})

    def test_fewer_things_than_slots_gives_a_smaller_shop_and_nothing_gives_none(self):
        raw = {"gear": [entity_dict(check_entity("gear", dict(key="a", name="a", emoji="⚔️", slot="weapon", tier="일반", weight=1, attack=5))[0])],
               "consumables": [entity_dict(check_entity("consumables", dict(key="p", name="p", emoji="💾", tier="일반", weight=1, heal=5))[0])]}
        run = cleared_boss(Balance.build({}, raw))
        dungeon.open_shop(run, random.Random(1), Balance.build({}, raw))
        self.assertEqual(sorted(o["key"] for o in run.shop), ["a", "p"])
        gone = Balance.build({}, {"gear": [entity_dict(check_entity("gear", dict(key="a", name="a", emoji="⚔️", slot="weapon", tier="일반", weight=0, attack=5))[0])],
                                   "consumables": [entity_dict(check_entity("consumables", dict(key="p", name="p", emoji="💾", tier="일반", weight=0, heal=5))[0])]})
        run = cleared_boss(gone)
        self.assertEqual(dungeon.open_shop(run, random.Random(1), gone), [])
        self.assertEqual((run.phase, run.shop), ("cleared", []))

    def test_an_empty_shop_does_not_block_the_climb_nor_roll_the_secret_room_twice(self):
        gone = Balance.build({}, {"gear": [entity_dict(check_entity("gear", dict(key="a", name="a", emoji="⚔️", slot="weapon", tier="일반", weight=0, attack=5))[0])],
                                  "consumables": [entity_dict(check_entity("consumables", dict(key="p", name="p", emoji="💾", tier="일반", weight=0, heal=5))[0])]})
        run = cleared_boss(gone)
        dungeon.advance(run, Scripted(randoms=[NO_SECRET]), gone)                       # 난수는 시크릿 추첨 하나뿐
        self.assertEqual((run.floor, run.room), (6, 1))
        self.assertNotEqual(run.phase, "shop")

    def test_prices_use_the_current_markup_and_stay_fixed_after_opening(self):
        bal = Balance.build({"상점.가격배율": 100})
        run = cleared_boss(bal)
        dungeon.open_shop(run, random.Random(2), bal)
        prices = [o["price"] for o in run.shop]
        pricey = Balance.build({"상점.가격배율": 1000})
        dungeon.buy_offer(run, 0, pricey)
        self.assertEqual(run.shop[0]["price"], prices[0])


class FlowTests(unittest.TestCase):
    def test_leaving_the_shop_climbs_and_it_does_not_open_again(self):
        bal = Balance.build({})
        run = cleared_boss(bal)
        dungeon.advance(run, Scripted(randoms=[NO_SECRET] + [0.0] * 6), bal)
        self.assertEqual(run.phase, "shop")
        events = dungeon.advance(run, Scripted(randoms=[0.99]), bal)                    # 두 번째 advance: 위 층으로
        self.assertEqual((run.floor, run.room, run.shop_floor), (6, 1, 5))
        self.assertTrue(any("6층" in e for e in events))
        self.assertNotEqual(run.phase, "shop")

    def test_the_secret_boss_comes_first_and_the_shop_after_it(self):
        bal = Balance.build({"일반.시크릿확률": 100})
        run = cleared_boss(bal)
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual((run.kind, run.room), ("secret", dungeon.ROOMS + 1))
        run.phase, run.enemy = "cleared", None
        dungeon.advance(run, Scripted(randoms=[0.0] * 6), bal)
        self.assertEqual((run.phase, run.kind), ("shop", "shop"))
        dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.floor, 6)

    def test_on_the_top_floor_the_shop_comes_before_the_summit_question(self):
        bal = Balance.build({})
        run = cleared_boss(bal, floor=10)
        dungeon.advance(run, Scripted(randoms=[NO_SECRET] + [0.0] * 6), bal)
        self.assertEqual(run.phase, "shop")
        dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.phase, "summit")
        off = Balance.build({"상점.간격": 0})
        run = cleared_boss(off, floor=10)
        dungeon.advance(run, Scripted(randoms=[NO_SECRET]), off)
        self.assertEqual(run.phase, "summit")

    def test_infinite_floors_have_shops_too(self):
        bal = Balance.build({})
        run = cleared_boss(bal, floor=15, infinite=True)
        dungeon.advance(run, Scripted(randoms=[NO_SECRET] + [0.0] * 6), bal)
        self.assertEqual((run.phase, run.floor), ("shop", 15))

    def test_old_saves_have_no_shop_and_it_survives_saving(self):
        data = json.loads(dungeon.new_run().to_json())
        data.pop("shop"), data.pop("shop_floor")
        old = Run.from_json(json.dumps(data))
        self.assertEqual((old.shop, old.shop_floor), ([], 0))
        run = cleared_boss(Balance.build({}))
        dungeon.open_shop(run, random.Random(3), Balance.build({}))
        self.assertEqual(Run.from_json(run.to_json()).shop, run.shop)


class BuyTests(unittest.TestCase):
    def shop(self, **kw):
        bal = Balance.build({})
        run = cleared_boss(bal, **kw)
        dungeon.open_shop(run, Scripted(randoms=[0.0, 0.0, 0.9, 0.0, 0.0, 0.0]), bal)         # 장비, 소모품, 장비
        return run, bal

    def test_buying_an_item_puts_it_in_the_bag_and_returns_the_price(self):
        run, bal = self.shop()
        offer = run.shop[1]
        price = dungeon.buy_offer(run, 1, bal)
        self.assertEqual(price, offer["price"])
        self.assertTrue(offer["sold"])
        self.assertEqual(run.items, {offer["key"]: 1})
        self.assertIn(f"item_gain:{offer['key']}", run.last)
        self.assertTrue(any("샀어요" in e for e in run.events))
        self.assertEqual(run.phase, "shop")                                              # 더 살 수 있다

    def test_buying_gear_makes_it_undecided_like_a_chest_and_blocks_a_second_one(self):
        run, bal = self.shop()
        dungeon.buy_offer(run, 0, bal)
        self.assertEqual(run.pending_gear, run.shop[0]["key"])
        with self.assertRaisesRegex(DungeonError, "먼저 .*장착"):
            dungeon.buy_offer(run, 2, bal)
        self.assertFalse(run.shop[2]["sold"])
        dungeon.equip_pending(run, bal)
        dungeon.buy_offer(run, 2, bal)
        self.assertEqual(run.pending_gear, run.shop[2]["key"])

    def test_bad_requests_are_explained_and_change_nothing(self):
        run, bal = self.shop()
        for arg, text in ((9, "알 수 없는 상품"), (-1, "알 수 없는 상품"), ("x", "알 수 없는 상품"), (None, "알 수 없는 상품")):
            with self.assertRaisesRegex(DungeonError, text):
                dungeon.buy_offer(run, arg, bal)
        dungeon.buy_offer(run, 1, bal)
        with self.assertRaisesRegex(DungeonError, "이미 산"):
            dungeon.buy_offer(run, 1, bal)
        self.assertEqual(sum(run.items.values()), 1)
        outside = dungeon.new_run(bal=bal)
        with self.assertRaisesRegex(DungeonError, "지금은 상점이 아니에요"):
            dungeon.buy_offer(outside, 0, bal)

    def test_a_product_deleted_in_the_meantime_cannot_be_bought(self):
        run, bal = self.shop()
        run.shop[1]["key"] = "gone"
        with self.assertRaisesRegex(DungeonError, "사라졌어요"):
            dungeon.buy_offer(run, 1, bal)
        self.assertFalse(run.shop[1]["sold"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def start(self, wallet):
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        self.store.enter_dungeon(G, U, '{"a": 1}')
        self.store.add_candy(G, U, wallet)

    def test_spending_takes_the_candy_and_saves_the_run_together(self):
        self.start(100)
        self.assertEqual(self.store.spend_in_dungeon(G, U, '{"a": 2}', 60), 40)
        self.assertEqual((self.store.wallet(G, U), self.store.dungeon_run(G, U)), (40, '{"a": 2}'))

    def test_not_enough_candy_changes_nothing(self):
        self.start(50)
        with self.assertRaisesRegex(StoreError, "모자라요"):
            self.store.spend_in_dungeon(G, U, '{"a": 2}', 51)
        self.assertEqual((self.store.wallet(G, U), self.store.dungeon_run(G, U)), (50, '{"a": 1}'))
        self.assertEqual(self.store.spend_in_dungeon(G, U, '{"a": 3}', 50), 0)             # 딱 맞으면 된다

    def test_no_dungeon_means_no_spending(self):
        self.store.add_candy(G, U, 100)
        with self.assertRaisesRegex(StoreError, "진행 중인 던전이 없어요"):
            self.store.spend_in_dungeon(G, U, '{"a": 2}', 10)
        self.assertEqual(self.store.wallet(G, U), 100)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    def tearDown(self):
        self.store.close()

    async def at_shop(self, wallet=1000):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=5, room=dungeon.ROOMS, phase="cleared", kind="boss")
        self.store.save_dungeon(G, U, json.dumps(data))
        self.store.add_candy(G, U, wallet)
        opened = await service.act(self.store, self.locks, Scripted(randoms=[NO_SECRET, 0.0, 0.0, 0.9, 0.0, 0.0, 0.0]), G, U, out.run.rev, "advance")
        self.assertEqual(opened.run.phase, "shop", opened.text)
        return opened.run

    async def buy(self, run, number, **kw):
        return await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "buy", str(number), **kw)

    async def test_buying_pays_once_and_saves_the_run(self):
        run = await self.at_shop(1000)
        price = run.shop[1]["price"]
        out = await self.buy(run, 1)
        self.assertTrue(out.ok, out.text)
        self.assertEqual(self.store.wallet(G, U), 1000 - price)
        saved = Run.from_json(self.store.dungeon_run(G, U))
        self.assertTrue(saved.shop[1]["sold"])
        self.assertEqual(saved.rev, run.rev + 1)
        again = await self.buy(saved, 1)
        self.assertFalse(again.ok)
        self.assertEqual(self.store.wallet(G, U), 1000 - price)

    async def test_too_little_candy_is_refused_without_changes(self):
        run = await self.at_shop(5)
        out = await self.buy(run, 1)
        self.assertFalse(out.ok)
        self.assertIn("모자라요", out.text)
        self.assertEqual(self.store.wallet(G, U), 5)
        saved = Run.from_json(self.store.dungeon_run(G, U))
        self.assertEqual((saved.rev, saved.items, [o["sold"] for o in saved.shop]), (run.rev, {}, [False, False, False]))

    async def test_a_stale_screen_and_the_computer_cannot_buy(self):
        run = await self.at_shop(1000)
        stale = await service.act(self.store, self.locks, Scripted(), G, U, run.rev - 1, "buy", "0")
        self.assertTrue(stale.stale)
        self.assertEqual(self.store.wallet(G, U), 1000)

    async def test_buy_outside_the_shop_is_refused(self):
        out = await service.enter(self.store, self.locks, G, U)
        self.store.add_candy(G, U, 100)
        bought = await service.act(self.store, self.locks, Scripted(), G, U, out.run.rev, "buy", "0")
        self.assertFalse(bought.ok)
        self.assertEqual(self.store.wallet(G, U), 100)

    async def test_leaving_the_shop_goes_up_and_gear_must_be_decided_first(self):
        run = await self.at_shop(1000)
        bought = await self.buy(run, 0)                                                 # 장비
        self.assertTrue(bought.ok and bought.run.pending_gear)
        equipped = await service.act(self.store, self.locks, Scripted(), G, U, bought.run.rev, "equip")
        self.assertTrue(equipped.ok, equipped.text)
        self.assertEqual(equipped.run.phase, "shop")
        up = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, equipped.run.rev, "advance")
        self.assertTrue(up.ok, up.text)
        self.assertEqual((up.run.floor, up.run.room), (6, 1))

    async def test_selling_inside_the_shop_gives_candy_to_spend(self):
        run = await self.at_shop(0)
        self.assertFalse((await self.buy(run, 1)).ok)
        run = Run.from_json(self.store.dungeon_run(G, U))
        data = json.loads(run.to_json())
        data.update(items={"candy": 5})
        self.store.save_dungeon(G, U, json.dumps(data))
        run = Run.from_json(self.store.dungeon_run(G, U))
        sold = await service.sell(self.store, self.locks, G, U, run.rev, ["item:candy"], "all")
        self.assertTrue(sold.ok, sold.text)
        self.assertEqual(self.store.wallet(G, U), 25)

    async def test_the_computer_just_walks_out_of_the_shop(self):
        run = await self.at_shop(1000)
        self.assertEqual(dungeon_auto.choose(run, self.store.balance(G)), ("advance", None))


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.bal = self.store.balance(G)

    async def asyncTearDown(self):
        await self.w.close()

    def shop(self, **kw):
        run = cleared_boss(self.bal, **kw)
        dungeon.open_shop(run, Scripted(randoms=[0.0, 0.0, 0.9, 0.0, 0.0, 0.0]), self.bal)
        return run

    def labels(self, view):
        return [getattr(c, "label", None) for c in view.children]

    def test_the_shop_screen_lists_products_with_prices(self):
        run = self.shop()
        embed = run_embed(run, None, self.bal)
        self.assertEqual(embed.title, "🛒 5층 상점")
        text = shop_text(run, self.bal)
        self.assertEqual(len(text.splitlines()), 3)
        self.assertIn(f"{run.shop[0]['price']}", text)
        self.assertTrue(any(f.name.startswith("🛒") for f in embed.fields))

    def test_buttons_are_one_per_unsold_product_and_a_way_out(self):
        run = self.shop()
        view = DungeonView(self.w.bot, U, run, self.bal)
        labels = self.labels(view)
        self.assertEqual(sum(1 for l in labels if l and l[-1].isdigit()), 3)
        self.assertIn("상점 나가기", labels)
        self.assertNotIn("앞으로", labels)
        run.shop[1]["sold"] = True
        after = self.labels(DungeonView(self.w.bot, U, run, self.bal))
        self.assertEqual(len(after), len(labels) - 1)
        self.assertIn("~~", shop_text(run, self.bal))

    def test_pending_gear_hides_the_shop_buttons_until_decided(self):
        run = self.shop(pending_gear="club")
        labels = self.labels(DungeonView(self.w.bot, U, run, self.bal))
        self.assertIn("장착", labels)
        self.assertNotIn("상점 나가기", labels)

    def test_screens_stay_within_discords_component_rules(self):
        for run in (self.shop(), self.shop(weapon="club", spare_weapon="dagger", items={"candy": 2, "cookie": 1}, armor="cloth_armor"),
                    self.shop(items={k: 1 for k in list(self.bal.item_by_key)[:6]})):
            DungeonView(self.w.bot, U, run, self.bal).to_components()

    async def test_pressing_a_buy_button_buys(self):
        self.store.ensure_defaults(G)
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        out = await service.enter(self.store, self.w.bot.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=5, room=dungeon.ROOMS, phase="cleared", kind="boss")
        self.store.save_dungeon(G, U, json.dumps(data))
        self.store.add_candy(G, U, 500)
        opened = await service.act(self.store, self.w.bot.locks, Scripted(randoms=[NO_SECRET, 0.0, 0.0, 0.9, 0.0, 0.0, 0.0]), G, U, out.run.rev, "advance")
        view = DungeonView(self.w.bot, U, opened.run, self.bal)
        thing = self.bal.item_by_key[opened.run.shop[1]["key"]]
        button = next(c for c in view.children if getattr(c, "label", "") == f"{thing.name} {opened.run.shop[1]['price']:,}")
        press = self.w.interaction(uid=U)
        await button.callback(press)
        self.assertEqual(self.store.wallet(G, U), 500 - opened.run.shop[1]["price"])
        shown = press.response.edit_message.await_args.kwargs
        self.assertIn("샀어요", shown["embed"].description)
        self.assertIsInstance(shown["view"], DungeonView)


class AutoRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_whole_automatic_run_passes_through_both_shops_and_finishes(self):
        store, locks = Store(":memory:"), Locks()
        try:
            store.update_settings(G, dungeon={"일반.맨손공격력": 900, "일반.최대체력": 20000})
            store.add_item(G, U, store.ensure_system_items(G)["key"], 1)
            out = await service.enter(store, locks, G, U)
            data = json.loads(out.run.to_json())
            data["auto"] = True
            store.save_dungeon(G, U, json.dumps(data))
            rng, shops, ended = random.Random(11), [], None
            for _ in range(3000):
                run = await service.peek(store, G, U)
                if run is None:
                    break
                if run.phase == "shop":
                    shops.append(run.floor)
                action, arg = dungeon_auto.choose(run, store.balance(G))
                step = await service.act(store, locks, rng, G, U, run.rev, action, arg, auto=True)
                self.assertTrue(step.ok, step.text)
                ended = step.ended or ended
            self.assertEqual(ended, "clear")
            self.assertEqual(shops, [5, 10])                                              # 5층과 10층의 보스 뒤에 한 번씩
        finally:
            store.close()


class FullRunTests(unittest.TestCase):
    def test_the_simulator_walks_through_shops_and_gives_the_same_result(self):
        import balance_sim
        with_shop = balance_sim.simulate(Balance.build({}), 100, seed=9)
        without = balance_sim.simulate(Balance.build({"상점.간격": 0}), 100, seed=9)
        self.assertIn("attack", with_shop)
        self.assertEqual(with_shop["attack"]["clear"], without["attack"]["clear"])        # 시뮬레이터는 상점에서 사지 않는다
