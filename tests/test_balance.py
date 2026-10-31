"""던전 밸런스 시험: 기본값, 항목 검사, 값 적용, 서버별 저장, 관리자 명령어, 시뮬레이터."""
import json
import random
import unittest

import discord

import balance
import balance_sim
import dungeon
import dungeon_service as service
from balance import PARAMS, Balance
from cogs.dungeon_admin import ConfirmResetAll, DungeonAdmin, param_choices, settings_lines
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7


def build(**changes):
    """{'몬스터.슬라임.체력': 500} 같은 값을 적용한 Balance."""
    return Balance.build(changes)


class DefaultsTests(unittest.TestCase):
    def test_defaults_match_the_module_constants_everyone_else_imports(self):
        bal = Balance.build({})
        self.assertEqual((bal.max_hp, bal.base_damage, bal.guard_chance, bal.guard_base, bal.guard_heal, bal.reflect_base),
                         (dungeon.MAX_HP, dungeon.BASE_DAMAGE, dungeon.GUARD_CHANCE, dungeon.GUARD_BASE, dungeon.GUARD_HEAL,
                          dungeon.REFLECT_BASE))
        self.assertEqual((bal.monsters, bal.bosses, bal.gear, bal.consumables), (dungeon.MONSTERS, dungeon.BOSSES, dungeon.GEAR,
                                                                                  dungeon.CONSUMABLES))
        self.assertEqual(bal.room_plan, dungeon.ROOM_PLAN)
        self.assertEqual(bal.clear_candy, dungeon.CLEAR_CANDY)

    def test_every_default_sits_inside_its_own_range_and_validates(self):
        for param in PARAMS.values():
            self.assertTrue(param.low <= param.default <= param.high, param.key)
        self.assertIsNone(balance.validate({}))
        self.assertEqual(balance.warnings(Balance.build({})), [])

    def test_param_keys_cover_every_table_row_and_are_unique(self):
        keys = set(PARAMS)
        self.assertEqual(len(keys), len(PARAMS))
        for m in dungeon.MONSTERS:
            self.assertTrue({f"몬스터.{m.name}.체력", f"몬스터.{m.name}.공격", f"몬스터.{m.name}.회피"} <= keys)
        for b in dungeon.BOSSES:
            self.assertIn(f"보스.{b.name}.체력", keys)
            self.assertNotIn(f"보스.{b.name}.회피", keys)                       # 보스는 회피 불가라 항목이 없다
        import re
        self.assertEqual(sum(1 for k in keys if re.match(r"방\d+\.", k)), balance.ROOMS * len(balance.ROOM_KINDS))

    def test_descriptions_follow_the_numbers(self):
        self.assertEqual(dungeon.GEAR_BY_KEY["dagger"].description, "공격 피해량 +35, 크리티컬(2배) 확률 50%")
        bal = build(**{"장비.낡은 몽둥이.공격력": 77, "아이템.사탕.회복량": 123})
        self.assertEqual(bal.gear_by_key["club"].description, "공격 피해량 +77")
        self.assertEqual(bal.item_by_key["candy"].description, "HP +123")


class ValidationTests(unittest.TestCase):
    def test_unknown_key_wrong_type_range_and_integer(self):
        self.assertIn("항목이 없어요", balance.check_value("몬스터.없는애.체력", 5)[1])
        self.assertIn("숫자", balance.check_value("일반.최대체력", True)[1])
        self.assertIn("숫자", balance.check_value("일반.최대체력", float("nan"))[1])
        self.assertIn("사이", balance.check_value("일반.최대체력", 5)[1])
        self.assertIn("사이", balance.check_value("방어.성공확률", 101)[1])
        self.assertIn("정수", balance.check_value("일반.최대체력", 1000.5)[1])
        self.assertEqual(balance.check_value("일반.최대체력", 2000.0), (2000, None))
        self.assertEqual(balance.check_value("보상.획득권확률", 12.5), (12.5, None))          # 확률은 소수 허용

    def test_combinations_that_break_the_game_are_refused(self):
        self.assertIn("최소", balance.validate({"보상.최소": 500}))                            # 기본 최대 150보다 큼
        zero_room = {"방1.상자": 0}                                                            # 1번째 방은 상자뿐 → 전부 0
        self.assertIn("1번째 방", balance.validate(zero_room))
        self.assertIsNone(balance.validate({"방1.상자": 0, "방1.잡몹": 5}))
        all_gear = {f"장비.{g.name}.등장": 0 for g in dungeon.GEAR}
        self.assertIn("장비", balance.validate(all_gear))
        all_items = {f"아이템.{c.name}.등장": 0 for c in dungeon.CONSUMABLES}
        self.assertIn("아이템", balance.validate(all_items))

    def test_garbage_in_storage_is_ignored_not_fatal(self):
        bal = Balance.build({"몬스터.슬라임.체력": 999, "없는.항목": 5, "일반.최대체력": "많이", "방어.성공확률": 500,
                             "몬스터.마녀.체력": 1.5, "일반.맨손공격력": True})
        self.assertEqual([m.hp for m in bal.monsters if m.key == "slime"], [999])
        self.assertEqual((bal.max_hp, bal.guard_chance, bal.base_damage), (1000, 60, 20))
        self.assertEqual(Balance.build("엉망").max_hp, 1000)

    def test_farmable_guard_is_warned_about_but_allowed(self):
        loose = {"방어.회복률": 300}
        self.assertIsNone(balance.validate(loose))
        self.assertTrue(any("방어" in w for w in balance.warnings(Balance.build(loose))))
        self.assertTrue(any("0%" in w for w in balance.warnings(build(**{"방어.성공확률": 0}))))


class EffectTests(unittest.TestCase):
    """바꾼 값이 실제 규칙에 반영되는지(기본값과 다르게 움직이는지)."""

    def test_max_hp_start_and_heal_cap(self):
        bal = build(**{"일반.최대체력": 2000})
        run = dungeon.new_run(bal=bal)
        self.assertEqual(run.hp, 2000)
        run.hp, run.items = 1900, {"pumpkin_soup": 1}
        dungeon.use_consumable(run, "pumpkin_soup", bal)
        self.assertEqual(run.hp, 2000)

    def test_base_damage_and_weapon_attack(self):
        bal = build(**{"일반.맨손공격력": 50, "장비.낡은 몽둥이.공격력": 5})
        self.assertEqual(dungeon.player_damage(dungeon.new_run(), bal), 50)
        self.assertEqual(dungeon.player_damage(dungeon.new_run(weapon="club"), bal), 55)
        self.assertEqual(dungeon.player_damage(dungeon.new_run(weapon="club")), 50)             # bal 없이는 기본값

    def test_monster_numbers_and_rat_dodge(self):
        bal = build(**{"몬스터.슬라임.체력": 500, "몬스터.거대 쥐.회피": 80})
        slime = next(m for m in bal.monsters if m.key == "slime")
        self.assertEqual(dungeon._spawn(slime)["hp"], 500)
        run = dungeon.new_run(bal=bal)
        run.enemy = dungeon._spawn(next(m for m in bal.monsters if m.key == "giant_rat"))
        self.assertEqual(dungeon.enemy_dodge(run, bal), 80)                  # 나타날 때의 회피가 몬스터에 저장된다
        default_rat = dungeon.new_run()
        default_rat.enemy = dungeon._spawn(next(m for m in dungeon.MONSTERS if m.key == "giant_rat"))
        self.assertEqual(dungeon.enemy_dodge(default_rat), 30)

    def test_weapon_dodge_conversion_value_is_adjustable_but_bosses_stay_undodgeable(self):
        bal = build(**{"장비.총.회피변환": 5})
        run = dungeon.new_run(weapon="gun", bal=bal)
        run.enemy = dungeon._spawn(bal.monsters[1])
        self.assertEqual(dungeon.enemy_dodge(run, bal), 5)
        run.enemy = dungeon._spawn(bal.bosses[0])
        self.assertEqual(dungeon.enemy_dodge(run, bal), 0)

    def test_guard_numbers(self):
        bal = build(**{"방어.성공확률": 100, "방어.기본피해감소": 100, "방어.회복률": 0, "방어.기본반사": 0})
        self.assertTrue(dungeon.roll_guard(Scripted(randoms=[0.9999]), bal))
        self.assertFalse(dungeon.roll_guard(Scripted(randoms=[0.9999]), build(**{"방어.성공확률": 0})))
        run = dungeon.new_run(bal=bal)
        run.enemy, run.phase = dungeon._spawn(bal.monsters[2]), "battle"
        run.hp = 500
        dungeon.player_defend(run, True, bal)
        self.assertEqual((run.hp, run.enemy["hp"]), (500, bal.monsters[2].hp))                  # 전부 막고, 회복·반사 없음

    def test_shield_gear_values(self):
        bal = build(**{"장비.방패.가드": 50, "장비.방패.반사": 100})
        run = dungeon.new_run(shield="shield", bal=bal)
        self.assertEqual((dungeon.guard_percent(run, bal), dungeon.reflect_percent(run, bal)), (50, 100))

    def test_reward_numbers(self):
        bal = build(**{"보상.최소": 700, "보상.최대": 700, "보상.획득권확률": 100})
        self.assertEqual(dungeon.roll_clear_reward(Scripted(randoms=[0.9999]), bal), (700, True))
        bal = build(**{"보상.획득권확률": 0})
        self.assertEqual(dungeon.roll_clear_reward(Scripted(randoms=[0.0], ints=[60]), bal), (60, False))

    def test_room_plan_overrides(self):
        bal = build(**{"방1.상자": 0, "방1.잡몹": 1})
        self.assertEqual(dungeon.roll_room(Scripted(), 1, bal), "monster")                       # 칸이 하나뿐이면 난수를 뽑지 않는다
        bal = build(**{"방2.빈방": 0})
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.0]), 2, bal), "monster")
        bal = build(**{"방2.빈방": 1, "방2.잡몹": 3})                                             # 합이 100이 아니어도 비율로 쓴다
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.2499]), 2, bal), "empty")
        self.assertEqual(dungeon.roll_room(Scripted(randoms=[0.25]), 2, bal), "monster")

    def test_chest_weights_need_not_sum_to_100(self):
        only_dagger = {f"장비.{g.name}.등장": (1 if g.key == "dagger" else 0) for g in dungeon.GEAR}
        bal = build(**only_dagger)
        for draw in (0.0, 0.5, 0.9999):
            run = dungeon.new_run(bal=bal)
            dungeon.open_chest(run, Scripted(randoms=[draw, 0.0]), bal)
            self.assertEqual(run.pending_gear, "dagger")

    def test_chest_weights_are_used_as_ratios_at_the_boundary(self):
        """클럽 3 : 단검 1 → 난수 0.7499는 클럽, 0.75는 단검(합이 4이므로). 합을 100으로 착각하면 여기서 틀린다."""
        weights = {f"장비.{g.name}.등장": 0 for g in dungeon.GEAR}
        weights.update({"장비.낡은 몽둥이.등장": 3, "장비.단검.등장": 1})
        bal = build(**weights)
        for draw, expected in ((0.0, "club"), (0.7499, "club"), (0.75, "dagger"), (0.9999, "dagger")):
            run = dungeon.new_run(bal=bal)
            dungeon.open_chest(run, Scripted(randoms=[draw, 0.0]), bal)
            self.assertEqual(run.pending_gear, expected, draw)
        items = {f"아이템.{c.name}.등장": 0 for c in dungeon.CONSUMABLES}
        items.update({"아이템.사탕.등장": 1, "아이템.호박죽.등장": 1})
        bal = build(**items)
        for draw, expected in ((0.4999, "candy"), (0.5, "pumpkin_soup")):
            run = dungeon.new_run(bal=bal)
            dungeon.open_chest(run, Scripted(randoms=[0.0, draw]), bal)
            self.assertEqual(list(run.items), [expected], draw)

    def test_boss_hp_change_shows_up_in_a_real_fight(self):
        bal = build(**{"보스.해골 왕.체력": 40, "보스.해골 왕.공격": 1})
        run = dungeon.new_run(bal=bal)
        run.enemy, run.phase, run.room = dungeon._spawn(bal.bosses[0]), "battle", 10
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)
        self.assertEqual(run.phase, "cleared")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_default_then_set_persists_and_is_per_server(self):
        self.assertEqual(self.store.balance(G).max_hp, 1000)
        self.store.update_settings(G, dungeon={"일반.최대체력": 1500})
        self.assertEqual(self.store.balance(G).max_hp, 1500)             # 캐시가 새 값으로 바뀐다
        self.assertEqual(self.store.balance(101).max_hp, 1000)           # 다른 서버는 그대로

    def test_survives_a_restart(self):
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
        first = Store(path)
        first.update_settings(G, dungeon={"몬스터.마녀.공격": 99})
        first.close()
        second = Store(path)
        self.assertEqual([m.damage for m in second.balance(G).monsters if m.key == "witch"], [99])
        second.close()

    def test_bad_dungeon_settings_are_refused(self):
        for bad in ("문자열", {"a": "b"}, {"a": True}, {1: 5}):
            with self.assertRaises(ValueError):
                self.store.update_settings(G, dungeon=bad)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from services import Locks
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 2)

    def tearDown(self):
        self.store.close()

    async def test_entering_uses_this_servers_max_hp_and_lowering_it_clamps_progress(self):
        self.store.update_settings(G, dungeon={"일반.최대체력": 2000})
        first = await service.enter(self.store, self.locks, G, U)
        self.assertEqual(first.run.hp, 2000)
        self.store.update_settings(G, dungeon={"일반.최대체력": 500})
        again = await service.enter(self.store, self.locks, G, U)
        self.assertEqual(again.run.hp, 500)                               # 이어하기 때 새 상한으로 맞춘다

    async def test_lowering_max_hp_also_clamps_when_pressing_a_button(self):
        self.store.update_settings(G, dungeon={"일반.최대체력": 2000})
        run = (await service.enter(self.store, self.locks, G, U)).run
        self.assertEqual(run.hp, 2000)
        self.store.update_settings(G, dungeon={"일반.최대체력": 400})
        result = await service.act(self.store, self.locks, Scripted(), G, U, run.rev, "advance")   # 1번째 방(상자)으로
        self.assertEqual((result.ok, result.run.hp), (True, 400))
        self.assertEqual(json.loads(self.store.dungeon_run(G, U))["hp"], 400)

    async def test_a_changed_clear_reward_is_what_gets_paid(self):
        self.store.update_settings(G, dungeon={"보상.최소": 777, "보상.최대": 777, "보상.획득권확률": 0})
        run = (await service.enter(self.store, self.locks, G, U)).run
        data = json.loads(run.to_json())
        data.update(room=dungeon.ROOMS, phase="cleared")
        self.store.save_dungeon(G, U, json.dumps(data))
        done = await service.act(self.store, self.locks, Scripted(randoms=[0.0], ints=[777]), G, U, run.rev, "advance")
        self.assertEqual((done.ended, done.reward.candy, done.reward.ticket), ("clear", 777, False))
        self.assertEqual(self.store.wallet(G, U)[0], 777)


class AdminCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = DungeonAdmin(self.w.bot)
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def run_cmd(self, command, interaction, *args, **kwargs):
        await command.callback(self.cog, interaction, *args, **kwargs)
        return interaction

    def text(self, interaction, which="send_message"):
        embed, _ = sent_embed(interaction, which)
        return (embed.description or "") + "\n".join(f.value for f in embed.fields)

    async def test_regular_members_are_refused_everywhere_and_nothing_changes(self):
        calls = [(DungeonAdmin.set_param, ("몬스터.슬라임.체력", 5.0)), (DungeonAdmin.show, ()),
                 (DungeonAdmin.reset, ("몬스터.슬라임.체력",)), (DungeonAdmin.reset, ()), (DungeonAdmin.simulate, (100,))]
        self.store.update_settings(G, dungeon={"몬스터.슬라임.체력": 400})
        for command, args in calls:
            interaction = self.ask(admin=False)
            await self.run_cmd(command, interaction, *args)
            self.assertIn("관리자만", self.text(interaction), command.name)
            interaction.response.defer.assert_not_awaited()
        self.assertEqual(self.store.settings(G)["dungeon"], {"몬스터.슬라임.체력": 400})

    async def test_set_changes_the_value_and_reports_before_and_after(self):
        i = await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.슬라임.체력", 300.0)
        self.assertIn("270 → **300**", self.text(i))
        self.assertEqual(self.store.settings(G)["dungeon"], {"몬스터.슬라임.체력": 300})
        self.assertEqual([m.hp for m in self.store.balance(G).monsters if m.key == "slime"], [300])

    async def test_set_back_to_default_removes_the_record_and_same_value_is_a_noop(self):
        await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.슬라임.체력", 300.0)
        await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.슬라임.체력", 270.0)
        self.assertEqual(self.store.settings(G)["dungeon"], {})
        i = await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.슬라임.체력", 270.0)
        self.assertIn("이미", self.text(i))

    async def test_bad_input_is_explained_and_nothing_is_saved(self):
        for key, value, needle in (("없는.항목", 1.0, "항목이 없어요"), ("일반.최대체력", 5.0, "사이"),
                                   ("일반.최대체력", 1000.5, "정수"), ("보상.최소", 9999.0, "최소"), ("방1.상자", 0.0, "1번째 방")):
            i = await self.run_cmd(DungeonAdmin.set_param, self.ask(), key, value)
            self.assertIn(needle, self.text(i), (key, value))
        self.assertEqual(self.store.settings(G)["dungeon"], {})

    async def test_risky_values_are_saved_with_a_warning(self):
        i = await self.run_cmd(DungeonAdmin.set_param, self.ask(), "방어.회복률", 300.0)
        self.assertEqual(self.store.settings(G)["dungeon"], {"방어.회복률": 300})
        self.assertIn("⚠️", self.text(i))

    async def test_show_lists_changed_values_and_whole_groups(self):
        i = await self.run_cmd(DungeonAdmin.show, self.ask())
        self.assertIn("모두 기본값", self.text(i))
        await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.슬라임.체력", 300.0)
        i = await self.run_cmd(DungeonAdmin.show, self.ask())
        self.assertIn("몬스터.슬라임.체력", self.text(i))
        group = type("C", (), {"value": "몬스터"})()
        i = await self.run_cmd(DungeonAdmin.show, self.ask(), group)
        body = self.text(i)
        self.assertIn("`몬스터.슬라임.체력` **300** ✏️ 기본 270", body)
        self.assertIn("`몬스터.마녀.공격` **135**", body)

    def test_every_group_renders_within_discord_limits(self):
        for group in balance.GROUPS:
            lines = settings_lines(group, {})
            self.assertTrue(lines, group)
            self.assertLess(len("\n".join(lines)), 4000, group)

    def test_room_plan_view_is_one_line_per_room(self):
        lines = settings_lines("방 배치", {"방2.잡몹": 90})
        self.assertEqual(len(lines), balance.ROOMS)
        self.assertIn("잡몹 90", lines[1])
        self.assertTrue(lines[1].endswith("✏️"))
        self.assertFalse(lines[0].endswith("✏️"))

    async def test_reset_one_and_reset_all_with_confirmation(self):
        await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.슬라임.체력", 300.0)
        await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.마녀.공격", 100.0)
        i = await self.run_cmd(DungeonAdmin.reset, self.ask(), "몬스터.슬라임.체력")
        self.assertIn("300 → 기본값 **270**", self.text(i))
        self.assertEqual(self.store.settings(G)["dungeon"], {"몬스터.마녀.공격": 100})
        i = await self.run_cmd(DungeonAdmin.reset, self.ask(), "몬스터.슬라임.체력")
        self.assertIn("이미 기본값", self.text(i))
        i = await self.run_cmd(DungeonAdmin.reset, self.ask(), "없는.항목")
        self.assertIn("항목이 없어요", self.text(i))
        # 전부 되돌리기: 확인 전에는 그대로, 취소해도 그대로, 확인해야 지워진다
        i = await self.run_cmd(DungeonAdmin.reset, self.ask())
        view = i.response.send_message.await_args.kwargs["view"]
        self.assertIsInstance(view, ConfirmResetAll)
        self.assertEqual(self.store.settings(G)["dungeon"], {"몬스터.마녀.공격": 100})
        stranger = self.ask(uid=99)
        self.assertFalse(await view.interaction_check(stranger))
        await view.cancel(self.ask())
        self.assertEqual(self.store.settings(G)["dungeon"], {"몬스터.마녀.공격": 100})
        confirm = self.ask()
        await ConfirmResetAll(self.w.bot, 7).confirm(confirm)
        self.assertEqual(self.store.settings(G)["dungeon"], {})

    async def test_autocomplete_finds_by_typed_text_and_shows_current_and_default(self):
        await self.run_cmd(DungeonAdmin.set_param, self.ask(), "몬스터.슬라임.체력", 300.0)
        choices = param_choices(self.store, G, "슬라임")
        self.assertEqual({c.value for c in choices}, {"몬스터.슬라임.체력", "몬스터.슬라임.공격", "몬스터.슬라임.회피",
                                                     "보스.거대 슬라임.체력", "보스.거대 슬라임.공격"})        # 보스 '거대 슬라임'도 포함
        hp = next(c for c in choices if c.value.endswith("체력"))
        self.assertEqual(hp.name, "✏️ 몬스터.슬라임.체력 = 300 (기본 270)")
        self.assertLessEqual(len(param_choices(self.store, G, "")), 25)
        self.assertEqual({c.value for c in param_choices(self.store, G, "슬라임 체력")}, set())  # 글자가 이어져 있어야 찾는다(공백만 무시)
        self.assertEqual({c.value for c in param_choices(self.store, G, "슬라임.체력")}, {"몬스터.슬라임.체력", "보스.거대 슬라임.체력"})
        self.assertEqual({c.value for c in param_choices(self.store, G, "몬스터.슬라임.체력")}, {"몬스터.슬라임.체력"})
        self.assertEqual({c.value for c in param_choices(self.store, G, "몬스터. 슬라임.체 력")}, {"몬스터.슬라임.체력"})
        for c in param_choices(self.store, G, "장비"):
            self.assertLessEqual(len(c.name), 100)
            self.assertLessEqual(len(c.value), 100)

    async def test_simulation_reports_three_ways_and_uses_this_servers_numbers(self):
        i = await self.run_cmd(DungeonAdmin.simulate, self.ask(), 100)
        i.response.defer.assert_awaited_once()
        embed, _ = sent_embed(i, "followup")
        self.assertEqual(len(embed.fields) >= 4, True)
        self.assertTrue(all("클리어" in f.value for f in embed.fields[:3]))
        # 이 서버만 몬스터를 거의 못 이길 수준으로 만들면 클리어율이 떨어진다
        easy = balance_sim.simulate(self.store.balance(G), 300, seed=1)
        for m in dungeon.MONSTERS + dungeon.BOSSES:
            prefix = "보스" if m.boss else "몬스터"
            await self.run_cmd(DungeonAdmin.set_param, self.ask(), f"{prefix}.{m.name}.공격", 5000.0)
        hard = balance_sim.simulate(self.store.balance(G), 300, seed=1)
        self.assertLess(hard["attack"]["clear"], easy["attack"]["clear"])
        self.assertEqual(hard["attack"]["clear"], 0.0)


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    """던전 화면(버튼 처리·표시)이 이 서버의 밸런스를 쓰는지."""

    async def asyncSetUp(self):
        self.w = World(rng=Scripted(randoms=[0.0] * 50))
        self.store = self.w.store
        self.w.channel(400)
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)

    async def asyncTearDown(self):
        await self.w.close()

    async def in_battle(self, **overrides):
        self.store.update_settings(G, dungeon=overrides)
        run = (await service.enter(self.store, self.w.locks, G, U)).run
        run.phase, run.room, run.kind = "battle", 2, "monster"
        run.enemy = dungeon._spawn(next(m for m in self.store.balance(G).monsters if m.key == "witch"))
        run.items = {"candy": 1}
        self.store.save_dungeon(G, U, run.to_json())
        return run

    async def press(self, run, action, arg=None):
        from cogs.dungeon import respond
        interaction = self.w.interaction()
        await respond(interaction, self.w.bot, run.rev, action, arg)
        return interaction.response.edit_message.await_args.kwargs

    async def test_guard_chance_setting_decides_the_guard_roll(self):
        run = await self.in_battle(**{"방어.성공확률": 0})
        shown = await self.press(run, "defend")                        # 난수 0.0이어도 확률 0%라 실패
        self.assertIn("가드 실패", shown["embed"].description)
        run = await self.in_battle()                                   # 기본값(60%)으로 되돌리면 같은 난수 0.0이 성공
        shown = await self.press(run, "defend")
        self.assertIn("가드 성공", shown["embed"].description)

    async def test_hp_bar_and_item_text_follow_the_settings(self):
        run = await self.in_battle(**{"일반.최대체력": 2000, "아이템.사탕.회복량": 321})
        shown = await self.press(run, "attack")
        fields = {f.name: f.value for f in shown["embed"].fields}
        self.assertRegex(fields["❤️ HP"], r"/2000$")
        select = next(c for c in shown["view"].children if isinstance(c, discord.ui.Select))
        self.assertEqual(select.options[0].description, "HP +321")

    async def test_entering_screen_uses_the_servers_max_hp(self):
        from cogs.dungeon import Dungeon
        self.store.update_settings(G, dungeon={"일반.최대체력": 3000})
        interaction = self.w.interaction()
        await Dungeon.enter.callback(Dungeon(self.w.bot), interaction)
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertRegex({f.name: f.value for f in embed.fields}["❤️ HP"], r"3000/3000$|3000$")


class SimulatorTests(unittest.TestCase):
    def test_same_seed_same_result_and_shape(self):
        a = balance_sim.simulate(Balance.build({}), 200, seed=5)
        b = balance_sim.simulate(Balance.build({}), 200, seed=5)
        self.assertEqual(a, b)
        self.assertEqual(set(a), {"attack", "mixed", "defend"})
        for entry in a.values():
            self.assertTrue(0 <= entry["clear"] <= 1 and 1 <= entry["avg_room"] <= 10)
            self.assertAlmostEqual(entry["clear"] + sum(entry["deaths"].values()), 1.0, places=9)

    def test_matches_the_numbers_we_measured_by_hand(self):
        """기본값에서 공격만 하면 약 70%, 방어를 섞으면 90% 안팎(손으로 따로 돌려 본 결과와 같은 범위)."""
        result = balance_sim.simulate(Balance.build({}), 2000, seed=11)
        self.assertTrue(0.62 < result["attack"]["clear"] < 0.80, result["attack"]["clear"])
        self.assertTrue(0.82 < result["mixed"]["clear"] < 0.96, result["mixed"]["clear"])

    def test_extreme_values_move_the_result_the_right_way(self):
        base = balance_sim.simulate(Balance.build({}), 400, seed=3)["attack"]["clear"]
        weak = balance_sim.simulate(build(**{"보스.해골 왕.체력": 1, "보스.거대 슬라임.체력": 1, "보스.고블린 왕.체력": 1}), 400, seed=3)["attack"]["clear"]
        strong = balance_sim.simulate(build(**{"일반.최대체력": 100}), 400, seed=3)["attack"]["clear"]
        self.assertGreater(weak, base)
        self.assertLess(strong, base)


if __name__ == "__main__":
    unittest.main()
