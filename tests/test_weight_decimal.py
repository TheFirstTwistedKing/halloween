"""장비·소모품의 등장 비율(weight)을 소수로 정하는 기능 시험: 검사, 명령어, 저장, 실제 추첨에 반영되는지."""
import json
import random
import unittest

import balance
import dungeon
from balance import Balance, check_entity, check_value, entity_dict
from cogs.content_admin import ContentAdmin
from tests.helpers import World, sent_embed

G = 100
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


def gear(weight):
    return dict(key="g", name="활", emoji="🏹", slot="weapon", tier="일반", weight=weight, attack=5)


def potion(weight):
    return dict(key="p", name="약", emoji="💊", tier="일반", weight=weight, heal=10)


class WeightValidationTests(unittest.TestCase):
    def test_decimals_are_accepted_for_gear_and_consumables(self):
        for value in (0.5, 0.05, 0.0001, 12.75, 999.9999):
            for section, data in (("gear", gear(value)), ("consumables", potion(value))):
                entity, problem = check_entity(section, data)
                self.assertIsNone(problem, (section, value))
                self.assertEqual(entity.weight, value)

    def test_whole_numbers_stay_ints_and_round_trip_through_json(self):
        entity, _ = check_entity("gear", gear(3.0))
        self.assertIsInstance(entity.weight, int)
        entity, _ = check_entity("gear", gear(0.25))
        loaded, problem = check_entity("gear", json.loads(json.dumps(entity_dict(entity))))
        self.assertIsNone(problem)
        self.assertEqual(loaded, entity)

    def test_more_than_four_decimals_are_rounded_and_tiny_values_are_refused(self):
        entity, _ = check_entity("gear", gear(0.123456))
        self.assertEqual(entity.weight, 0.1235)
        _, problem = check_entity("gear", gear(0.00001))
        self.assertIn("너무 작아요", problem)
        entity, _ = check_entity("gear", gear(0))                       # 0은 '안 나옴'이라 그대로 허용
        self.assertEqual(entity.weight, 0)

    def test_bad_numbers_are_refused_not_crashing(self):
        for value in (-0.1, 1000.01, float("nan"), float("inf"), float("-inf"), True, "3", None):
            for section, data in (("gear", gear(value)), ("consumables", potion(value))):
                entity, problem = check_entity(section, data)
                self.assertIsNone(entity, (section, value))
                self.assertIn("weight", problem)

    def test_dungeon_settings_accept_decimal_drop_weights(self):
        key = f"장비.{balance.GEAR[0].name}.등장"
        self.assertEqual(check_value(key, 0.5), (0.5, None))
        key = f"아이템.{balance.CONSUMABLES[0].name}.등장"
        self.assertEqual(check_value(key, 2.25), (2.25, None))
        self.assertIsNotNone(check_value(key, 1000.5)[1])
        self.assertEqual(balance.clean({key: 0.75}), {key: 0.75})        # 저장됐다 다시 읽어도 소수가 살아남는다
        bal = Balance.build({f"장비.{balance.GEAR[0].name}.등장": 0.5})
        self.assertEqual(bal.gear[0].weight, 0.5)


class DecimalChanceTests(unittest.TestCase):
    def test_a_decimal_weight_really_changes_how_often_it_drops(self):
        """낡은 몽둥이 0.5 : 단검 1.5 → 몽둥이는 25% 안팎으로 나와야 한다."""
        changes = {f"장비.{g.name}.등장": 0 for g in dungeon.GEAR}
        changes.update({"장비.낡은 몽둥이.등장": 0.5, "장비.단검.등장": 1.5})
        bal = Balance.build(changes)
        rng, counts = random.Random(7), {}
        for _ in range(20000):
            key = dungeon.pick_weighted(rng, bal.gear).key
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(set(counts), {"club", "dagger"})
        self.assertAlmostEqual(counts["club"] / 20000, 0.25, delta=0.015)

    def test_boundary_with_decimal_weights(self):
        changes = {f"장비.{g.name}.등장": 0 for g in dungeon.GEAR}
        changes.update({"장비.낡은 몽둥이.등장": 0.25, "장비.단검.등장": 0.75})
        bal = Balance.build(changes)
        for draw, expected in ((0.0, "club"), (0.2499, "club"), (0.25, "dagger"), (0.9999, "dagger")):
            run = dungeon.new_run(bal=bal)
            self.assertEqual(dungeon.pick_weighted(type("R", (), {"random": lambda self, d=draw: d})(), bal.gear).key, expected, draw)


class DecimalCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def run_cmd(self, command, interaction, *args, **kwargs):
        await command.callback(self.cog, interaction, *args, **kwargs)
        return interaction

    def text(self, interaction):
        embed, _ = sent_embed(interaction)
        return (embed.description or "") + "\n".join(f.value for f in embed.fields)

    async def test_gear_add_and_edit_take_decimal_weight(self):
        i = await self.run_cmd(ContentAdmin.gear_add, self.ask(), "전설 활", "🏹", Choice("weapon"), Choice("전설"), 0.35, attack=40)
        self.assertIn("등장 0.35", self.text(i))
        saved = next(g for g in self.store.balance(G).gear if g.name == "전설 활")
        self.assertEqual(saved.weight, 0.35)
        i = await self.run_cmd(ContentAdmin.gear_edit, self.ask(), "전설 활", weight=0.0125)
        self.assertIn("등장 0.0125", self.text(i))
        self.assertEqual(next(g for g in self.store.balance(G).gear if g.name == "전설 활").weight, 0.0125)
        i = await self.run_cmd(ContentAdmin.gear_edit, self.ask(), "전설 활", weight=4.0)       # 정수로 떨어지면 정수로 저장
        self.assertIsInstance(next(g for g in self.store.balance(G).gear if g.name == "전설 활").weight, int)

    async def test_potion_add_and_edit_take_decimal_weight(self):
        i = await self.run_cmd(ContentAdmin.potion_add, self.ask(), "비약", "🧪", Choice("희귀"), 0.6, heal=100)
        self.assertIn("등장 0.6", self.text(i))
        await self.run_cmd(ContentAdmin.potion_edit, self.ask(), "비약", weight=0.07)
        self.assertEqual(next(c for c in self.store.balance(G).consumables if c.name == "비약").weight, 0.07)

    async def test_too_small_weight_through_the_command_is_refused_with_a_reason(self):
        i = await self.run_cmd(ContentAdmin.gear_add, self.ask(), "먼지", "🏹", Choice("weapon"), Choice("일반"), 0.00001, attack=1)
        self.assertIn("너무 작아요", self.text(i))
        self.assertFalse(any(g.name == "먼지" for g in self.store.balance(G).gear))

    def test_command_options_are_declared_as_decimals(self):
        for command in (ContentAdmin.gear_add, ContentAdmin.gear_edit, ContentAdmin.potion_add, ContentAdmin.potion_edit):
            weight = next(p for p in command.parameters if p.name == "weight")
            self.assertEqual(weight.type.name, "number", command.name)          # 디스코드의 소수 옵션 형식
            self.assertEqual((weight.min_value, weight.max_value), (0, 1000), command.name)
