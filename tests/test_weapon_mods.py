"""장비 개조 레시피(/장비개조): 개조 재료 소모품을 특정 무기를 든 채 쓰면 개조된 무기로 바뀐다."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
import dungeon_service as service
from balance import Balance, check_entity, clean_mods, entity_dict, item_summary
from cogs.content_admin import ContentAdmin
from cogs.dungeon import ItemSelect, run_embed
from dungeon import DungeonError
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


def material(**kw):
    data = dict(key="m", name="숫돌", emoji="🪨", tier="희귀", weight=1, material=True)
    data.update(kw)
    return data


def bal_with(*recipes, extra_items=()):
    """숫돌(m) 재료와 레시피 [(무기, 재료, 결과)]가 있는 밸런스."""
    items = [entity_dict(check_entity("consumables", i)[0]) for i in (material(), *extra_items)]
    return Balance.build({}, {"consumables": items}, [{"id": n, "weapon": w, "item": i, "result": r} for n, (w, i, r) in enumerate(recipes, start=1)])


class EntityTests(unittest.TestCase):
    def test_a_material_has_no_effect_and_needs_none(self):
        item, problem = check_entity("consumables", material())
        self.assertIsNone(problem)
        self.assertEqual((item.material, item.heal, item.full, item.regen, item.regen_turns, item.modify), (True, 0, False, 0, 0, None))
        self.assertIn("재료", item.description)
        item, _ = check_entity("consumables", material(heal=300, full=True, regen=4, regen_turns=2))
        self.assertEqual((item.heal, item.full, item.regen, item.regen_turns), (0, False, 0, 0))     # 재료면 효과는 지운다

    def test_a_material_cannot_also_be_a_fixed_modify_item_and_must_be_a_bool(self):
        entity, problem = check_entity("consumables", material(modify="longsword"))
        self.assertIsNone(entity)
        self.assertIn("함께 쓸 수 없어요", problem)
        for bad in ("yes", 1, None.__class__):
            self.assertIsNone(check_entity("consumables", material(material=bad))[0], bad)

    def test_it_survives_save_and_load_and_defaults_are_not_materials(self):
        item, _ = check_entity("consumables", material(conditions=[["hp_le", 50]]))
        loaded, problem = check_entity("consumables", json.loads(json.dumps(entity_dict(item))))
        self.assertIsNone(problem)
        self.assertEqual(loaded, item)
        self.assertFalse(any(c.material for c in balance.CONSUMABLES))
        self.assertEqual(check_entity("consumables", dict(key="p", name="약", emoji="💊", tier="일반", weight=1, heal=5))[0].material, False)

    def test_a_plain_item_without_any_effect_is_still_refused(self):
        _, problem = check_entity("consumables", dict(key="p", name="약", emoji="💊", tier="일반", weight=1))
        self.assertIn("회복량", problem)


class RecipeCleaningTests(unittest.TestCase):
    def test_only_usable_recipes_survive(self):
        bal = bal_with(("club", "m", "dagger"), ("dagger", "m", "longsword"))
        self.assertEqual(bal.mods, (("club", "m", "dagger"), ("dagger", "m", "longsword")))
        self.assertEqual(bal.mod_result("club", "m"), "dagger")
        self.assertEqual(bal.mod_result("dagger", "m"), "longsword")
        self.assertIsNone(bal.mod_result("longsword", "m"))
        self.assertIsNone(bal.mod_result(None, "m"))                                            # 맨손은 개조할 수 없다

    def test_broken_recipes_are_dropped_not_fatal(self):
        gear, items = balance.GEAR, (check_entity("consumables", material())[0],
                                     check_entity("consumables", dict(key="p", name="약", emoji="💊", tier="일반", weight=1, heal=5))[0])
        raw = [{"id": 1, "weapon": "club", "item": "m", "result": "dagger"},
               {"id": 2, "weapon": "club", "item": "m", "result": "longsword"},                   # 같은 (무기, 재료) 중복: 앞의 것만
               {"id": 3, "weapon": "gone", "item": "m", "result": "dagger"}, {"id": 4, "weapon": "club", "item": "m", "result": "gone"},
               {"id": 5, "weapon": "longsword", "item": "m", "result": "longsword"}, {"id": 6, "weapon": "club", "item": "p", "result": "dagger"},
               {"id": 7, "weapon": "wooden_shield", "item": "m", "result": "dagger"}, {"id": 8, "weapon": "dagger", "item": "m", "result": "wooden_shield"},
               "깨짐", {"id": 9, "weapon": "dagger"}, {"id": 10, "weapon": 1, "item": "m", "result": "club"}]
        self.assertEqual(clean_mods(raw, gear, items), (("club", "m", "dagger"),))
        self.assertEqual(clean_mods("깨짐", gear, items), ())
        self.assertEqual(clean_mods(None, gear, items), ())

    def test_deleting_the_material_from_the_item_list_disables_the_recipe(self):
        raw = [{"id": 1, "weapon": "club", "item": "m", "result": "dagger"}]
        self.assertEqual(Balance.build({}, {"consumables": []}, raw).mods, ())


class StoreTests(unittest.TestCase):
    def test_settings_default_empty_and_reject_bad_shapes(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        self.assertEqual(store.settings(G)["weapon_mods"], [])
        self.assertEqual(store.balance(G).mods, ())
        for bad in ("x", [1], [{"id": True, "weapon": "a", "item": "b", "result": "c"}], [{"id": 1, "weapon": "a"}], [{"id": 1, "weapon": 1, "item": "b", "result": "c"}],
                    [{"id": 1, "weapon": "a", "item": "b", "result": "c"}] * (balance.MAX_MODS + 1)):
            with self.assertRaises(ValueError, msg=bad):
                store.update_settings(G, weapon_mods=bad)


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        content.add(self.store, G, "consumables", dict(name="숫돌", emoji="🪨", tier="희귀", weight=1, material=True))
        content.add(self.store, G, "consumables", dict(name="미약", emoji="💊", tier="일반", weight=1, heal=10))

    def tearDown(self):
        self.store.close()

    def test_add_and_delete_a_recipe(self):
        entry = content.add_mod(self.store, G, "낡은 몽둥이", "숫돌", "단검")
        self.assertEqual((entry["id"], entry["weapon"], entry["result"]), (1, "club", "dagger"))
        self.assertEqual(self.store.balance(G).mod_result("club", entry["item"]), "dagger")
        text = content.mod_text(self.store, G, entry)
        self.assertTrue(text.endswith("단검"), text)
        self.assertIn("낡은 몽둥이 + ", text)
        self.assertIn("🪨 숫돌 → ", text)
        second = content.add_mod(self.store, G, "단검", "숫돌", "롱소드")
        self.assertEqual(second["id"], 2)
        content.delete_mod(self.store, G, "1")
        self.assertEqual([m["id"] for m in content.mods(self.store, G)], [2])
        self.assertEqual(content.add_mod(self.store, G, "낡은 몽둥이", "숫돌", "롱소드")["id"], 3)       # 번호는 다시 쓰지 않는다
        self.assertIsNone(self.store.balance(G).mod_result("club", "x"))

    def test_bad_recipes_are_refused_with_reasons(self):
        cases = [(("없는무기", "숫돌", "단검"), "무기 **없는무기**"), (("낡은 몽둥이", "없는재료", "단검"), "재료 **없는재료**"),
                 (("낡은 몽둥이", "숫돌", "없는무기"), "결과 무기"), (("방패", "숫돌", "단검"), "방패"), (("낡은 몽둥이", "숫돌", "방패"), "방패"),
                 (("낡은 몽둥이", "미약", "단검"), "개조 재료가 아니에요"), (("낡은 몽둥이", "숫돌", "낡은 몽둥이"), "같아요")]
        for args, expect in cases:
            with self.assertRaises(content.ContentError, msg=args) as ctx:
                content.add_mod(self.store, G, *args)
            self.assertIn(expect, str(ctx.exception), args)
        self.assertEqual(content.mods(self.store, G), [])
        content.add_mod(self.store, G, "낡은 몽둥이", "숫돌", "단검")
        with self.assertRaisesRegex(content.ContentError, "이미 같은 레시피"):
            content.add_mod(self.store, G, "낡은 몽둥이", "숫돌", "롱소드")
        with self.assertRaisesRegex(content.ContentError, "그런 레시피가 없어요"):
            content.delete_mod(self.store, G, "99")
        with self.assertRaisesRegex(content.ContentError, "그런 레시피가 없어요"):
            content.delete_mod(self.store, G, "x")

    def test_the_recipe_limit(self):
        self.store.update_settings(G, weapon_mods=[{"id": n, "weapon": "a", "item": "b", "result": "c"} for n in range(1, balance.MAX_MODS + 1)])
        with self.assertRaisesRegex(content.ContentError, "까지예요"):
            content.add_mod(self.store, G, "낡은 몽둥이", "숫돌", "단검")

    def test_what_a_recipe_uses_cannot_be_deleted_or_changed_into_something_else(self):
        content.add_mod(self.store, G, "낡은 몽둥이", "숫돌", "단검")
        for gear in ("낡은 몽둥이", "단검"):                                                        # 원래 무기·결과 무기 모두
            with self.assertRaisesRegex(content.ContentError, "개조 레시피"):
                content.delete(self.store, G, ("gear",), gear)
        with self.assertRaisesRegex(content.ContentError, "재료라서 지울 수 없어요"):
            content.delete(self.store, G, ("consumables",), "숫돌")
        with self.assertRaisesRegex(content.ContentError, "재료가 아니게 바꿀 수 없어요"):
            content.edit(self.store, G, ("consumables",), "숫돌", {"heal": 50, "material": False})
        content.edit(self.store, G, ("consumables",), "숫돌", {"weight": 3})                       # 다른 값은 고칠 수 있다
        content.delete_mod(self.store, G, "1")
        content.delete(self.store, G, ("consumables",), "숫돌")
        content.delete(self.store, G, ("gear",), "단검")

    def test_a_recipe_goes_dead_but_does_not_crash_when_its_content_is_reset(self):
        weapon = content.add(self.store, G, "gear", dict(name="새 무기", emoji="🗡️", slot="weapon", tier="일반", weight=1, attack=9))
        content.add_mod(self.store, G, "새 무기", "숫돌", "단검")
        self.assertEqual(len(self.store.balance(G).mods), 1)
        content.reset(self.store, G, ("gear",))
        self.assertEqual(self.store.balance(G).mods, ())
        self.assertEqual(len(content.mods(self.store, G)), 1)
        self.assertIn("(없어짐)", content.mod_text(self.store, G, content.mods(self.store, G)[0]))


class UseTests(unittest.TestCase):
    def test_the_material_changes_the_held_weapon_and_is_used_up(self):
        bal = bal_with(("club", "m", "dagger"))
        run = dungeon.new_run(weapon="club", bal=bal)
        run.items, run.spare_weapon = {"m": 2}, "longsword"
        events = dungeon.use_consumable(run, "m", bal)
        self.assertEqual((run.weapon, run.items, run.spare_weapon), ("dagger", {"m": 1}, "longsword"))
        text = "\n".join(events)
        self.assertIn("개조", text)
        self.assertIn("낡은 몽둥이", text)
        self.assertIn("단검", text)
        self.assertEqual(run.last, ["item_use:m"])

    def test_the_same_material_gives_different_results_for_different_weapons(self):
        bal = bal_with(("club", "m", "dagger"), ("dagger", "m", "longsword"))
        run = dungeon.new_run(weapon="club", bal=bal)
        run.items = {"m": 2}
        dungeon.use_consumable(run, "m", bal)
        dungeon.use_consumable(run, "m", bal)                                                   # 몽둥이 → 단검 → 롱소드
        self.assertEqual((run.weapon, run.items), ("longsword", {}))

    def test_it_cannot_be_used_on_the_wrong_weapon_or_bare_hands_and_is_not_spent(self):
        bal = bal_with(("club", "m", "dagger"))
        for weapon, held in ((None, "맨손"), ("longsword", "롱소드")):
            run = dungeon.new_run(weapon=weapon, bal=bal)
            run.items = {"m": 1}
            with self.assertRaisesRegex(DungeonError, f"든 무기\\({held}\\)에는 쓸 수 없어요") as ctx:
                dungeon.use_consumable(run, "m", bal)
            self.assertIn("낡은 몽둥이", str(ctx.exception))                                      # 쓸 수 있는 무기를 알려 준다
            self.assertEqual((run.items, run.weapon, run.last), ({"m": 1}, weapon, []))

    def test_a_material_with_no_recipe_says_so(self):
        run = dungeon.new_run(weapon="club", bal=bal_with())
        run.items = {"m": 1}
        with self.assertRaisesRegex(DungeonError, "아직 이 재료로 개조할 수 있는 무기가 없어요"):
            dungeon.use_consumable(run, "m", bal_with())

    def test_the_materials_own_conditions_still_apply(self):
        raw = material(conditions=[["guard_success", None]])
        bal = Balance.build({}, {"consumables": [entity_dict(check_entity("consumables", raw)[0])]},
                            [{"id": 1, "weapon": "club", "item": "m", "result": "dagger"}])
        run = dungeon.new_run(weapon="club", bal=bal)
        run.items = {"m": 1}
        with self.assertRaisesRegex(DungeonError, "방어 성공 직후"):
            dungeon.use_consumable(run, "m", bal)
        run.last = ["guard_success"]
        dungeon.use_consumable(run, "m", bal)
        self.assertEqual(run.weapon, "dagger")

    def test_using_it_in_a_fight_costs_the_turn(self):
        bal = bal_with(("club", "m", "dagger"))
        run = dungeon.new_run(weapon="club", bal=bal)
        enemy = dungeon._spawn(balance.MONSTERS[1], 1, bal)
        run.phase, run.kind, run.room, run.enemy, run.items = "battle", "monster", 2, enemy, {"m": 1}
        dungeon.use_consumable(run, "m", bal)
        self.assertLess(run.hp, bal.max_hp)

    def test_availability_marks_and_summary(self):
        bal = bal_with(("club", "m", "dagger"))
        item = bal.item_by_key["m"]
        run = dungeon.new_run(weapon="club", bal=bal)
        run.items = {"m": 1}
        self.assertTrue(dungeon.can_use_now(run, item, bal))
        select = ItemSelect(run, bal)
        self.assertTrue(select.options[0].description.startswith("✨"))
        self.assertIn("낡은 몽둥이→단검", select.options[0].description)
        run.weapon = "longsword"
        self.assertFalse(dungeon.can_use_now(run, item, bal))
        self.assertTrue(ItemSelect(run, bal).options[0].description.startswith("🔒"))
        embed = run_embed(run, None, bal)
        self.assertIn("숫돌 ×1 🔒", (embed.description or "") + "".join(f.value for f in embed.fields))
        self.assertIn("개조 재료: 낡은 몽둥이→단검", item_summary(item, bal))

    def test_the_simulated_player_ignores_materials(self):
        bal = bal_with(("club", "m", "dagger"))
        run = dungeon.new_run(weapon="club", bal=bal)
        run.items = {"m": 1}
        self.assertIsNone(balance_sim._best_heal(run, bal))
        self.assertIn("attack", balance_sim.simulate(bal, 20, seed=2))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_item_menu_button_path_end_to_end(self):
        store, locks = Store(":memory:"), Locks()
        self.addCleanup(store.close)
        store.add_item(G, U, store.ensure_system_items(G)["key"], 1)
        content.add(store, G, "consumables", dict(name="숫돌", emoji="🪨", tier="희귀", weight=1, material=True))
        content.add_mod(store, G, "낡은 몽둥이", "숫돌", "단검")
        key = next(c.key for c in store.balance(G).consumables if c.name == "숫돌")
        out = await service.enter(store, locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(phase="cleared", room=1, weapon="club", items={key: 1})
        store.save_dungeon(G, U, json.dumps(data))
        rev = dungeon.Run.from_json(store.dungeon_run(G, U)).rev
        out = await service.act(store, locks, Scripted(), G, U, rev, "item", key)
        self.assertTrue(out.ok)
        self.assertEqual((out.run.weapon, out.run.items), ("dagger", {}))
        self.assertEqual(dungeon.Run.from_json(store.dungeon_run(G, U)).weapon, "dagger")
        self.assertIsNone(out.ended)


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

    def item(self, name):
        return next(c for c in self.store.balance(G).consumables if c.name == name)

    async def test_potion_add_makes_a_material_and_refuses_effects_with_it(self):
        text = await self.run_cmd(ContentAdmin.potion_add, "숫돌", "🪨", Choice("희귀"), 1, material=True)
        self.assertIn("개조 재료", text)
        self.assertTrue(self.item("숫돌").material)
        for kwargs in (dict(heal=5), dict(full=True), dict(regen=3), dict(turns=2), dict(modify="단검")):
            text = await self.run_cmd(ContentAdmin.potion_add, "나쁜재료", "🪨", Choice("희귀"), 1, material=True, **kwargs)
            self.assertIn("효과가 없어요", text, kwargs)
        self.assertFalse(any(c.name == "나쁜재료" for c in self.store.balance(G).consumables))

    async def test_potion_edit_converts_between_material_healing_and_fixed_modify(self):
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", material=True)
        self.assertEqual((self.item("사탕").material, self.item("사탕").heal), (True, 0))
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", heal=40)
        self.assertEqual((self.item("사탕").material, self.item("사탕").heal), (False, 40))
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", material=True)
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", modify="단검")
        self.assertEqual((self.item("사탕").material, self.item("사탕").modify), (False, "dagger"))
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", material=True)
        self.assertEqual((self.item("사탕").material, self.item("사탕").modify), (True, None))
        self.assertIn("효과가 없어요", await self.run_cmd(ContentAdmin.potion_edit, "사탕", material=True, heal=5))

    async def test_recipe_commands_add_list_delete(self):
        await self.run_cmd(ContentAdmin.potion_add, "숫돌", "🪨", Choice("희귀"), 1, material=True)
        text = await self.run_cmd(ContentAdmin.mod_add, "낡은 몽둥이", "숫돌", "단검")
        self.assertIn("레시피를 만들었어요", text)
        self.assertIn("낡은 몽둥이", text)
        text = await self.run_cmd(ContentAdmin.mod_list)
        self.assertIn("`1`", text)
        self.assertIn("→", text)
        self.assertNotIn("쓸 수 없음", text)
        self.assertIn("이미 같은 레시피", await self.run_cmd(ContentAdmin.mod_add, "낡은 몽둥이", "숫돌", "롱소드"))
        self.assertIn("개조 재료가 아니에요", await self.run_cmd(ContentAdmin.mod_add, "낡은 몽둥이", "사탕", "롱소드"))
        text = await self.run_cmd(ContentAdmin.mod_delete, "1")
        self.assertIn("지웠어요", text)
        self.assertIn("레시피가 없어요", await self.run_cmd(ContentAdmin.mod_list))
        self.assertIn("그런 레시피가 없어요", await self.run_cmd(ContentAdmin.mod_delete, "1"))

    async def test_a_dead_recipe_is_flagged_in_the_list(self):
        await self.run_cmd(ContentAdmin.potion_add, "숫돌", "🪨", Choice("희귀"), 1, material=True)
        await self.run_cmd(ContentAdmin.gear_add, "새 무기", "🗡️", Choice("weapon"), Choice("일반"), 1, attack=9)
        await self.run_cmd(ContentAdmin.mod_add, "새 무기", "숫돌", "단검")
        content.reset(self.store, G, ("gear",))
        self.assertIn("쓸 수 없음", await self.run_cmd(ContentAdmin.mod_list))

    async def test_recipe_commands_are_admin_only(self):
        for command, args in ((ContentAdmin.mod_add, ("낡은 몽둥이", "숫돌", "단검")), (ContentAdmin.mod_delete, ("1",)), (ContentAdmin.mod_list, ())):
            self.assertIn("관리자만", await self.run_cmd(command, *args, admin=False))
        self.assertEqual(content.mods(self.store, G), [])

    async def test_autocomplete_offers_only_the_right_things(self):
        await self.run_cmd(ContentAdmin.potion_add, "숫돌", "🪨", Choice("희귀"), 1, material=True)
        await self.run_cmd(ContentAdmin.mod_add, "낡은 몽둥이", "숫돌", "단검")
        i = self.w.interaction(admin=True)
        weapons = [c.value for c in await self.cog.mod_weapons(i, "")]
        self.assertIn("낡은 몽둥이", weapons)
        self.assertNotIn("방패", weapons)                                                       # 방패는 제안하지 않는다
        self.assertEqual([c.value for c in await self.cog.mod_materials(i, "")], ["숫돌"])      # 재료만
        self.assertEqual([c.value for c in await self.cog.mod_idents(i, "")], ["1"])

    def test_options_are_declared(self):
        for name in ("weapon", "item", "result"):
            self.assertEqual(next(p for p in ContentAdmin.mod_add.parameters if p.name == name).type.name, "string")
        for command in (ContentAdmin.potion_add, ContentAdmin.potion_edit):
            self.assertEqual(next(p for p in command.parameters if p.name == "material").type.name, "boolean", command.name)
