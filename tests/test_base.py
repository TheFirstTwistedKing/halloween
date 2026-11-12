"""기지: 보관함의 장비를 종류마다 하나씩 전시하고(던전에는 못 가져감), 능력치 일부를 던전에서 패시브로 받는다."""
import json
import unittest

import dungeon
import dungeon_service as service
from balance import ARMOR_MAX, DEFAULT, Balance, base_bonus
from cogs.base import EMPTY, Base, BaseView, base_embed
from cogs.dungeon import run_embed
from dungeon import Run
from services import Locks
from store import Store, StoreError
from tests.helpers import World, sent_embed

G, U = 100, 7
B = DEFAULT.gear_by_key


class BonusTests(unittest.TestCase):
    def test_each_slot_gives_its_part(self):
        sword, shield, armor = B["longsword"], B["shield"], B["chain_armor"]
        got = base_bonus({"weapon": "longsword", "shield": "shield", "armor": "chain_armor"}, DEFAULT)
        self.assertEqual(got, {"attack": sword.attack * 10 // 100, "guard": shield.guard * 10 // 100,
                               "reflect": shield.reflect * 10 // 100, "reduction": armor.reduction * 10 // 100})
        self.assertGreater(got["attack"], 0)

    def test_percent_setting_and_bad_entries(self):
        full = Balance.build({"기지.패시브": 100})
        self.assertEqual(base_bonus({"weapon": "longsword"}, full)["attack"], B["longsword"].attack)
        armor = base_bonus({"armor": "chain_armor"}, full)["reduction"]
        self.assertEqual(armor, B["chain_armor"].reduction)
        self.assertGreater(armor, 0)
        self.assertEqual(base_bonus({"weapon": "longsword"}, Balance.build({"기지.패시브": 0}))["attack"], 0)
        self.assertEqual(base_bonus({"weapon": "shield", "armor": "gone"}, full), {"attack": 0, "guard": 0, "reflect": 0, "reduction": 0})
        self.assertEqual(base_bonus({}, full)["attack"], 0)


class CombatTests(unittest.TestCase):
    def test_the_passive_adds_to_attack_guard_reflect_and_reduction(self):
        run = dungeon.new_run()
        plain = (dungeon.player_damage(run), dungeon.guard_percent(run), dungeon.reflect_percent(run), dungeon.armor_percent(run))
        run.base_attack, run.base_guard, run.base_reflect, run.base_reduction = 7, 5, 4, 3
        self.assertEqual((dungeon.player_damage(run), dungeon.guard_percent(run), dungeon.reflect_percent(run), dungeon.armor_percent(run)),
                         (plain[0] + 7, plain[1] + 5, plain[2] + 4, 3))                 # 갑옷이 없어도 피해 감소가 붙는다
        run.armor = "chain_armor"
        self.assertEqual(dungeon.armor_percent(run), B["chain_armor"].reduction + 3)

    def test_caps(self):
        run = dungeon.new_run()
        run.base_guard, run.base_reflect, run.base_reduction = 1000, 1000, 1000
        self.assertEqual((dungeon.guard_percent(run), dungeon.reflect_percent(run), dungeon.armor_percent(run)), (100, 500, ARMOR_MAX))

    def test_old_saves_read_as_no_passive(self):
        data = json.loads(dungeon.new_run().to_json())
        for key in ("base_attack", "base_guard", "base_reflect", "base_reduction"):
            data.pop(key)
        run = Run.from_json(json.dumps(data))
        self.assertEqual((run.base_attack, run.base_guard, run.base_reflect, run.base_reduction), (0, 0, 0, 0))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_display_swap_and_clear_move_gear_between_vault_and_base(self):
        s = self.store
        s.vault_put(G, U, [("gear", "dagger", 1), ("gear", "longsword", 1)])
        s.set_display(G, U, "weapon", "dagger")
        self.assertEqual((s.base(G, U), s.vault(G, U)["gear"]), ({"weapon": "dagger"}, {"longsword": 1}))
        s.set_display(G, U, "weapon", "dagger")                                        # 같은 것: 그대로
        self.assertEqual(s.vault(G, U)["gear"], {"longsword": 1})
        s.set_display(G, U, "weapon", "longsword")
        self.assertEqual((s.base(G, U), s.vault(G, U)["gear"]), ({"weapon": "longsword"}, {"dagger": 1}))
        s.set_display(G, U, "weapon", None)
        self.assertEqual((s.base(G, U), s.vault(G, U)["gear"]), ({}, {"dagger": 1, "longsword": 1}))
        self.assertEqual(s.base(G, 8), {})

    def test_refusals_change_nothing(self):
        s = self.store
        s.vault_put(G, U, [("gear", "dagger", 1)])
        s.set_display(G, U, "weapon", "dagger")
        with self.assertRaises(StoreError):
            s.set_display(G, U, "weapon", "longsword")                                 # 보관함에 없다
        self.assertEqual(s.base(G, U), {"weapon": "dagger"})
        s.vault_put(G, U, [("gear", "club", 30)])
        with self.assertRaises(StoreError):
            s.set_display(G, U, "weapon", None)                                        # 돌려놓을 자리가 없다
        self.assertEqual((s.base(G, U), s.vault(G, U)["gear"]), ({"weapon": "dagger"}, {"club": 30}))
        s.set_display(G, U, "weapon", "club")                                          # 바꾸기는 하나 빼고 하나 넣으니 된다
        self.assertEqual((s.base(G, U), s.vault(G, U)["gear"]), ({"weapon": "club"}, {"club": 29, "dagger": 1}))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 5)
        self.store.vault_put(G, U, [("gear", "longsword", 1), ("gear", "shield", 1)])
        self.store.set_display(G, U, "weapon", "longsword")
        self.store.set_display(G, U, "shield", "shield")

    def tearDown(self):
        self.store.close()

    async def test_the_passive_is_set_when_entering_and_display_cannot_come_along(self):
        self.store.set_loadout(G, U, {"weapon": "longsword", "shield": "shield"})     # 전시한 것을 골라 둬도
        out = await service.enter(self.store, self.locks, G, U)
        run = out.run
        self.assertEqual((run.weapon, run.shield), (None, None))                       # 가져가지 못한다
        want = base_bonus({"weapon": "longsword", "shield": "shield"}, DEFAULT)
        self.assertEqual((run.base_attack, run.base_guard, run.base_reflect), (want["attack"], want["guard"], want["reflect"]))
        self.assertTrue(any("기지 패시브" in e for e in run.events))
        self.assertIn("🏠 기지 패시브", [f.name for f in run_embed(run, None, DEFAULT).fields])
        self.store.set_display(G, U, "weapon", None)                                   # 던전 중에 바꿔도 이번 던전은 그대로
        again = await service.enter(self.store, self.locks, G, U)
        self.assertEqual(again.run.base_attack, want["attack"])
        self.assertFalse(any("기지 패시브" in e for e in again.run.events))

    async def test_no_display_no_passive_line(self):
        self.store.set_display(G, U, "weapon", None)
        self.store.set_display(G, U, "shield", None)
        out = await service.enter(self.store, self.locks, G, U)
        self.assertEqual(out.run.base_attack, 0)
        self.assertFalse(any("기지 패시브" in e for e in out.run.events))
        self.assertNotIn("🏠 기지 패시브", [f.name for f in run_embed(out.run, None, DEFAULT).fields])


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.store.vault_put(G, U, [("gear", "dagger", 1), ("gear", "longsword", 1), ("gear", "chain_armor", 1)])

    async def asyncTearDown(self):
        await self.w.close()

    def selects(self, view):
        return [c for c in view.children if hasattr(c, "options")]

    async def test_command_and_screen(self):
        i = self.w.interaction(uid=U)
        await Base.base.callback(Base(self.bot), i)
        embed, _ = sent_embed(i)
        self.assertIn("기지", embed.title)
        view = i.response.send_message.call_args.kwargs["view"]
        self.assertEqual(len(self.selects(view)), 2)                                   # 무기·갑옷(방패는 보관함에 없다)
        view.to_components()

    async def test_display_and_empty_through_the_menu(self):
        weapon = self.selects(BaseView(self.bot, U, G))[0]
        i = self.w.interaction(uid=U, data={"values": ["longsword"]})
        await weapon.callback(i)
        self.assertEqual(self.store.base(G, U), {"weapon": "longsword"})
        embed = i.response.edit_message.call_args.kwargs["embed"]
        self.assertIn("전시했어요", embed.description)
        self.assertIn(f"공격 +{B['longsword'].attack // 10}", next(f.value for f in embed.fields if "받는 패시브" in f.name))
        view = BaseView(self.bot, U, G)
        values = [o.value for o in self.selects(view)[0].options]
        self.assertEqual(values[:2], ["longsword", EMPTY])                             # 전시 중인 것 + 비우기 + 보관함의 다른 무기
        self.assertIn("dagger", values)
        i = self.w.interaction(uid=U, data={"values": [EMPTY]})
        await self.selects(view)[0].callback(i)
        self.assertEqual(self.store.base(G, U), {})
        self.assertIn("돌려놨어요", i.response.edit_message.call_args.kwargs["embed"].description)

    async def test_wrong_slot_and_others(self):
        weapon = self.selects(BaseView(self.bot, U, G))[0]
        i = self.w.interaction(uid=U, data={"values": ["chain_armor"]})                # 무기 칸에 갑옷
        await weapon.callback(i)
        self.assertEqual(self.store.base(G, U), {})
        self.assertIn("⚠️", i.response.edit_message.call_args.kwargs["embed"].description)
        self.assertFalse(await BaseView(self.bot, U, G).interaction_check(self.w.interaction(uid=8)))

    def test_embed_lists_each_stand(self):
        self.store.set_display(G, U, "armor", "chain_armor")
        names = {f.name: f.value for f in base_embed(self.store, G, U).fields}
        self.assertIn("비어 있어요", names["⚔️ 무기 전시대"])
        self.assertIn(B["chain_armor"].name, names["🧥 갑옷 전시대"])


if __name__ == "__main__":
    unittest.main()
