"""시크릿 보스: 보스를 이기고 올라갈 때 1%(기본)로 열리는 11번째 방. 추가·수정·삭제, 장비/아이템 중 하나는 반드시 드롭, 등장 연출."""
import asyncio
import json
import random
import unittest

import balance
import balance_sim
import content
import dungeon
import dungeon_service as service
from balance import Balance, SecretBoss, check_entity, check_value, entity_dict
from cogs import dungeon as cog
from cogs.content_admin import ContentAdmin
from cogs.dungeon import DungeonView, respond, run_embed, secret_intro_frames, start_auto   # 시험 시작 때 한 번 불러 와야 cog(시간 설정)와 같은 모듈을 쓴다
from dungeon import Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


def secret(**kw):
    data = dict(key="s", name="심연의 왕", emoji="🌌", hp=500, damage=100, drop_gear="gun", drop_item="golden_soup")
    data.update(kw)
    return data


def bal_with(*secrets, overrides=None):
    raw = [entity_dict(check_entity("secrets", s)[0]) for s in secrets]
    return Balance.build(overrides or {}, {"secrets": raw})


def boss_cleared(bal, floor=1, **kw):
    run = dungeon.new_run(bal=bal)
    run.floor, run.room, run.kind, run.phase = floor, dungeon.ROOMS, "boss", "cleared"
    for k, v in kw.items():
        setattr(run, k, v)
    return run


def in_secret_fight(bal, **kw):
    run = boss_cleared(bal)
    dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)            # 시크릿 방이 열리고 첫 시크릿 보스가 나온다
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class EntityTests(unittest.TestCase):
    def test_the_default_secret_boss_exists_and_always_has_a_drop(self):
        self.assertEqual([b.name for b in balance.SECRET_BOSSES], ["공허의 군주"])
        for boss in balance.SECRET_BOSSES:
            self.assertTrue(boss.drop_gear or boss.drop_item)
            self.assertIn(boss.drop_gear, [g.key for g in balance.GEAR])
            self.assertIn(boss.drop_item, [c.key for c in balance.CONSUMABLES])
        self.assertEqual(Balance.build({}).secrets, balance.SECRET_BOSSES)
        self.assertEqual(Balance.build({}).secret_chance, 10.0)

    def test_a_valid_secret_boss_round_trips_through_json(self):
        entity, problem = check_entity("secrets", secret(drop_gear_chance=12.5, drop_item_amount=3, intro="나타났다", image="https://x.test/a.gif", weight=0.5))
        self.assertIsNone(problem)
        self.assertEqual((entity.drop_gear_chance, entity.drop_item_amount, entity.weight, entity.boss, entity.dodge), (12.5, 3, 0.5, True, 0))
        loaded, problem = check_entity("secrets", json.loads(json.dumps(entity_dict(entity))))
        self.assertIsNone(problem)
        self.assertEqual(loaded, entity)

    def test_it_must_have_at_least_one_drop(self):
        for kw in (dict(drop_gear=None, drop_item=None), dict(drop_gear="", drop_item=""), dict(drop_gear=None, drop_item=None, drop_item_chance=100)):
            entity, problem = check_entity("secrets", secret(**kw))
            self.assertIsNone(entity, kw)
            self.assertIn("하나는", problem)
        for kw in (dict(drop_item=None), dict(drop_gear=None)):                                       # 하나만 있어도 된다
            self.assertIsNone(check_entity("secrets", secret(**kw))[1], kw)

    def test_bad_values_are_refused(self):
        bad = [dict(drop_gear_chance=100.5), dict(drop_gear_chance=-1), dict(drop_item_chance=float("nan")), dict(drop_item_chance=0.00001),
               dict(drop_item_amount=0), dict(drop_item_amount=100), dict(hp=0), dict(damage=-1), dict(weight=-1), dict(weight=1001),
               dict(intro="가" * 201), dict(intro=5), dict(image="ftp://x/a.gif"), dict(image="https://x/a b.gif"), dict(image="https://" + "a" * 300),
               dict(image=5), dict(drop_gear=5), dict(drop_gear="x" * 41), dict(name=""), dict(name=" 앞공백")]
        for kw in bad:
            entity, problem = check_entity("secrets", secret(**kw))
            self.assertIsNone(entity, kw)
            self.assertTrue(problem, kw)

    def test_chances_can_be_decimals_and_zero(self):
        for chance in (0, 0.1, 33.3333, 100):
            entity, problem = check_entity("secrets", secret(drop_gear_chance=chance))
            self.assertIsNone(problem, chance)
            self.assertEqual(entity.drop_gear_chance, chance)

    def test_the_secret_chance_setting_takes_decimals(self):
        self.assertEqual(check_value("일반.시크릿확률", 0.5), (0.5, None))
        self.assertEqual(check_value("일반.시크릿확률", 0), (0, None))
        self.assertIsNotNone(check_value("일반.시크릿확률", 100.5)[1])
        self.assertEqual(Balance.build({"일반.시크릿확률": 2.5}).secret_chance, 2.5)

    def test_a_custom_section_replaces_the_default_and_an_empty_one_means_none(self):
        self.assertEqual([b.name for b in bal_with(secret()).secrets], ["심연의 왕"])
        self.assertEqual(Balance.build({}, {"secrets": []}).secrets, ())


class RoomTests(unittest.TestCase):
    def test_beating_a_boss_has_a_ten_percent_chance_to_open_the_eleventh_room(self):
        bal = Balance.build({})
        for draw, opens in ((0.0, True), (0.09999, True), (0.1, False), (0.99, False)):
            run = boss_cleared(bal)
            rng = Scripted(randoms=[draw, 0.0] if opens else [draw])
            dungeon.advance(run, rng, bal)
            self.assertEqual(run.kind == "secret", opens, draw)
            self.assertEqual(rng.randoms, [], draw)                                                  # 열리지 않으면 추첨은 한 번뿐
        run = boss_cleared(bal)
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual((run.floor, run.room, run.kind, run.phase), (1, 11, "secret", "battle"))
        self.assertTrue(run.enemy["secret"] and run.enemy["boss"])
        self.assertEqual(run.enemy["name"], "공허의 군주")
        self.assertIn("secret_intro", run.last)
        self.assertIn("시크릿 방", run.events[0])

    def test_the_chance_is_adjustable_with_decimals_and_zero_never_draws(self):
        bal = Balance.build({"일반.시크릿확률": 0.5})
        for draw, opens in ((0.00499, True), (0.005, False)):
            run = boss_cleared(bal)
            dungeon.advance(run, Scripted(randoms=[draw, 0.0] if opens else [draw]), bal)
            self.assertEqual(run.kind == "secret", opens, draw)
        zero = Balance.build({"일반.시크릿확률": 0})
        run = boss_cleared(zero)
        dungeon.advance(run, Scripted(), zero)                                                       # 난수를 아예 안 뽑는다
        self.assertEqual((run.floor, run.room), (2, 1))
        none = Balance.build({}, {"secrets": []})
        run = boss_cleared(none)
        dungeon.advance(run, Scripted(), none)
        self.assertEqual((run.floor, run.room), (2, 1))
        zero_weight = bal_with(secret(weight=0))
        run = boss_cleared(zero_weight)
        dungeon.advance(run, Scripted(), zero_weight)
        self.assertEqual((run.floor, run.room), (2, 1))

    def test_only_a_just_beaten_boss_room_can_open_it(self):
        bal = Balance.build({"일반.시크릿확률": 100})
        for kind, room in (("monster", 4), ("empty", 10), ("chest", 9), ("monster", 10)):
            run = boss_cleared(bal, kind=kind, room=room)
            dungeon.advance(run, Scripted(randoms=[0.99, 0.99, 0.99]), bal)
            self.assertNotEqual(run.kind, "secret", (kind, room))

    def test_after_the_secret_boss_you_climb_without_a_second_roll(self):
        bal = Balance.build({"일반.시크릿확률": 100})
        run = in_secret_fight(bal)
        run.phase, run.enemy, run.pending_gear = "cleared", None, None                              # 시크릿 보스를 이겼다
        dungeon.advance(run, Scripted(randoms=[0.99, 0.99, 0.99]), bal)                            # 또 추첨하지 않는다
        self.assertEqual((run.floor, run.room, run.kind == "secret"), (2, 1, False))

    def test_on_the_last_floor_the_secret_room_comes_before_the_clear(self):
        bal = Balance.build({"상점.간격": 0})
        run = boss_cleared(bal, floor=bal.floors)
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual((run.kind, run.floor), ("secret", bal.floors))
        run.phase, run.enemy = "cleared", None
        dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.phase, "summit")                                            # 시크릿 보스를 이긴 뒤에도 꼭대기에서 클리어할지 묻는다

    def test_secret_bosses_are_picked_by_weight_and_scale_with_the_floor(self):
        bal = bal_with(secret(key="a", name="가", weight=0.25), secret(key="b", name="나", weight=0.75), overrides={"일반.시크릿확률": 100})
        for draw, name in ((0.0, "가"), (0.2499, "가"), (0.25, "나"), (0.9999, "나")):
            run = boss_cleared(bal)
            dungeon.advance(run, Scripted(randoms=[0.0, draw]), bal)
            self.assertEqual(run.enemy["name"], name, draw)
        run = boss_cleared(bal, floor=5)
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        self.assertEqual(run.enemy["max_hp"], balance.floor_scaled(500, 5, bal.floor_scale))
        self.assertEqual(run.enemy["damage"], balance.floor_scaled(100, 5, bal.floor_scale))

    def test_stage_counts_the_secret_room_as_the_last_room_of_its_floor(self):
        run = dungeon.new_run()
        run.floor, run.room = 2, 11
        self.assertEqual(run.stage, 20)
        run.room = 10
        self.assertEqual(run.stage, 20)

    def test_the_run_with_a_secret_boss_survives_saving(self):
        run = in_secret_fight(Balance.build({}))
        back = Run.from_json(run.to_json())
        self.assertEqual((back.kind, back.room, back.enemy["drops"]), ("secret", 11, run.enemy["drops"]))


class DropTests(unittest.TestCase):
    def kill(self, bal, rolls, how="attack", **kw):
        run = in_secret_fight(bal, **kw)
        run.enemy["hp"] = 1
        run.enemy["dodge"] = 0
        if how == "attack":
            dungeon.player_attack(run, Scripted(randoms=[0.99, *rolls]), bal)       # 맨 앞은 회피 굴림
        else:                                                                       # 가드 성공의 반사 피해로 쓰러뜨린다
            run.enemy["damage"] = 1000
            dungeon.player_defend(run, True, bal, Scripted(randoms=rolls))
        return run

    def test_both_drops_when_both_rolls_succeed(self):
        bal = bal_with(secret(drop_gear_chance=50, drop_item_chance=50, drop_item_amount=2), overrides={"일반.시크릿확률": 100})
        run = self.kill(bal, [0.49, 0.49])
        self.assertEqual(run.phase, "cleared")
        self.assertEqual((run.pending_gear, run.items), ("gun", {"golden_soup": 2}))
        text = "\n".join(run.events)
        self.assertIn("시크릿 보스의 드롭", text)
        self.assertIn("총", text)
        self.assertIn("황금 호박죽", text)
        self.assertIn("×2", text)
        self.assertFalse(run.can_advance)                                          # 장비는 장착/예비/버리기를 정해야 앞으로 간다
        self.assertIn("item_gain:golden_soup", run.last)
        self.assertEqual(run.loot, ["총", "황금 호박죽"])

    def test_each_drop_has_its_own_chance(self):
        bal = bal_with(secret(drop_gear_chance=50, drop_item_chance=50), overrides={"일반.시크릿확률": 100})
        run = self.kill(bal, [0.49, 0.5])
        self.assertEqual((run.pending_gear, run.items), ("gun", {}))
        run = self.kill(bal, [0.5, 0.49])
        self.assertEqual((run.pending_gear, run.items), (None, {"golden_soup": 1}))

    def test_if_neither_rolls_the_better_chance_is_forced_so_something_always_drops(self):
        bal = bal_with(secret(drop_gear_chance=10, drop_item_chance=20), overrides={"일반.시크릿확률": 100})
        run = self.kill(bal, [0.99, 0.99])
        self.assertEqual((run.pending_gear, run.items), (None, {"golden_soup": 1}))               # 아이템 확률이 더 높다
        bal = bal_with(secret(drop_gear_chance=30, drop_item_chance=20), overrides={"일반.시크릿확률": 100})
        run = self.kill(bal, [0.99, 0.99])
        self.assertEqual((run.pending_gear, run.items), ("gun", {}))
        bal = bal_with(secret(drop_gear_chance=25, drop_item_chance=25), overrides={"일반.시크릿확률": 100})   # 같으면 장비
        run = self.kill(bal, [0.99, 0.99])
        self.assertEqual((run.pending_gear, run.items), ("gun", {}))
        bal = bal_with(secret(drop_gear_chance=0, drop_item_chance=0), overrides={"일반.시크릿확률": 100})      # 0%여도 하나는 나온다
        run = self.kill(bal, [0.0, 0.0])
        self.assertTrue(run.pending_gear or run.items)

    def test_a_boss_with_only_one_kind_of_drop(self):
        only_gear = bal_with(secret(drop_item=None, drop_gear_chance=1), overrides={"일반.시크릿확률": 100})
        run = self.kill(only_gear, [0.99, 0.99])
        self.assertEqual((run.pending_gear, run.items), ("gun", {}))
        only_item = bal_with(secret(drop_gear=None, drop_item_chance=1), overrides={"일반.시크릿확률": 100})
        run = self.kill(only_item, [0.99, 0.99])
        self.assertEqual((run.pending_gear, run.items), (None, {"golden_soup": 1}))

    def test_a_drop_that_no_longer_exists_is_skipped_and_the_other_is_forced(self):
        bal = bal_with(secret(drop_gear="no_such_gear", drop_gear_chance=100, drop_item_chance=1), overrides={"일반.시크릿확률": 100})
        run = self.kill(bal, [0.0, 0.99])
        self.assertEqual((run.pending_gear, run.items), (None, {"golden_soup": 1}))
        both_gone = bal_with(secret(drop_gear="gone", drop_item="gone"), overrides={"일반.시크릿확률": 100})
        run = self.kill(both_gone, [0.0, 0.0])                                    # 줄 수 있는 게 없어도 오류 없이 끝난다
        self.assertEqual((run.phase, run.pending_gear, run.items), ("cleared", None, {}))

    def test_items_stack_with_what_you_already_hold(self):
        bal = bal_with(secret(drop_gear=None, drop_item_amount=3), overrides={"일반.시크릿확률": 100})
        run = self.kill(bal, [0.0, 0.0], items={"golden_soup": 2})
        self.assertEqual(run.items, {"golden_soup": 5})

    def test_a_kill_by_reflection_drops_too(self):
        bal = bal_with(secret(drop_gear_chance=100, drop_item_chance=100), overrides={"일반.시크릿확률": 100})
        run = in_secret_fight(bal, shield="shield")
        run.enemy["hp"], run.enemy["damage"] = 1, 1000
        dungeon.player_defend(run, True, bal, Scripted(randoms=[0.0, 0.0]))
        self.assertEqual((run.phase, run.pending_gear, run.items), ("cleared", "gun", {"golden_soup": 1}))

    def test_a_long_run_never_ends_without_a_drop(self):
        bal = bal_with(secret(drop_gear_chance=5, drop_item_chance=5), overrides={"일반.시크릿확률": 100})
        rng, empty = random.Random(11), 0
        for _ in range(3000):
            run = in_secret_fight(bal)
            run.enemy["hp"], run.enemy["dodge"] = 1, 0
            dungeon.player_attack(run, rng, bal)
            empty += not (run.pending_gear or run.items)
        self.assertEqual(empty, 0)

    def test_normal_bosses_and_monsters_drop_nothing_extra(self):
        bal = Balance.build({})
        run = boss_cleared(bal)
        dungeon.advance(run, Scripted(randoms=[0.99, 0.99]), bal)                    # 다음 층의 1번째 방(상자)
        run = dungeon.new_run(bal=bal)
        run.phase, run.kind, run.enemy = "battle", "boss", dungeon._spawn(balance.BOSSES[0], 1, bal)
        run.enemy["hp"], run.enemy["dodge"] = 1, 0
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual((run.phase, run.pending_gear, run.items), ("cleared", None, {}))


class PlayThroughTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_computer_handles_secret_rooms_all_the_way(self):
        for seed in range(4):
            store, locks = Store(":memory:"), Locks()
            store.update_settings(G, dungeon={"일반.시크릿확률": 100})
            store.add_item(G, U, store.ensure_system_items(G)["key"], 2)
            run = (await service.enter(store, locks, G, U)).run
            await service.set_auto(store, locks, G, U, run.rev, True)
            rng, steps, saw = random.Random(seed), 0, False
            while True:
                out = await service.auto_step(store, locks, rng, G, U)
                steps += 1
                self.assertTrue(out.ok, (seed, steps, out.text))
                self.assertLess(steps, 30000)
                saw = saw or (out.run is not None and out.run.kind == "secret")
                if out.ended:
                    break
            store.close()

    def test_the_simulator_handles_secret_rooms_and_stays_deterministic(self):
        bal = Balance.build({"일반.시크릿확률": 100})
        a, b = balance_sim.simulate(bal, 40, seed=9), balance_sim.simulate(bal, 40, seed=9)
        self.assertEqual(a, b)
        for entry in a.values():
            self.assertTrue(1 <= entry["avg_room"] <= dungeon.FLOORS * dungeon.ROOMS)


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World(rng=Scripted(randoms=[0.0, 0.0]))
        self.bot, self.store = self.w.bot, self.w.store
        self.old = (cog.SECRET_FRAME_DELAY, cog.AUTO_DELAY)
        cog.SECRET_FRAME_DELAY = cog.AUTO_DELAY = 0

    async def asyncTearDown(self):
        cog.SECRET_FRAME_DELAY, cog.AUTO_DELAY = self.old
        for task in list(self.bot.autos.values()):
            task.cancel()
        await self.w.close()

    def test_the_battle_screen_is_golden_and_shows_the_picture(self):
        bal = bal_with(secret(image="https://x.test/boss.gif"), overrides={"일반.시크릿확률": 100})
        run = in_secret_fight(bal)
        embed = run_embed(run, None, bal)
        self.assertIn("시크릿 방", embed.title)
        self.assertEqual(embed.color.value, 0xF1C40F)
        self.assertEqual(embed.image.url, "https://x.test/boss.gif")
        self.assertIn("SECRET", embed.fields[0].name)
        self.assertNotIn("11/10", embed.title)                                       # 11/10 같은 어색한 방 번호를 보이지 않는다
        plain = run_embed(boss_cleared(bal), None, bal)
        self.assertIn("방 10/10", plain.title)

    def test_the_intro_has_six_scenes_ending_with_the_name_and_the_line(self):
        enemy = {"name": "심연의 왕", "emoji": "🌌", "intro": "어둠이 숨을 쉰다", "image": "https://x.test/a.gif"}
        frames = secret_intro_frames(enemy)
        self.assertEqual(len(frames), 6)
        colors = [f.color.value for f in frames]
        self.assertEqual(len(set(colors)), 6)                                        # 장면마다 색이 달라 번쩍이는 느낌
        self.assertIn("？？？？？", frames[3].description)                              # 이름이 가려져 있다가
        self.assertIn("심연", frames[4].description)                                   # 절반쯤 드러나고
        self.assertNotIn("심연의 왕", frames[4].description)
        self.assertIn("『 심연의 왕 』", frames[5].description)                          # 마지막에 전부 나타난다
        self.assertIn("어둠이 숨을 쉰다", frames[5].description)
        self.assertEqual(frames[5].image.url, "https://x.test/a.gif")
        self.assertIsNone(frames[0].image.url if frames[0].image else None)
        plain = secret_intro_frames({"name": "A", "emoji": "x"})                      # 한 글자 이름·문구 없음도 괜찮다
        self.assertEqual(len(plain), 6)

    async def test_pressing_advance_into_the_secret_room_plays_the_cinematic_then_the_fight(self):
        self.store.update_settings(G, dungeon={"일반.시크릿확률": 100})
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        out = await service.enter(self.store, self.bot.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(room=dungeon.ROOMS, kind="boss", phase="cleared")
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        i = self.w.interaction()
        await respond(i, self.bot, rev, "advance")
        first = i.response.edit_message.await_args.kwargs
        self.assertIsNone(first["view"])
        self.assertEqual(first["embed"].title, "…")
        edits = i.edit_original_response.await_args_list
        self.assertEqual(len(edits), 6)                                              # 나머지 5장면 + 전투 화면
        self.assertIn("S E C R E T", edits[4].kwargs["embed"].title)
        last = edits[-1].kwargs
        self.assertIsInstance(last["view"], DungeonView)
        self.assertIn("시크릿 방", last["embed"].title)
        self.assertEqual(Run.from_json(self.store.dungeon_run(G, U)).kind, "secret")

    async def test_normal_moves_do_not_play_it(self):
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        out = await service.enter(self.store, self.bot.locks, G, U)
        i = self.w.interaction()
        self.bot.rng.randoms[:] = [0.99, 0.99, 0.99]
        await respond(i, self.bot, out.run.rev, "advance")
        self.assertEqual(i.edit_original_response.await_count, 0)
        self.assertNotEqual(i.response.edit_message.await_args.kwargs["embed"].title, "…")

    async def test_the_cinematic_also_plays_during_quick_play(self):
        self.w.bot.rng = random.Random(4)
        self.store.update_settings(G, dungeon={"일반.시크릿확률": 100, "일반.층수": 1, "일반.맨손공격력": 100000, "일반.최대체력": 100000})   # 한 층, 한 방에 쓰러뜨린다
        key = self.store.ensure_system_items(G)["key"]
        self.store.add_item(G, U, key, 2)
        out = await service.enter(self.store, self.bot.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(room=dungeon.ROOMS, kind="boss", phase="cleared")
        self.store.save_dungeon(G, U, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, U)).rev
        i = self.w.interaction()
        await start_auto(i, self.bot, rev)
        for task in list(self.bot.autos.values()):
            await task
        titles = [c.kwargs["embed"].title for c in i.edit_original_response.await_args_list]
        self.assertTrue(any("S E C R E T" in (t or "") for t in titles))
        self.assertTrue(any("WARNING" in (t or "") for t in titles))


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

    def boss(self, name):
        return next(s for s in self.store.balance(G).secrets if s.name == name)

    async def test_add_a_secret_boss_with_both_drops_and_decimal_chances(self):
        text = await self.run_cmd(ContentAdmin.secret_add, "심연의 왕", "🌌", 3000, 250, drop_gear="호박 대검", gear_chance=12.5,
                                  drop_item="영혼 에센스", item_chance=40, amount=2, weight=0.5, intro="어둠이 숨을 쉰다", image="https://x.test/a.gif")
        self.assertIn("시크릿 보스를 만들었어요", text)
        self.assertIn("호박 대검 12.5%", text)
        self.assertIn("영혼 에센스 ×2 40%", text)
        boss = self.boss("심연의 왕")
        self.assertEqual((boss.hp, boss.damage, boss.drop_gear, boss.drop_gear_chance, boss.drop_item, boss.drop_item_amount, boss.weight, boss.intro, boss.image),
                         (3000, 250, "pumpkin_blade", 12.5, "soul_essence", 2, 0.5, "어둠이 숨을 쉰다", "https://x.test/a.gif"))
        self.assertEqual(len(self.store.balance(G).secrets), 2)                       # 기본 시크릿 보스에 더해진다

    async def test_a_drop_is_required_and_must_exist(self):
        self.assertIn("하나는", await self.run_cmd(ContentAdmin.secret_add, "무드롭", "🌌", 10, 1))
        self.assertIn("드롭 장비가 없어요", await self.run_cmd(ContentAdmin.secret_add, "가짜", "🌌", 10, 1, drop_gear="없는장비"))
        self.assertIn("드롭 아이템이 없어요", await self.run_cmd(ContentAdmin.secret_add, "가짜", "🌌", 10, 1, drop_item="없는약"))
        self.assertIn("그림은", await self.run_cmd(ContentAdmin.secret_add, "그림이상", "🌌", 10, 1, drop_item="사탕", image="notaurl"))
        self.assertIn("이모지", await self.run_cmd(ContentAdmin.secret_add, "이모지이상", "ㅁ", 10, 1, drop_item="사탕"))
        self.assertEqual([s.name for s in self.store.balance(G).secrets], ["공허의 군주"])

    async def test_names_cannot_collide_with_monsters_and_bosses(self):
        self.assertIn("같은 이름", await self.run_cmd(ContentAdmin.secret_add, "해골 왕", "🌌", 10, 1, drop_item="사탕"))
        await self.run_cmd(ContentAdmin.secret_add, "심연의 왕", "🌌", 10, 1, drop_item="사탕")
        self.assertIn("같은 이름", await self.run_cmd(ContentAdmin.monster_add, "심연의 왕", "🐉", 10, 1))

    async def test_edit_values_and_clear_with_a_dash(self):
        await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주", hp=5000, damage=300, gear_chance=7.5, item_chance=0.5, amount=4, weight=2)
        boss = self.boss("공허의 군주")
        self.assertEqual((boss.hp, boss.damage, boss.drop_gear_chance, boss.drop_item_chance, boss.drop_item_amount, boss.weight), (5000, 300, 7.5, 0.5, 4, 2))
        await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주", intro="새 문구", image="https://x.test/b.gif")
        self.assertEqual((self.boss("공허의 군주").intro, self.boss("공허의 군주").image), ("새 문구", "https://x.test/b.gif"))
        await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주", intro="-", image="-", drop_item="-")          # 지우기
        boss = self.boss("공허의 군주")
        self.assertEqual((boss.intro, boss.image, boss.drop_item, boss.drop_gear), ("", "", None, "gun"))
        text = await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주", drop_gear="-")                         # 마지막 드롭은 못 지운다
        self.assertIn("하나는", text)
        self.assertEqual(self.boss("공허의 군주").drop_gear, "gun")
        await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주", drop_item="사탕")                              # 바꿔 넣기
        self.assertEqual(self.boss("공허의 군주").drop_item, "candy")
        self.assertIn("바꿀 내용", await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주"))
        self.assertIn("그런 항목이 없어요", await self.run_cmd(ContentAdmin.secret_edit, "없는왕", hp=5))

    async def test_delete_all_of_them_and_no_room_ever_opens(self):
        text = await self.run_cmd(ContentAdmin.secret_delete, "공허의 군주")
        self.assertIn("지웠어요", text)
        self.assertEqual(self.store.balance(G).secrets, ())
        run = boss_cleared(self.store.balance(G))
        dungeon.advance(run, Scripted(), self.store.balance(G))                        # 난수를 뽑지 않고 그냥 올라간다
        self.assertEqual((run.floor, run.room), (2, 1))
        self.assertIn("그런 항목이 없어요", await self.run_cmd(ContentAdmin.secret_delete, "공허의 군주"))

    async def test_a_gear_or_item_that_is_a_bosss_last_drop_cannot_be_deleted(self):
        await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주", drop_item="-")
        self.assertIn("마지막 드롭", await self.run_cmd(ContentAdmin.gear_delete, "총"))
        await self.run_cmd(ContentAdmin.secret_edit, "공허의 군주", drop_item="사탕")                              # 아이템 드롭이 있으니 장비는 지워도 된다
        self.assertIn("지웠어요", await self.run_cmd(ContentAdmin.gear_delete, "총"))
        text = await self.run_cmd(ContentAdmin.potion_delete, "사탕")
        self.assertIn("마지막 드롭", text)
        self.assertTrue(any(c.name == "사탕" for c in self.store.balance(G).consumables))

    async def test_a_deleted_drop_is_skipped_in_the_fight_but_something_still_drops(self):
        await self.run_cmd(ContentAdmin.gear_delete, "총")
        bal = self.store.balance(G)
        run = boss_cleared(bal)
        dungeon.advance(run, Scripted(randoms=[0.0, 0.0]), bal)
        run.enemy["hp"], run.enemy["dodge"] = 1, 0
        dungeon.player_attack(run, Scripted(randoms=[0.99, 0.99, 0.99]), bal)
        self.assertEqual(run.items, {"golden_soup": 1})

    async def test_the_list_command_shows_secret_bosses_and_their_drops(self):
        i = self.w.interaction(admin=True)
        await ContentAdmin.dungeon_list.callback(self.cog, i)
        embed, _ = sent_embed(i)
        fields = {f.name: f.value for f in embed.fields}
        self.assertIn("시크릿 보스 1개", fields)
        self.assertIn("공허의 군주", fields["시크릿 보스 1개"])
        self.assertIn("드롭(하나는 반드시)", fields["시크릿 보스 1개"])

    async def test_the_default_chance_is_ten_percent_and_the_command_shows_it(self):
        self.assertEqual(self.store.balance(G).secret_chance, 10.0)
        text = await self.run_cmd(ContentAdmin.secret_chance)
        self.assertIn("지금 시크릿 방 확률은 **10%**", text)
        self.assertIn("기본 10%", text)
        self.assertIn("65%", text)                                                    # 10층을 오르면 한 판에 한 번 이상 열릴 확률 1-(0.9^10)
        self.assertEqual(self.store.settings(G)["dungeon"], {})                         # 보기만 해서는 아무것도 바꾸지 않는다

    async def test_set_the_chance_with_decimals_zero_and_full(self):
        text = await self.run_cmd(ContentAdmin.secret_chance, 2.5)
        self.assertIn("10% → **2.5%**", text)
        self.assertEqual((self.store.balance(G).secret_chance, self.store.settings(G)["dungeon"]), (2.5, {"일반.시크릿확률": 2.5}))
        self.assertIn("2.5%", await self.run_cmd(ContentAdmin.secret_chance))
        text = await self.run_cmd(ContentAdmin.secret_chance, 0)
        self.assertIn("열리지 않아요", text)
        self.assertEqual(self.store.balance(G).secret_chance, 0)
        await self.run_cmd(ContentAdmin.secret_chance, 100)
        self.assertEqual(self.store.balance(G).secret_chance, 100)
        bal = self.store.balance(G)
        run = boss_cleared(bal)
        dungeon.advance(run, Scripted(randoms=[0.9999, 0.0]), bal)                    # 100%면 어떤 난수로도 열린다
        self.assertEqual(run.kind, "secret")

    async def test_going_back_to_the_default_clears_the_override_and_a_repeat_is_refused(self):
        await self.run_cmd(ContentAdmin.secret_chance, 3)
        text = await self.run_cmd(ContentAdmin.secret_chance, 10)
        self.assertIn("3% → **10%**", text)
        self.assertEqual(self.store.settings(G)["dungeon"], {})                         # 기본값이면 기록을 지운다
        self.assertIn("이미 10%예요", await self.run_cmd(ContentAdmin.secret_chance, 10))

    async def test_the_command_and_the_dungeon_setting_are_the_same_value(self):
        await self.run_cmd(ContentAdmin.secret_chance, 4)
        self.assertEqual(self.store.balance(G).secret_chance, 4)
        from cogs.dungeon_admin import DungeonAdmin
        admin = DungeonAdmin(self.w.bot)
        i = self.w.interaction(admin=True)
        await DungeonAdmin.set_param.callback(admin, i, "일반.시크릿확률", 6.5)
        self.assertEqual(self.store.balance(G).secret_chance, 6.5)
        self.assertIn("6.5%", await self.run_cmd(ContentAdmin.secret_chance))

    async def test_the_chance_command_is_admin_only_and_range_checked(self):
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.secret_chance, 50, admin=False))
        self.assertEqual(self.store.balance(G).secret_chance, 10)
        option = next(p for p in ContentAdmin.secret_chance.parameters if p.name == "percent")
        self.assertEqual((option.type.name, option.min_value, option.max_value, option.required), ("number", 0, 100, False))

    async def test_admin_only_and_autocomplete(self):
        for command, args in ((ContentAdmin.secret_add, ("왕", "🌌", 10, 1)), (ContentAdmin.secret_edit, ("공허의 군주",)), (ContentAdmin.secret_delete, ("공허의 군주",))):
            self.assertIn("관리자만", await self.run_cmd(command, *args, admin=False))
        self.assertEqual([s.name for s in self.store.balance(G).secrets], ["공허의 군주"])
        i = self.w.interaction(admin=True)
        self.assertEqual([c.value for c in await self.cog.secret_names(i, "")], ["공허의 군주"])
        self.assertIn("총", [c.value for c in await self.cog.secret_gear_names(i, "")])
        self.assertIn("사탕", [c.value for c in await self.cog.secret_item_names(i, "")])

    def test_options_are_declared(self):
        kinds = {p.name: p.type.name for p in ContentAdmin.secret_add.parameters}
        self.assertEqual((kinds["hp"], kinds["gear_chance"], kinds["item_chance"], kinds["amount"], kinds["image"]), ("integer", "number", "number", "integer", "string"))

    async def test_the_reset_command_brings_the_default_back(self):
        await self.run_cmd(ContentAdmin.secret_delete, "공허의 군주")
        self.assertEqual(self.store.balance(G).secrets, ())
        i = self.w.interaction(admin=True)
        await ContentAdmin.content_reset.callback(self.cog, i, Choice("secrets"))
        view = i.response.send_message.await_args.kwargs["view"]
        await view.confirm(self.w.interaction(admin=True))
        self.assertEqual([s.name for s in self.store.balance(G).secrets], ["공허의 군주"])
