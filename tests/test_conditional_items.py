"""조건부 소모품과 무기 개조: 사용 조건(HP·몬스터 HP·방어·반사·공격·아이템 획득/사용, 전부 만족해야 함)과 개조 소모품의 규칙, 서비스, 명령어, 화면."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
import dungeon_service as service
from balance import Balance, check_conditions, check_entity, entity_dict, item_summary
from cogs.content_admin import ContentAdmin
from cogs.dungeon import ItemSelect, run_embed
from dungeon import DungeonError, Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})
MAX = balance.DEFAULT.max_hp


def potion(**kw):
    data = dict(key="p", name="비약", emoji="🧪", tier="일반", weight=1, heal=100)
    data.update(kw)
    return data


def bal_with(*items, gear=None):
    """주어진 소모품(사전)을 가진 밸런스. 기본 장비는 그대로 둔다."""
    raw = {"consumables": [entity_dict(check_entity("consumables", i)[0]) for i in items]}
    if gear is not None:
        raw["gear"] = gear
    return Balance.build({}, raw)


def battle_run(bal, hp=MAX, enemy_hp=100, enemy_max=100, last=(), **kw):
    run = dungeon.new_run(bal=bal)
    enemy = dungeon._spawn(balance.MONSTERS[1], 1, bal)
    enemy.update(hp=enemy_hp, max_hp=enemy_max)
    run.phase, run.kind, run.room, run.hp, run.enemy, run.last = "battle", "monster", 2, hp, enemy, list(last)
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class ConditionValidationTests(unittest.TestCase):
    def test_every_kind_is_accepted_in_its_proper_form(self):
        good = [[["hp_le", 30]], [["hp_ge", 0]], [["enemy_hp_le", 100]], [["enemy_hp_ge", 50]], [["guard_success", None]], [["guard_fail", None]],
                [["reflect_success", None]], [["reflect_fail", None]], [["attack_hit", None]], [["attack_miss", None]],
                [["item_gain", "candy"]], [["item_use", "candy"]]]
        for raw in good:
            conds, problem = check_conditions(raw)
            self.assertIsNone(problem, raw)
            self.assertEqual(conds, tuple(tuple(c) for c in raw))
        self.assertEqual(check_conditions(None), ((), None))
        self.assertEqual(check_conditions([]), ((), None))

    def test_hp_conditions_combine_with_one_event_condition(self):
        conds, problem = check_conditions([["hp_le", 50], ["hp_ge", 10], ["enemy_hp_le", 30], ["enemy_hp_ge", 5], ["guard_success", None]])
        self.assertIsNone(problem)
        self.assertEqual(len(conds), 5)

    def test_bad_conditions_are_refused_with_a_reason(self):
        bad = {
            "알 수 없는": [["fly", 1]], "형식": ["hp_le"], "범위": [["hp_le", 101]], "음수": [["hp_le", -1]], "불리언": [["hp_le", True]],
            "소수": [["hp_le", 30.5]], "같은 종류 HP": [["hp_le", 30], ["hp_le", 40]], "값 필요 없음": [["guard_success", 5]],
            "아이템 이름 없음": [["item_use", None]], "빈 이름": [["item_gain", ""]], "6개": [["hp_le", 1], ["hp_ge", 0], ["enemy_hp_le", 1], ["enemy_hp_ge", 0], ["guard_success", None], ["item_use", "a"]],
            "모순 범위": [["hp_ge", 80], ["hp_le", 20]], "모순 몬스터 범위": [["enemy_hp_ge", 80], ["enemy_hp_le", 20]],
            "획득+몬스터HP": [["item_gain", "a"], ["enemy_hp_le", 50]], "문자열 아님": "hp_le",
        }
        for label, raw in bad.items():
            conds, problem = check_conditions(raw)
            self.assertIsNone(conds, label)
            self.assertTrue(problem, label)

    def test_two_event_conditions_can_never_both_hold_so_they_are_refused(self):
        for pair in ([["guard_success", None], ["attack_hit", None]], [["guard_success", None], ["guard_fail", None]],
                     [["item_use", "a"], ["item_use", "b"]], [["item_gain", "a"], ["item_use", "a"]], [["guard_success", None], ["reflect_success", None]]):
            conds, problem = check_conditions(pair)
            self.assertIsNone(conds, pair)
            self.assertIn("하나만", problem)

    def test_the_entity_keeps_conditions_through_save_and_load_and_old_saves_have_none(self):
        item, problem = check_entity("consumables", potion(conditions=[["hp_le", 30], ["guard_fail", None]]))
        self.assertIsNone(problem)
        loaded, problem = check_entity("consumables", json.loads(json.dumps(entity_dict(item))))
        self.assertIsNone(problem)
        self.assertEqual(loaded, item)
        self.assertEqual(check_entity("consumables", potion())[0].conditions, ())
        self.assertTrue(all(c.conditions == () and c.modify is None for c in balance.CONSUMABLES))      # 기본 소모품은 그대로

    def test_a_bad_condition_makes_the_whole_item_invalid(self):
        entity, problem = check_entity("consumables", potion(conditions=[["hp_le", 500]]))
        self.assertIsNone(entity)
        self.assertIn("0~100", problem)


class ModifyValidationTests(unittest.TestCase):
    def test_a_modify_item_has_no_healing_and_needs_no_heal_value(self):
        item, problem = check_entity("consumables", potion(heal=0, modify="longsword"))
        self.assertIsNone(problem)
        self.assertEqual((item.modify, item.heal, item.full, item.regen, item.regen_turns), ("longsword", 0, False, 0, 0))
        item, _ = check_entity("consumables", potion(heal=500, full=True, regen=5, regen_turns=3, modify="longsword"))
        self.assertEqual((item.heal, item.full, item.regen, item.regen_turns), (0, False, 0, 0))     # 개조면 회복은 지운다

    def test_without_modify_a_heal_effect_is_still_required(self):
        _, problem = check_entity("consumables", potion(heal=0))
        self.assertIn("회복량", problem)

    def test_bad_modify_values_are_refused(self):
        for bad in (5, "", "x" * 41, True):
            entity, problem = check_entity("consumables", potion(modify=bad))
            self.assertIsNone(entity, bad)
            self.assertIn("개조", problem)


class ConditionMeaningTests(unittest.TestCase):
    def test_my_hp_thresholds_are_percentages_of_max_hp_and_inclusive(self):
        bal = Balance.build({})
        run = battle_run(bal, hp=MAX * 30 // 100)
        self.assertTrue(dungeon.condition_met(run, ("hp_le", 30), bal))
        self.assertTrue(dungeon.condition_met(run, ("hp_ge", 30), bal))                          # 딱 30%면 이하·이상 둘 다
        run.hp += 1
        self.assertFalse(dungeon.condition_met(run, ("hp_le", 30), bal))
        run.hp -= 2
        self.assertFalse(dungeon.condition_met(run, ("hp_ge", 30), bal))
        self.assertTrue(dungeon.condition_met(battle_run(bal, hp=MAX), ("hp_ge", 100), bal))
        self.assertTrue(dungeon.condition_met(battle_run(bal, hp=1), ("hp_le", 0 + 1), bal))

    def test_monster_hp_thresholds_use_the_monsters_own_max_hp(self):
        bal = Balance.build({})
        run = battle_run(bal, enemy_hp=50, enemy_max=200)                                         # 25%
        self.assertTrue(dungeon.condition_met(run, ("enemy_hp_le", 25), bal))
        self.assertFalse(dungeon.condition_met(run, ("enemy_hp_le", 24), bal))
        self.assertTrue(dungeon.condition_met(run, ("enemy_hp_ge", 25), bal))
        self.assertFalse(dungeon.condition_met(run, ("enemy_hp_ge", 26), bal))
        run.enemy.pop("max_hp")                                                                   # 예전 저장본(최대 체력 기록 없음)은 지금 체력을 100%로 본다
        self.assertTrue(dungeon.condition_met(run, ("enemy_hp_ge", 100), bal))

    def test_monster_hp_conditions_cannot_hold_without_a_fight(self):
        bal = Balance.build({})
        run = dungeon.new_run(bal=bal)
        run.phase = "empty"
        for kind in ("enemy_hp_le", "enemy_hp_ge"):
            self.assertFalse(dungeon.condition_met(run, (kind, 100), bal))
        run.phase, run.enemy = "cleared", {"hp": 0, "max_hp": 10}
        self.assertFalse(dungeon.condition_met(run, ("enemy_hp_le", 100), bal))

    def test_event_conditions_look_only_at_the_last_action(self):
        bal = Balance.build({})
        run = battle_run(bal, last=["guard_success"])
        self.assertTrue(dungeon.condition_met(run, ("guard_success", None), bal))
        self.assertTrue(dungeon.condition_met(run, ("reflect_success", None), bal))              # 반사 성공 = 가드 성공
        self.assertFalse(dungeon.condition_met(run, ("guard_fail", None), bal))
        self.assertFalse(dungeon.condition_met(run, ("reflect_fail", None), bal))
        run.last = ["guard_fail"]
        self.assertTrue(dungeon.condition_met(run, ("reflect_fail", None), bal))                 # 반사 실패 = 가드 실패
        run.last = ["attack_hit"]
        self.assertTrue(dungeon.condition_met(run, ("attack_hit", None), bal))
        self.assertFalse(dungeon.condition_met(run, ("attack_miss", None), bal))
        run.last = ["item_gain:candy", ]
        self.assertTrue(dungeon.condition_met(run, ("item_gain", "candy"), bal))
        self.assertFalse(dungeon.condition_met(run, ("item_gain", "pumpkin_soup"), bal))
        self.assertFalse(dungeon.condition_met(run, ("item_use", "candy"), bal))
        run.last = []
        self.assertFalse(dungeon.condition_met(run, ("attack_hit", None), bal))

    def test_text_names_the_watched_item(self):
        names = {"candy": "소형 배터리"}
        self.assertEqual(balance.condition_text(("item_gain", "candy"), names), "소형 배터리 획득 직후")
        self.assertEqual(balance.condition_text(("item_use", "gone"), names), "gone 사용 직후")
        self.assertEqual(balance.condition_text(("hp_le", 30)), "내 HP 30% 이하")
        self.assertEqual(balance.condition_text(("reflect_fail", None)), "반사 실패 직후")


class EventRecordingTests(unittest.TestCase):
    def test_attack_records_hit_or_miss(self):
        bal = Balance.build({})
        run = battle_run(bal)
        run.enemy["dodge"] = 50
        dungeon.player_attack(run, Scripted(randoms=[0.0]), bal)                                  # 0 < 회피 → 빗나감
        self.assertEqual(run.last, ["attack_miss"])
        run = battle_run(bal)
        run.enemy["dodge"] = 50
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.last, ["attack_hit"])

    def test_defend_records_success_or_failure_and_a_stunned_enemy_records_nothing(self):
        bal = Balance.build({})
        run = battle_run(bal)
        dungeon.player_defend(run, True, bal)
        self.assertEqual(run.last, ["guard_success"])
        self.assertTrue(run.enemy["stunned"])
        run.last = []
        dungeon.player_defend(run, True, bal)                                                     # 기절한 몬스터 앞: 가드를 굴리지 않았다
        self.assertEqual(run.last, [])
        run = battle_run(bal)
        dungeon.player_defend(run, False, bal)
        self.assertEqual(run.last, ["guard_fail"])

    def test_opening_a_chest_records_the_item_that_came_out(self):
        bal = Balance.build({})
        run = dungeon.new_run(bal=bal)
        run.phase = "chest"
        _, item, _ = dungeon.open_chest(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual(run.last, [f"item_gain:{item.key}"])

    def test_using_an_item_records_which_one(self):
        bal = Balance.build({})
        run = dungeon.new_run(bal=bal)
        run.hp, run.items = 10, {"candy": 1}
        dungeon.use_consumable(run, "candy", bal)
        self.assertEqual(run.last, ["item_use:candy"])

    def test_last_survives_json_and_old_saves_start_empty(self):
        run = dungeon.new_run()
        run.last = ["guard_success", "item_gain:candy"]
        self.assertEqual(Run.from_json(run.to_json()).last, ["guard_success", "item_gain:candy"])
        data = json.loads(run.to_json())
        data.pop("last")
        self.assertEqual(Run.from_json(json.dumps(data)).last, [])


class UseTests(unittest.TestCase):
    def test_a_conditional_item_is_refused_until_every_condition_holds_and_is_not_consumed(self):
        item = potion(conditions=[["hp_le", 50], ["enemy_hp_le", 50]])
        bal = bal_with(item)
        run = battle_run(bal, hp=MAX, enemy_hp=40, enemy_max=100, items={"p": 2})
        with self.assertRaisesRegex(DungeonError, "내 HP 50% 이하"):                              # 못 맞춘 조건만 알려 준다
            dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.items, {"p": 2})
        self.assertEqual(run.hp, MAX)
        run.hp = MAX // 2
        run.enemy["hp"] = 51
        with self.assertRaisesRegex(DungeonError, "몬스터 HP 50% 이하"):
            dungeon.use_consumable(run, "p", bal)
        run.enemy["hp"] = 50
        dungeon.use_consumable(run, "p", bal)                                                     # 둘 다 맞아야 쓴다
        self.assertEqual(run.items, {"p": 1})
        self.assertGreater(run.hp, MAX // 2 - 1)

    def test_an_item_without_conditions_works_as_before(self):
        bal = bal_with(potion())
        run = battle_run(bal, hp=100, items={"p": 1})
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.items, {})

    def test_the_event_must_be_from_the_action_right_before(self):
        bal = bal_with(potion(conditions=[["guard_success", None]]))
        run = battle_run(bal, hp=500, items={"p": 1})
        with self.assertRaises(DungeonError):
            dungeon.use_consumable(run, "p", bal)
        run.last = ["guard_success"]
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.items, {})

    def test_item_use_and_item_gain_conditions_watch_the_named_item(self):
        bal = bal_with(potion(key="a", name="가", conditions=[["item_use", "b"]]), potion(key="b", name="나"))
        run = dungeon.new_run(bal=bal)
        run.hp, run.items = 500, {"a": 1, "b": 1}
        with self.assertRaises(DungeonError):
            dungeon.use_consumable(run, "a", bal)
        dungeon.use_consumable(run, "b", bal)
        dungeon.use_consumable(run, "a", bal)                                                     # 나를 쓴 직후라서 가를 쓸 수 있다
        self.assertEqual(run.items, {})

    def test_a_modify_item_turns_the_held_weapon_into_the_target(self):
        bal = bal_with(potion(heal=0, modify="longsword"))
        run = dungeon.new_run(weapon="club", bal=bal)
        run.items = {"p": 1}
        events = dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.weapon, "longsword")
        self.assertEqual(run.items, {})
        text = "\n".join(events)
        self.assertIn("개조", text)
        self.assertIn("낡은 스패너", text)
        self.assertEqual(dungeon.player_damage(run, bal), bal.base_damage + bal.gear_by_key["longsword"].attack)

    def test_modifying_bare_hands_equips_the_target_and_the_spare_slot_is_untouched(self):
        bal = bal_with(potion(heal=0, modify="dagger"))
        run = dungeon.new_run(bal=bal)
        run.items, run.spare_weapon = {"p": 1}, "club"
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual((run.weapon, run.spare_weapon), ("dagger", "club"))

    def test_modify_is_refused_without_spending_the_item_when_it_cannot_work(self):
        bal = bal_with(potion(heal=0, modify="longsword"), potion(key="q", name="사라진", heal=0, modify="no_such_weapon"),
                       potion(key="r", name="방패개조", heal=0, modify="shield"))
        run = dungeon.new_run(weapon="longsword", bal=bal)
        run.items = {"p": 1, "q": 1, "r": 1}
        with self.assertRaisesRegex(DungeonError, "이미"):
            dungeon.use_consumable(run, "p", bal)
        for key, why in (("q", "사라졌어요"), ("r", "사라졌어요")):                                # 없는 무기, 방패는 개조 목표가 될 수 없다
            with self.assertRaisesRegex(DungeonError, why):
                dungeon.use_consumable(run, key, bal)
        self.assertEqual(run.items, {"p": 1, "q": 1, "r": 1})
        self.assertEqual(run.weapon, "longsword")

    def test_using_a_modify_item_in_a_fight_costs_the_turn(self):
        bal = bal_with(potion(heal=0, modify="longsword"))
        run = battle_run(bal, hp=MAX, items={"p": 1})
        events = dungeon.use_consumable(run, "p", bal)
        self.assertLess(run.hp, MAX)                                                              # 몬스터가 때렸다
        self.assertIn("공격", "\n".join(events))

    def test_a_modify_item_can_also_have_conditions(self):
        bal = bal_with(potion(heal=0, modify="longsword", conditions=[["guard_success", None]]))
        run = dungeon.new_run(weapon="club", bal=bal)
        run.items = {"p": 1}
        with self.assertRaises(DungeonError):
            dungeon.use_consumable(run, "p", bal)
        run.last = ["guard_success"]
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.weapon, "longsword")


class ServiceFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        content.add(self.store, G, "consumables", dict(name="반격약", emoji="🧪", tier="희귀", weight=1, heal=200, conditions=[["guard_success", None]]))
        content.add(self.store, G, "consumables", dict(name="연타약", emoji="💊", tier="희귀", weight=1, heal=200, conditions=[["attack_hit", None]]))
        self.bal = self.store.balance(G)
        self.k = {c.name: c.key for c in self.bal.consumables}

    def tearDown(self):
        self.store.close()

    async def fight(self, **items):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        enemy = dungeon._spawn(balance.MONSTERS[1], 1, self.bal)
        enemy["dodge"] = 0
        data.update(phase="battle", kind="monster", room=2, hp=300, enemy=enemy, items={self.k[n]: c for n, c in items.items()})
        self.store.save_dungeon(G, U, json.dumps(data))
        return Run.from_json(self.store.dungeon_run(G, U)).rev

    async def act(self, rev, action, arg=None, **kw):
        return await service.act(self.store, self.locks, Scripted(randoms=[0.99, 0.99]), G, U, rev, action, arg, **kw)

    async def test_guard_success_unlocks_the_item_for_the_next_action_only(self):
        rev = await self.fight(반격약=1, 연타약=1)
        out = await self.act(rev, "item", self.k["반격약"])                                       # 아직 방어 성공 전
        self.assertFalse(out.ok)
        self.assertIn("방어 성공 직후", out.text)
        out = await self.act(rev, "defend", guard_ok=True)
        self.assertEqual(out.run.last, ["guard_success"])
        out2 = await self.act(out.run.rev, "item", self.k["반격약"])
        self.assertTrue(out2.ok)
        self.assertNotIn(self.k["반격약"], out2.run.items)

    async def test_the_next_action_clears_the_event(self):
        rev = await self.fight(반격약=1)
        out = await self.act(rev, "defend", guard_ok=True)
        out = await self.act(out.run.rev, "attack")                                               # 다른 행동을 하면 방어 성공은 지난 일
        self.assertEqual(out.run.last, ["attack_hit"])
        out = await self.act(out.run.rev, "item", self.k["반격약"])
        self.assertFalse(out.ok)

    async def test_a_failed_use_does_not_spend_a_turn_or_change_the_screen_number(self):
        rev = await self.fight(연타약=1)
        out = await self.act(rev, "item", self.k["연타약"])
        self.assertFalse(out.ok)
        data = Run.from_json(self.store.dungeon_run(G, U))
        self.assertEqual((data.rev, data.hp, data.items), (rev, 300, {self.k["연타약"]: 1}))

    async def test_attack_hit_unlocks_the_other_item(self):
        rev = await self.fight(연타약=1)
        out = await self.act(rev, "attack")
        out = await self.act(out.run.rev, "item", self.k["연타약"])
        self.assertTrue(out.ok)

    async def test_selling_clears_the_event(self):
        rev = await self.fight(반격약=1)
        out = await self.act(rev, "defend", guard_ok=True)
        run = Run.from_json(self.store.dungeon_run(G, U))
        run.phase, run.enemy = "cleared", None
        self.store.save_dungeon(G, U, run.to_json())
        sold = await service.sell(self.store, self.locks, G, U, run.rev, ["item:" + self.k["반격약"]], "all")
        self.assertEqual(Run.from_json(self.store.dungeon_run(G, U)).last, [])


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

    async def test_potion_add_with_a_modify_weapon(self):
        text = await self.run_cmd(ContentAdmin.potion_add, "개조약", "🔧", Choice("희귀"), 1, modify="플라즈마 블레이드")
        self.assertIn("개조", text)
        self.assertIn("플라즈마 블레이드", text)
        item = self.item("개조약")
        self.assertEqual((item.modify, item.heal, item.full), ("longsword", 0, False))

    async def test_potion_add_refuses_modify_with_healing_and_bad_weapons(self):
        text = await self.run_cmd(ContentAdmin.potion_add, "개조약", "🔧", Choice("희귀"), 1, heal=50, modify="플라즈마 블레이드")
        self.assertIn("함께 쓸 수 없어요", text)
        text = await self.run_cmd(ContentAdmin.potion_add, "개조약", "🔧", Choice("희귀"), 1, modify="에너지 실드")      # 방패는 안 된다
        self.assertIn("무기 이름", text)
        text = await self.run_cmd(ContentAdmin.potion_add, "개조약", "🔧", Choice("희귀"), 1, modify="없는무기")
        self.assertIn("무기 이름", text)
        self.assertFalse(any(c.name == "개조약" for c in self.store.balance(G).consumables))

    async def test_potion_edit_swaps_between_modify_and_healing(self):
        await self.run_cmd(ContentAdmin.potion_edit, "소형 배터리", modify="레이저 커터")
        item = self.item("소형 배터리")
        self.assertEqual((item.modify, item.heal), ("dagger", 0))
        await self.run_cmd(ContentAdmin.potion_edit, "소형 배터리", heal=77)                              # 새 효과를 정하면 개조는 사라진다
        item = self.item("소형 배터리")
        self.assertEqual((item.modify, item.heal), (None, 77))
        await self.run_cmd(ContentAdmin.potion_edit, "소형 배터리", modify="레이저 커터")
        await self.run_cmd(ContentAdmin.potion_edit, "소형 배터리", full=True)
        self.assertEqual((self.item("소형 배터리").modify, self.item("소형 배터리").full), (None, True))
        text = await self.run_cmd(ContentAdmin.potion_edit, "소형 배터리", heal=5, modify="레이저 커터")
        self.assertIn("함께 쓸 수 없어요", text)

    async def test_condition_add_list_delete_and_clear(self):
        text = await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("hp_le"), value=30)
        self.assertIn("내 HP 30% 이하", text)
        await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("enemy_hp_ge"), value=10)
        await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("guard_success"))
        self.assertEqual(self.item("소형 배터리").conditions, (("hp_le", 30), ("enemy_hp_ge", 10), ("guard_success", None)))
        text = await self.run_cmd(ContentAdmin.cond_list)
        self.assertIn("1. 내 HP 30% 이하", text)
        self.assertIn("3. 방어 성공 직후", text)
        text = await self.run_cmd(ContentAdmin.cond_delete, "소형 배터리", 2)
        self.assertEqual(self.item("소형 배터리").conditions, (("hp_le", 30), ("guard_success", None)))
        text = await self.run_cmd(ContentAdmin.cond_delete, "소형 배터리", 5)
        self.assertIn("조건은 2개예요", text)
        await self.run_cmd(ContentAdmin.cond_clear, "소형 배터리")
        self.assertEqual(self.item("소형 배터리").conditions, ())
        text = await self.run_cmd(ContentAdmin.cond_clear, "소형 배터리")
        self.assertIn("사용 조건이 없어요", text)

    async def test_item_conditions_pick_the_watched_item_by_name(self):
        await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("item_use"), other="정비 모듈")
        self.assertEqual(self.item("소형 배터리").conditions, (("item_use", self.item("정비 모듈").key),))
        text = await self.run_cmd(ContentAdmin.cond_list, "소형 배터리")
        self.assertIn("정비 모듈 사용 직후", text)

    async def test_wrong_arguments_and_impossible_combinations_are_explained(self):
        cases = [((Choice("hp_le"),), {}, "필요해요"), ((Choice("hp_le"),), dict(value=5, other="정비 모듈"), "필요 없어요"),
                 ((Choice("item_use"),), {}, "필요해요"), ((Choice("item_use"),), dict(other="없는약"), "그런 소모품이 없어요"),
                 ((Choice("item_use"),), dict(value=3, other="정비 모듈"), "필요 없어요"), ((Choice("guard_fail"),), dict(value=3), "필요 없어요")]
        for args, kwargs, expect in cases:
            self.assertIn(expect, await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", *args, **kwargs), (args, kwargs))
        await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("guard_success"))
        self.assertIn("하나만", await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("attack_hit")))
        await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("hp_le"), value=40)
        self.assertIn("하나만", await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("hp_le"), value=20))
        self.assertIn("조건을 만족할 수 없어요", await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("hp_ge"), value=90))
        self.assertEqual(self.item("소형 배터리").conditions, (("guard_success", None), ("hp_le", 40)))
        self.assertIn("그런 소모품이 없어요", await self.run_cmd(ContentAdmin.cond_clear, "없는약"))

    async def test_everything_is_admin_only(self):
        for command, args, kwargs in ((ContentAdmin.cond_add, ("소형 배터리", Choice("guard_fail")), {}), (ContentAdmin.cond_delete, ("소형 배터리", 1), {}),
                                      (ContentAdmin.cond_clear, ("소형 배터리",), {}), (ContentAdmin.cond_list, (), {})):
            self.assertIn("관리자만", await self.run_cmd(command, *args, admin=False, **kwargs))
        self.assertEqual(self.item("소형 배터리").conditions, ())

    async def test_a_weapon_or_item_in_use_by_a_consumable_cannot_be_deleted(self):
        await self.run_cmd(ContentAdmin.potion_add, "개조약", "🔧", Choice("희귀"), 1, modify="플라즈마 블레이드")
        text = await self.run_cmd(ContentAdmin.gear_delete, "플라즈마 블레이드")
        self.assertIn("개조 목표", text)
        self.assertTrue(any(g.name == "플라즈마 블레이드" for g in self.store.balance(G).gear))
        await self.run_cmd(ContentAdmin.cond_add, "소형 배터리", Choice("item_use"), other="정비 모듈")
        text = await self.run_cmd(ContentAdmin.potion_delete, "정비 모듈")
        self.assertIn("사용 조건", text)
        await self.run_cmd(ContentAdmin.potion_delete, "개조약")
        await self.run_cmd(ContentAdmin.gear_delete, "플라즈마 블레이드")                                       # 쓰는 곳이 사라지면 지워진다
        self.assertFalse(any(g.name == "플라즈마 블레이드" for g in self.store.balance(G).gear))
        await self.run_cmd(ContentAdmin.cond_clear, "소형 배터리")
        await self.run_cmd(ContentAdmin.potion_delete, "정비 모듈")
        self.assertFalse(any(c.name == "정비 모듈" for c in self.store.balance(G).consumables))

    async def test_the_dungeon_list_shows_conditions_and_the_modify_target(self):
        await self.run_cmd(ContentAdmin.potion_add, "개조약", "🔧", Choice("희귀"), 1, modify="플라즈마 블레이드")
        await self.run_cmd(ContentAdmin.cond_add, "개조약", Choice("hp_le"), value=25)
        text = await self.run_cmd(ContentAdmin.dungeon_list, Choice("consumables"))
        self.assertIn("플라즈마 블레이드(으)로 개조", text)
        self.assertIn("조건: 내 HP 25% 이하", text)

    def test_the_new_options_are_declared_with_the_right_types(self):
        kind = next(p for p in ContentAdmin.cond_add.parameters if p.name == "value")
        self.assertEqual((kind.type.name, kind.min_value, kind.max_value), ("integer", 0, 100))
        for command in (ContentAdmin.potion_add, ContentAdmin.potion_edit):
            self.assertEqual(next(p for p in command.parameters if p.name == "modify").type.name, "string", command.name)


class ScreenAndSimulatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_item_menu_marks_locked_and_ready_items(self):
        bal = bal_with(potion(key="a", name="잠김약", conditions=[["hp_le", 10]]), potion(key="b", name="준비약", conditions=[["hp_ge", 10]]),
                       potion(key="c", name="보통약"))
        run = dungeon.new_run(bal=bal)
        run.items = {"a": 1, "b": 1, "c": 1}
        select = ItemSelect(run, bal)
        desc = {o.value: o.description for o in select.options}
        self.assertTrue(desc["a"].startswith("🔒"))
        self.assertIn("내 HP 10% 이하", desc["a"])
        self.assertTrue(desc["b"].startswith("✨"))
        self.assertFalse(desc["c"].startswith(("🔒", "✨")))
        embed = run_embed(run, None, bal)
        shown = (embed.description or "") + "".join(f.value for f in embed.fields)
        self.assertIn("잠김약 ×1 🔒", shown)
        self.assertIn("준비약 ×1 ✨", shown)

    def test_the_summary_names_the_target_weapon(self):
        bal = bal_with(potion(heal=0, modify="longsword", conditions=[["guard_fail", None]]))
        text = item_summary(bal.item_by_key["p"], bal)
        self.assertIn("플라즈마 블레이드", text)
        self.assertIn("개조", text)
        self.assertIn("방어 실패 직후", text)

    def test_the_simulated_player_never_tries_conditional_or_modify_items(self):
        bal = bal_with(potion(key="a", name="조건약", heal=900, conditions=[["hp_le", 90]]), potion(key="m", name="개조약", heal=0, modify="longsword"))
        run = dungeon.new_run(bal=bal)
        run.items = {"a": 1, "m": 1}
        self.assertIsNone(balance_sim._best_heal(run, bal))
        result = balance_sim.simulate(bal, 30, seed=1)                                            # 이런 소모품이 상자에서 나와도 시뮬레이션이 멈추지 않는다
        self.assertIn("attack", result)
