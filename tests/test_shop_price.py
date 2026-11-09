"""상점 가격을 물건마다 따로 정하기: 장비·소모품의 상점가. 비우면 판매가 × 상점.가격배율로 자동 계산한다."""
import json
import random
import unittest

import balance
import content
import dungeon
import dungeon_service as service
from balance import Balance, check_entity, entity_dict, shop_price
from cogs.content_admin import ContentAdmin
from cogs.dungeon import shop_text
from dungeon import Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


def gear(**kw):
    data = dict(key="g", name="검", emoji="⚔️", slot="weapon", tier="희귀", weight=1, attack=10)
    data.update(kw)
    return data


def item(**kw):
    data = dict(key="p", name="약", emoji="💾", tier="일반", weight=1, heal=10)
    data.update(kw)
    return data


class EntityTests(unittest.TestCase):
    def test_default_is_automatic_and_old_saves_have_none(self):
        self.assertIsNone(check_entity("gear", gear())[0].shop_price)
        self.assertIsNone(check_entity("consumables", item())[0].shop_price)
        self.assertTrue(all(x.shop_price is None for x in balance.GEAR + balance.CONSUMABLES))
        old = entity_dict(check_entity("gear", gear())[0])
        old.pop("shop_price")
        self.assertIsNone(check_entity("gear", old)[0].shop_price)

    def test_a_price_is_accepted_for_both_and_round_trips(self):
        for section, data in (("gear", gear(shop_price=77)), ("consumables", item(shop_price=77)), ("gear", gear(slot="armor", reduction=5, shop_price=3))):
            entity, problem = check_entity(section, data)
            self.assertIsNone(problem)
            self.assertEqual(entity.shop_price, data["shop_price"])
            self.assertEqual(check_entity(section, json.loads(json.dumps(entity_dict(entity))))[0], entity)

    def test_bad_prices_are_refused_and_none_means_automatic(self):
        for bad in (0, -1, 1.5, True, "5", balance.SELL_MAX + 1):
            self.assertIsNotNone(check_entity("gear", gear(shop_price=bad))[1], bad)
            self.assertIsNotNone(check_entity("consumables", item(shop_price=bad))[1], bad)
        self.assertIsNone(check_entity("gear", gear(shop_price=None))[1])

    def test_materials_and_modify_items_can_have_one_too(self):
        self.assertEqual(check_entity("consumables", item(heal=0, material=True, shop_price=40))[0].shop_price, 40)
        self.assertEqual(check_entity("consumables", item(heal=0, modify="longsword", shop_price=40))[0].shop_price, 40)


class PriceTests(unittest.TestCase):
    def test_a_set_price_beats_the_markup_and_the_tier(self):
        rare = check_entity("gear", gear(shop_price=7))[0]
        self.assertEqual((shop_price(rare, 400), shop_price(rare, 1)), (7, 7))
        auto = check_entity("gear", gear())[0]
        self.assertEqual(shop_price(auto, 400), 60)                                      # 희귀 판매가 15 × 4

    def test_a_cheaper_or_dearer_one_than_the_formula_is_fine(self):
        legend = check_entity("consumables", item(tier="전설", shop_price=1))[0]
        self.assertEqual(shop_price(legend, 400), 1)
        self.assertEqual(shop_price(check_entity("consumables", item(shop_price=9999))[0], 400), 9999)

    def test_the_shop_uses_each_items_own_price_and_fixes_it_when_it_opens(self):
        raw = {"gear": [entity_dict(check_entity("gear", gear(key="a", name="가", shop_price=11))[0]),
                        entity_dict(check_entity("gear", gear(key="b", name="나"))[0])],
               "consumables": [entity_dict(check_entity("consumables", item(key="c", name="다", shop_price=222))[0])]}
        bal = Balance.build({}, raw)
        run = dungeon.new_run(bal=bal)
        run.floor, run.room, run.kind, run.phase = 5, dungeon.ROOMS, "boss", "cleared"
        dungeon.open_shop(run, random.Random(3), bal)
        prices = {o["key"]: o["price"] for o in run.shop}
        self.assertEqual(prices, {"a": 11, "b": 60, "c": 222})
        changed = Balance.build({}, {**raw, "gear": [entity_dict(check_entity("gear", gear(key="a", name="가", shop_price=999))[0]), raw["gear"][1]]})
        dungeon.buy_offer(run, next(i for i, o in enumerate(run.shop) if o["key"] == "a"), changed)
        self.assertEqual(next(o["price"] for o in run.shop if o["key"] == "a"), 11)       # 열릴 때 정한 값 그대로


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def find(self, section, name):
        return next(x for x in getattr(self.store.balance(G), section) if x.name == name)

    async def test_set_change_and_clear_a_gears_price(self):
        text = await self.run_cmd(ContentAdmin.gear_add, "활", "🏹", Choice("weapon"), Choice("희귀"), 3, attack=10, shop_price=45)
        self.assertIn("상점가 45", text)
        self.assertEqual(self.find("gear", "활").shop_price, 45)
        await self.run_cmd(ContentAdmin.gear_edit, "활", shop_price=80)
        self.assertEqual(self.find("gear", "활").shop_price, 80)
        await self.run_cmd(ContentAdmin.gear_edit, "활", attack=20)                       # 다른 걸 고쳐도 가격은 그대로
        self.assertEqual(self.find("gear", "활").shop_price, 80)
        text = await self.run_cmd(ContentAdmin.gear_edit, "활", shop_price=-1)
        self.assertIsNone(self.find("gear", "활").shop_price)
        self.assertNotIn("상점가", text)

    async def test_set_change_and_clear_a_consumables_price(self):
        text = await self.run_cmd(ContentAdmin.potion_add, "붕대", "🩹", Choice("일반"), 3, heal=50, shop_price=12)
        self.assertIn("상점가 12", text)
        self.assertEqual(self.find("consumables", "붕대").shop_price, 12)
        await self.run_cmd(ContentAdmin.potion_edit, "붕대", shop_price=30)
        await self.run_cmd(ContentAdmin.potion_edit, "붕대", heal=60)
        item_now = self.find("consumables", "붕대")
        self.assertEqual((item_now.shop_price, item_now.heal), (30, 60))
        await self.run_cmd(ContentAdmin.potion_edit, "붕대", shop_price=-1)
        self.assertIsNone(self.find("consumables", "붕대").shop_price)

    async def test_default_items_can_get_a_price_too_and_zero_is_refused(self):
        await self.run_cmd(ContentAdmin.potion_edit, "소형 배터리", shop_price=3)
        await self.run_cmd(ContentAdmin.gear_edit, "레이저 커터", shop_price=500)
        self.assertEqual((self.find("consumables", "소형 배터리").shop_price, self.find("gear", "레이저 커터").shop_price), (3, 500))
        for command, name in ((ContentAdmin.potion_edit, "소형 배터리"), (ContentAdmin.gear_edit, "레이저 커터")):
            text = await self.run_cmd(command, name, shop_price=0)
            self.assertIn("shop_price", text)
        self.assertEqual(self.find("consumables", "소형 배터리").shop_price, 3)

    async def test_the_list_shows_the_price_only_when_set_and_admin_only(self):
        plain = content.describe("consumables", next(d for d in content.current(self.store, G, "consumables") if d["name"] == "소형 배터리"))
        self.assertNotIn("상점가", plain)
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.gear_edit, "레이저 커터", shop_price=5, admin=False))
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.potion_edit, "소형 배터리", shop_price=5, admin=False))
        self.assertIsNone(self.find("gear", "레이저 커터").shop_price)

    def test_option_names_and_counts(self):
        for command in (ContentAdmin.gear_add, ContentAdmin.gear_edit, ContentAdmin.potion_add, ContentAdmin.potion_edit):
            self.assertLessEqual(len(command.parameters), 25)
            self.assertIn("상점가", {p.display_name for p in command.parameters})


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        content.edit(self.store, G, ("consumables",), "소형 배터리", {"shop_price": 3})
        content.edit(self.store, G, ("consumables",), "납땜 키트", {"shop_price": 4000})

    def tearDown(self):
        self.store.close()

    async def test_the_screen_and_the_wallet_use_the_custom_price(self):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=5, room=dungeon.ROOMS, phase="cleared", kind="boss")
        self.store.save_dungeon(G, U, json.dumps(data))
        self.store.add_candy(G, U, 100)
        bal = self.store.balance(G)
        # 소모품(0.9)에서 사탕(첫째)
        opened = await service.act(self.store, self.locks, Scripted(randoms=[0.99, 0.9, 0.0, 0.9, 0.0, 0.9, 0.0]), G, U, out.run.rev, "advance")
        self.assertEqual(opened.run.phase, "shop", opened.text)
        prices = {o["key"]: o["price"] for o in opened.run.shop}
        self.assertEqual(prices["candy"], 3)
        self.assertIn("3", shop_text(opened.run, bal))
        number = next(i for i, o in enumerate(opened.run.shop) if o["key"] == "candy")
        bought = await service.act(self.store, self.locks, Scripted(), G, U, opened.run.rev, "buy", str(number))
        self.assertTrue(bought.ok, bought.text)
        self.assertEqual(self.store.wallet(G, U), 97)
        pricey = next((i for i, o in enumerate(bought.run.shop) if o["key"] == "cookie"), None)
        if pricey is not None:
            refused = await service.act(self.store, self.locks, Scripted(), G, U, bought.run.rev, "buy", str(pricey))
            self.assertFalse(refused.ok)
            self.assertEqual(self.store.wallet(G, U), 97)
