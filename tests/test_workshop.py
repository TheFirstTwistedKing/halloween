"""기지 요리(소모품)·제작(장비): 관리자 레시피, 가방 물품 + 재화로 만들기, 결과물은 보관함, /기지 화면에서만."""
import unittest

import workshop
from balance import DEFAULT, VAULT_GEAR_MAX, VAULT_ITEM_MAX
from cogs.base import BaseView, WorkshopView, workshop_embed
from cogs.workshop_admin import WorkshopAdmin
from store import DEFAULT_SETTINGS, Store, StoreError, validate_settings
from tests.helpers import World, sent_embed

G, U = 100, 7
POTION = DEFAULT.consumables[0]


class Setup:
    def make_items(self):
        s = self.store
        self.wire = s.create_item(G, "구리선", emoji="🔌", sell_price=10)
        self.chip = s.create_item(G, "칩", emoji="🔲", sell_price=10)
        s.add_item(G, U, self.wire["id"], 5)
        s.add_item(G, U, self.chip["id"], 1)
        s.add_candy(G, U, 500)


class RecipeTests(Setup, unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.make_items()

    def tearDown(self):
        self.store.close()

    def test_add_list_and_delete(self):
        cook = workshop.add_recipe(self.store, G, "cook", POTION.name, [("구리선", 2), ("칩", 1), ("구리선", 1)], 100, 3)
        self.assertEqual((cook["kind"], cook["result"], cook["amount"], cook["cost"]), ("cook", POTION.key, 3, 100))
        self.assertEqual(cook["ingredients"], [[self.wire["id"], 3], [self.chip["id"], 1]])        # 같은 재료는 합쳐진다
        craft = workshop.add_recipe(self.store, G, "craft", "레이저 커터", [("칩", 1)], 0, 5)
        self.assertEqual((craft["result"], craft["amount"], craft["id"]), ("dagger", 1, 2))         # 제작은 늘 1개
        self.assertEqual([r["id"] for r in workshop.recipes(self.store, G)], [1, 2])
        self.assertEqual([r["id"] for r in workshop.recipes(self.store, G, "craft")], [2])
        self.assertIn("×3", workshop.recipe_line(self.store, G, cook))
        workshop.delete_recipe(self.store, G, 1)
        self.assertEqual([r["id"] for r in workshop.recipes(self.store, G)], [2])
        with self.assertRaises(StoreError):
            workshop.delete_recipe(self.store, G, 1)

    def test_bad_recipes(self):
        bad = [("cook", "레이저 커터", [("칩", 1)], 0, 1),          # 요리는 소모품만
               ("craft", POTION.name, [("칩", 1)], 0, 1),           # 제작은 장비만
               ("cook", "없는 것", [("칩", 1)], 0, 1),
               ("cook", POTION.name, [("없는 물품", 1)], 0, 1),
               ("cook", POTION.name, [], 0, 1),
               ("cook", POTION.name, [("칩", 1)], -1, 1),
               ("cook", POTION.name, [("칩", 1)], 0, 11),
               ("bake", POTION.name, [("칩", 1)], 0, 1)]
        for case in bad:
            with self.assertRaises(StoreError, msg=case):
                workshop.add_recipe(self.store, G, *case)
        workshop.add_recipe(self.store, G, "cook", POTION.name, [("칩", 1)], 0, 1)
        with self.assertRaises(StoreError):
            workshop.add_recipe(self.store, G, "cook", POTION.name, [("구리선", 1)], 0, 1)     # 결과물 하나에 레시피 하나
        self.assertEqual(len(workshop.recipes(self.store, G)), 1)

    def test_settings_shape_is_checked(self):
        good = [{"id": 1, "kind": "cook", "result": "x", "amount": 1, "ingredients": [[1, 2]], "cost": 0}]
        validate_settings(dict(DEFAULT_SETTINGS, workshop=good))
        for broken in ([{"id": 1}], [dict(good[0], kind="bake")], [dict(good[0], ingredients=[[1, 2], [1, 3]])],
                       [dict(good[0], ingredients=[], cost=0)], [good[0], good[0]], "x"):
            with self.assertRaises(ValueError, msg=broken):
                validate_settings(dict(DEFAULT_SETTINGS, workshop=broken))

    def test_ingredients_in_a_recipe_cannot_be_deleted(self):
        workshop.add_recipe(self.store, G, "cook", POTION.name, [("칩", 1)], 0, 1)
        with self.assertRaises(StoreError):
            self.store.delete_item(G, self.chip["id"])
        self.store.delete_item(G, self.wire["id"])                                        # 레시피에 없는 물품은 지울 수 있다


class MakeTests(Setup, unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.make_items()
        self.cook = workshop.add_recipe(self.store, G, "cook", POTION.name, [("구리선", 2)], 100, 3)
        self.craft = workshop.add_recipe(self.store, G, "craft", "플라즈마 블레이드", [("구리선", 1), ("칩", 1)], 50)

    def tearDown(self):
        self.store.close()

    def test_making_spends_and_fills_the_vault(self):
        s = self.store
        workshop.make(s, G, U, self.cook["id"])
        self.assertEqual((s.qty(G, U, self.wire["id"]), s.wallet(G, U), s.vault(G, U)["item"]), (3, 400, {POTION.key: 3}))
        workshop.make(s, G, U, self.craft["id"])
        self.assertEqual((s.qty(G, U, self.wire["id"]), s.qty(G, U, self.chip["id"]), s.wallet(G, U)), (2, 0, 350))
        self.assertEqual(s.vault(G, U)["gear"], {"longsword": 1})
        with self.assertRaisesRegex(StoreError, "모자라요"):
            workshop.make(s, G, U, self.craft["id"])                                     # 칩이 없다
        self.assertEqual((s.qty(G, U, self.wire["id"]), s.wallet(G, U)), (2, 350))         # 아무것도 줄지 않는다

    def test_not_enough_money_or_room(self):
        s = self.store
        s.update_settings(G, workshop=[dict(self.cook, cost=10 ** 6)])
        self.assertFalse(workshop.can_make(s, G, U, workshop.recipes(s, G)[0]))           # 재료는 있지만 재화가 모자라다
        with self.assertRaisesRegex(StoreError, "모자라요"):
            workshop.make(s, G, U, self.cook["id"])
        self.assertEqual(s.qty(G, U, self.wire["id"]), 5)
        s.update_settings(G, workshop=[self.cook, self.craft])
        s.vault_put(G, U, [("item", POTION.key, VAULT_ITEM_MAX - 2), ("gear", "club", VAULT_GEAR_MAX)])
        with self.assertRaisesRegex(StoreError, "보관함"):
            workshop.make(s, G, U, self.cook["id"])                                      # 3개가 들어갈 자리가 없다
        with self.assertRaisesRegex(StoreError, "보관함"):
            workshop.make(s, G, U, self.craft["id"])
        self.assertEqual((s.qty(G, U, self.wire["id"]), s.wallet(G, U)), (5, 500))

    def test_exact_amounts_are_used_up(self):
        s = self.store
        s.add_item(G, U, self.wire["id"], -3)                                           # 구리선 2개만 남긴다
        workshop.make(s, G, U, self.cook["id"])
        self.assertEqual(s.qty(G, U, self.wire["id"]), 0)
        self.assertFalse(workshop.can_make(s, G, U, self.cook))

    def test_missing_recipe_or_result(self):
        with self.assertRaises(StoreError):
            workshop.make(self.store, G, U, 99)
        self.store.update_settings(G, workshop=[dict(self.cook, result="gone")])
        with self.assertRaisesRegex(StoreError, "지워졌어요"):
            workshop.make(self.store, G, U, self.cook["id"])
        self.assertFalse(workshop.can_make(self.store, G, U, workshop.recipes(self.store, G)[0]))


class ViewTests(Setup, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.make_items()
        self.cook = workshop.add_recipe(self.store, G, "cook", POTION.name, [("구리선", 2)], 100, 2)

    async def asyncTearDown(self):
        await self.w.close()

    def child(self, view, label):
        return next(c for c in view.children if getattr(c, "label", None) == label)

    async def test_the_base_has_cook_and_craft_buttons(self):
        view = BaseView(self.bot, U, G)
        i = self.w.interaction(uid=U)
        await self.child(view, "요리").callback(i)
        embed = i.response.edit_message.call_args.kwargs["embed"]
        self.assertIn("요리", embed.title)
        self.assertIn("✅", embed.description)
        self.assertIsInstance(i.response.edit_message.call_args.kwargs["view"], WorkshopView)
        i = self.w.interaction(uid=U)
        await self.child(view, "제작").callback(i)
        self.assertIn("레시피가 없어요", i.response.edit_message.call_args.kwargs["embed"].description)

    async def test_pick_and_make(self):
        view = WorkshopView(self.bot, U, G, "cook")
        self.assertTrue(self.child(view, "요리하기").disabled)                             # 고르기 전에는 못 누른다
        select = next(c for c in view.children if hasattr(c, "options"))
        i = self.w.interaction(uid=U, data={"values": [str(self.cook["id"])]})
        await select.callback(i)
        picked = i.response.edit_message.call_args.kwargs["view"]
        self.assertFalse(self.child(picked, "요리하기").disabled)
        i = self.w.interaction(uid=U)
        await picked.make(i)
        self.assertIn("보관함에 넣었어요", i.response.edit_message.call_args.kwargs["embed"].description)
        self.assertEqual(self.store.vault(G, U)["item"], {POTION.key: 2})
        for _ in range(2):
            await picked.make(self.w.interaction(uid=U))
        i = self.w.interaction(uid=U)
        await picked.make(i)                                                            # 구리선이 떨어졌다
        self.assertIn("⚠️", i.response.edit_message.call_args.kwargs["embed"].description)
        self.assertEqual(self.store.vault(G, U)["item"], {POTION.key: 4})
        self.assertFalse(await picked.interaction_check(self.w.interaction(uid=8)))
        i = self.w.interaction(uid=U)
        await self.child(picked, "기지로").callback(i)
        self.assertIsInstance(i.response.edit_message.call_args.kwargs["view"], BaseView)

    def test_embed_marks_what_cannot_be_made(self):
        self.store.add_item(G, U, self.wire["id"], -5)
        self.assertIn("▫️", workshop_embed(self.store, G, U, "cook").description)


class CommandTests(Setup, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.make_items()
        self.cog = WorkshopAdmin(self.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    async def test_add_list_delete_and_admin_only(self):
        self.assertIn("더했어요", await self.run_cmd(WorkshopAdmin.add_cook, POTION.name, "구리선", 2, cost=30, amount=2))
        self.assertIn("더했어요", await self.run_cmd(WorkshopAdmin.add_craft, "레이저 커터", "칩", 1, "구리선", 3))
        listed = await self.run_cmd(WorkshopAdmin.list_recipes)
        self.assertIn("#1", listed)
        self.assertIn("레이저 커터", listed)
        self.assertIn("물품이 없어요", await self.run_cmd(WorkshopAdmin.add_craft, "레일건", "모래"))
        self.assertIn("관리자만", await self.run_cmd(WorkshopAdmin.add_cook, POTION.name, "칩", admin=False))
        self.assertIn("지웠어요", await self.run_cmd(WorkshopAdmin.delete, 1))
        self.assertIn("그런 레시피가 없어요", await self.run_cmd(WorkshopAdmin.delete, 1))
        self.assertEqual([r["id"] for r in workshop.recipes(self.store, G)], [2])

    async def test_autocomplete(self):
        i = self.w.interaction(admin=True)
        cooks = [c.value for c in await self.cog.cook_results(i, "")]
        crafts = [c.value for c in await self.cog.craft_results(i, "")]
        self.assertIn(POTION.name, cooks)
        self.assertNotIn("레이저 커터", cooks)
        self.assertIn("레이저 커터", crafts)
        self.assertIn("구리선", [c.value for c in await self.cog.ingredients(i, "구리")])
        await self.run_cmd(WorkshopAdmin.add_cook, POTION.name, "칩")
        self.assertEqual([c.value for c in await self.cog.recipe_ids(i, "")], [1])


if __name__ == "__main__":
    unittest.main()
