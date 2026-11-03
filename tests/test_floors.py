"""던전의 층: 10번째 방(보스)을 이기면 위 층으로 올라가고, 마지막 층의 보스를 이기면 클리어. 층마다 몬스터가 강해지고 HP가 일부 회복된다."""
import json
import unittest

import balance
import balance_sim
import dungeon
import dungeon_service as service
from balance import Balance, check_value, floor_scaled
from cogs.dungeon import run_embed
from cogs.dungeon_admin import where_text
from dungeon import Run
from store import Store
from tests.helpers import Scripted

G, U = 100, 7
MONSTER = 0.99      # 짝수 방(빈 방 40% · 잡몹 60%)에서 잡몹


def at_boss_cleared(floor, hp=1000, bal=None):
    """floor층의 10번째 방(보스)을 막 이긴 상태."""
    run = dungeon.new_run(bal=bal)
    run.floor, run.room, run.phase, run.kind, run.hp = floor, dungeon.ROOMS, "cleared", "boss", hp
    return run


class AscendTests(unittest.TestCase):
    def test_beating_the_boss_of_a_floor_takes_you_up_to_room_one_of_the_next(self):
        run = at_boss_cleared(1)
        self.assertTrue(run.can_advance)
        dungeon.advance(run, Scripted(randoms=[0.99]))   # 맨 앞 난수는 시크릿 방 추첨(1% 미만이어야 열린다)
        self.assertEqual((run.floor, run.room, run.phase, run.kind), (2, 1, "chest", "chest"))   # 첫 방은 늘 상자
        self.assertFalse(run.finished)
        self.assertIn("⬆️ **2층**", run.events[0])
        self.assertIn("1층의 104%", run.events[0])
        self.assertEqual(len(run.events), 2)                       # 올라왔다는 안내 + 새 방 안내

    def test_climbing_heals_half_of_the_lost_hp(self):
        run = at_boss_cleared(3, hp=400)
        dungeon.advance(run, Scripted(randoms=[0.99]))   # 맨 앞 난수는 시크릿 방 추첨(1% 미만이어야 열린다)
        self.assertEqual(run.hp, 700)                              # 잃은 600의 절반
        self.assertIn("HP +300", run.events[0])
        run = at_boss_cleared(3, hp=1)
        dungeon.advance(run, Scripted(randoms=[0.99]))
        self.assertEqual(run.hp, 1 + 999 * 50 // 100)

    def test_full_hp_has_nothing_to_heal(self):
        run = at_boss_cleared(3)
        dungeon.advance(run, Scripted(randoms=[0.99]))   # 맨 앞 난수는 시크릿 방 추첨(1% 미만이어야 열린다)
        self.assertEqual(run.hp, 1000)
        self.assertNotIn("HP +", run.events[0])

    def test_the_heal_rate_is_adjustable(self):
        for rate, expected in ((0, 400), (100, 1000), (25, 550)):
            bal = Balance.build({"일반.층이동회복": rate})
            run = at_boss_cleared(2, hp=400)
            dungeon.advance(run, Scripted(randoms=[0.99]), bal)
            self.assertEqual(run.hp, expected, rate)

    def test_the_last_boss_clears_the_dungeon_and_no_other_boss_does(self):
        for floor in range(1, dungeon.FLOORS):
            run = at_boss_cleared(floor)
            dungeon.advance(run, Scripted(randoms=[0.99]))   # 맨 앞 난수는 시크릿 방 추첨(1% 미만이어야 열린다)
            self.assertEqual((run.phase == "clear", run.floor), (False, floor + 1), floor)
        run = at_boss_cleared(dungeon.FLOORS)
        dungeon.advance(run, Scripted(randoms=[0.99]))
        self.assertEqual(run.phase, "summit")                              # 꼭대기: 바로 클리어하지 않고 먼저 묻는다
        self.assertIn("10층", run.events[0])
        dungeon.take_clear(run)
        self.assertEqual(run.phase, "clear")
        direct = Balance.build({"일반.무한모드": 0})                          # 묻지 않는 설정이면 예전처럼 바로 클리어
        run = at_boss_cleared(dungeon.FLOORS)
        dungeon.advance(run, Scripted(randoms=[0.99]), direct)
        self.assertEqual(run.phase, "clear")
        self.assertIn("10층", run.events[0])

    def test_the_number_of_floors_is_adjustable(self):
        bal = Balance.build({"일반.층수": 3})
        run = at_boss_cleared(2)
        dungeon.advance(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.floor, 3)
        run = at_boss_cleared(3)
        dungeon.advance(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.phase, "summit")
        self.assertIn("3층", run.events[0])
        lowered = Balance.build({"일반.층수": 2, "일반.무한모드": 0})   # 진행 중에 층수를 줄이면 이미 넘은 층이어도 이번 보스로 클리어
        run = at_boss_cleared(5)
        dungeon.advance(run, Scripted(randoms=[0.99]), lowered)
        self.assertEqual(run.phase, "clear")

    def test_a_middle_room_is_not_a_floor_change(self):
        run = dungeon.new_run()
        run.floor, run.room, run.phase = 4, 3, "chest_done"
        dungeon.advance(run, Scripted(randoms=[MONSTER, 0.0]))     # 방 종류 추첨, 그다음 어떤 몬스터인지 추첨
        self.assertEqual((run.floor, run.room), (4, 4))
        self.assertNotIn("⬆️", "\n".join(run.events))


class ScalingTests(unittest.TestCase):
    def test_the_formula(self):
        self.assertEqual([floor_scaled(100, f, 4) for f in (1, 2, 5, 10)], [100, 104, 116, 136])
        self.assertEqual(floor_scaled(65, 1, 50), 65)              # 1층은 언제나 그대로
        self.assertEqual(floor_scaled(65, 10, 0), 65)              # 0%면 그대로
        self.assertEqual(floor_scaled(0, 10, 50), 0)               # 공격 0은 0
        self.assertEqual(floor_scaled(225, 3, 4), 243)             # 225 * 1.08 = 243
        self.assertEqual(floor_scaled(5, 0, 10), 5)                # 이상한 층 번호도 1층처럼

    def spawn(self, name, floor, bal=None):
        monster = next(m for m in balance.MONSTERS + balance.BOSSES if m.name == name)
        return dungeon._spawn(monster, floor, bal or balance.DEFAULT)

    def test_monsters_and_bosses_get_stronger_but_dodge_stays(self):
        bat = self.spawn("거대 박쥐", 1)
        self.assertEqual((bat["hp"], bat["max_hp"], bat["damage"], bat["dodge"]), (225, 225, 65, 10))
        bat = self.spawn("거대 박쥐", 10)                           # 136%
        self.assertEqual((bat["hp"], bat["max_hp"], bat["damage"], bat["dodge"]), (306, 306, 88, 10))
        king = self.spawn("해골 왕", 10)
        self.assertEqual((king["hp"], king["damage"], king["dodge"], king["boss"]), (1224, 184, 0, True))
        rat = self.spawn("거대 쥐", 10)
        self.assertEqual(rat["dodge"], 30)

    def test_zero_percent_means_every_floor_is_the_same(self):
        bal = Balance.build({"일반.층당강화": 0})
        self.assertEqual(self.spawn("마녀", 10, bal), self.spawn("마녀", 1, bal))

    def test_a_monster_in_the_room_uses_the_floors_numbers(self):
        run = dungeon.new_run()
        run.floor, run.room, run.phase = 10, 1, "chest_done"
        dungeon.advance(run, Scripted(randoms=[MONSTER, 0.3]))          # 2번째 방, 잡몹, 같은 확률 4칸 중 2번째(0.25~0.5)인 슬라임
        self.assertEqual((run.enemy["name"], run.enemy["hp"], run.enemy["damage"]), ("슬라임", 367, 61))
        run.room, run.phase = dungeon.ROOMS - 1, "cleared"
        dungeon.advance(run, Scripted(randoms=[0.0]))                           # 10번째 방 보스(해골 왕)
        self.assertEqual((run.enemy["name"], run.enemy["hp"]), ("해골 왕", 1224))

    def test_the_player_is_not_scaled(self):
        run = dungeon.new_run()
        run.floor = 10
        self.assertEqual((dungeon.player_damage(run), run.hp), (20, 1000))


class SettingsTests(unittest.TestCase):
    def test_the_new_settings_exist_with_defaults_and_limits(self):
        self.assertEqual((balance.DEFAULT.floors, balance.DEFAULT.floor_scale, balance.DEFAULT.floor_heal), (10, 4, 50))
        for key, good, bad in (("일반.층수", 100, 101), ("일반.층당강화", 200, 201), ("일반.층이동회복", 100, 101)):
            self.assertEqual(check_value(key, good)[1], None, key)
            self.assertIsNotNone(check_value(key, bad)[1], key)
            self.assertIsNotNone(check_value(key, -1)[1], key)
        self.assertIsNotNone(check_value("일반.층수", 0)[1])                  # 층이 없는 던전은 없다
        self.assertIsNotNone(check_value("일반.층수", 2.5)[1])

    def test_settings_apply_per_server(self):
        store = Store(":memory:")
        store.update_settings(G, dungeon={"일반.층수": 4, "일반.층당강화": 0, "일반.층이동회복": 100})
        bal = store.balance(G)
        self.assertEqual((bal.floors, bal.floor_scale, bal.floor_heal), (4, 0, 100))
        self.assertEqual(store.balance(101).floors, 10)
        store.close()


class RunDataTests(unittest.TestCase):
    def test_a_run_saved_before_floors_existed_is_floor_one(self):
        old = json.loads(dungeon.new_run().to_json())
        old.pop("floor")
        old["room"] = 7
        run = Run.from_json(json.dumps(old))
        self.assertEqual((run.floor, run.room, run.stage), (1, 7, 7))

    def test_stage_counts_rooms_from_the_start(self):
        run = dungeon.new_run()
        for floor, room, stage in ((1, 1, 1), (1, 10, 10), (2, 1, 11), (10, 10, 100)):
            run.floor, run.room = floor, room
            self.assertEqual(run.stage, stage)
        self.assertEqual(Run.from_json(run.to_json()).floor, 10)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from services import Locks
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    def tearDown(self):
        self.store.close()

    async def test_entering_says_how_many_floors_there_are(self):
        out = await service.enter(self.store, self.locks, G, U)
        self.assertIn("10층까지", out.run.events[0])
        self.assertEqual((out.run.floor, out.run.room), (1, 0))

    async def test_climbing_through_the_service_saves_and_pays_nothing(self):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=1, room=dungeon.ROOMS, phase="cleared", kind="boss", hp=300)
        self.store.save_dungeon(G, U, json.dumps(data))
        done = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, out.run.rev, "advance")
        self.assertTrue(done.ok)
        self.assertIsNone(done.ended)                                     # 아직 끝나지 않았다
        self.assertIsNone(done.reward)
        saved = json.loads(self.store.dungeon_run(G, U))
        self.assertEqual((saved["floor"], saved["room"], saved["hp"], saved["rev"]), (2, 1, 650, out.run.rev + 1))
        self.assertEqual(self.store.wallet(G, U), 0)                      # 층을 올라간다고 보상이 나가지는 않는다

    async def test_only_the_last_floor_pays_the_clear_reward(self):
        out = await service.enter(self.store, self.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=10, room=dungeon.ROOMS, phase="cleared", kind="boss")
        self.store.save_dungeon(G, U, json.dumps(data))
        low, _ = dungeon.CLEAR_CANDY
        top = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, out.run.rev, "advance")
        self.assertEqual((top.ok, top.ended, top.run.phase), (True, None, "summit"))       # 꼭대기에서 묻는다
        self.assertEqual(self.store.wallet(G, U), 0)                                       # 고르기 전에는 보상이 없다
        done = await service.act(self.store, self.locks, Scripted(randoms=[0.99], ints=[low]), G, U, top.run.rev, "finish")
        self.assertEqual((done.ended, done.reward.candy), ("clear", low))
        self.assertIsNone(self.store.dungeon_run(G, U))


class ScreenTests(unittest.TestCase):
    def test_the_title_shows_floor_and_room(self):
        run = dungeon.new_run()
        run.floor, run.room, run.phase = 3, 4, "empty"
        self.assertEqual(run_embed(run).title, "🏚️ 던전 — 3/10층 · 방 4/10")
        run.floor, run.room = 1, 0
        self.assertEqual(run_embed(run).title, "🏚️ 던전 — 1/10층 · 방 1/10")
        short = Balance.build({"일반.층수": 3})
        self.assertIn("1/3층", run_embed(run, None, short).title)

    def test_simulation_places_are_floor_and_room(self):
        self.assertEqual([where_text(s) for s in (1, 10, 11, 100)], ["1층 1번째 방", "1층 10번째 방", "2층 1번째 방", "10층 10번째 방"])


class SimulatorTests(unittest.TestCase):
    def test_more_floors_and_stronger_floors_are_harder(self):
        def clear(**o):
            return balance_sim.simulate(Balance.build(o), 600, seed=21)["mixed"]["clear"]
        one, ten = clear(**{"일반.층수": 1}), clear()
        harsh = clear(**{"일반.층당강화": 10})
        flat = clear(**{"일반.층당강화": 0})
        self.assertGreater(one, ten)
        self.assertGreater(flat, ten)
        self.assertGreater(ten, harsh)

    def test_a_clear_run_reaches_the_hundredth_room(self):
        won, stage = balance_sim.play(Scripted2(), "defend", Balance.build({"일반.층당강화": 0, "일반.맨손공격력": 200, "일반.최대체력": 100000}))
        self.assertTrue(won)
        self.assertEqual(stage, 100)


class Scripted2:
    """플레이를 끝까지 돌리는 단순한 난수: 늘 같은 값을 돌려준다(0.5 → 짝수 방은 잡몹, 이길 수 있게 맨손 공격력을 크게 한다)."""

    def random(self):
        return 0.5

    def randint(self, low, high):
        return low


if __name__ == "__main__":
    unittest.main()
