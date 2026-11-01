"""업데이트 전(층이 생기기 전) 버전이 저장한 던전을 새 코드로 이어서 할 수 있는지.
아래 JSON은 처음 올라온 옛 코드가 실제로 저장한 값이다(floor 키가 없다). 봇을 업데이트하고 /던전을 치면 이어진다."""
import json
import unittest

import dungeon
import dungeon_service as service
from cogs.dungeon import DungeonView, run_embed
from services import Locks
from store import Store
from tests.helpers import Scripted

G, U = 100, 7
# 상자에서 나온 나무 방패를 장착할지 정하는 중 (1번째 방)
CHEST_PENDING = json.loads('{"enemy": null, "events": ["🎁 상자를 열었어요!"], "hp": 1000, "items": {"soul_essence": 1}, "kind": "chest", '
                           '"loot": ["나무 방패", "영혼 에센스"], "pending_gear": "wooden_shield", "phase": "chest_done", "regen_amount": 0, '
                           '"regen_turns": 0, "rev": 3, "room": 1, "shield": null, "weapon": null}')
# 10번째 방의 해골 왕을 막 이긴 상태
BOSS_DOWN = json.loads('{"enemy": {"boss": true, "damage": 135, "dodge": 0, "emoji": "💀", "hp": 0, "key": "skeleton_king", "max_hp": 900, '
                       '"name": "해골 왕", "stunned": false}, "events": ["🎉 **해골 왕**을(를) 쓰러뜨렸어요!"], "hp": 865, '
                       '"items": {"candy": 3, "cookie": 1, "soul_essence": 1}, "kind": "boss", "loot": ["낡은 몽둥이", "사탕"], '
                       '"pending_gear": null, "phase": "cleared", "regen_amount": 0, "regen_turns": 0, "rev": 27, "room": 10, '
                       '"shield": null, "weapon": "club"}')
# 몬스터와 싸우는 중 (8번째 방)
IN_BATTLE = json.loads('{"enemy": {"boss": false, "damage": 65, "dodge": 10, "emoji": "🦇", "hp": 225, "key": "giant_bat", "max_hp": 225, '
                       '"name": "거대 박쥐", "stunned": false}, "events": ["⚠️ 몬스터 조우!"], "hp": 700, "items": {"cookie": 1}, "kind": "monster", '
                       '"loot": [], "pending_gear": null, "phase": "battle", "regen_amount": 0, "regen_turns": 0, "rev": 12, "room": 8, '
                       '"shield": null, "weapon": "club"}')


class ResumeOldRunsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.ensure_defaults(G)
        self.key = self.store.ensure_system_items(G)["key"]

    def tearDown(self):
        self.store.close()

    def save(self, data):
        self.assertNotIn("floor", data)                                   # 옛 저장 형식 그대로다.
        self.store.add_item(G, U, self.key, 2)                            # 이어하기에서 열쇠가 줄지 않는지 보려고 몇 개 가지고 있게 한다.
        with self.store.db:
            self.store.db.execute("INSERT INTO dungeons VALUES(?,?,?)", (G, U, json.dumps(data, ensure_ascii=False)))

    async def resume(self, data):
        self.save(data)
        keys = self.store.qty(G, U, self.key)
        out = await service.enter(self.store, self.locks, G, U)
        self.assertTrue(out.ok)
        self.assertFalse(out.started)                                     # 이어하기: 입장 물품을 쓰지 않는다.
        self.assertEqual(self.store.qty(G, U, self.key), keys)
        DungeonView(object(), U, out.run, self.store.balance(G)).to_components()
        return out.run

    async def finish(self, run, rng=None):
        """끝날 때까지 이어서 한다(전투는 강한 플레이어로). 끝난 결과를 돌려준다."""
        self.store.update_settings(G, dungeon={"일반.맨손공격력": 800})
        import random
        rng = rng or random.Random(4)
        for _ in range(4000):
            act = ("attack" if run.phase == "battle" else "chest" if run.phase == "chest" else "equip" if run.pending_gear
                   else "advance" if run.can_advance else None)
            self.assertIsNotNone(act, run.phase)
            out = await service.act(self.store, self.locks, rng, G, U, run.rev, act)
            self.assertTrue(out.ok, out.text)
            if out.ended:
                return out
            run = out.run
        self.fail("끝나지 않았어요")

    async def test_an_old_run_without_a_floor_resumes_as_floor_one_with_everything_kept(self):
        run = await self.resume(CHEST_PENDING)
        self.assertEqual((run.floor, run.room, run.phase, run.pending_gear, run.items, run.hp), (1, 1, "chest_done", "wooden_shield", {"soul_essence": 1}, 1000))
        self.assertEqual(run_embed(run, None, self.store.balance(G)).title, "🏚️ 던전 — 1/10층 · 방 1/10")
        self.assertEqual(run.rev, CHEST_PENDING["rev"] + 1)                # 이전에 띄운 화면은 지난 화면이 된다.

    async def test_the_undecided_gear_can_still_be_equipped_and_the_run_goes_on_to_the_end(self):
        run = await self.resume(CHEST_PENDING)
        self.assertFalse(run.can_advance)                                 # 장비를 정하기 전에는 못 간다(옛 규칙 그대로)
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "equip")
        self.assertEqual((out.run.shield, out.run.pending_gear), ("wooden_shield", None))
        done = await self.finish(out.run)
        self.assertEqual(done.ended, "clear")
        self.assertGreater(self.store.wallet(G, U), 0)

    async def test_a_fight_in_progress_continues(self):
        run = await self.resume(IN_BATTLE)
        self.assertEqual((run.phase, run.enemy["name"], run.enemy["hp"], run.hp), ("battle", "거대 박쥐", 225, 700))
        done = await self.finish(run)
        self.assertEqual(done.ended, "clear")

    async def test_a_run_that_had_just_beaten_the_old_final_boss_now_climbs_to_floor_two_instead_of_clearing(self):
        """옛 규칙에서는 10번째 방 보스를 이기면 클리어였다. 층이 생긴 뒤에는 2층으로 올라가고 10층에서 클리어한다."""
        run = await self.resume(BOSS_DOWN)
        self.assertEqual((run.floor, run.room, run.phase), (1, 10, "cleared"))
        out = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "advance")
        self.assertTrue(out.ok)
        self.assertIsNone(out.ended)
        self.assertEqual((out.run.floor, out.run.room), (2, 1))
        self.assertEqual(out.run.hp, 865 + (1000 - 865) // 2)             # 올라가며 잃은 HP의 절반 회복
        self.assertEqual(self.store.wallet(G, U), 0)                      # 아직 클리어가 아니라 보상은 없다.
        self.assertIsNotNone(self.store.dungeon_run(G, U))                # 클리어 전이라 기록이 남아 있다.
        done = await self.finish(out.run)
        self.assertEqual(done.ended, "clear")

    async def test_the_old_servers_dungeon_settings_still_apply(self):
        self.store.update_settings(G, dungeon={"일반.최대체력": 1500, "방어.성공확률": 10})
        bal = self.store.balance(G)
        self.assertEqual((bal.max_hp, bal.guard_chance, bal.floors, bal.floor_scale), (1500, 10, 10, 4))     # 옛 값은 그대로, 새 값은 기본

    async def test_the_old_saved_json_loads_with_every_field_it_had(self):
        for data in (CHEST_PENDING, BOSS_DOWN, IN_BATTLE):
            run = dungeon.Run.from_json(json.dumps(data))
            self.assertEqual((run.floor, run.room, run.phase), (1, data["room"], data["phase"]))
            self.assertEqual(json.loads(run.to_json())["items"], data["items"])


if __name__ == "__main__":
    unittest.main()
