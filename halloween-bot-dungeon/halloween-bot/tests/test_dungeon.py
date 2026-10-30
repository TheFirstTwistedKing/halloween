"""던전 시험: 규칙(dungeon.py), 저장(store.py), 진행(dungeon_service.py). 디스코드 없이 돌아간다."""
import asyncio
import json
import unittest

import dungeon
import dungeon_service as service
from dungeon import DungeonError, Run
from store import CANDY_MAX, Store

try:
    from services import Locks
except ImportError:                              # discord.py가 없는 환경에서도 시험할 수 있게
    class Locks:
        def __init__(self):
            self._locks = [asyncio.Lock() for _ in range(128)]

        def get(self, guild_id, user_id):
            return self._locks[hash((guild_id, user_id)) % len(self._locks)]

G, U = 100, 7
EMPTY = 0.4        # roll_room: 30 <= 40 < 60 → 빈 방
MONSTER = 0.1      # 0 <= 10 < 30 → 몬스터
CHEST = 0.7        # 60 <= 70 < 80 → 상자


class Scripted:
    """정해 둔 값을 차례로 돌려주는 가짜 난수 생성기. 값이 모자라면 예상 밖의 추첨이 있었다는 뜻이라 실패한다."""

    def __init__(self, randoms=(), ints=()):
        self.randoms, self.ints = list(randoms), list(ints)

    def random(self):
        assert self.randoms, "예상보다 난수를 더 뽑았어요"
        return self.randoms.pop(0)

    def randint(self, low, high):
        return self.ints.pop(0) if self.ints else low


class RuleTests(unittest.TestCase):
    def test_run_survives_json_and_ignores_unknown_fields(self):
        run = dungeon.new_run()
        run.items, run.pending_gear, run.rev = {"candy": 2}, "club", 5
        data = json.loads(run.to_json())
        data["from_a_future_version"] = 1
        back = Run.from_json(json.dumps(data))
        self.assertEqual((back.items, back.pending_gear, back.rev), ({"candy": 2}, "club", 5))

    def test_chest_gear_must_be_decided_before_advancing(self):
        run = dungeon.new_run()
        run.phase = "chest"
        dungeon.open_chest(run, Scripted(randoms=[0.0, 0.0]))        # 첫 장비(몽둥이)와 첫 아이템(사탕)
        self.assertEqual((run.pending_gear, run.items), ("club", {"candy": 1}))
        self.assertFalse(run.can_advance)
        dungeon.equip_pending(run)
        self.assertEqual((run.weapon, run.pending_gear), ("club", None))
        self.assertTrue(run.can_advance)

    def test_discard_keeps_the_old_gear_and_equip_replaces_it(self):
        run = dungeon.new_run(weapon="club")
        run.pending_gear = "wooden_shield"
        dungeon.equip_pending(run)
        self.assertEqual((run.weapon, run.shield), ("club", "wooden_shield"))
        run.pending_gear = "dagger"
        dungeon.discard_pending(run)
        self.assertEqual((run.weapon, run.pending_gear), ("club", None))
        with self.assertRaises(DungeonError):
            dungeon.equip_pending(run)
        with self.assertRaises(DungeonError):
            dungeon.discard_pending(run)

    def test_using_items_needs_one_and_uses_it_up(self):
        run = dungeon.new_run()
        run.hp = 500
        with self.assertRaises(DungeonError):
            dungeon.use_consumable(run, "candy")
        with self.assertRaises(DungeonError):
            dungeon.use_consumable(run, "nonsense")
        run.items = {"pumpkin_soup": 1}
        dungeon.use_consumable(run, "pumpkin_soup")
        self.assertEqual((run.hp, run.items), (700, {}))

    def test_last_room_ends_the_dungeon(self):
        run = dungeon.new_run()
        run.room = dungeon.ROOMS
        dungeon.advance(run, Scripted())
        self.assertEqual(run.phase, "clear")
        self.assertTrue(run.finished)
        self.assertIn("🏆", run.events[0])

    def test_clear_reward_range_and_ticket_edge(self):
        low, high = dungeon.CLEAR_CANDY
        self.assertEqual(dungeon.roll_clear_reward(Scripted(randoms=[0.0], ints=[high])), (high, True))
        edge = dungeon.TICKET_CHANCE / 100
        self.assertEqual(dungeon.roll_clear_reward(Scripted(randoms=[edge], ints=[low])), (low, False))

    def test_guard_edge(self):
        self.assertTrue(dungeon.roll_guard(Scripted(randoms=[0.5999])))
        self.assertFalse(dungeon.roll_guard(Scripted(randoms=[0.6])))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.key = self.store.ensure_system_items(G)["key"]

    def tearDown(self):
        self.store.close()

    def test_entering_costs_one_key_and_resuming_is_free(self):
        self.store.add_item(G, U, self.key, 2)
        data, started = self.store.enter_dungeon(G, U, '{"a":1}')
        self.assertEqual((data, started, self.store.qty(G, U, self.key)), ('{"a":1}', True, 1))
        data, started = self.store.enter_dungeon(G, U, '{"a":2}')
        self.assertEqual((data, started, self.store.qty(G, U, self.key)), ('{"a":1}', False, 1))

    def test_no_key_no_dungeon(self):
        from store import StoreError
        with self.assertRaisesRegex(StoreError, "열쇠"):
            self.store.enter_dungeon(G, U, "{}")
        self.assertIsNone(self.store.dungeon_run(G, U))

    def test_the_last_key_row_is_removed(self):
        self.store.add_item(G, U, self.key, 1)
        self.store.enter_dungeon(G, U, "{}")
        self.assertEqual(self.store.inventory(G, U), [])

    def test_end_pays_once_caps_candy_and_counts_tickets(self):
        self.store.add_item(G, U, self.key, 1)
        self.store.enter_dungeon(G, U, "{}")
        self.store.add_candy(G, U, CANDY_MAX - 10)
        self.assertEqual(self.store.end_dungeon(G, U, candy=100, tickets=1), (10, 1))
        self.assertEqual(self.store.wallet(G, U)[0], CANDY_MAX)
        self.assertIsNone(self.store.end_dungeon(G, U, candy=100, tickets=1))       # 두 번째는 보상 없음
        self.assertEqual((self.store.wallet(G, U)[0], self.store.tickets(G, U)), (CANDY_MAX, 1))

    def test_saving_does_not_bring_back_a_finished_dungeon(self):
        self.store.add_item(G, U, self.key, 1)
        self.store.enter_dungeon(G, U, "{}")
        self.store.end_dungeon(G, U)
        self.store.save_dungeon(G, U, '{"zombie":true}')
        self.assertIsNone(self.store.dungeon_run(G, U))

    def test_dungeons_are_per_server_and_per_person(self):
        for guild, user in ((G, U), (G, 8), (101, U)):
            self.store.add_item(guild, user, self.store.ensure_system_items(guild)["key"], 1)
            self.store.enter_dungeon(guild, user, json.dumps({"who": [guild, user]}))
        self.assertEqual(json.loads(self.store.dungeon_run(101, U))["who"], [101, U])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    def tearDown(self):
        self.store.close()

    async def go(self, rng, action, rev, arg=None, guard_ok=False):
        return await service.act(self.store, self.locks, rng, G, U, rev, action, arg, guard_ok)

    async def test_enter_needs_a_key_then_resumes_without_one(self):
        first = await service.enter(self.store, self.locks, G, U)
        self.assertEqual((first.ok, first.started), (True, True))
        again = await service.enter(self.store, self.locks, G, U)
        self.assertEqual((again.ok, again.started, again.run.rev), (True, False, first.run.rev + 1))
        other = await service.enter(self.store, self.locks, G, 8)
        self.assertFalse(other.ok)
        self.assertIn("열쇠", other.text)

    async def test_an_old_screen_is_ignored(self):
        first = await service.enter(self.store, self.locks, G, U)
        await service.enter(self.store, self.locks, G, U)             # 다시 띄우면 먼저 화면은 지난 화면
        result = await self.go(Scripted(randoms=[EMPTY]), "advance", first.run.rev)
        self.assertEqual((result.ok, result.stale), (False, True))
        self.assertEqual(json.loads(self.store.dungeon_run(G, U))["room"], 0)

    async def test_a_full_clear_pays_the_reward_exactly_once(self):
        run = (await service.enter(self.store, self.locks, G, U)).run
        rng = Scripted(randoms=[EMPTY] * dungeon.ROOMS)
        for _ in range(dungeon.ROOMS):
            result = await self.go(rng, "advance", run.rev)
            self.assertTrue(result.ok, result.text)
            run = result.run
        self.assertEqual(run.room, dungeon.ROOMS)
        low, _ = dungeon.CLEAR_CANDY
        final = Scripted(randoms=[0.0], ints=[low])                    # 획득권도 나오는 추첨
        results = await asyncio.gather(self.go(final, "advance", run.rev), self.go(Scripted(), "advance", run.rev))
        self.assertEqual(sorted(r.ok for r in results), [False, True])  # 겹친 요청 중 하나만 성공
        done = next(r for r in results if r.ok)
        self.assertEqual((done.ended, done.reward.candy, done.reward.ticket, done.reward.tickets_total), ("clear", low, True, 1))
        self.assertEqual((self.store.wallet(G, U)[0], self.store.tickets(G, U)), (low, 1))
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_dying_and_quitting_give_nothing_and_end_the_run(self):
        run = (await service.enter(self.store, self.locks, G, U)).run
        data = json.loads(run.to_json())
        data.update(hp=10, phase="battle", room=1, kind="monster",
                    enemy={"key": "witch", "name": "마녀", "emoji": "🧙", "hp": 150, "max_hp": 150, "damage": 150,
                           "dodge": 20, "boss": False})
        self.store.save_dungeon(G, U, json.dumps(data))
        result = await self.go(Scripted(randoms=[0.99]), "attack", run.rev)         # 공격이 빗나가고 마녀에게 맞는다
        self.assertEqual((result.ok, result.ended, result.reward), (True, "dead", None))
        self.assertIsNone(self.store.dungeon_run(G, U))
        self.assertEqual(self.store.wallet(G, U)[0], 0)

        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        run = (await service.enter(self.store, self.locks, G, U)).run
        result = await self.go(Scripted(), "quit", run.rev)
        self.assertEqual((result.ok, result.ended), (True, "quit"))
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_chest_flow_and_items(self):
        run = (await service.enter(self.store, self.locks, G, U)).run
        run = (await self.go(Scripted(randoms=[CHEST]), "advance", run.rev)).run
        self.assertEqual(run.phase, "chest")
        blocked = await self.go(Scripted(), "advance", run.rev)
        self.assertFalse(blocked.ok)
        run = (await self.go(Scripted(randoms=[0.0, 0.0]), "chest", run.rev)).run
        blocked = await self.go(Scripted(), "advance", run.rev)
        self.assertEqual((blocked.ok, "장착" in blocked.text), (False, True))
        run = (await self.go(Scripted(), "equip", run.rev)).run
        self.assertEqual(run.weapon, "club")
        hurt = json.loads(run.to_json())
        hurt["hp"] = 100
        self.store.save_dungeon(G, U, json.dumps(hurt))
        healed = await self.go(Scripted(), "item", run.rev, "candy")
        self.assertEqual((healed.ok, healed.run.hp, healed.run.items), (True, 150, {}))
        none_left = await self.go(Scripted(), "item", healed.run.rev, "candy")
        self.assertFalse(none_left.ok)

    async def test_battle_actions_are_refused_outside_battle(self):
        run = (await service.enter(self.store, self.locks, G, U)).run
        for action in ("attack", "defend", "chest"):
            self.assertFalse((await self.go(Scripted(), action, run.rev)).ok)
        self.assertFalse((await self.go(Scripted(), "dance", run.rev)).ok)


if __name__ == "__main__":
    unittest.main()
