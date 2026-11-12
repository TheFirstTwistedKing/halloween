"""보스 2페이즈(체력 바 2개)와 보스 스킬: 검증, 전투 규칙, 화면, 명령어."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
from balance import Balance, check_entity, entity_dict, skill_text
from cogs.boss_patterns import BossPatterns, pattern_text
from cogs.dungeon import run_embed
from tests.helpers import Scripted, World, sent_embed

G = 100


def boss(**kw):
    data = dict(key="b", name="코어", emoji="🖥️", hp=300, damage=100, dodge=0)
    data.update(kw)
    return data


def bal_with(**kw):
    return Balance.build({"일반.층당강화": 0}, {"bosses": [entity_dict(check_entity("bosses", boss(**kw))[0])]})


def fight(bal, hp=1000, **kw):
    run = dungeon.new_run(bal=bal)
    run.enemy = dungeon._spawn(bal.bosses[0], 1, bal)
    run.phase, run.kind, run.room, run.hp = "battle", "boss", dungeon.ROOMS, hp
    for k, v in kw.items():
        setattr(run, k, v)
    return run


def hit(run, bal):
    return dungeon.player_attack(run, Scripted(randoms=[0.99]), bal)


class ValidationTests(unittest.TestCase):
    def test_defaults_are_off_and_old_saves_read_as_off(self):
        b = check_entity("bosses", boss())[0]
        self.assertEqual((b.phase2_hp, b.phase2_damage, b.skill, b.skill_every), (0, 0, None, 0))
        self.assertTrue(all(m.phase2_hp == 0 and m.skill is None for m in balance.BOSSES + balance.SECRET_BOSSES))

    def test_each_skill_checks_its_power_and_turns(self):
        good = [dict(skill="strike", skill_every=3, skill_power=200), dict(skill="heal", skill_every=2, skill_power=20),
                dict(skill="barrier", skill_every=4, skill_power=50, skill_turns=2), dict(skill="burn", skill_every=3, skill_power=30, skill_turns=3)]
        for kw in good:
            self.assertIsNone(check_entity("bosses", boss(**kw))[1], kw)
        bad = [dict(skill="fly", skill_every=1, skill_power=100), dict(skill="strike", skill_every=0, skill_power=200),
               dict(skill="strike", skill_every=3, skill_power=50), dict(skill="heal", skill_every=3, skill_power=101),
               dict(skill="barrier", skill_every=3, skill_power=50), dict(skill="burn", skill_every=3, skill_power=10, skill_turns=0),
               dict(skill="strike", skill_every=21, skill_power=200), dict(phase2_hp=-1), dict(phase2_hp=10 ** 7)]
        for kw in bad:
            self.assertIsNotNone(check_entity("bosses", boss(**kw))[1], kw)

    def test_no_skill_clears_the_numbers_and_turns_are_dropped_when_unused(self):
        b = check_entity("bosses", boss(skill=None, skill_every=5, skill_power=9))[0]
        self.assertEqual((b.skill_every, b.skill_power), (0, 0))
        self.assertEqual(check_entity("bosses", boss(skill="strike", skill_every=2, skill_power=150, skill_turns=5))[0].skill_turns, 0)

    def test_secret_bosses_have_patterns_too_and_round_trip(self):
        data = dict(key="s", name="널", emoji="🕳️", hp=5, damage=1, drop_item="candy", phase2_hp=50, skill="heal", skill_every=2, skill_power=10)
        entity = check_entity("secrets", data)[0]
        self.assertEqual(check_entity("secrets", json.loads(json.dumps(entity_dict(entity))))[0], entity)

    def test_skill_text(self):
        self.assertEqual(skill_text("burn", 3, 40, 2), "[과열] 3번째 공격마다 · 2턴 동안 턴마다 40 피해")
        self.assertEqual(skill_text(None, 0, 0, 0), "")


class PhaseTests(unittest.TestCase):
    def test_emptying_the_first_bar_starts_phase_two_without_an_attack(self):
        bal = bal_with(hp=10, phase2_hp=500, phase2_damage=150)
        run = fight(bal)
        events = hit(run, bal)
        self.assertEqual((run.phase, run.enemy["phase"], run.enemy["hp"], run.enemy["max_hp"], run.enemy["damage"]), ("battle", 2, 500, 500, 150))
        self.assertEqual(run.hp, 1000)                                                  # 넘어가는 턴에는 공격하지 않는다
        self.assertTrue(any("2페이즈 돌입" in e for e in events))
        run.enemy["hp"] = 10
        events = hit(run, bal)
        self.assertEqual(run.phase, "cleared")
        self.assertTrue(any("무력화했어요" in e for e in events))

    def test_without_phase_two_damage_it_keeps_attacking_as_hard(self):
        bal = bal_with(hp=10, phase2_hp=500)
        run = fight(bal)
        hit(run, bal)
        self.assertEqual(run.enemy["damage"], 100)

    def test_phase_two_also_starts_from_reflect_dot_and_items(self):
        bal = bal_with(hp=10, phase2_hp=500, damage=100)
        run = fight(bal)
        dungeon.player_defend(run, True, bal)                                           # 반사 50 → 1페이즈 끝
        self.assertEqual((run.enemy["phase"], run.phase), (2, "battle"))
        self.assertFalse(run.enemy["stunned"])
        dotted = fight(bal)
        dotted.enemy.update(dot=999, dot_turns=3)
        dungeon._end_turn(dotted, bal)
        self.assertEqual((dotted.enemy["phase"], dotted.enemy["dot_turns"]), (2, 0))    # 지속 피해는 새 체력 바로 넘어가지 않는다

    def test_floor_scaling_applies_to_phase_two(self):
        bal = Balance.build({"일반.층당강화": 50}, {"bosses": [entity_dict(check_entity("bosses", boss(phase2_hp=100, phase2_damage=40))[0])]})
        enemy = dungeon._spawn(bal.bosses[0], 3, bal)
        self.assertEqual((enemy["phase2_hp"], enemy["phase2_damage"]), (200, 80))

    def test_a_saved_fight_from_before_this_update_has_one_bar(self):
        bal = bal_with(hp=10, phase2_hp=500)
        run = fight(bal)
        data = json.loads(run.to_json())
        for k in ("phase", "phase2_hp", "phase2_damage", "turn"):
            data["enemy"].pop(k)
        run = dungeon.Run.from_json(json.dumps(data))
        hit(run, bal)
        self.assertEqual(run.phase, "cleared")


class SkillTests(unittest.TestCase):
    def test_strike_hits_harder_every_nth_attack(self):
        bal = bal_with(hp=100000, skill="strike", skill_every=2, skill_power=300)
        run = fight(bal, hp=10 ** 4)
        hit(run, bal)
        self.assertEqual(run.hp, 10 ** 4 - 100)
        events = hit(run, bal)
        self.assertEqual(run.hp, 10 ** 4 - 100 - 300)
        self.assertTrue(any("[강타]" in e for e in events))

    def test_heal_replaces_the_attack(self):
        bal = bal_with(hp=1000, skill="heal", skill_every=1, skill_power=20)
        run = fight(bal)
        run.enemy["hp"] = 500
        events = hit(run, bal)
        self.assertEqual((run.enemy["hp"], run.hp), (500 - 20 + 200, 1000))
        self.assertTrue(any("[자가 수리]" in e for e in events))
        run.enemy["hp"] = 990
        hit(run, bal)
        self.assertEqual(run.enemy["hp"], 1000)                                        # 최대 체력을 넘지 않는다

    def test_barrier_cuts_my_damage_for_its_turns(self):
        bal = bal_with(hp=100000, skill="barrier", skill_every=1, skill_power=50, skill_turns=2)
        run = fight(bal)
        hit(run, bal)                                                                    # 첫 공격 20 피해, 그 뒤 방어막
        self.assertEqual((run.enemy["barrier"], run.enemy["barrier_turns"]), (50, 1))   # 턴이 끝나며 하나 줄었다
        before = run.enemy["hp"]
        events = hit(run, bal)
        self.assertEqual(before - run.enemy["hp"], 10)
        self.assertIn("방어막 50%", events[0])

    def test_burn_ticks_and_can_kill(self):
        bal = bal_with(hp=100000, skill="burn", skill_every=1, skill_power=60, skill_turns=3)
        run = fight(bal, hp=100)
        events = hit(run, bal)                                                          # 과열을 걸고, 같은 턴 끝에 60
        self.assertEqual((run.hp, run.burn_turns), (40, 2))
        self.assertTrue(any("[과열]" in e for e in events))
        events = hit(run, bal)
        self.assertEqual(run.phase, "dead")
        self.assertTrue(any("시스템이 다운됐어요" in e for e in events))

    def test_invulnerability_and_armor_soften_burn(self):
        bal = bal_with(hp=100000, skill="burn", skill_every=1, skill_power=60, skill_turns=3)
        run = fight(bal, invuln_turns=5)
        hit(run, bal)
        self.assertEqual(run.hp, 1000)

    def test_guarding_a_non_attack_skill_gives_no_heal_reflect_or_stun(self):
        bal = bal_with(hp=100000, skill="heal", skill_every=1, skill_power=1)
        run = fight(bal, hp=500)
        dungeon.player_defend(run, True, bal)
        self.assertEqual(run.hp, 500)
        self.assertFalse(run.enemy["stunned"])
        strike = bal_with(hp=100000, skill="strike", skill_every=1, skill_power=200)
        run = fight(strike, hp=500)
        dungeon.player_defend(run, True, strike)                                         # 강타는 막을 수 있는 공격이다
        self.assertTrue(run.enemy["stunned"])

    def test_a_stun_skips_the_turn_without_advancing_the_skill(self):
        bal = bal_with(hp=100000, skill="strike", skill_every=2, skill_power=300)
        run = fight(bal, hp=10 ** 4)
        run.enemy["stunned"] = True
        hit(run, bal)
        self.assertEqual(run.enemy["turn"], 0)
        hit(run, bal)
        hit(run, bal)
        self.assertEqual(run.hp, 10 ** 4 - 100 - 300)

    def test_burn_power_scales_with_the_floor(self):
        bal = Balance.build({"일반.층당강화": 100}, {"bosses": [entity_dict(check_entity("bosses", boss(skill="burn", skill_every=1, skill_power=10, skill_turns=1))[0])]})
        self.assertEqual(dungeon._spawn(bal.bosses[0], 3, bal)["skill"]["power"], 30)

    def test_the_simulator_handles_patterns(self):
        bal = Balance.build({}, {"bosses": [entity_dict(check_entity("bosses", boss(hp=500, phase2_hp=500, skill="burn", skill_every=2, skill_power=20, skill_turns=2))[0])]})
        result = balance_sim.simulate(bal, 30, seed=3)
        self.assertEqual(result, balance_sim.simulate(bal, 30, seed=3))


class ScreenTests(unittest.TestCase):
    def test_the_battle_screen_shows_the_phase_and_next_skill(self):
        bal = bal_with(hp=100000, phase2_hp=500, skill="strike", skill_every=3, skill_power=200)
        run = fight(bal)
        embed = run_embed(run, None, bal)
        self.assertTrue(any("[1/2페이즈]" in f.name for f in embed.fields))
        shown = "".join(f.value for f in embed.fields)
        self.assertIn("다음 스킬 [강타]", shown)
        run.enemy["turn"] = 2
        self.assertIn("다음 공격이 스킬 [강타]", "".join(f.value for f in run_embed(run, None, bal).fields))

    def test_burn_and_barrier_are_listed_as_effects(self):
        bal = bal_with(hp=100000)
        run = fight(bal, burn_amount=40, burn_turns=2)
        run.enemy.update(barrier=30, barrier_turns=1)
        shown = "".join(f.value for f in run_embed(run, None, bal).fields)
        self.assertIn("과열: 턴마다 40", shown)
        self.assertIn("몬스터 방어막: 받는 피해 30%", shown)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = BossPatterns(self.w.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def find(self, name):
        bal = self.store.balance(G)
        return next(b for b in bal.bosses + bal.secrets if b.name == name)

    async def test_phase_skill_view_and_clear(self):
        Kind = type("C", (), {"__init__": lambda self, value: setattr(self, "value", value)})
        text = await self.run_cmd(BossPatterns.phase, "메인프레임 군주", 1200, 200)
        self.assertIn("HP 1,200", text)
        self.assertEqual((self.find("메인프레임 군주").phase2_hp, self.find("메인프레임 군주").phase2_damage), (1200, 200))
        text = await self.run_cmd(BossPatterns.skill, "NULL 군주", Kind("burn"), 2, 50, 3)
        self.assertIn("[과열] 2번째 공격마다", text)
        self.assertEqual(self.find("NULL 군주").skill, "burn")
        self.assertIn("2페이즈: 1페이즈", await self.run_cmd(BossPatterns.show, "메인프레임 군주"))
        await self.run_cmd(BossPatterns.skill, "NULL 군주", Kind("none"))
        self.assertIsNone(self.find("NULL 군주").skill)
        await self.run_cmd(BossPatterns.phase, "메인프레임 군주", 0, 300)
        self.assertEqual((self.find("메인프레임 군주").phase2_hp, self.find("메인프레임 군주").phase2_damage), (0, 0))

    async def test_bad_input_is_explained_and_admin_only(self):
        Kind = type("C", (), {"__init__": lambda self, value: setattr(self, "value", value)})
        self.assertIn("세기", await self.run_cmd(BossPatterns.skill, "메인프레임 군주", Kind("heal"), 2, 500))
        self.assertIn("턴 수", await self.run_cmd(BossPatterns.skill, "메인프레임 군주", Kind("barrier"), 2, 50, 0))
        self.assertIn("그런 보스가 없어요", await self.run_cmd(BossPatterns.phase, "오일 슬러지", 100))       # 잡몹은 대상이 아니다
        self.assertIn("관리자만", await self.run_cmd(BossPatterns.phase, "메인프레임 군주", 100, admin=False))
        self.assertEqual(self.find("메인프레임 군주").phase2_hp, 0)

    async def test_autocomplete_lists_bosses_and_secrets_only(self):
        i = self.w.interaction(admin=True)
        names = [c.value for c in await self.cog.boss_names(i, "")]
        self.assertIn("메인프레임 군주", names)
        self.assertIn("NULL 군주", names)
        self.assertNotIn("오일 슬러지", names)

    def test_pattern_text(self):
        self.assertIn("2페이즈: 없음", pattern_text({"hp": 1, "damage": 1}))
