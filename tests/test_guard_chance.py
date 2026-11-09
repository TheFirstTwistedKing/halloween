"""방패마다 가드 성공률을 따로 정하는 기능 시험: 검사, 판정, 서비스 경로, 명령어, 경고."""
import json
import unittest

import balance
import content
import dungeon
import dungeon_service as service
from balance import Balance, check_entity, entity_dict
from cogs.content_admin import ContentAdmin
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


def shield(chance=None, **kw):
    data = dict(key="s", name="강철 방패", emoji="🛡️", slot="shield", tier="일반", weight=1, guard=50, guard_chance=chance)
    data.update(kw)
    return data


def bal_with_shield(chance):
    return Balance.build({}, {"gear": [entity_dict(check_entity("gear", shield(chance))[0])]})


class ValidationTests(unittest.TestCase):
    def test_a_shield_can_have_its_own_chance_and_default_shields_have_none(self):
        entity, problem = check_entity("gear", shield(25))
        self.assertIsNone(problem)
        self.assertEqual(entity.guard_chance, 25)
        self.assertIn("가드 성공률 25%", entity.description)
        self.assertEqual([g.guard_chance for g in balance.GEAR if g.slot == "shield"], [None, None])   # 기본 방패는 서버 기본값을 따른다
        for chance in (0, 100):
            self.assertEqual(check_entity("gear", shield(chance))[0].guard_chance, chance)

    def test_bad_values_are_refused_and_weapons_ignore_it(self):
        for chance in (-1, 101, 12.5, True, "30"):
            entity, problem = check_entity("gear", shield(chance))
            self.assertIsNone(entity, chance)
            self.assertIn("guard_chance", problem)
        weapon, problem = check_entity("gear", dict(key="w", name="칼", emoji="🗡️", slot="weapon", tier="일반", weight=1, attack=5, guard_chance=30))
        self.assertIsNone(problem)
        self.assertIsNone(weapon.guard_chance)

    def test_it_survives_save_and_load_and_old_saves_without_it_still_load(self):
        entity, _ = check_entity("gear", shield(40))
        loaded, problem = check_entity("gear", json.loads(json.dumps(entity_dict(entity))))
        self.assertIsNone(problem)
        self.assertEqual(loaded, entity)
        old = shield()
        old.pop("guard_chance")
        self.assertIsNone(check_entity("gear", old)[0].guard_chance)


class RollTests(unittest.TestCase):
    def test_each_shield_rolls_with_its_own_chance(self):
        bal = Balance.build({}, {"gear": [entity_dict(check_entity("gear", shield(25, key="a", name="약한"))[0]),
                                          entity_dict(check_entity("gear", shield(90, key="b", name="강한"))[0]),
                                          entity_dict(check_entity("gear", shield(None, key="c", name="보통"))[0])]})
        weak, strong, normal = (dungeon.new_run(shield=k, bal=bal) for k in "abc")
        self.assertEqual([dungeon.guard_success_percent(r, bal) for r in (weak, strong, normal)], [25, 90, 60])
        for draw, expected in ((0.2499, (True, True, True)), (0.25, (False, True, True)), (0.5999, (False, True, True)),
                                (0.6, (False, True, False)), (0.8999, (False, True, False)), (0.9, (False, False, False))):
            got = tuple(dungeon.roll_guard(Scripted(randoms=[draw]), bal, r) for r in (weak, strong, normal))
            self.assertEqual(got, expected, draw)

    def test_without_a_shield_or_without_a_run_the_server_default_is_used(self):
        bal = Balance.build({"방어.성공확률": 30})
        self.assertEqual(dungeon.guard_success_percent(dungeon.new_run(bal=bal), bal), 30)
        self.assertEqual(dungeon.guard_success_percent(None, bal), 30)
        self.assertTrue(dungeon.roll_guard(Scripted(randoms=[0.2999]), bal))
        self.assertFalse(dungeon.roll_guard(Scripted(randoms=[0.3]), bal))
        self.assertTrue(dungeon.roll_guard(Scripted(randoms=[0.5999])))              # 예전 호출 방식(run 없이)도 그대로

    def test_a_deleted_shield_falls_back_to_the_default(self):
        run = dungeon.new_run(shield="gone")
        self.assertEqual(dungeon.guard_success_percent(run, bal_with_shield(10)), 60)


class WarningTests(unittest.TestCase):
    def test_a_zero_percent_shield_is_warned_about(self):
        self.assertTrue(any("강철 방패" in w and "0%" in w for w in balance.warnings(bal_with_shield(0))))
        self.assertFalse(any("0%" in w for w in balance.warnings(bal_with_shield(10))))

    def test_farming_check_uses_each_shields_own_chance(self):
        base = {"방어.회복률": 500, "방어.기본피해감소": 100}
        easy = Balance.build(base, {"gear": [entity_dict(check_entity("gear", shield(100, guard=100))[0])]})
        hard = Balance.build({**base, "방어.성공확률": 100}, {"gear": [entity_dict(check_entity("gear", shield(0, guard=0))[0])]})
        self.assertTrue(any("강철 방패" in w for w in balance.warnings(easy) if "반복" in w))
        self.assertFalse(any("강철 방패" in w for w in balance.warnings(hard) if "반복" in w))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    def tearDown(self):
        self.store.close()

    async def defend(self, chance, draw, **kw):
        content.add(self.store, G, "gear", dict(name=f"내 방패{chance}", emoji="🛡️", slot="shield", tier="일반", weight=1, guard=50, guard_chance=chance))
        key = next(g.key for g in self.store.balance(G).gear if g.name == f"내 방패{chance}")
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        enemy = dungeon._spawn(balance.MONSTERS[1], 1, balance.DEFAULT)
        data.update(phase="battle", kind="monster", room=2, shield=key, enemy=enemy)
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = dungeon.Run.from_json(self.store.dungeon_run(G, U)).rev
        return await service.act(self.store, self.locks, Scripted(randoms=[draw]), G, U, rev, "defend", **kw)

    async def test_the_defend_button_rolls_with_the_equipped_shields_chance(self):
        out = await self.defend(25, 0.3)
        self.assertIn("가드 실패", "\n".join(out.run.events))
        out = await self.defend(40, 0.3)                                                          # 같은 난수, 다른 방패 → 성공
        self.assertIn("가드 성공", "\n".join(out.run.events))

    async def test_an_explicit_result_is_still_honored(self):
        out = await self.defend(0, 0.99, guard_ok=True)
        self.assertIn("가드 성공", "\n".join(out.run.events))
        out = await self.defend(100, 0.0, guard_ok=False)
        self.assertIn("가드 실패", "\n".join(out.run.events))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, **kwargs):
        i = self.w.interaction(admin=True)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def chance_of(self, name):
        return next(g.guard_chance for g in self.store.balance(G).gear if g.name == name)

    async def test_gear_add_and_edit_set_and_clear_the_chance(self):
        text = await self.run_cmd(ContentAdmin.gear_add, "수호 방패", "🛡️", Choice("shield"), Choice("희귀"), 3, guard=80, guard_chance=35)
        self.assertIn("가드 성공률 35%", text)
        self.assertEqual(self.chance_of("수호 방패"), 35)
        await self.run_cmd(ContentAdmin.gear_edit, "수호 방패", guard_chance=90)
        self.assertEqual(self.chance_of("수호 방패"), 90)
        await self.run_cmd(ContentAdmin.gear_edit, "수호 방패", guard_chance=0)                   # 0%는 해제가 아니라 진짜 0%
        self.assertEqual(self.chance_of("수호 방패"), 0)
        await self.run_cmd(ContentAdmin.gear_edit, "수호 방패", guard_chance=-1)
        self.assertIsNone(self.chance_of("수호 방패"))

    async def test_default_shields_can_be_given_a_chance_too(self):
        await self.run_cmd(ContentAdmin.gear_edit, "에너지 실드", guard_chance=75)
        self.assertEqual(self.chance_of("에너지 실드"), 75)
        self.assertIsNone(self.chance_of("구리 방열판"))                                              # 다른 방패는 그대로

    def test_options_are_declared_as_integers_in_range(self):
        for command, low in ((ContentAdmin.gear_add, 0), (ContentAdmin.gear_edit, -1)):
            option = next(p for p in command.parameters if p.name == "guard_chance")
            self.assertEqual((option.type.name, option.min_value, option.max_value), ("integer", low, 100), command.name)
