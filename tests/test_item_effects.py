"""소모품의 새 효과: 몬스터에게 주는 피해·지속 피해, 자해, 무적, 피해 감소. 규칙·검증·명령어·화면·서비스."""
import json
import unittest

import balance
import balance_sim
import dungeon
import dungeon_auto
import dungeon_service as service
from balance import Balance, check_entity, entity_dict
from cogs.content_admin import ContentAdmin
from cogs.dungeon import run_embed
from dungeon import DungeonError, Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})
MAX = balance.DEFAULT.max_hp


def potion(**kw):
    data = dict(key="p", name="비약", emoji="🧪", tier="일반", weight=1)
    data.update(kw)
    return data


def bal_with(*items):
    return Balance.build({}, {"consumables": [entity_dict(check_entity("consumables", i)[0]) for i in items]})


def battle(bal, hp=MAX, enemy_hp=1000, damage=100, **items):
    run = dungeon.new_run(bal=bal)
    enemy = dungeon._spawn(balance.MONSTERS[1], 1, bal)
    enemy.update(hp=enemy_hp, max_hp=1000, damage=damage, dodge=0)
    run.phase, run.kind, run.room, run.hp, run.enemy = "battle", "monster", 2, hp, enemy
    run.items = dict(items)
    return run


class ValidationTests(unittest.TestCase):
    def test_each_effect_is_accepted_alone_and_old_items_have_none(self):
        for kw in (dict(damage=300), dict(dot=50, dot_turns=3), dict(self_damage=100), dict(invuln_turns=2), dict(reduce=50, reduce_turns=3)):
            item, problem = check_entity("consumables", potion(**kw))
            self.assertIsNone(problem, kw)
            self.assertTrue(item.description)
        old = check_entity("consumables", potion(heal=30))[0]
        self.assertEqual((old.damage, old.dot, old.self_damage, old.invuln_turns, old.reduce), (0, 0, 0, 0, 0))

    def test_effects_combine_with_healing(self):
        item, problem = check_entity("consumables", potion(heal=100, damage=200, self_damage=50, invuln_turns=1))
        self.assertIsNone(problem)
        self.assertEqual((item.heal, item.damage, item.self_damage, item.invuln_turns), (100, 200, 50, 1))
        for part in ("HP +100", "몬스터에게 200 피해", "내 HP -50", "1턴 동안 피해를 받지 않음"):
            self.assertIn(part, item.description)

    def test_turns_are_required_with_dot_and_reduce_and_extra_turns_are_dropped(self):
        self.assertIn("지속 턴", check_entity("consumables", potion(dot=10))[1])
        self.assertIn("지속 턴", check_entity("consumables", potion(reduce=30))[1])
        item = check_entity("consumables", potion(heal=10, dot_turns=5, reduce_turns=4, self_turns=3))[0]       # 짝이 없는 턴은 0이 된다
        self.assertEqual((item.dot_turns, item.reduce_turns, item.self_turns), (0, 0, 0))
        self.assertEqual(check_entity("consumables", potion(self_damage=10))[0].self_turns, 1)

    def test_some_effect_is_required_and_ranges_are_checked(self):
        self.assertIn("효과가 하나는", check_entity("consumables", potion())[1])
        for kw in (dict(reduce=101, reduce_turns=1), dict(damage=-1, heal=1), dict(invuln_turns=1001), dict(dot=1.5, dot_turns=1), dict(damage=True)):
            self.assertIsNotNone(check_entity("consumables", potion(**kw))[1], kw)

    def test_modify_and_material_items_have_no_other_effects(self):
        for kw in (dict(modify="longsword"), dict(material=True)):
            item = check_entity("consumables", potion(damage=50, invuln_turns=2, **kw))[0]
            self.assertEqual((item.damage, item.invuln_turns), (0, 0))

    def test_round_trip(self):
        item = check_entity("consumables", potion(heal=5, damage=9, dot=4, dot_turns=2, self_damage=3, self_turns=2, invuln_turns=1, reduce=20, reduce_turns=2))[0]
        loaded = check_entity("consumables", json.loads(json.dumps(entity_dict(item))))[0]
        self.assertEqual(loaded, item)


class DamageTests(unittest.TestCase):
    def test_damage_hits_the_monster_and_it_hits_back(self):
        bal = bal_with(potion(damage=300))
        run = battle(bal, p=1)
        events = dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.enemy["hp"], 700)
        self.assertEqual(run.hp, MAX - 100)
        self.assertNotIn("p", run.items)
        self.assertTrue(any("300" in e and "피해를 줬어요" in e for e in events))

    def test_a_killing_blow_ends_the_fight_without_a_counterattack(self):
        bal = bal_with(potion(damage=5000))
        run = battle(bal, p=1)
        events = dungeon.use_consumable(run, "p", bal)
        self.assertEqual((run.phase, run.hp), ("cleared", MAX))
        self.assertTrue(any("쓰러뜨렸어요" in e for e in events))
        self.assertFalse(any("공격!" in e for e in events))

    def test_a_killing_blow_on_a_secret_boss_gives_its_drops(self):
        bal = bal_with(potion(damage=5000))
        run = dungeon.new_run(bal=bal)
        run.phase, run.kind, run.room, run.items = "battle", "secret", 11, {"p": 1}
        run.enemy = dungeon._spawn(bal.secrets[0], 1, bal)
        dungeon.use_consumable(run, "p", bal, Scripted(randoms=[0.0, 0.0]))
        self.assertEqual(run.phase, "cleared")
        self.assertTrue(run.pending_gear or run.items)

    def test_it_cannot_be_used_without_a_monster_and_is_not_spent(self):
        bal = bal_with(potion(damage=10), potion(key="q", name="독약", dot=5, dot_turns=2))
        run = dungeon.new_run(bal=bal)
        run.phase, run.items = "empty", {"p": 1, "q": 1}
        for key in ("p", "q"):
            self.assertFalse(dungeon.can_use_now(run, bal.item_by_key[key], bal))
            with self.assertRaises(DungeonError) as ctx:
                dungeon.use_consumable(run, key, bal)
            self.assertIn("싸우는 중에만", str(ctx.exception))
        self.assertEqual(run.items, {"p": 1, "q": 1})

    def test_with_a_dead_monster_it_cannot_be_used_either(self):
        bal = bal_with(potion(damage=10))
        run = battle(bal, enemy_hp=0, p=1)
        self.assertFalse(dungeon.can_use_now(run, bal.item_by_key["p"], bal))


class DotTests(unittest.TestCase):
    def test_dot_ticks_every_turn_then_ends(self):
        bal = bal_with(potion(dot=50, dot_turns=2))
        run = battle(bal, p=1)
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual((run.enemy["hp"], run.enemy["dot_turns"]), (950, 1))          # 쓴 턴의 끝에 첫 번째 피해
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)                       # 회피 0이라 적중: 공격 20 + 지속 50
        self.assertEqual(run.enemy["hp"], 950 - 20 - 50)
        self.assertEqual(run.enemy["dot_turns"], 0)
        hp = run.enemy["hp"]
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.enemy["hp"], hp - 20)                                      # 끝났다

    def test_dot_can_kill_and_a_dead_monster_stops_ticking(self):
        bal = bal_with(potion(dot=500, dot_turns=5))
        run = battle(bal, enemy_hp=300, p=1)
        events = dungeon.use_consumable(run, "p", bal)
        self.assertEqual((run.phase, run.enemy["hp"]), ("cleared", 0))
        self.assertTrue(any("쓰러뜨렸어요" in e for e in events))
        self.assertEqual(run.enemy["dot_turns"], 4)                                     # 한 번만 들어갔다

    def test_using_it_again_restarts_instead_of_stacking(self):
        bal = bal_with(potion(dot=10, dot_turns=3))
        run = battle(bal, p=2)
        dungeon.use_consumable(run, "p", bal)
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual((run.enemy["dot"], run.enemy["dot_turns"]), (10, 2))

    def test_dot_on_a_secret_boss_drops_loot(self):
        bal = bal_with(potion(dot=5000, dot_turns=1))
        run = dungeon.new_run(bal=bal)
        run.phase, run.kind, run.room, run.items = "battle", "secret", 11, {"p": 1}
        run.enemy = dungeon._spawn(bal.secrets[0], 1, bal)
        dungeon.use_consumable(run, "p", bal, Scripted(randoms=[0.0, 0.0]))
        self.assertEqual(run.phase, "cleared")
        self.assertTrue(run.pending_gear or run.items)


class SelfDamageTests(unittest.TestCase):
    def test_it_costs_hp_but_never_kills(self):
        bal = bal_with(potion(damage=10, self_damage=5000))
        run = battle(bal, hp=400, damage=0, p=1)
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.hp, 1)
        self.assertNotEqual(run.phase, "dead")

    def test_it_works_outside_battle_too(self):
        bal = bal_with(potion(self_damage=100))
        run = dungeon.new_run(bal=bal)
        run.phase, run.items = "empty", {"p": 1}
        events = dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.hp, MAX - 100)
        self.assertTrue(any("줄었어요" in e for e in events))

    def test_over_several_turns_the_first_is_immediate_and_the_rest_follow(self):
        bal = bal_with(potion(self_damage=30, self_turns=3))
        run = battle(bal, damage=0, p=1)
        dungeon.use_consumable(run, "p", bal)                                          # 쓴 즉시 -30, 그 턴 끝에 -30
        self.assertEqual((run.hp, run.drain_turns), (MAX - 60, 1))
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual((run.hp, run.drain_turns), (MAX - 90, 0))
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.hp, MAX - 90)

    def test_the_drain_never_goes_below_one(self):
        bal = bal_with(potion(self_damage=500, self_turns=4))
        run = battle(bal, hp=600, damage=0, p=1)
        dungeon.use_consumable(run, "p", bal)
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.hp, 1)
        self.assertNotEqual(run.phase, "dead")


class DefenseBuffTests(unittest.TestCase):
    def test_invulnerability_blocks_that_many_attacks_including_the_one_after_using_it(self):
        bal = bal_with(potion(invuln_turns=2))
        run = battle(bal, p=1)
        events = dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.hp, MAX)
        self.assertTrue(any("무적" in e for e in events))
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.hp, MAX)
        self.assertEqual(run.invuln_turns, 0)
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.hp, MAX - 100)                                             # 세 번째 공격부터는 맞는다

    def test_reduction_applies_after_guard_and_expires(self):
        bal = bal_with(potion(reduce=50, reduce_turns=2))
        run = battle(bal, p=1)
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual(run.hp, MAX - 50)
        dungeon.player_defend(run, False, bal)                                          # 가드 실패 + 감소 50%
        self.assertEqual(run.hp, MAX - 100)
        self.assertEqual((run.reduce_turns, run.reduce_percent), (0, 0))
        dungeon.player_defend(run, False, bal)
        self.assertEqual(run.hp, MAX - 200)

    def test_guard_and_reduction_stack(self):
        bal = bal_with(potion(reduce=50, reduce_turns=3))
        run = battle(bal, hp=500, damage=1000)
        run.reduce_percent, run.reduce_turns = 50, 3
        dungeon.player_defend(run, True, bal)                                           # 기본 가드 60% 감소 → 400, 거기서 50% → 200
        self.assertEqual(run.hp, 500 - 200 + 500)                                       # 맞은 뒤 300, 가드 성공 회복 +500(원래 피해의 50%)
        self.assertEqual(dungeon.incoming_damage(run, 1000, 60), 200)
        self.assertEqual(dungeon.incoming_damage(run, 1000, 0), 500)

    def test_invulnerability_beats_reduction(self):
        run = dungeon.new_run()
        run.invuln_turns, run.reduce_percent, run.reduce_turns = 1, 10, 5
        self.assertEqual(dungeon.incoming_damage(run, 1000, 0), 0)

    def test_buffs_do_not_run_down_outside_battle_and_survive_the_save(self):
        bal = bal_with(potion(invuln_turns=3, reduce=20, reduce_turns=4))
        run = dungeon.new_run(bal=bal)
        run.phase, run.items = "empty", {"p": 1}
        dungeon.use_consumable(run, "p", bal)
        self.assertEqual((run.invuln_turns, run.reduce_turns), (3, 4))
        loaded = Run.from_json(run.to_json())
        self.assertEqual((loaded.invuln_turns, loaded.reduce_percent, loaded.reduce_turns), (3, 20, 4))

    def test_a_stunned_monster_is_not_hit_and_a_later_longer_buff_wins(self):
        bal = bal_with(potion(invuln_turns=5), potion(key="q", name="짧은약", invuln_turns=2))
        run = battle(bal, p=1, q=1)
        dungeon.use_consumable(run, "p", bal)
        dungeon.use_consumable(run, "q", bal)
        self.assertEqual(run.invuln_turns, 3)                                           # 5턴 → 쓴 턴 끝에 4 → 짧은 약(2)으로 덮어쓰지 않고 → 또 한 턴 지나 3


class ScreenAndBotTests(unittest.TestCase):
    def test_the_screen_shows_active_effects_and_locks_hitting_items(self):
        bal = bal_with(potion(invuln_turns=2, reduce=30, reduce_turns=3), potion(key="q", name="폭탄", damage=10))
        run = battle(bal, q=1)
        run.invuln_turns, run.reduce_percent, run.reduce_turns, run.drain_amount, run.drain_turns = 2, 30, 3, 10, 2
        run.enemy.update(dot=5, dot_turns=4)
        embed = run_embed(run, None, bal)
        shown = "".join(f.value for f in embed.fields)
        for part in ("무적 (남은 2턴)", "30% 감소", "HP -10", "몬스터가 턴마다 5"):
            self.assertIn(part, shown)
        self.assertIn("폭탄 ×1 ✨", shown)
        run.phase = "empty"
        self.assertIn("폭탄 ×1 🔒", "".join(f.value for f in run_embed(run, None, bal).fields))

    def test_the_auto_player_and_simulator_never_use_non_healing_effects(self):
        bal = bal_with(potion(damage=10), potion(key="q", name="독", dot=5, dot_turns=2), potion(key="r", name="피의약", heal=500, self_damage=10))
        run = battle(bal, hp=50, p=1, q=1, r=1)
        self.assertEqual(dungeon_auto._plain_heals(run, bal), [])
        self.assertIsNone(balance_sim._best_heal(run, bal))
        self.assertIn("attack", balance_sim.simulate(bal, 20, seed=1))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    def tearDown(self):
        self.store.close()

    async def test_a_bomb_kills_through_the_service_and_a_failed_use_costs_nothing(self):
        import content
        content.add(self.store, G, "consumables", dict(name="폭탄", emoji="💣", tier="희귀", weight=1, damage=5000))
        bal = self.store.balance(G)
        key = next(c.key for c in bal.consumables if c.name == "폭탄")
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(phase="empty", kind="empty", room=2, items={key: 1})
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        out = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, rev, "item", key)
        self.assertFalse(out.ok)                                                        # 몬스터가 없을 때
        run = Run.from_json(self.store.dungeon_run(G, U))
        self.assertEqual(run.items, {key: 1})
        enemy = dungeon._spawn(balance.MONSTERS[1], 1, bal)
        data = json.loads(run.to_json())
        data.update(phase="battle", kind="monster", enemy=enemy)
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        out = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, rev, "item", key)
        self.assertTrue(out.ok)
        self.assertEqual(out.run.phase, "cleared")


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

    async def test_add_with_every_new_option(self):
        text = await self.run_cmd(ContentAdmin.potion_add, "저주약", "☠️", Choice("전설"), 1, damage=300, dot=40, dot_turns=3, self_damage=80,
                                  self_turns=2, invuln_turns=1, reduce=25, reduce_turns=2)
        self.assertIn("저주약", text)
        item = self.item("저주약")
        self.assertEqual((item.damage, item.dot, item.dot_turns, item.self_damage, item.self_turns, item.invuln_turns, item.reduce, item.reduce_turns),
                         (300, 40, 3, 80, 2, 1, 25, 2))

    async def test_add_explains_missing_turns_and_no_effect(self):
        self.assertIn("지속 턴", await self.run_cmd(ContentAdmin.potion_add, "독약", "🧪", Choice("일반"), 1, dot=10))
        self.assertIn("효과가 하나는", await self.run_cmd(ContentAdmin.potion_add, "물", "🧪", Choice("일반"), 1))
        self.assertIn("함께 쓸 수 없어요", await self.run_cmd(ContentAdmin.potion_add, "재료약", "🧪", Choice("일반"), 1, material=True, damage=5))
        self.assertIn("함께 쓸 수 없어요", await self.run_cmd(ContentAdmin.potion_add, "개조약", "🧪", Choice("일반"), 1, modify="롱소드", invuln_turns=1))
        self.assertFalse(any(c.name in ("독약", "물", "재료약", "개조약") for c in self.store.balance(G).consumables))

    async def test_edit_adds_changes_and_clears_effects_without_touching_healing(self):
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", invuln_turns=2, reduce=40, reduce_turns=3)
        item = self.item("사탕")
        self.assertEqual((item.heal, item.invuln_turns, item.reduce, item.reduce_turns), (50, 2, 40, 3))
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", invuln_turns=0, reduce=0)
        item = self.item("사탕")
        self.assertEqual((item.heal, item.invuln_turns, item.reduce, item.reduce_turns), (50, 0, 0, 0))

    async def test_edit_with_a_new_effect_turns_a_modify_item_into_a_normal_one(self):
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", modify="단검")
        await self.run_cmd(ContentAdmin.potion_edit, "사탕", damage=77)
        item = self.item("사탕")
        self.assertEqual((item.modify, item.damage), (None, 77))

    async def test_the_list_describes_the_effects_and_admin_only(self):
        await self.run_cmd(ContentAdmin.potion_add, "폭탄", "💣", Choice("희귀"), 1, damage=300)
        import content
        line = content.describe("consumables", next(d for d in content.current(self.store, G, "consumables") if d["name"] == "폭탄"), self.store.balance(G))
        self.assertIn("몬스터에게 300 피해", line)
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.potion_add, "폭탄2", "💣", Choice("희귀"), 1, damage=3, admin=False))

    def test_option_names_fit_discords_limits(self):
        for command in (ContentAdmin.potion_add, ContentAdmin.potion_edit):
            self.assertLessEqual(len(command.parameters), 25)
            self.assertTrue({"몬스터피해", "지속피해", "자해", "무적턴", "피해감소"} <= {p.display_name for p in command.parameters})
