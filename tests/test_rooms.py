"""함정 방과 커스텀 방: 방 배치에 섞여 나오고, 들어서자마자 효과가 일어난다. 규칙·검증·서비스·명령어·목록."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
import dungeon_auto
import dungeon_service as service
from balance import Balance, check_entity, check_value, entity_dict
from cogs.content_admin import ContentAdmin
from dungeon import Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
MAX = balance.DEFAULT.max_hp


def room(**kw):
    data = dict(key="r", name="샘물", emoji="⛲", text="맑은 샘이 있어요.", weight=10, heal=100)
    data.update(kw)
    return data


def bal_with(*rooms, **overrides):
    raw = {"rooms": [entity_dict(check_entity("rooms", r)[0]) for r in rooms]} if rooms else {}
    return Balance.build(overrides, raw)


def climbing(bal, floor=1, room_no=1, **kw):
    """floor층 room_no번째 방을 막 마친 사람(다음 advance가 room_no + 1번째 방)."""
    run = dungeon.new_run(bal=bal)
    run.floor, run.room, run.phase, run.kind = floor, room_no, "cleared", "empty"
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class TrapRuleTests(unittest.TestCase):
    def test_traps_are_off_by_default_so_the_old_plan_is_unchanged(self):
        bal = Balance.build({})
        self.assertTrue(all(kind != "trap" for table in bal.room_plan for kind, _ in table))
        self.assertEqual(bal.trap_damage, 100)

    def test_a_trap_weight_puts_it_in_that_rooms_draw(self):
        bal = Balance.build({"방2.함정": 10})
        self.assertEqual(bal.room_plan[1], (("empty", 40), ("trap", 10), ("monster", 60)))
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.4]), 2, bal), "trap")                 # 40~50 / 110
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.99]), 2, bal), "monster")
        self.assertEqual(check_value("방2.함정", 5), (5, None))
        self.assertIsNotNone(check_value("방2.함정", -1)[1])
        self.assertEqual(check_value("함정.피해", 250), (250, None))

    def test_entering_a_trap_hurts_and_you_can_keep_going(self):
        bal = Balance.build({"방2.함정": 1000, "방2.빈방": 0, "방2.잡몹": 0})
        run = climbing(bal)
        events = dungeon.advance(run, Scripted(), bal)
        self.assertEqual((run.kind, run.phase, run.room, run.hp), ("trap", "empty", 2, MAX - 100))
        self.assertTrue(any("함정 방!" in e and "**100** 피해" in e for e in events))
        self.assertTrue(run.can_advance)

    def test_the_damage_grows_with_the_floor_like_monsters(self):
        bal = Balance.build({"방2.함정": 1000, "방2.빈방": 0, "방2.잡몹": 0, "함정.피해": 100, "일반.층당강화": 10})
        run = climbing(bal, floor=5)
        dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.hp, MAX - 140)                                                          # 1층의 140%

    def test_armor_reduction_and_invulnerability_apply_but_not_the_guard(self):
        bal = Balance.build({"방2.함정": 1000, "방2.빈방": 0, "방2.잡몹": 0})
        run = climbing(bal, armor="pumpkin_armor", reduce_percent=50, reduce_turns=2)
        events = dungeon.advance(run, Scripted(), bal)
        self.assertEqual(run.hp, MAX - 44)                                                           # 100 → 갑옷 12% → 88 → 감소 50% → 44
        self.assertTrue(any("갑옷 12%" in e for e in events))
        safe = climbing(bal, invuln_turns=1)
        events = dungeon.advance(safe, Scripted(), bal)
        self.assertEqual(safe.hp, MAX)
        self.assertTrue(any("무적" in e for e in events))
        self.assertEqual(safe.invuln_turns, 1)                                                       # 싸움 밖이라 무적 턴은 줄지 않는다

    def test_a_trap_can_kill(self):
        bal = Balance.build({"방2.함정": 1000, "방2.빈방": 0, "방2.잡몹": 0})
        run = climbing(bal, hp=50)
        events = dungeon.advance(run, Scripted(), bal)
        self.assertEqual((run.phase, run.hp), ("dead", 0))
        self.assertTrue(any("다운됐어요" in e for e in events))

    def test_zero_damage_is_harmless(self):
        bal = Balance.build({"방2.함정": 1000, "방2.빈방": 0, "방2.잡몹": 0, "함정.피해": 0})
        run = climbing(bal)
        events = dungeon.advance(run, Scripted(), bal)
        self.assertEqual((run.hp, run.phase), (MAX, "empty"))
        self.assertTrue(any("아무 일도 없었어요" in e for e in events))

    def test_the_flavor_text_varies_by_place_and_is_stable(self):
        bal = Balance.build({"방2.함정": 1000, "방2.빈방": 0, "방2.잡몹": 0})
        texts = set()
        for floor in range(1, 5):
            run = climbing(bal, floor=floor)
            texts.add(dungeon.advance(run, Scripted(), bal)[0])
        self.assertEqual(len(texts), 4)


class CustomRoomValidationTests(unittest.TestCase):
    def test_a_room_needs_text_or_an_effect(self):
        self.assertIsNone(check_entity("rooms", room(text="", heal=0))[0])
        self.assertIn("문구나 효과", check_entity("rooms", room(text="", heal=0))[1])
        for kw in (dict(text="", heal=0, damage=5), dict(text="", heal=0, candy=5), dict(text="", heal=0, item="candy"), dict(heal=0)):
            self.assertIsNone(check_entity("rooms", room(**kw))[1], kw)

    def test_numbers_and_places_are_checked(self):
        for bad in (dict(damage=-1), dict(heal=100001), dict(candy=1.5), dict(item_amount=0), dict(weight=-1), dict(weight=1001),
                    dict(rooms=[10]), dict(rooms=[0]), dict(rooms=["3"]), dict(floors=[0]), dict(text="가" * 301)):
            self.assertIsNotNone(check_entity("rooms", room(**bad))[1], bad)
        item = check_entity("rooms", room(rooms=[5, 3, 3], floors=[2, 1]))[0]
        self.assertEqual((item.rooms, item.floors), ((3, 5), (1, 2)))

    def test_round_trip_and_defaults(self):
        item = check_entity("rooms", room(damage=3, candy=4, item="candy", item_amount=2, rooms=[2], floors=[7]))[0]
        loaded = check_entity("rooms", json.loads(json.dumps(entity_dict(item))))[0]
        self.assertEqual(loaded, item)
        plain = check_entity("rooms", room())[0]
        self.assertEqual((plain.rooms, plain.floors, plain.item, plain.item_amount, plain.weight), ((), (), None, 1, 10))

    def test_balance_starts_with_no_custom_rooms_and_keeps_the_saved_ones(self):
        self.assertEqual(Balance.build({}).rooms, ())
        self.assertEqual([r.name for r in bal_with(room()).rooms], ["샘물"])


class DrawTests(unittest.TestCase):
    def test_a_custom_room_joins_the_draw_of_its_rooms_only(self):
        bal = bal_with(room(rooms=[2], weight=100))
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.99]), 2, bal), "custom:r")           # 40 + 60 + 100 중 마지막
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.0]), 2, bal), "empty")
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.99]), 4, bal), "monster")             # 2번째 방에만 나온다

    def test_a_single_entry_room_becomes_a_draw_when_a_custom_room_can_appear(self):
        bal = bal_with(room(rooms=[1], weight=100))
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.1]), 1, bal), "chest")
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.9]), 1, bal), "custom:r")
        self.assertEqual(dungeon.roll_room(Scripted(), 3, bal), "chest")                             # 다른 방은 그대로: 난수를 뽑지 않는다

    def test_the_boss_room_never_has_a_custom_room(self):
        bal = bal_with(room(weight=1000))
        self.assertEqual(dungeon.roll_room(Scripted(), 10, bal), "boss")
        self.assertEqual(dungeon.custom_rooms_for(bal, 10, 1), [])

    def test_floors_and_zero_weight_limit_the_candidates(self):
        bal = bal_with(room(key="a", name="가", floors=[3]), room(key="b", name="나", weight=0), room(key="c", name="다"))
        self.assertEqual([c.key for c in dungeon.custom_rooms_for(bal, 2, 3)], ["a", "c"])
        self.assertEqual([c.key for c in dungeon.custom_rooms_for(bal, 2, 1)], ["c"])

    def test_the_weight_is_in_the_same_unit_as_the_plan(self):
        bal = bal_with(room(weight=10, rooms=[2]))
        kinds = [dungeon.roll_room(Scripted(randoms=[i / 1100 + 0.0001]), 2, bal) for i in range(1100)]
        share = kinds.count("custom:r") / len(kinds)
        self.assertAlmostEqual(share, 10 / 110, delta=0.002)                                         # 10 / (40 + 60 + 10)


class EffectTests(unittest.TestCase):
    def enter(self, bal, **kw):
        run = climbing(bal, **kw)
        events = dungeon.advance(run, Scripted(randoms=[0.99]), bal)
        return run, events

    def test_text_heal_and_the_room_screen(self):
        bal = bal_with(room(heal=300, rooms=[2], weight=1000))
        run, events = self.enter(bal, hp=500)
        self.assertEqual((run.kind, run.phase, run.hp), ("custom", "empty", 800))
        self.assertIn("⛲ **샘물**\n맑은 샘이 있어요.", events)
        self.assertTrue(any("HP +300" in e for e in events))
        self.assertTrue(run.can_advance)

    def test_heal_stops_at_the_maximum(self):
        bal = bal_with(room(heal=300, rooms=[2], weight=1000))
        run, events = self.enter(bal, hp=MAX - 10)
        self.assertEqual(run.hp, MAX)
        self.assertTrue(any("HP +10" in e for e in events))

    def test_damage_uses_armor_and_can_kill(self):
        bal = bal_with(room(heal=0, damage=200, rooms=[2], weight=1000))
        run, events = self.enter(bal, armor="pumpkin_armor")
        self.assertEqual(run.hp, MAX - 176)
        dead, events = self.enter(bal, hp=100)
        self.assertEqual((dead.phase, dead.hp), ("dead", 0))
        self.assertTrue(any("다운됐어요" in e for e in events))

    def test_damage_comes_before_healing(self):
        bal = bal_with(room(heal=500, damage=300, rooms=[2], weight=1000))
        run, _ = self.enter(bal, hp=200)
        self.assertEqual(run.phase, "dead")                                                          # 피해로 먼저 다운되면 회복은 없다
        run, _ = self.enter(bal, hp=600)
        self.assertEqual(run.hp, 800)

    def test_candy_waits_for_the_service_and_items_go_into_the_bag(self):
        bal = bal_with(room(heal=0, candy=40, item="candy", item_amount=3, rooms=[2], weight=1000))
        run, events = self.enter(bal)
        self.assertEqual((run.room_candy, run.items), (40, {"candy": 3}))
        self.assertIn("item_gain:candy", run.last)
        self.assertTrue(any("소형 배터리" in e and "×3" in e for e in events))

    def test_a_deleted_item_or_room_is_skipped_without_breaking(self):
        bal = bal_with(room(heal=50, item="gone", rooms=[2], weight=1000))
        run, events = self.enter(bal)
        self.assertEqual((run.items, run.hp), ({}, MAX))
        stale = climbing(bal)
        stale_rng = Scripted(randoms=[0.99])
        orig = dungeon.roll_room
        dungeon.roll_room = lambda *a, **k: "custom:vanished"
        try:
            dungeon.advance(stale, stale_rng, bal)
        finally:
            dungeon.roll_room = orig
        self.assertEqual((stale.kind, stale.phase), ("empty", "empty"))

    def test_old_saves_have_no_room_candy(self):
        data = json.loads(dungeon.new_run().to_json())
        data.pop("room_candy")
        self.assertEqual(Run.from_json(json.dumps(data)).room_candy, 0)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    def tearDown(self):
        self.store.close()

    async def start(self, **data):
        out = await service.enter(self.store, self.locks, G, U)
        saved = json.loads(out.run.to_json())
        saved.update(phase="cleared", kind="empty", room=1, **data)
        self.store.save_dungeon(G, U, json.dumps(saved))
        return Run.from_json(self.store.dungeon_run(G, U)).rev

    async def test_candy_from_a_room_is_paid_once_and_shown(self):
        content.add(self.store, G, "rooms", dict(name="보물방", emoji="💰", text="금화 더미!", weight=1000, candy=77, rooms=[2]))
        rev = await self.start()
        before = self.store.wallet(G, U)
        out = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, rev, "advance")
        self.assertTrue(out.ok, out.text)
        self.assertEqual(self.store.wallet(G, U), before + 77)
        self.assertEqual(out.run.room_candy, 0)
        self.assertTrue(any("방에서 얻은 재화" in e and "77" in e for e in out.run.events))
        self.assertEqual(Run.from_json(self.store.dungeon_run(G, U)).room_candy, 0)

    async def test_a_trap_death_ends_the_dungeon_and_a_revive_ticket_saves_it(self):
        self.store.update_settings(G, dungeon={"방2.함정": 1000, "방2.빈방": 0, "방2.잡몹": 0})
        rev = await self.start(hp=30)
        out = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, rev, "advance")
        self.assertEqual(out.ended, "dead")
        self.assertIsNone(self.store.dungeon_run(G, U))
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        self.store.add_item(G, U, self.store.revive_item(G)["id"], 1)
        rev = await self.start(hp=30)
        out = await service.act(self.store, self.locks, Scripted(randoms=[0.99]), G, U, rev, "advance")
        self.assertIsNone(out.ended)
        self.assertEqual(out.notes, ["revived"])
        self.assertTrue(out.run.can_advance)
        self.assertEqual(out.run.hp, MAX * balance.REVIVE_HEAL // 100)


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

    def rooms(self):
        return {r.name: r for r in self.store.balance(G).rooms}

    async def test_add_edit_and_delete_a_room(self):
        text = await self.run_cmd(ContentAdmin.room_add, "샘물", "⛲", "맑은 샘!", 12, heal=200, candy=5, item="소형 배터리", amount=2, places="2,4", floors="3-5")
        self.assertIn("샘물", text)
        r = self.rooms()["샘물"]
        self.assertEqual((r.heal, r.candy, r.item_amount, r.rooms, r.floors, r.weight), (200, 5, 2, (2, 4), (3, 4, 5), 12))
        self.assertEqual(r.item, "candy")
        await self.run_cmd(ContentAdmin.room_edit, "샘물", damage=30, places="전체", text="-", item="-", weight=0.5)
        r = self.rooms()["샘물"]
        self.assertEqual((r.damage, r.rooms, r.text, r.item, r.weight), (30, (), "", None, 0.5))
        await self.run_cmd(ContentAdmin.room_delete, "샘물")
        self.assertEqual(self.rooms(), {})

    async def test_bad_input_is_explained(self):
        self.assertIn("문구나 효과", await self.run_cmd(ContentAdmin.room_add, "빈 방", "⛲"))
        self.assertIn("1~9", await self.run_cmd(ContentAdmin.room_add, "보스방", "⛲", "문구", places="10"))
        self.assertIn("층 번호가 아니에요", await self.run_cmd(ContentAdmin.room_add, "이상", "⛲", "문구", floors="abc"))
        self.assertIn("그런 소모품이 없어요", await self.run_cmd(ContentAdmin.room_add, "이상", "⛲", "문구", item="없는약"))
        self.assertIn("이모지", await self.run_cmd(ContentAdmin.room_add, "이상", "ㅁ", "문구"))
        self.assertEqual(self.rooms(), {})
        await self.run_cmd(ContentAdmin.room_add, "샘물", "⛲", "문구")
        self.assertIn("같은 이름", await self.run_cmd(ContentAdmin.room_add, "샘물", "⛲", "문구"))
        self.assertIn("그런 항목이 없어요", await self.run_cmd(ContentAdmin.room_edit, "없는방", damage=3))

    async def test_the_limit_and_admin_only(self):
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.room_add, "샘물", "⛲", "문구", admin=False))
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.trap_chance, 10, admin=False))
        self.assertIn("관리자만", await self.run_cmd(ContentAdmin.trap_damage, 10, admin=False))
        for n in range(balance.SECTION_LIMITS["rooms"]):
            content.add(self.store, G, "rooms", dict(name=f"방{n}", emoji="⛲", text="문구"))
        self.assertIn("최대 30개", await self.run_cmd(ContentAdmin.room_add, "하나 더", "⛲", "문구"))

    async def test_the_list_shows_rooms_only_when_there_are_some(self):
        i = self.w.interaction(admin=True)
        await ContentAdmin.dungeon_list.callback(self.cog, i, None)
        self.assertEqual([f.name.split()[0] for f in sent_embed(i)[0].fields], ["몬스터", "보스", "장비", "소모품", "시크릿"])
        self.assertNotIn("view", i.response.send_message.await_args.kwargs)                         # 한 쪽에 다 들어간다
        await self.run_cmd(ContentAdmin.room_add, "샘물", "⛲", "맑은 샘!", heal=200, places="2")
        i = self.w.interaction(admin=True)
        await ContentAdmin.dungeon_list.callback(self.cog, i, type("C", (), {"value": "rooms"})())
        fields = {f.name: f.value for f in sent_embed(i)[0].fields}
        key = next(n for n in fields if n.startswith("커스텀 방"))
        self.assertIn("샘물", fields[key])
        self.assertIn("회복 200", fields[key])
        self.assertIn("2번째 방", fields[key])

    async def test_trap_chance_view_set_and_reset(self):
        self.assertIn("2번째 0.0%", await self.run_cmd(ContentAdmin.trap_chance))
        text = await self.run_cmd(ContentAdmin.trap_chance, 10)
        self.assertIn("2번째 10.0%", text)
        bal = self.store.balance(G)
        self.assertEqual({r: dict(bal.room_plan[r - 1]).get("trap", 0) for r in (2, 4, 6, 8)}, {2: 11.1111, 4: 11.1111, 6: 11.1111, 8: 11.1111})
        self.assertEqual(dict(bal.room_plan[2]), {"chest": 100})                                       # 상자 방은 그대로
        await self.run_cmd(ContentAdmin.trap_chance, 0)
        self.assertTrue(all(kind != "trap" for table in self.store.balance(G).room_plan for kind, _ in table))
        self.assertEqual(self.store.settings(G)["dungeon"], {})                                        # 기본값으로 돌아오면 기록을 지운다

    async def test_trap_chance_follows_the_other_weights_of_the_room(self):
        self.store.update_settings(G, dungeon={"방2.빈방": 10, "방2.잡몹": 10})
        await self.run_cmd(ContentAdmin.trap_chance, 50)
        table = dict(self.store.balance(G).room_plan[1])
        self.assertAlmostEqual(table["trap"], 20)

    async def test_trap_damage_view_and_set(self):
        self.assertIn("100", await self.run_cmd(ContentAdmin.trap_damage))
        self.assertIn("250", await self.run_cmd(ContentAdmin.trap_damage, 250))
        self.assertEqual(self.store.balance(G).trap_damage, 250)
        await self.run_cmd(ContentAdmin.trap_damage, 100)
        self.assertEqual(self.store.settings(G)["dungeon"], {})

    async def test_reset_removes_custom_rooms_too(self):
        await self.run_cmd(ContentAdmin.room_add, "샘물", "⛲", "문구")
        import content as c
        c.reset(self.store, G, ("rooms",))
        self.assertEqual(self.rooms(), {})

    def test_option_names_and_counts(self):
        for command in (ContentAdmin.room_add, ContentAdmin.room_edit):
            self.assertLessEqual(len(command.parameters), 25)
        self.assertTrue({"문구", "가중치", "방번호", "층"} <= {p.display_name for p in ContentAdmin.room_add.parameters})


class WholeRunTests(unittest.TestCase):
    def test_the_simulator_and_the_computer_cope_with_traps_and_custom_rooms(self):
        bal = Balance.build({"방2.함정": 20, "방4.함정": 20}, {"rooms": [entity_dict(check_entity("rooms", room(heal=0, damage=50, candy=3, item="candy"))[0])]})
        result = balance_sim.simulate(bal, 30, seed=4)
        self.assertIn("attack", result)
        self.assertEqual(balance_sim.simulate(bal, 30, seed=4), result)
        run = dungeon.new_run(bal=bal)
        run.phase = "empty"
        self.assertEqual(dungeon_auto.choose(run, bal), ("advance", None))

    def test_traps_make_the_default_dungeon_harder(self):
        base = balance_sim.simulate(Balance.build({}), 300, seed=7)["attack"]["clear"]
        trapped = balance_sim.simulate(Balance.build({"방2.함정": 900, "방4.함정": 900, "방6.함정": 900, "방8.함정": 900, "함정.피해": 300}), 300, seed=7)["attack"]["clear"]
        self.assertLess(trapped, base)
