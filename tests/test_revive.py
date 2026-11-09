"""부활권: 패키지에서 0.1%로 나오고, 던전에서 다운됐을 때 가방에 있으면 한 장 쓰여서 그 자리에서 되살아난다."""
import json
import unittest

import balance
import dungeon
import dungeon_service as service
from balance import Balance, check_value
from cogs.dungeon import run_embed
from services import Locks
from store import (MAX_PACKAGE_ENTRIES, NEW_ITEMS, REVIVE_PERCENT, Store, package_table_v1, roll_package_result,
                   table_with_revive)
from tests.helpers import Scripted

G, U = 100, 7


def v1_state(store, guild=G):
    """버전 1까지만 이전된 서버(부활권이 없고 표가 버전 1 기본값)로 되돌린다."""
    store.ensure_defaults(guild)
    revive = store.revive_item(guild)
    ids = store.ensure_system_items(guild)
    new_ids = {system: ids[system] for system, *_ in NEW_ITEMS}
    with store.db:
        store.db.execute("DELETE FROM items WHERE id=?", (revive["id"],))
    store.update_settings(guild, seed_version=1, package_table=package_table_v1(ids, new_ids))
    return ids, new_ids


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_a_new_server_gets_a_revive_ticket_that_drops_at_exactly_a_tenth_of_a_percent(self):
        table = self.store.package_table(G)
        revive = self.store.revive_item(G)
        self.assertEqual((revive["name"], revive["system"], revive["sellable"], revive["buyable"]), ("부활권", "revive", 0, 0))
        entry = next(e for e in table if e["id"] == "revive")
        self.assertEqual((entry["kind"], entry["item"], entry["weight"]), ("item", revive["id"], 0.1))
        self.assertAlmostEqual(sum(e["weight"] for e in table), 100)
        self.assertEqual(next(e for e in table if e["id"] == "none")["weight"], 39.9)             # 꽝에서 0.1을 덜어냈다
        self.assertEqual(REVIVE_PERCENT, 0.1)

    def test_rolling_the_package_gives_it_only_in_the_last_tenth_of_a_percent(self):
        table = self.store.package_table(G)
        self.assertEqual(roll_package_result(table, Scripted([0.9991]))["id"], "revive")
        self.assertEqual(roll_package_result(table, Scripted([0.9989]))["id"], "mystery_bottle")
        hits = sum(1 for i in range(100_000) if roll_package_result(table, Scripted([i / 100_000]))["id"] == "revive")
        self.assertEqual(hits, 100)                                                              # 10만 칸 중 정확히 100칸 = 0.1%

    def test_upgrading_a_server_with_the_old_default_table_swaps_in_the_new_default(self):
        ids, new_ids = v1_state(self.store)
        self.assertIsNone(self.store.revive_item(G))
        self.store.ensure_defaults(G)
        table = self.store.settings(G)["package_table"]
        revive = self.store.revive_item(G)
        self.assertEqual(self.store.settings(G)["seed_version"], 4)
        self.assertEqual([e["id"] for e in table][-1], "revive")
        self.assertEqual(table[-1]["item"], revive["id"])
        self.assertEqual(table[0]["weight"], 39.9)
        self.assertEqual(table[1:-1], package_table_v1(ids, new_ids)[1:])                         # 나머지 칸은 그대로
        snapshot = (table, len(self.store.items(G)))
        self.store.ensure_defaults(G)                                                            # 다시 열어도 한 번만
        self.assertEqual((self.store.settings(G)["package_table"], len(self.store.items(G))), snapshot)

    def test_a_table_the_admin_changed_keeps_its_ratios_and_gets_the_ticket_at_a_tenth_of_a_percent(self):
        v1_state(self.store)
        custom = [{"id": "none", "kind": "none", "weight": 70, "amount": 1}, {"id": "candy", "kind": "candy", "weight": 30, "amount": 5}]
        self.store.update_settings(G, package_table=custom)
        self.store.ensure_defaults(G)
        table = self.store.settings(G)["package_table"]
        self.assertEqual(table[:2], custom)
        self.assertAlmostEqual(table[2]["weight"] / sum(e["weight"] for e in table), 0.001, places=5)

    def test_the_ticket_is_not_added_twice_or_when_there_is_no_room_or_no_table(self):
        table = [{"id": "x", "kind": "item", "item": 5, "weight": 1, "amount": 1}]
        self.assertEqual(table_with_revive(table, 5), table)                                     # 이미 있다
        full = [{"id": f"e{i}", "kind": "none", "weight": 1, "amount": 1} for i in range(MAX_PACKAGE_ENTRIES)]
        self.assertEqual(table_with_revive(full, 9), full)
        self.assertEqual(table_with_revive([], 9), [])
        zero = [{"id": "a", "kind": "none", "weight": 0, "amount": 1}]
        self.assertEqual(table_with_revive(zero, 9), zero)
        taken = [{"id": "revive", "kind": "none", "weight": 99, "amount": 1}]
        self.assertEqual([e["id"] for e in table_with_revive(taken, 9)], ["revive", "revive_"])    # 칸 이름이 겹치면 피한다

    def test_an_item_the_admin_already_named_revive_ticket_is_adopted_with_its_bag_intact(self):
        v1_state(self.store)
        mine = self.store.create_item(G, "부활권", emoji="🔥", description="내 부활권", sell_price=77, sellable=True)
        self.store.add_item(G, U, mine["id"], 3)
        self.store.ensure_defaults(G)
        revive = self.store.revive_item(G)
        self.assertEqual((revive["id"], revive["emoji"], revive["sell_price"], revive["sellable"]), (mine["id"], "🔥", 77, 1))
        self.assertEqual(self.store.qty(G, U, mine["id"]), 3)
        self.assertEqual(len([r for r in self.store.items(G) if r["name"] == "부활권"]), 1)

    def test_a_role_product_with_the_same_name_does_not_break_the_upgrade(self):
        v1_state(self.store)
        self.store.create_item(G, "부활권", kind="role", role_id=50, buyable=True, buy_price=5)
        self.store.ensure_defaults(G)
        self.assertEqual(self.store.revive_item(G)["name"], "던전 부활권")

    def test_the_default_table_command_includes_the_ticket(self):
        self.store.ensure_defaults(G)
        self.store.update_settings(G, package_table=[{"id": "none", "kind": "none", "weight": 1, "amount": 1}])
        table = self.store.apply_default_package_table(G)
        self.assertEqual(next(e for e in table if e["id"] == "revive")["weight"], 0.1)
        self.assertEqual(self.store.settings(G)["package_table"], table)


class UseTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.ensure_defaults(G)
        self.revive = self.store.revive_item(G)
        self.store.update_settings(G, entry_item=None)                                           # 무료 입장
        self.store.enter_dungeon(G, U, "{}")

    def test_it_uses_one_and_saves_the_new_state_together(self):
        self.store.add_item(G, U, self.revive["id"], 2)
        self.assertTrue(self.store.use_revive(G, U, '{"a": 1}'))
        self.assertEqual((self.store.qty(G, U, self.revive["id"]), self.store.dungeon_run(G, U)), (1, '{"a": 1}'))
        self.assertTrue(self.store.use_revive(G, U, '{"a": 2}'))
        self.assertEqual(self.store.qty(G, U, self.revive["id"]), 0)

    def test_without_a_ticket_or_a_dungeon_nothing_changes(self):
        self.assertFalse(self.store.use_revive(G, U, '{"a": 1}'))
        self.assertEqual(self.store.dungeon_run(G, U), "{}")
        self.store.add_item(G, U, self.revive["id"], 1)
        self.store.add_item(G, 999, self.revive["id"], 1)                                         # 부활권은 있지만 던전이 없는 사람
        self.assertFalse(self.store.use_revive(G, 999, '{"a": 1}'))
        self.assertEqual((self.store.qty(G, U, self.revive["id"]), self.store.qty(G, 999, self.revive["id"])), (1, 1))   # 부활권은 안 줄어든다

    def test_it_is_refused_if_the_admin_deleted_the_ticket_item(self):
        self.store.update_settings(G, package_table=[e for e in self.store.package_table(G) if e["id"] != "revive"])
        self.store.delete_item(G, self.revive["id"])
        self.assertIsNone(self.store.revive_item(G))
        self.assertFalse(self.store.use_revive(G, U, '{"a": 1}'))


class DungeonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        ids = self.store.ensure_system_items(G)
        self.store.add_item(G, U, ids["key"], 1)
        self.revive = self.store.revive_item(G)

    def tearDown(self):
        self.store.close()

    async def near_death(self, hp=5, damage=100):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        enemy = dungeon._spawn(balance.MONSTERS[1], 1, balance.DEFAULT)
        enemy["damage"] = damage
        data.update(phase="battle", kind="monster", room=2, hp=hp, enemy=enemy)
        self.store.save_dungeon(G, U, json.dumps(data))
        return Run_from(self.store).rev

    async def get_hit(self, rev):
        return await service.act(self.store, self.locks, Scripted(), G, U, rev, "defend", guard_ok=False)

    async def test_with_a_ticket_dying_stands_you_back_up_and_the_fight_goes_on(self):
        self.store.add_item(G, U, self.revive["id"], 2)
        out = await self.get_hit(await self.near_death())
        self.assertTrue(out.ok)
        self.assertIsNone(out.ended)                                                              # 던전은 끝나지 않았다
        self.assertEqual((out.run.phase, out.run.hp), ("battle", balance.DEFAULT.max_hp * 50 // 100))
        self.assertEqual(out.run.enemy["name"], "오일 슬러지")                                           # 싸우던 상대는 그대로
        self.assertEqual(self.store.qty(G, U, self.revive["id"]), 1)                              # 한 장만 썼다
        text = "\n".join(out.run.events)
        self.assertIn("다운됐어요", text)
        self.assertIn("부활권", text)
        self.assertIn("남은 부활권 1장", text)
        saved = Run_from(self.store)
        self.assertEqual((saved.phase, saved.hp), ("battle", out.run.hp))                         # 저장도 되살아난 상태
        self.assertIn("부활권", run_embed(out.run, out).description + "".join(f.value for f in run_embed(out.run, out).fields))

    async def test_the_last_ticket_is_used_up_and_the_next_death_is_final(self):
        self.store.add_item(G, U, self.revive["id"], 1)
        out = await self.get_hit(await self.near_death())
        self.assertIsNone(out.ended)
        self.assertNotIn("남은", "\n".join(out.run.events))
        self.assertEqual(self.store.qty(G, U, self.revive["id"]), 0)
        data = json.loads(out.run.to_json())
        data.update(hp=5)
        self.store.save_dungeon(G, U, json.dumps(data))
        out = await self.get_hit(Run_from(self.store).rev)
        self.assertEqual(out.ended, "dead")
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_without_a_ticket_dying_ends_the_run_as_before(self):
        out = await self.get_hit(await self.near_death())
        self.assertEqual(out.ended, "dead")
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_the_ticket_is_only_used_when_you_actually_die(self):
        self.store.add_item(G, U, self.revive["id"], 1)
        out = await self.get_hit(await self.near_death(hp=500, damage=10))
        self.assertEqual((out.run.phase, self.store.qty(G, U, self.revive["id"])), ("battle", 1))

    async def test_dying_to_a_consumable_turn_also_uses_the_ticket(self):
        self.store.add_item(G, U, self.revive["id"], 1)
        rev = await self.near_death()
        data = json.loads(self.store.dungeon_run(G, U))
        data["items"] = {"candy": 1}
        self.store.save_dungeon(G, U, json.dumps(data))
        out = await service.act(self.store, self.locks, Scripted(), G, U, rev, "item", "candy")
        self.assertTrue(out.ok)
        self.assertEqual(self.store.qty(G, U, self.revive["id"]), 0)
        self.assertIsNone(out.ended)

    async def test_the_revive_hp_follows_the_dungeon_setting(self):
        self.store.update_settings(G, dungeon={"일반.부활체력": 100})
        self.store.add_item(G, U, self.revive["id"], 1)
        out = await self.get_hit(await self.near_death())
        self.assertEqual(out.run.hp, self.store.balance(G).max_hp)
        self.assertEqual(check_value("일반.부활체력", 0)[1] is not None, True)                       # 0%로는 일어날 수 없다
        self.assertEqual(Balance.build({}).revive_heal, 50)

    async def test_a_stale_button_does_not_use_a_ticket(self):
        self.store.add_item(G, U, self.revive["id"], 1)
        rev = await self.near_death()
        out = await service.act(self.store, self.locks, Scripted(), G, U, rev + 5, "defend", guard_ok=False)
        self.assertTrue(out.stale)
        self.assertEqual(self.store.qty(G, U, self.revive["id"]), 1)


def Run_from(store):
    return dungeon.Run.from_json(store.dungeon_run(G, U))
