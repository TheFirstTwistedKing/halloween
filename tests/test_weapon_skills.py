"""무기 스킬(쿨타임)·궁극기(게이지)·각성(궁극기로 충전, 특수 스킬): 검증, 전투 규칙, 화면, 빠른진행, 명령어."""
import json
import unittest

import balance
import dungeon
import content
import dungeon_auto
import dungeon_service as service
from balance import Balance, check_entity, clean_awakening, describe_gear, entity_dict, weapon_skill_text
from cogs.dungeon import DungeonView, run_embed
from cogs.weapon_skills import WeaponSkills, awakening_text
from dungeon import DungeonError, Run
from services import Locks
from store import DEFAULT_SETTINGS, Store, validate_settings
from tests.helpers import Scripted, World, sent_embed

G = 100
HIT = [0.99] * 20            # 회피·크리티컬이 터지지 않는 주사위


def weapon(**kw):
    data = dict(key="w", name="테스트 커터", emoji="🔪", slot="weapon", tier="일반", weight=1, attack=80)
    data.update(kw)
    return data


def mob(**kw):
    data = dict(key="m", name="테스트 드론", emoji="🛸", hp=100000, damage=100, dodge=0)
    data.update(kw)
    return data


def bal_with(awakening=None, gear=None, monster=None):
    raw = {"gear": [entity_dict(check_entity("gear", gear or weapon(skill="power", skill_power=300, skill_cooldown=2,
                                                                       ult="multi", ult_power=5))[0])],
           "monsters": [entity_dict(check_entity("monsters", monster or mob())[0])]}
    return Balance.build({"일반.층당강화": 0}, raw, None, None, None, awakening)


def fight(b, **kw):
    run = dungeon.new_run(weapon="w", bal=b)
    run.enemy = dungeon._spawn(b.monsters[0], 1, b)
    run.enemy["dodge"] = 0
    run.phase, run.kind, run.room, run.hp = "battle", "monster", 2, b.max_hp
    for k, v in kw.items():
        setattr(run, k, v)
    return run


def dealt(run, action):
    before = run.enemy["hp"]
    action()
    return before - run.enemy["hp"]


class ValidationTests(unittest.TestCase):
    def test_each_kind_checks_its_power(self):
        for kind, power in (("power", 300), ("multi", 3), ("drain", 50), ("stun", 100), ("pierce", 150)):
            self.assertIsNone(check_entity("gear", weapon(skill=kind, skill_power=power))[1], kind)
            self.assertIsNone(check_entity("gear", weapon(ult=kind, ult_power=power))[1], kind)
        for kw in (dict(skill="fly", skill_power=100), dict(skill="multi", skill_power=11), dict(skill="multi", skill_power=1),
                   dict(skill="power", skill_power=50), dict(skill="drain", skill_power=201), dict(ult="power", ult_power=2001),
                   dict(skill="power", skill_power=200, skill_cooldown=21), dict(skill="power", skill_power=200, skill_cooldown=0)):
            self.assertIsNotNone(check_entity("gear", weapon(**kw))[1], kw)

    def test_defaults_cooldown_and_non_weapons(self):
        g = check_entity("gear", weapon(skill="power", skill_power=200))[0]
        self.assertEqual(g.skill_cooldown, 3)
        plain = check_entity("gear", weapon())[0]
        self.assertEqual((plain.skill, plain.skill_power, plain.skill_cooldown, plain.ult, plain.ult_power), (None, 0, 0, None, 0))
        shield = check_entity("gear", dict(key="s", name="실드", emoji="🛡️", slot="shield", tier="일반", weight=1, guard=50,
                                           skill="power", skill_power=300, ult="multi", ult_power=3))[0]
        self.assertEqual((shield.skill, shield.ult), (None, None))
        self.assertTrue(all(g.skill is None and g.ult is None for g in balance.GEAR))           # 기본 장비는 그대로

    def test_round_trip_and_description(self):
        g = check_entity("gear", weapon(skill="stun", skill_power=150, skill_cooldown=4, ult="pierce", ult_power=400))[0]
        self.assertEqual(check_entity("gear", json.loads(json.dumps(entity_dict(g))))[0], g)
        text = describe_gear(g)
        self.assertIn("스킬 [기절탄]", text)
        self.assertIn("궁극기 [관통]", text)
        self.assertEqual(weapon_skill_text("multi", 4), "[연타] 4연타")
        self.assertEqual(weapon_skill_text(None, 0), "")

    def test_awakening_settings_are_cleaned(self):
        self.assertEqual(clean_awakening(None), balance.AWAKENING_DEFAULT)
        got = clean_awakening({"ult_charge": 50, "awaken_charge": 0, "special": "multi", "special_power": 4, "special_ends": False,
                               "awaken_bonus": True, "junk": 1})
        self.assertEqual(got, dict(balance.AWAKENING_DEFAULT, ult_charge=50, special="multi", special_power=4, special_ends=False))
        self.assertEqual(clean_awakening({"special": "multi", "special_power": 500})["special"], "power")     # 세기가 맞지 않으면 기본값
        b = Balance.build({}, None, None, None, None, {"ult_charge": 25, "special_ends": False})
        self.assertEqual((b.ult_charge, b.awaken_charge, b.special_ends), (25, 10, False))
        validate_settings(dict(DEFAULT_SETTINGS, awakening={"ult_charge": 30}))
        with self.assertRaises(ValueError):
            validate_settings(dict(DEFAULT_SETTINGS, awakening=[1]))


class SkillTests(unittest.TestCase):
    def test_skill_hits_by_power_and_then_cools_down(self):
        b = bal_with()
        run = fight(b)
        base = dungeon.player_damage(run, b)
        self.assertTrue(dungeon.skill_ready(run, b)["skill"])
        self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)), base * 3)
        self.assertFalse(dungeon.skill_ready(run, b)["skill"])
        with self.assertRaisesRegex(DungeonError, "2턴 뒤"):
            dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertFalse(dungeon.skill_ready(run, b)["skill"])                 # 쿨타임 2: 두 턴을 쉰다
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertTrue(dungeon.skill_ready(run, b)["skill"])

    def test_the_monster_strikes_back_after_a_skill(self):
        b = bal_with()
        run = fight(b)
        dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)
        self.assertEqual(run.hp, b.max_hp - 100)

    def test_multi_drain_stun_and_pierce(self):
        def one(kind, power, **kw):
            b = bal_with(gear=weapon(skill=kind, skill_power=power, skill_cooldown=1))
            r = fight(b, **kw)
            return b, r, dealt(r, lambda: dungeon.use_technique(r, "skill", Scripted(randoms=HIT), b))
        b, run, hit = one("multi", 4)
        self.assertEqual(hit, dungeon.player_damage(run, b) * 4)
        b, run, hit = one("drain", 50, hp=500)
        self.assertEqual(hit, dungeon.player_damage(run, b))
        self.assertEqual(run.hp, 500 + hit // 2 - 100)                           # 회복한 뒤 반격을 맞는다
        b, run, hit = one("stun", 100, hp=500)
        self.assertEqual(run.hp, 500)                                            # 기절해서 반격하지 못한다
        b = bal_with(gear=weapon(skill="pierce", skill_power=200, skill_cooldown=1))
        run = fight(b)
        run.enemy.update(dodge=100, barrier=50, barrier_turns=2)
        self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "skill", Scripted(randoms=[0.0] * 9), b)), dungeon.player_damage(run, b) * 2)

    def test_other_skills_can_be_dodged_and_are_cut_by_barriers(self):
        b = bal_with()
        run = fight(b)
        run.enemy["dodge"] = 100
        self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "skill", Scripted(randoms=[0.0] * 9), b)), 0)
        self.assertFalse(dungeon.skill_ready(run, b)["skill"])                 # 빗나가도 쿨타임은 돈다
        run = fight(b)
        run.enemy.update(barrier=50, barrier_turns=2)
        self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)), dungeon.player_damage(run, b) * 3 // 2)

    def test_no_skill_outside_battle_or_without_one(self):
        b = bal_with(gear=weapon())
        run = fight(b)
        self.assertEqual(dungeon.skill_ready(run, b), {"skill": False, "ult": False, "special": False})
        with self.assertRaisesRegex(DungeonError, "스킬이 없어요"):
            dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)
        with self.assertRaisesRegex(DungeonError, "궁극기가 없어요"):
            dungeon.use_technique(run, "ult", Scripted(randoms=HIT), b)
        b = bal_with()
        run = fight(b)
        run.phase = "cleared"
        with self.assertRaisesRegex(DungeonError, "싸우는 중"):
            dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)
        with self.assertRaises(DungeonError):
            dungeon.use_technique(fight(b), "dance", Scripted(randoms=HIT), b)


class UltimateTests(unittest.TestCase):
    def test_gauge_fills_on_attack_and_defend_and_resets_on_use(self):
        b = bal_with()
        run = fight(b)
        for _ in range(2):
            dungeon.player_attack(run, Scripted(randoms=HIT), b)
        dungeon.player_defend(run, False, b, Scripted(randoms=HIT))
        self.assertEqual(run.ult_gauge, 60)
        with self.assertRaisesRegex(DungeonError, "60%"):
            dungeon.use_technique(run, "ult", Scripted(randoms=HIT), b)
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        events = dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual(run.ult_gauge, 100)
        self.assertTrue(any("궁극기 게이지 **100%**" in e for e in events))
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual(run.ult_gauge, 100)                                     # 넘치지 않는다
        self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "ult", Scripted(randoms=HIT), b)), dungeon.player_damage(run, b) * 5)
        self.assertEqual((run.ult_gauge, run.awaken_gauge, run.awakened), (0, 10, False))

    def test_no_gauge_without_an_ultimate_and_charge_is_configurable(self):
        b = bal_with(gear=weapon(skill="power", skill_power=200))
        run = fight(b)
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertEqual(run.ult_gauge, 0)
        b = bal_with({"ult_charge": 100})
        run = fight(b)
        dungeon.player_attack(run, Scripted(randoms=HIT), b)
        self.assertTrue(dungeon.skill_ready(run, b)["ult"])

    def test_skills_do_not_charge_the_gauge(self):
        b = bal_with()
        run = fight(b)
        dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)
        self.assertEqual(run.ult_gauge, 0)


class AwakeningTests(unittest.TestCase):
    def ult(self, run, b):
        run.ult_gauge, run.hp = 100, b.max_hp                                   # 반격에 쓰러지지 않게
        return dungeon.use_technique(run, "ult", Scripted(randoms=HIT), b)

    def test_ten_ultimates_awaken_and_the_special_ends_it(self):
        b = bal_with()
        run = fight(b)
        for n in range(9):
            self.ult(run, b)
            self.assertFalse(run.awakened, n)
        events = self.ult(run, b)
        self.assertTrue(run.awakened)
        self.assertTrue(any("각성!" in e for e in events))
        self.assertTrue(dungeon.skill_ready(run, b)["special"])
        self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "special", Scripted(randoms=HIT), b)), dungeon.player_damage(run, b) * 5)
        self.assertEqual((run.awakened, run.awaken_gauge), (False, 0))
        with self.assertRaisesRegex(DungeonError, "각성 중에만"):
            dungeon.use_technique(run, "special", Scripted(randoms=HIT), b)

    def test_settings_change_charge_special_and_ending(self):
        b = bal_with({"awaken_charge": 50, "special": "multi", "special_power": 3, "special_ends": False})
        run = fight(b)
        self.ult(run, b)
        self.assertFalse(run.awakened)
        self.ult(run, b)
        self.assertTrue(run.awakened)
        for _ in range(2):
            self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "special", Scripted(randoms=HIT), b)), dungeon.player_damage(run, b) * 3)
        self.assertTrue(run.awakened)                                            # 끝나지 않게 정하면 계속 쓸 수 있다

    def test_awakening_bonus_boosts_every_hit(self):
        b = bal_with({"awaken_bonus": 100})
        run = fight(b, awakened=True)
        base = dungeon.player_damage(run, b)
        self.assertEqual(dealt(run, lambda: dungeon.player_attack(run, Scripted(randoms=HIT), b)), base * 2)
        run.skill_cd = 0
        self.assertEqual(dealt(run, lambda: dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)), base * 6)
        run.awakened = False
        self.assertEqual(dealt(run, lambda: dungeon.player_attack(run, Scripted(randoms=HIT), b)), base)

    def test_old_saves_and_round_trip(self):
        b = bal_with()
        run = fight(b, ult_gauge=40, awaken_gauge=30, awakened=True, skill_cd=2)
        back = dungeon.Run.from_json(run.to_json())
        self.assertEqual((back.ult_gauge, back.awaken_gauge, back.awakened, back.skill_cd), (40, 30, True, 2))
        old = json.loads(run.to_json())
        for key in ("ult_gauge", "awaken_gauge", "awakened", "skill_cd"):
            old.pop(key)
        back = dungeon.Run.from_json(json.dumps(old))
        self.assertEqual((back.ult_gauge, back.awaken_gauge, back.awakened, back.skill_cd), (0, 0, False, 0))


class ScreenAndAutoTests(unittest.TestCase):
    def labels(self, run, b):
        return {c.label: c.disabled for c in DungeonView(object(), 7, run, b).children if hasattr(c, "label") and c.label}

    def test_buttons_show_cooldown_gauge_and_awakening(self):
        b = bal_with()
        run = fight(b)
        self.assertEqual(self.labels(run, b).get("스킬 강타"), False)
        self.assertEqual(self.labels(run, b).get("궁극기 연타 (0%)"), True)
        self.assertNotIn("각성 특수 스킬 강타", self.labels(run, b))
        run.skill_cd, run.ult_gauge, run.awakened = 2, 100, True
        labels = self.labels(run, b)
        self.assertEqual((labels.get("스킬 강타 (2턴)"), labels.get("궁극기 연타"), labels.get("각성 특수 스킬 강타")), (True, False, False))
        DungeonView(object(), 7, run, b).to_components()                    # 디스코드 한도(한 줄 5개) 안에 들어간다
        self.assertNotIn("스킬 강타", self.labels(fight(bal_with(gear=weapon())), bal_with(gear=weapon())))

    def test_status_lines(self):
        b = bal_with()
        run = fight(b, ult_gauge=40, awaken_gauge=30, skill_cd=2)
        shown = "".join(f.value for f in run_embed(run, None, b).fields)
        self.assertIn("궁극기 게이지", shown)
        self.assertIn("40%", shown)
        self.assertIn("각성 게이지", shown)
        run.awakened = True
        self.assertIn("각성 중!", "".join(f.value for f in run_embed(run, None, b).fields))

    def test_auto_play_uses_the_strongest_ready_technique(self):
        b = bal_with()
        run = fight(b)
        self.assertEqual(dungeon_auto.choose(run, b), ("skill", None))
        run.ult_gauge = 100
        self.assertEqual(dungeon_auto.choose(run, b), ("ult", None))
        run.awakened = True
        self.assertEqual(dungeon_auto.choose(run, b), ("special", None))
        run.skill_cd, run.ult_gauge, run.awakened = 2, 0, False
        self.assertEqual(dungeon_auto.choose(run, b), ("attack", None))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = WeaponSkills(self.w.bot)
        self.Kind = type("C", (), {"__init__": lambda self, value: setattr(self, "value", value)})

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def gear(self, name):
        return next(g for g in self.store.balance(G).gear if g.name == name)

    async def test_skill_ult_view_and_clear(self):
        text = await self.run_cmd(WeaponSkills.skill, "레이저 커터", self.Kind("stun"), 150, 4)
        self.assertIn("스킬을 정했어요", text)
        g = self.gear("레이저 커터")
        self.assertEqual((g.skill, g.skill_power, g.skill_cooldown, g.crit), ("stun", 150, 4, balance.DEFAULT.gear_by_key["dagger"].crit))
        await self.run_cmd(WeaponSkills.ult, "레이저 커터", self.Kind("multi"), 6)
        self.assertEqual((self.gear("레이저 커터").ult, self.gear("레이저 커터").ult_power), ("multi", 6))
        shown = await self.run_cmd(WeaponSkills.show)
        self.assertIn("[기절탄]", shown)
        self.assertIn("[연타] 6연타", shown)
        self.assertIn("각성 게이지", shown)
        await self.run_cmd(WeaponSkills.skill, "레이저 커터", self.Kind("none"))
        await self.run_cmd(WeaponSkills.ult, "레이저 커터", self.Kind("none"))
        self.assertEqual((self.gear("레이저 커터").skill, self.gear("레이저 커터").ult), (None, None))
        self.assertIn("없어요", await self.run_cmd(WeaponSkills.show))

    async def test_bad_input_shields_and_admin_only(self):
        self.assertIn("세기", await self.run_cmd(WeaponSkills.skill, "레이저 커터", self.Kind("multi"), 50))
        self.assertIn("그런 무기가 없어요", await self.run_cmd(WeaponSkills.skill, "에너지 실드", self.Kind("power"), 200))
        self.assertIn("관리자만", await self.run_cmd(WeaponSkills.skill, "레이저 커터", self.Kind("power"), 200, admin=False))
        self.assertIsNone(self.gear("레이저 커터").skill)

    async def test_awakening_settings(self):
        shown = await self.run_cmd(WeaponSkills.awakening)
        self.assertIn("+10%", shown)
        text = await self.run_cmd(WeaponSkills.awakening, 25, 50, self.Kind("drain"), 80, False, 30)
        self.assertIn("바꿨어요", text)
        b = self.store.balance(G)
        self.assertEqual((b.awaken_charge, b.ult_charge, b.special, b.special_power, b.special_ends, b.awaken_bonus), (25, 50, "drain", 80, False, 30))
        await self.run_cmd(WeaponSkills.awakening, 40)                           # 고른 칸만 바뀐다
        b = self.store.balance(G)
        self.assertEqual((b.awaken_charge, b.ult_charge, b.special), (40, 50, "drain"))
        self.assertIn("2~10", await self.run_cmd(WeaponSkills.awakening, special=self.Kind("multi"), power=50))
        await self.run_cmd(WeaponSkills.awakening, special=self.Kind("multi"))  # 세기를 비우면 그 종류의 가장 낮은 값
        self.assertEqual((self.store.balance(G).special, self.store.balance(G).special_power), ("multi", 2))
        self.assertIn("관리자만", await self.run_cmd(WeaponSkills.awakening, 99, admin=False))
        self.assertEqual(self.store.balance(G).awaken_charge, 40)

    async def test_autocomplete_lists_weapons_only(self):
        names = [c.value for c in await self.cog.weapon_names(self.w.interaction(admin=True), "")]
        self.assertIn("레이저 커터", names)
        self.assertNotIn("에너지 실드", names)
        self.assertNotIn("방진 커버", names)

    def test_awakening_text(self):
        self.assertIn("이어져요", awakening_text(Balance.build({}, None, None, None, None, {"special_ends": False})))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, 7, self.store.ensure_system_items(G)["key"], 1)
        content.edit(self.store, G, ("gear",), "레이저 커터", {"skill": "power", "skill_power": 300, "skill_cooldown": 2})

    def tearDown(self):
        self.store.close()

    async def test_the_skill_button_goes_through_the_service_and_is_saved(self):
        out = await service.enter(self.store, self.locks, G, 7)
        bal = self.store.balance(G)
        data = json.loads(out.run.to_json())
        data.update(phase="battle", kind="monster", room=2, weapon="dagger",
                    enemy=dungeon._spawn(bal.monsters[0], 1, bal) | {"hp": 100000, "max_hp": 100000, "dodge": 0})
        self.store.save_dungeon(G, 7, json.dumps(data))
        rev = Run.from_json(self.store.dungeon_run(G, 7)).rev
        out = await service.act(self.store, self.locks, Scripted(randoms=HIT), G, 7, rev, "skill")
        self.assertTrue(out.ok, out.text)
        saved = Run.from_json(self.store.dungeon_run(G, 7))
        self.assertEqual(saved.enemy["hp"], 100000 - dungeon.player_damage(saved, bal) * 3)
        self.assertEqual(saved.skill_cd, 2)
        out = await service.act(self.store, self.locks, Scripted(randoms=HIT), G, 7, saved.rev, "skill")
        self.assertFalse(out.ok)
        self.assertIn("턴 뒤", out.text)


if __name__ == "__main__":
    unittest.main()
