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
EMPTY = 0.1        # 짝수 방(빈 방 40% · 잡몹 60%): 10 < 40 → 빈 방
MONSTER = 0.7      # 70 >= 40 → 잡몹
# 홀수 방은 상자, 마지막(10번째) 방은 미니 보스로 정해져 있어 난수를 뽑지 않는다.


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

    # ---- 방 배치 ----
    def test_room_plan_chests_on_odd_rooms_boss_last(self):
        self.assertEqual(len(dungeon.ROOM_PLAN), dungeon.ROOMS)
        for room in range(1, dungeon.ROOMS + 1):
            if room == dungeon.ROOMS:
                self.assertEqual(dungeon.roll_room(Scripted(), room), "boss")          # 난수를 뽑지 않는다
            elif room % 2:
                self.assertEqual(dungeon.roll_room(Scripted(), room), "chest")         # 난수를 뽑지 않는다
            else:                                                                      # 빈 방 40% · 잡몹 60%
                self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.399]), room), "empty")
                self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.4]), room), "monster")

    # ---- 회피 ----
    def _fight(self, monster, weapon=None, shield=None):
        run = dungeon.new_run(weapon=weapon, shield=shield)
        run.enemy, run.phase = dungeon._spawn(monster), "battle"
        return run

    def test_mobs_dodge_10_rat_30_bosses_never(self):
        by = {m.key: m for m in dungeon.MONSTERS}
        for key in ("giant_bat", "slime", "witch"):
            self.assertEqual(dungeon.enemy_dodge(self._fight(by[key])), 10)
        self.assertEqual(dungeon.enemy_dodge(self._fight(by["giant_rat"])), 30)
        for boss in dungeon.BOSSES:
            self.assertEqual(dungeon.enemy_dodge(self._fight(boss)), 0)
            self.assertEqual(dungeon.hit_chance(self._fight(boss)), 100)

    def test_boss_cannot_dodge_even_when_the_weapon_converts_dodge(self):
        for weapon in ("crossbow", "gun"):
            run = self._fight(dungeon.BOSSES[0], weapon=weapon)
            self.assertEqual(dungeon.enemy_dodge(run), 0)
            before = run.enemy["hp"]
            dungeon.player_attack(run, Scripted(randoms=[0.0]))              # 회피 굴림 0%도 맞는다(0 < 0 은 거짓)
            self.assertLess(run.enemy["hp"], before)
        mob = self._fight(dungeon.MONSTERS[1], weapon="gun")
        self.assertEqual(dungeon.enemy_dodge(mob), 50)                       # 잡몹에게는 기존대로 변환

    # ---- 기본 장비 ----
    def test_common_gear_was_buffed(self):
        g = dungeon.GEAR_BY_KEY
        self.assertEqual((g["club"].attack, g["dagger"].attack, g["dagger"].crit), (30, 35, 50))
        self.assertEqual((g["wooden_shield"].guard, g["wooden_shield"].reflect, g["shield"].reflect), (80, 40, 50))
        for common in ("club", "dagger"):                                    # 일반 < 희귀 순서는 유지
            self.assertLess(g[common].attack, g["longsword"].attack)

    # ---- 방어 ----
    def test_guard_success_reduces_heals_reflects_and_stuns(self):
        run = self._fight(dungeon.MONSTERS[2])                               # 마녀: HP 135, 공격 135
        run.hp = 500
        dungeon.player_defend(run, True)
        taken = dungeon.reduce_damage(135, dungeon.GUARD_BASE)               # 60% 줄어 54
        healed = 135 * dungeon.GUARD_HEAL // 100                             # 67
        self.assertEqual(run.hp, 500 - taken + healed)
        self.assertEqual(run.enemy["hp"], 135 - 135 * dungeon.REFLECT_BASE // 100)   # 30% 반사
        self.assertTrue(run.enemy["stunned"])
        # 기절한 몬스터는 다음 공격을 못 한다: 내가 공격해도 반격이 없다.
        hp = run.hp
        dungeon.player_attack(run, Scripted(randoms=[0.99]))
        self.assertEqual(run.hp, hp)
        self.assertFalse(run.enemy["stunned"])
        self.assertTrue(any("공격하지 못했" in e for e in run.events))
        dungeon.player_attack(run, Scripted(randoms=[0.99]))                 # 기절은 한 번뿐: 다음엔 맞는다
        self.assertLess(run.hp, hp)

    def test_guard_failure_gets_nothing(self):
        run = self._fight(dungeon.MONSTERS[2])
        run.hp = 500
        dungeon.player_defend(run, False)
        self.assertEqual((run.hp, run.enemy["hp"], run.enemy["stunned"]), (500 - 135, 135, False))

    def test_shield_gear_changes_reduction_and_reflect(self):
        wood = self._fight(dungeon.MONSTERS[2], shield="wooden_shield")
        dungeon.player_defend(wood, True)
        self.assertEqual(wood.enemy["hp"], 135 - 135 * 40 // 100)
        steel = self._fight(dungeon.MONSTERS[2], shield="shield")
        steel.hp = 500
        dungeon.player_defend(steel, True)
        self.assertEqual(steel.enemy["hp"], 135 - 135 * 50 // 100)
        self.assertEqual(steel.hp, 500 - dungeon.reduce_damage(135, 90) + 135 * dungeon.GUARD_HEAL // 100)

    def test_reflect_can_kill_and_heal_never_exceeds_max(self):
        run = self._fight(dungeon.MONSTERS[3])                               # 거대 쥐 HP 90
        run.enemy["hp"] = 10
        dungeon.player_defend(run, True)                                     # 반사 13 ≥ 10
        self.assertEqual((run.phase, run.enemy["hp"], run.hp), ("cleared", 0, dungeon.MAX_HP))
        self.assertTrue(run.can_advance)

    def test_dying_to_the_guarded_hit_gives_no_bonus(self):
        run = self._fight(dungeon.MONSTERS[2])
        run.hp = 10
        dungeon.player_defend(run, True)                                     # 54 피해로 쓰러진다
        self.assertEqual((run.phase, run.hp, run.enemy["hp"]), ("dead", 0, 135))

    def test_defending_against_a_stunned_enemy_just_burns_the_stun(self):
        run = self._fight(dungeon.MONSTERS[2])
        run.enemy["stunned"] = True
        run.hp = 500
        dungeon.player_defend(run, True)
        self.assertEqual((run.hp, run.enemy["hp"], run.enemy["stunned"]), (500, 135, False))

    def test_guarding_forever_cannot_farm_hp(self):
        """방어 한 번의 기대 HP 변화는 어떤 몬스터·방패 조합에서도 음수다 → 방어만 반복해서 HP를 채울 수 없다."""
        chance = dungeon.GUARD_CHANCE / 100
        for monster in dungeon.MONSTERS + dungeon.BOSSES:
            for shield in (None, "wooden_shield", "shield"):
                guard = dungeon.guard_percent(dungeon.new_run(shield=shield))
                taken_ok = dungeon.reduce_damage(monster.damage, guard)
                heal = monster.damage * dungeon.GUARD_HEAL // 100
                expected = chance * (heal - taken_ok) + (1 - chance) * (-monster.damage)
                self.assertLess(expected, 0, (monster.name, shield, expected))

    def test_old_saved_enemy_without_stun_field_still_works(self):
        run = dungeon.new_run()
        run.phase, run.enemy = "battle", {"key": "slime", "name": "슬라임", "emoji": "🟢", "hp": 300, "max_hp": 300,
                                          "damage": 50, "dodge": 20, "boss": False}
        dungeon.player_defend(run, True)
        self.assertTrue(run.enemy["stunned"])

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
        self.assertEqual(self.store.wallet(G, U), CANDY_MAX)
        self.assertIsNone(self.store.end_dungeon(G, U, candy=100, tickets=1))       # 두 번째는 보상 없음
        self.assertEqual((self.store.wallet(G, U), self.store.tickets(G, U)), (CANDY_MAX, 1))

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
        data = json.loads(run.to_json())
        data.update(room=dungeon.ROOMS, phase="cleared", kind="boss")      # 마지막 방(보스)을 막 이긴 상태
        self.store.save_dungeon(G, U, json.dumps(data))
        run = dungeon.Run.from_json(json.dumps(data))
        low, _ = dungeon.CLEAR_CANDY
        final = Scripted(randoms=[0.0], ints=[low])                    # 획득권도 나오는 추첨
        results = await asyncio.gather(self.go(final, "advance", run.rev), self.go(Scripted(), "advance", run.rev))
        self.assertEqual(sorted(r.ok for r in results), [False, True])  # 겹친 요청 중 하나만 성공
        done = next(r for r in results if r.ok)
        self.assertEqual((done.ended, done.reward.candy, done.reward.ticket, done.reward.tickets_total), ("clear", low, True, 1))
        self.assertEqual((self.store.wallet(G, U), self.store.tickets(G, U)), (low, 1))
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_first_rooms_follow_the_plan_chest_then_empty_or_mob_then_chest(self):
        run = (await service.enter(self.store, self.locks, G, U)).run
        run = (await self.go(Scripted(), "advance", run.rev)).run                 # 1번째 방: 상자(난수 없음)
        self.assertEqual((run.room, run.phase), (1, "chest"))
        run = (await self.go(Scripted(randoms=[0.0, 0.0]), "chest", run.rev)).run
        run = (await self.go(Scripted(), "discard", run.rev)).run
        run = (await self.go(Scripted(randoms=[EMPTY]), "advance", run.rev)).run  # 2번째 방: 빈 방
        self.assertEqual((run.room, run.phase), (2, "empty"))
        run = (await self.go(Scripted(), "advance", run.rev)).run                 # 3번째 방: 다시 상자
        self.assertEqual((run.room, run.phase), (3, "chest"))

    async def test_dying_and_quitting_give_nothing_and_end_the_run(self):
        run = (await service.enter(self.store, self.locks, G, U)).run
        data = json.loads(run.to_json())
        data.update(hp=10, phase="battle", room=1, kind="monster",
                    enemy={"key": "witch", "name": "마녀", "emoji": "🧙", "hp": 150, "max_hp": 150, "damage": 150,
                           "dodge": 20, "boss": False})
        self.store.save_dungeon(G, U, json.dumps(data))
        result = await self.go(Scripted(randoms=[0.99]), "attack", run.rev)         # 공격은 맞지만 마녀를 못 잡고 마녀에게 맞는다
        self.assertEqual((result.ok, result.ended, result.reward), (True, "dead", None))
        self.assertIsNone(self.store.dungeon_run(G, U))
        self.assertEqual(self.store.wallet(G, U), 0)

        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        run = (await service.enter(self.store, self.locks, G, U)).run
        result = await self.go(Scripted(), "quit", run.rev)
        self.assertEqual((result.ok, result.ended), (True, "quit"))
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_chest_flow_and_items(self):
        run = (await service.enter(self.store, self.locks, G, U)).run
        run = (await self.go(Scripted(), "advance", run.rev)).run
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
