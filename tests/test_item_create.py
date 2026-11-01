"""/물품추가: 상점과 상관없이 물품만 만든다. 만든 물품은 /상점진열·패키지·연금술·물품지급으로 이어 쓴다."""
import unittest
from unittest import mock
from unittest.mock import AsyncMock

import cogs.alchemy as alchemy_module
import cogs.shop as shop_module
from cogs.admin import Admin
from cogs.alchemy import Alchemy
from cogs.shop import Shop
from store import MAX_SHOP_ITEMS
from tests.helpers import World, sent_embed

G = 100


class CreateItemTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Shop(self.w.bot)
        self.w.channel(400)
        for module in (shop_module, alchemy_module):
            patcher = mock.patch.object(module, "refresh_panels", new=AsyncMock(return_value=1))
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.w.close()

    async def create(self, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await Shop.create_plain_item.callback(self.cog, i, *args, **kwargs)
        return i

    def text(self, i):
        return sent_embed(i)[0].description or ""

    def row(self, name):
        return self.store.item_by_name(G, name)

    async def test_it_makes_an_item_that_is_not_in_the_shop_and_not_in_anyones_bag(self):
        i = await self.create("마나 가루")
        item = self.row("마나 가루")
        self.assertEqual((item["emoji"], item["description"], item["buyable"], item["sellable"], item["buy_price"], item["sell_price"], item["kind"]),
                         ("🎃", "", 0, 0, 0, 0, "item"))
        self.assertNotIn("마나 가루", [r["name"] for r in self.store.items(G, shop_only=True)])
        self.assertEqual(self.store.holdings(G, item["id"]), 0)
        self.assertIn("🎃 **마나 가루**을(를) 만들었어요", self.text(i))
        self.assertIn("상점에는 올라가지 않았어요", self.text(i))
        self.assertFalse(i.response.send_message.await_args.kwargs["ephemeral"])

    async def test_emoji_and_description_and_trimming(self):
        await self.create("  달빛 이슬  ", "💧", "  새벽에만 맺혀요  ")
        item = self.row("달빛 이슬")
        self.assertEqual((item["name"], item["emoji"], item["description"]), ("달빛 이슬", "💧", "새벽에만 맺혀요"))

    async def test_the_reply_points_to_the_commands_to_use_it_next(self):
        text = self.text(await self.create("마나 가루"))
        for command in ("/상점진열", "/패키지결과 추가", "/연금술레시피 추가", "/물품지급"):
            self.assertIn(command, text)

    async def test_bad_input_is_explained_and_nothing_is_made(self):
        for args, needle in ((("가" * 31,), "30자"), (("마나 가루", "abc"), "이모지"), (("마나 가루", "🎃", "설" * 201), "200자"),
                             (("   ",), "이름")):
            i = await self.create(*args)
            self.assertIn(needle, self.text(i), args)
            self.assertFalse(i.response.send_message.await_args.kwargs["ephemeral"])
        self.assertEqual([r["name"] for r in self.store.items(G) if r["system"] is None], [])          # 기본 물품 말고는 아무것도 안 만들어졌다.

    async def test_the_default_items_names_are_taken_even_on_a_brand_new_server(self):
        """기본 물품(열쇠·쓰레기·패키지 물품)은 아직 만들어지기 전인 새 서버에서도 이름이 이미 쓰인 것으로 본다."""
        for name in ("열쇠", "쓰레기", "마녀 모자"):
            self.assertIn("같은 이름", self.text(await self.create(name)), name)
        self.assertEqual([r["name"] for r in self.store.items(G) if r["system"] is None], [])

    async def test_the_same_name_is_refused_even_in_other_letter_case_or_after_it_was_made(self):
        await self.create("Mana")
        self.assertIn("같은 이름", self.text(await self.create("mana")))
        self.assertIn("같은 이름", self.text(await self.create("Mana")))

    async def test_only_admins(self):
        i = await self.create("마나 가루", admin=False)
        self.assertIn("관리자만", self.text(i))
        self.assertIsNone(self.row("마나 가루"))

    async def test_it_still_works_when_the_shop_is_full_because_it_never_takes_a_shop_slot(self):
        self.store.ensure_defaults(G)
        with self.store.db:
            listed = len(self.store.items(G, shop_only=True))
            for n in range(MAX_SHOP_ITEMS - listed):
                self.store.db.execute("INSERT INTO items(guild,name,emoji,kind,buyable,buy_price) VALUES(?,?,?,?,?,?)", (G, f"자리{n}", "🎃", "item", 1, 1))
        self.assertEqual(len(self.store.items(G, shop_only=True)), MAX_SHOP_ITEMS)
        i = await self.create("마나 가루")
        self.assertIn("만들었어요", self.text(i))
        self.assertIsNotNone(self.row("마나 가루"))
        self.assertEqual(len(self.store.items(G, shop_only=True)), MAX_SHOP_ITEMS)

    async def test_the_new_item_works_with_the_other_commands(self):
        for name in ("마나 가루", "달빛 이슬", "마력 약"):
            await self.create(name)
        dust, dew, potion = (self.row(n) for n in ("마나 가루", "달빛 이슬", "마력 약"))
        # /상점진열: 상점에 올린다
        i = self.w.interaction(admin=True)
        await Shop.shelve_item.callback(self.cog, i, "마나 가루", 100, 100)
        self.assertIn("상점에 올렸어요", self.text(i))
        self.assertEqual(self.row("마나 가루")["sell_price"], 100)
        await Shop.shelve_item.callback(self.cog, self.w.interaction(admin=True), "달빛 이슬", None, 100)
        # /연금술레시피 추가: 재료와 결과물로 쓴다
        i = self.w.interaction(admin=True)
        await Alchemy.recipe_add.callback(Alchemy(self.w.bot), i, "마력 약", "마나 가루", "달빛 이슬")
        self.assertIn("레시피를 더했어요", self.text(i))
        self.assertEqual(self.row("마력 약")["sell_price"], 160)
        # /물품지급: 유저에게 준다
        i = self.w.interaction(admin=True)
        await Admin.give_item.callback(Admin(self.w.bot), i, self.w.member, "마나 가루", 3)
        self.assertEqual(self.store.qty(G, 7, dust["id"]), 3)


class AdoptSameNamedItemTests(unittest.TestCase):
    """기본 물품이 만들어지기 전에 같은 이름의 물품이 먼저 만들어진 서버(예전엔 기본 물품 준비가 KeyError로 실패했다)."""

    def test_a_key_made_before_the_defaults_is_adopted_not_duplicated(self):
        from store import Store
        store = Store(":memory:")
        key = store.create_item(1, "열쇠", emoji="🔑", description="내가 만든 열쇠")
        trash = store.create_item(1, "쓰레기", emoji="🧹")
        store.add_item(1, 7, key["id"], 4)                                   # 가방에 이미 들어 있다.
        store.ensure_defaults(1)                                             # 예전에는 KeyError
        names = [r["name"] for r in store.items(1)]
        self.assertEqual((names.count("열쇠"), names.count("쓰레기")), (1, 1))
        cfg = store.settings(1)
        self.assertEqual((cfg["seeded"], cfg["entry_item"]), (True, key["id"]))      # 내가 만든 열쇠가 던전 입장 물품이 된다.
        ids = store.ensure_system_items(1)
        self.assertEqual((ids["key"], ids["trash"]), (key["id"], trash["id"]))
        self.assertEqual((store.item(1, key["id"])["emoji"], store.item(1, key["id"])["description"], store.qty(1, 7, key["id"])), ("🔑", "내가 만든 열쇠", 4))
        table = {e["id"]: e for e in cfg["package_table"]}
        self.assertEqual((table["key"]["item"], table["trash"]["item"]), (key["id"], trash["id"]))
        self.assertEqual(len(cfg["package_table"]), 12)                      # 새 기본 확률표까지 그대로 적용된다.
        store.ensure_defaults(1)                                             # 다시 불러도 그대로
        self.assertEqual(len([r for r in store.items(1) if r["name"] == "열쇠"]), 1)
        store.close()

    def test_a_role_item_with_the_same_name_is_not_adopted(self):
        from store import Store
        store = Store(":memory:")
        store.create_item(1, "쓰레기장", kind="role", role_id=5, buy_price=10, buyable=True)       # 이름이 비슷할 뿐 다른 물품
        store.ensure_defaults(1)
        self.assertEqual(sorted(store.ensure_system_items(1))[:2], ["broken_glass", "ghost_cloth"])
        self.assertIn("key", store.ensure_system_items(1))
        store.close()


if __name__ == "__main__":
    unittest.main()
