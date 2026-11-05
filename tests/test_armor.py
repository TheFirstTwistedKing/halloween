"""갑옷: 받는 모든 피해를 줄이는 세 번째 장비 칸. 만들기·검증, 피해 계산, 장착·예비·판매, 개조, 상자·층, 명령어, 화면, 컴퓨터 진행."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
import dungeon_auto
import dungeon_service as service
from balance import Balance, check_entity, entity_dict, check_value
from cogs.content_admin import ContentAdmin
from cogs.dungeon import DungeonView, EquipView, equip_embed, run_embed
from dungeon import DungeonError, Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})
MAX = balance.DEFAULT.max_hp


def armor(**kw):
    data = dict(key="a", name="철갑", emoji="🛡️", slot="armor", tier="희귀", weight=1, reduction=30)
    data.update(kw)
    return data


def bal_with(*gear, **overrides):
    raw = {"gear": [entity_dict(check_entity("gear", g)[0]) for g in gear]}
    return Balance.build(overrides, raw)


def battle(bal, hp=MAX, damage=100, **kw):
    run = dungeon.new_run(bal=bal)
    enemy = dungeon._spawn(balance.MONSTERS[1], 1, bal)
    enemy.update(hp=1000, max_hp=1000, damage=damage, dodge=0)
    run.phase, run.kind, run.room, run.hp, run.enemy = "battle", "monster", 2, hp, enemy
    for k, v in kw.items():
        setattr(run, k, v)
    return run


def idle(**kw):
    run = dungeon.new_run()
    run.phase, run.room = "empty", 3
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class EntityTests(unittest.TestCase):
    def test_an_armor_needs_a_reduction_and_drops_weapon_and_shield_numbers(self):
        item, problem = check_entity("gear", armor(attack=50, crit=20, guard=70, reflect=30, dodge=40, guard_chance=50))
        self.assertIsNone(problem)
        self.assertEqual((item.slot, item.reduction, item.attack, item.crit, item.guard, item.reflect, item.dodge, item.guard_chance),
                         ("armor", 30, 0, 0, None, None, None, None))
        self.assertIn("받는 피해 30% 감소", item.description)
        self.assertIn("갑옷", check_entity("gear", armor(reduction=0))[1])

    def test_the_reduction_is_limited(self):
        self.assertIsNone(check_entity("gear", armor(reduction=balance.ARMOR_MAX))[1])
        for bad in (balance.ARMOR_MAX + 1, -1, 1.5, True):
            self.assertIsNotNone(check_entity("gear", armor(reduction=bad))[1], bad)

    def test_weapons_and_shields_ignore_a_reduction(self):
        weapon = check_entity("gear", dict(armor(), slot="weapon", attack=10))[0]
        shield = check_entity("gear", dict(armor(), slot="shield", guard=50))[0]
        self.assertEqual((weapon.reduction, shield.reduction), (0, 0))
        self.assertEqual(check_entity("gear", dict(armor(), slot="boots"))[0], None)

    def test_round_trip_floors_and_old_saves(self):
        item = check_entity("gear", armor(floors=[3, 4]))[0]
        loaded = check_entity("gear", json.loads(json.dumps(entity_dict(item))))[0]
        self.assertEqual(loaded, item)
        self.assertEqual(loaded.floors, (3, 4))
        old = entity_dict(check_entity("gear", dict(armor(), slot="weapon", attack=5))[0])
        old.pop("reduction")                                                              # 갑옷이 생기기 전에 저장된 장비
        self.assertEqual(check_entity("gear", old)[0].reduction, 0)

    def test_defaults_include_a_few_armors_that_can_be_tuned(self):
        armors = [g for g in balance.GEAR if g.slot == "armor"]
        self.assertEqual(len(armors), 3)
        self.assertEqual(sorted(g.tier for g in armors), ["일반", "전설", "희귀"])
        name = armors[0].name
        self.assertEqual(check_value(f"장비.{name}.피해감소", 15), (15, None))
        self.assertIsNotNone(check_value(f"장비.{name}.피해감소", 0)[1])
        bal = Balance.build({f"장비.{name}.피해감소": 15, f"장비.{name}.등장": 0.5})
        tuned = bal.gear_by_key[armors[0].key]
        self.assertEqual((tuned.reduction, tuned.weight), (15, 0.5))
        self.assertIn("15%", tuned.description)


class DamageTests(unittest.TestCase):
    def test_an_armor_reduces_every_hit(self):
        bal = bal_with(armor())
        run = battle(bal, armor="a")
        events = dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.hp, MAX - 70)
        self.assertTrue(any("갑옷으로 30% 줄였어요" in e for e in events))

    def test_without_a_guard_the_armor_comes_before_the_potion_reduction(self):
        bal = bal_with(armor(reduction=50))
        run = battle(bal, damage=1000, armor="a")
        self.assertEqual(dungeon.incoming_damage(run, 1000, 0, bal), 500)                # 1000 → 갑옷 50% → 500
        run.reduce_percent, run.reduce_turns = 50, 2
        self.assertEqual(dungeon.incoming_damage(run, 1000, 0, bal), 250)                # → 소모품 50% → 250
        run.invuln_turns = 1
        self.assertEqual(dungeon.incoming_damage(run, 1000, 0, bal), 0)

    def test_a_successful_guard_applies_only_the_shields_reduction(self):
        bal = bal_with(armor(reduction=50))
        run = battle(bal, hp=500, damage=1000, armor="a", reduce_percent=50, reduce_turns=2)
        self.assertEqual(dungeon.incoming_damage(run, 1000, 60, bal, guarded=True), 400)     # 갑옷·소모품 감소는 빠지고 가드 60%만
        events = dungeon.player_defend(run, True, bal)
        self.assertEqual(run.hp, 500 - 400 + 500)                                        # 가드 60%로 400 맞고, 가드 회복 +500(원래 피해의 50%)
        self.assertTrue(any("가드로 60% 줄였어요" in e for e in events))
        self.assertFalse(any("갑옷으로" in e or "피해 감소 50%" in e for e in events))

    def test_a_failed_guard_still_gets_the_armor_and_potion_reduction(self):
        bal = bal_with(armor(reduction=50))
        run = battle(bal, hp=1000, damage=1000, armor="a", reduce_percent=50, reduce_turns=2)
        events = dungeon.player_defend(run, False, bal)
        self.assertEqual(run.hp, 1000 - 250)
        self.assertTrue(any("갑옷으로 50% 줄였어요" in e for e in events))

    def test_invulnerability_still_blocks_a_guarded_hit(self):
        bal = bal_with(armor(reduction=50))
        run = battle(bal, hp=1000, damage=1000, invuln_turns=2)
        dungeon.player_defend(run, True, bal)
        self.assertEqual(run.hp, 1000 + 0)                                               # 맞지 않았고, 가드 회복은 최대 HP까지

    def test_no_armor_or_a_deleted_one_changes_nothing(self):
        bal = bal_with(armor())
        self.assertEqual(dungeon.armor_percent(battle(bal), bal), 0)
        gone = battle(bal, armor="deleted")
        self.assertEqual(dungeon.incoming_damage(gone, 100, 0, bal), 100)
        wrong = battle(bal, armor="a")
        wrong.armor = "club"                                                             # 무기를 갑옷 칸에 넣을 수는 없다
        self.assertEqual(dungeon.armor_percent(wrong, bal), 0)


class EquipTests(unittest.TestCase):
    def test_a_chest_armor_can_be_equipped_and_pushes_the_old_one_to_the_spare(self):
        bal = bal_with(armor(), armor(key="b", name="천옷", reduction=5))
        run = idle(pending_gear="b", armor="a")
        dungeon.equip_pending(run, bal)
        self.assertEqual((run.armor, run.spare_armor, run.pending_gear), ("b", "a", None))
        self.assertTrue(any("예비 칸으로 옮겼어요" in e for e in dungeon.equip_pending(idle(pending_gear="a", armor="b"), bal)))
        full = idle(pending_gear="b", armor="a", spare_armor="a")                          # 예비가 차 있으면 입던 것은 사라진다
        events = dungeon.equip_pending(full, bal)
        self.assertEqual((full.armor, full.spare_armor), ("b", "a"))
        self.assertTrue(any("사라졌어요" in e for e in events))

    def test_armor_does_not_use_the_weapon_or_shield_slot(self):
        bal = bal_with(armor())
        run = idle(pending_gear="a", weapon="club", shield="wooden_shield")
        dungeon.equip_pending(run, bal)
        self.assertEqual((run.weapon, run.shield, run.armor), ("club", "wooden_shield", "a"))

    def test_stash_and_a_full_spare_armor_slot(self):
        bal = bal_with(armor(), armor(key="b", name="천옷", reduction=5))
        run = idle(pending_gear="a")
        dungeon.stash_pending(run, bal)
        self.assertEqual(run.spare_armor, "a")
        run.pending_gear = "b"
        with self.assertRaises(DungeonError) as ctx:
            dungeon.stash_pending(run, bal)
        self.assertIn("예비 갑옷 칸이 이미 차 있어요", str(ctx.exception))

    def test_swap_exchanges_and_says_what_happened(self):
        bal = bal_with(armor(), armor(key="b", name="천옷", reduction=5))
        run = idle(armor="a", spare_armor="b")
        events = dungeon.swap_spare(run, "armor", bal)
        self.assertEqual((run.armor, run.spare_armor), ("b", "a"))
        self.assertIn("🔄 갑옷을 교체했어요", events[0])
        dungeon.swap_spare(run, "armor", bal)
        run.spare_armor = None
        events = dungeon.swap_spare(run, "armor", bal)
        self.assertIn("예비 칸에 넣었어요", events[0])
        self.assertIn("맨몸", events[0])
        self.assertIsNone(run.armor)

    def test_swap_with_nothing_is_explained_and_cannot_happen_in_battle(self):
        bal = bal_with(armor())
        with self.assertRaisesRegex(DungeonError, "바꿀 갑옷이 없어요"):
            dungeon.swap_spare(idle(), "armor", bal)
        with self.assertRaisesRegex(DungeonError, "싸우는 중에는"):
            dungeon.swap_spare(battle(bal, armor="a"), "armor", bal)

    def test_old_saves_have_no_armor_and_it_survives_saving(self):
        run = idle()
        old = json.loads(run.to_json())
        old.pop("armor"), old.pop("spare_armor")
        back = Run.from_json(json.dumps(old))
        self.assertEqual((back.armor, back.spare_armor), (None, None))
        run.armor, run.spare_armor = "a", "b"
        self.assertEqual((Run.from_json(run.to_json()).armor, Run.from_json(run.to_json()).spare_armor), ("a", "b"))

    def test_a_chest_message_names_the_armor_it_replaces(self):
        bal = bal_with(armor(), armor(key="b", name="천옷", reduction=5))
        run = idle(armor="b", phase="chest")
        gear, _, events = dungeon.open_chest(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual(gear.key, "a")
        self.assertTrue(any("지금 쓰는 **천옷**과(와) 바뀌어요" in e for e in events))


class SellTests(unittest.TestCase):
    def test_armor_can_be_sold_worn_spare_or_pending(self):
        bal = bal_with(armor(sell_price=40))
        run = idle(armor="a", spare_armor="a", pending_gear="a")
        entries = {e["id"]: e for e in dungeon.sellable(run, bal)}
        self.assertEqual({"pending", "armor", "spare_armor"} & set(entries), {"pending", "armor", "spare_armor"})
        total, _ = dungeon.sell(run, ["armor", "spare_armor"], "all", bal)
        self.assertEqual(total, 80)
        self.assertEqual((run.armor, run.spare_armor, run.pending_gear), (None, None, "a"))


class ModTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        content.add(self.store, G, "gear", dict(armor(name="강철 갑옷", reduction=40)))
        content.add(self.store, G, "consumables", dict(name="수선 도구", emoji="🔨", tier="희귀", weight=1, material=True))

    async def asyncTearDown(self):
        await self.w.close()

    def test_an_armor_recipe_changes_the_armor_only(self):
        content.add_mod(self.store, G, "천 갑옷", "수선 도구", "강철 갑옷")
        bal = self.store.balance(G)
        item = next(c.key for c in bal.consumables if c.name == "수선 도구")
        run = idle(armor="cloth_armor", weapon="club", items={item: 1})
        before, after = dungeon.mod_target(run, item, bal)
        self.assertEqual((before.key, after.name), ("cloth_armor", "강철 갑옷"))
        events = dungeon.use_consumable(run, item, bal)
        self.assertEqual((run.armor, run.weapon), (after.key, "club"))
        self.assertTrue(any("개조됐어요" in e and "천 갑옷" in e for e in events))
        self.assertNotIn(item, run.items)

    def test_mixing_slots_is_refused(self):
        for a, c in (("천 갑옷", "단검"), ("단검", "강철 갑옷"), ("나무 방패", "강철 갑옷")):
            with self.assertRaises(content.ContentError) as ctx:
                content.add_mod(self.store, G, a, "수선 도구", c)
            self.assertIn("갑옷은 갑옷으로만", str(ctx.exception))

    def test_the_weapon_is_changed_before_the_armor_when_both_have_a_recipe(self):
        content.add_mod(self.store, G, "천 갑옷", "수선 도구", "강철 갑옷")
        content.add_mod(self.store, G, "낡은 몽둥이", "수선 도구", "단검")
        bal = self.store.balance(G)
        item = next(c.key for c in bal.consumables if c.name == "수선 도구")
        run = idle(armor="cloth_armor", weapon="club", items={item: 2})
        dungeon.use_consumable(run, item, bal)
        self.assertEqual((run.weapon, run.armor), ("dagger", "cloth_armor"))
        dungeon.use_consumable(run, item, bal)                                              # 무기는 이제 레시피가 없다 → 갑옷
        self.assertEqual(bal.gear_by_key[run.armor].name, "강철 갑옷")

    def test_an_item_with_no_recipe_for_any_held_gear_says_the_armor_slot_too(self):
        bal = self.store.balance(G)
        item = next(c.key for c in bal.consumables if c.name == "수선 도구")
        with self.assertRaisesRegex(DungeonError, "갑옷: 없음"):
            dungeon.use_consumable(idle(items={item: 1}), item, bal)


class ChestAndFloorTests(unittest.TestCase):
    def test_an_armor_can_drop_from_a_chest_only_on_its_floors(self):
        bal = bal_with(armor(floors=[3]), dict(armor(), key="w", name="몽둥이2", slot="weapon", attack=5, reduction=0, floors=[]))
        def drop(floor):
            run = idle(phase="chest", floor=floor)
            return dungeon.open_chest(run, Scripted(randoms=[0.0, 0.0]), bal)[0].key
        self.assertEqual(drop(3), "a")
        self.assertEqual(drop(1), "w")


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

    def gear(self, name):
        return next(g for g in self.store.balance(G).gear if g.name == name)

    async def test_add_an_armor_and_it_needs_a_reduction(self):
        text = await self.run_cmd(ContentAdmin.gear_add, "용비늘 갑옷", "🐲", Choice("armor"), Choice("전설"), 1, reduction=45)
        self.assertIn("받는 피해 45% 감소", text)
        self.assertEqual(self.gear("용비늘 갑옷").reduction, 45)
        text = await self.run_cmd(ContentAdmin.gear_add, "빈 갑옷", "🐲", Choice("armor"), Choice("전설"), 1)
        self.assertIn("피해 감소", text)
        self.assertFalse(any(g.name == "빈 갑옷" for g in self.store.balance(G).gear))

    async def test_edit_the_reduction_and_the_floors(self):
        await self.run_cmd(ContentAdmin.gear_edit, "천 갑옷", reduction=22)
        self.assertEqual(self.gear("천 갑옷").reduction, 22)
        text = await self.run_cmd(ContentAdmin.gear_floors, "천 갑옷", "2,4")
        self.assertIn("2층, 4층", text)
        self.assertEqual(self.gear("천 갑옷").floors, (2, 4))

    async def test_autocomplete_says_which_kind_and_lists_show_the_description(self):
        i = self.w.interaction(admin=True)
        labels = {c.value: c.name for c in self.cog._gear_choices(G, "갑옷")}
        self.assertIn("천 갑옷", labels)
        self.assertTrue(labels["천 갑옷"].endswith("(갑옷)"))
        line = content.describe("gear", next(d for d in content.current(self.store, G, "gear") if d["name"] == "천 갑옷"))
        self.assertIn("받는 피해", line)

    async def test_admin_only_and_option_count(self):
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.gear_add, "갑", "🛡️", Choice("armor"), Choice("일반"), 1, reduction=5, admin=False))
        for command in (ContentAdmin.gear_add, ContentAdmin.gear_edit):
            self.assertLessEqual(len(command.parameters), 25)
            self.assertIn("피해감소", {p.display_name for p in command.parameters})


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.bal = self.store.balance(G)

    async def asyncTearDown(self):
        await self.w.close()

    def labels(self, view):
        return [c.label for c in view.children]

    def test_the_run_screen_shows_the_worn_and_spare_armor(self):
        shown = {f.name: f.value for f in run_embed(idle(armor="cloth_armor", spare_armor="chain_armor"), None, self.bal).fields}
        self.assertIn("천 갑옷", shown["🎒 장비"])
        self.assertIn("사슬 갑옷", shown["📦 예비"])
        bare = {f.name: f.value for f in run_embed(idle(), None, self.bal).fields}
        self.assertIn("🧥 맨몸", bare["🎒 장비"])

    def test_the_equipment_screen_has_an_armor_row_and_button(self):
        run = idle(armor="cloth_armor", spare_armor="chain_armor")
        fields = {f.name: f.value for f in equip_embed(run, self.bal).fields}
        self.assertIn("**장착**: 👘 **천 갑옷**", fields["🧥 갑옷"])
        self.assertIn("**예비**: ⛓️ **사슬 갑옷**", fields["🧥 갑옷"])
        view = EquipView(self.w.bot, U, run, self.bal)
        self.assertEqual(self.labels(view), ["무기 교체", "방패 교체", "갑옷 교체", "돌아가기"])
        self.assertEqual([c.disabled for c in view.children], [True, True, False, False])
        view.to_components()

    def test_only_armor_in_the_pockets_still_opens_the_equipment_screen(self):
        self.assertIn("장비", self.labels(DungeonView(self.w.bot, U, idle(armor="cloth_armor"), self.bal)))
        self.assertIn("장비", self.labels(DungeonView(self.w.bot, U, idle(spare_armor="cloth_armor"), self.bal)))

    def test_the_stash_button_depends_on_the_armor_spare_slot(self):
        free = DungeonView(self.w.bot, U, idle(pending_gear="chain_armor", phase="chest_done", spare_weapon="dagger"), self.bal)
        self.assertIn("예비에 넣기", self.labels(free))
        full = DungeonView(self.w.bot, U, idle(pending_gear="chain_armor", phase="chest_done", spare_armor="cloth_armor"), self.bal)
        self.assertNotIn("예비에 넣기", self.labels(full))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    def tearDown(self):
        self.store.close()

    async def test_swap_armor_action_and_admin_gift(self):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(phase="empty", kind="empty", room=2, armor="cloth_armor", spare_armor="chain_armor")
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        out = await service.act(self.store, self.locks, Scripted(), G, U, rev, "swap_armor")
        self.assertTrue(out.ok, out.text)
        self.assertEqual((out.run.armor, out.run.spare_armor), ("chain_armor", "cloth_armor"))
        gift = await service.grant_gear(self.store, self.locks, G, U, "pumpkin_armor")
        self.assertTrue(gift.ok, gift.text)
        self.assertEqual(Run.from_json(self.store.dungeon_run(G, U)).pending_gear, "pumpkin_armor")


class AutoPlayTests(unittest.TestCase):
    def test_the_computer_wears_only_a_better_armor(self):
        bal = bal_with(armor(key="low", name="낮은", reduction=10), armor(key="high", name="높은", reduction=40))
        run = idle(pending_gear="high", phase="chest_done")
        self.assertEqual(dungeon_auto.choose(run, bal), ("equip", None))
        run = idle(pending_gear="low", phase="chest_done", armor="high")
        self.assertEqual(dungeon_auto.choose(run, bal), ("discard", None))

    def test_the_computer_uses_a_material_when_it_improves_the_armor(self):
        raw = {"gear": [entity_dict(check_entity("gear", armor(key="low", name="낮은", reduction=10))[0]),
                        entity_dict(check_entity("gear", armor(key="high", name="높은", reduction=40))[0])],
               "consumables": [entity_dict(check_entity("consumables", dict(key="m", name="재료", emoji="🔨", tier="일반", weight=1, material=True))[0])]}
        bal = Balance.build({}, raw, [{"id": 1, "weapon": "low", "item": "m", "result": "high"}])
        run = idle(armor="low", items={"m": 1})
        self.assertEqual(dungeon_auto.choose(run, bal), ("item", "m"))
        worse = Balance.build({}, raw, [{"id": 1, "weapon": "high", "item": "m", "result": "low"}])
        self.assertNotEqual(dungeon_auto.choose(idle(armor="high", items={"m": 1}), worse), ("item", "m"))

    def test_the_simulator_equips_only_a_better_armor(self):
        bal = bal_with(armor(key="low", name="낮은", reduction=10), armor(key="high", name="높은", reduction=40))
        self.assertTrue(balance_sim._wants(idle(pending_gear="high"), bal))
        self.assertTrue(balance_sim._wants(idle(pending_gear="low"), bal))                  # 아무것도 안 입었으면 입는다
        self.assertFalse(balance_sim._wants(idle(pending_gear="low", armor="high"), bal))
        self.assertTrue(balance_sim._wants(idle(pending_gear="high", armor="low"), bal))

    def test_the_simulator_treats_armor_as_armor_not_a_shield(self):
        bal = bal_with(armor(key="x", name="아주 강한 갑옷", reduction=90, weight=1000))
        self.assertIn("attack", balance_sim.simulate(bal, 20, seed=2))
        strong = balance_sim.simulate(bal, 200, seed=2)["attack"]["clear"]
        weak = balance_sim.simulate(bal_with(armor(key="x", name="약한 갑옷", reduction=1, weight=1000)), 200, seed=2)["attack"]["clear"]
        self.assertGreater(strong, weak)
