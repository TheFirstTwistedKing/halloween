"""스킬·궁극기 이름 붙이기: 무기 스킬·궁극기, 각성 특수 스킬, 보스·시크릿 보스·최종보스 스킬마다 관리자가 따로 이름을 붙인다."""
import json
import unittest

import dungeon
from discord.app_commands import Choice
from balance import (Balance, check_entity, check_final, clean_awakening, clean_skill_name, entity_dict, name_change, skill_text,
                     weapon_skill_text)
from cogs.boss_patterns import BossPatterns
from cogs.dungeon import DungeonView, run_embed
from cogs.final_boss import FinalBoss
from cogs.weapon_skills import WeaponSkills
from tests.helpers import Scripted, World, sent_embed

G = 100
HIT = [0.99] * 20
Kind = type("C", (), {"__init__": lambda self, value: setattr(self, "value", value)})
SKILL, ULT = Choice(name="스킬", value="skill"), Choice(name="궁극기", value="ult")


def weapon(**kw):
    data = dict(key="w", name="테스트 커터", emoji="🔪", slot="weapon", tier="일반", weight=1, attack=80)
    data.update(kw)
    return data


def boss(**kw):
    data = dict(key="b", name="코어", emoji="🖥️", hp=100000, damage=100, dodge=0)
    data.update(kw)
    return data


def build(gear=None, bosses=None, awakening=None):
    raw = {"gear": [entity_dict(check_entity("gear", gear or weapon(skill="power", skill_power=300, skill_name="플라즈마 참격",
                                                                       ult="multi", ult_power=3, ult_name="천둥 난무"))[0])],
           "bosses": [entity_dict(check_entity("bosses", bosses or boss(skill="strike", skill_every=1, skill_power=200, skill_name="분쇄"))[0])]}
    return Balance.build({"일반.층당강화": 0}, raw, None, None, None, awakening)


def fight(b, **kw):
    run = dungeon.new_run(weapon="w", bal=b)
    run.enemy = dungeon._spawn(b.bosses[0], 1, b)
    run.phase, run.kind, run.room, run.hp = "battle", "boss", dungeon.ROOMS, b.max_hp
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class NameRuleTests(unittest.TestCase):
    def test_clean_and_change(self):
        self.assertEqual(clean_skill_name("  플라즈마 참격 "), ("플라즈마 참격", None))
        self.assertEqual(clean_skill_name(None), (None, None))
        for bad in ("가" * 21, "@everyone", "굵게**", "[괄호]", 5):
            self.assertIsNotNone(clean_skill_name(bad)[1], bad)
        self.assertEqual(name_change(None), (False, None, None))                         # 비우면 그대로
        self.assertEqual(name_change("기본"), (True, None, None))                        # '기본'이면 기본 이름
        self.assertEqual(name_change("지옥불"), (True, "지옥불", None))
        self.assertIsNotNone(name_change("  ")[2])
        self.assertIsNotNone(name_change("")[2])

    def test_text_shows_the_name_and_the_kind(self):
        self.assertEqual(weapon_skill_text("power", 300, "참격"), "[참격](강타) 공격 피해의 300%")
        self.assertEqual(weapon_skill_text("power", 300), "[강타] 공격 피해의 300%")
        self.assertEqual(skill_text("burn", 3, 40, 2, "지옥불"), "[지옥불](과열) 3번째 공격마다 · 2턴 동안 턴마다 40 피해")


class ValidationTests(unittest.TestCase):
    def test_weapon_names_need_the_technique(self):
        g = check_entity("gear", weapon(skill="power", skill_power=300, skill_name="참격", ult="multi", ult_power=3, ult_name="난무"))[0]
        self.assertEqual((g.skill_name, g.ult_name), ("참격", "난무"))
        self.assertEqual(check_entity("gear", json.loads(json.dumps(entity_dict(g))))[0], g)
        bare = check_entity("gear", weapon(skill_name="참격", ult_name="난무"))[0]
        self.assertEqual((bare.skill_name, bare.ult_name), (None, None))                 # 기술이 없으면 이름도 없다
        shield = check_entity("gear", dict(key="s", name="실드", emoji="🛡️", slot="shield", tier="일반", weight=1, guard=50,
                                           skill="power", skill_power=300, skill_name="참격"))[0]
        self.assertIsNone(shield.skill_name)
        self.assertIsNotNone(check_entity("gear", weapon(skill="power", skill_power=300, skill_name="@here"))[1])
        self.assertIn("[참격](강타)", build(gear=entity_dict(g)).gear[0].description)

    def test_boss_names(self):
        b = check_entity("bosses", boss(skill="burn", skill_every=2, skill_power=30, skill_turns=2, skill_name="지옥불"))[0]
        self.assertEqual(b.skill_name, "지옥불")
        self.assertIsNone(check_entity("bosses", boss(skill_name="지옥불"))[0].skill_name)
        self.assertIsNotNone(check_entity("bosses", boss(skill="strike", skill_every=2, skill_power=200, skill_name="가" * 30))[1])
        secret = check_entity("secrets", dict(key="s", name="널", emoji="🕳️", hp=5, damage=1, drop_item="candy", skill="heal", skill_every=2,
                                              skill_power=10, skill_name="자가 복구"))[0]
        self.assertEqual(secret.skill_name, "자가 복구")

    def test_awakening_and_final_boss(self):
        self.assertEqual(clean_awakening({"special_name": "오버드라이브"})["special_name"], "오버드라이브")
        self.assertIsNone(clean_awakening({"special_name": "@everyone"})["special_name"])     # 잘못된 이름은 기본으로
        self.assertIsNone(clean_awakening({})["special_name"])
        self.assertEqual(check_final({"skill_name": "종말"})[0]["skill_name"], "종말")
        self.assertIsNone(check_final({"skill": "none", "skill_name": "종말"})[0]["skill_name"])
        self.assertIsNotNone(check_final({"skill_name": "<@1>"})[1])


class BattleTests(unittest.TestCase):
    def test_techniques_use_their_names(self):
        b = build(awakening={"special_name": "오버드라이브"})
        run = fight(b)
        self.assertTrue(any("[플라즈마 참격]" in e for e in dungeon.use_technique(run, "skill", Scripted(randoms=HIT), b)))
        run.ult_gauge = 80
        run.hp = b.max_hp
        self.assertTrue(any("[천둥 난무]" in e for e in dungeon.player_attack(run, Scripted(randoms=HIT), b)))   # 게이지 100% 알림
        run.hp = b.max_hp
        self.assertTrue(any("[천둥 난무]" in e for e in dungeon.use_technique(run, "ult", Scripted(randoms=HIT), b)))
        run.awakened, run.hp = True, b.max_hp
        self.assertTrue(any("[오버드라이브]" in e for e in dungeon.use_technique(run, "special", Scripted(randoms=HIT), b)))

    def test_boss_skill_uses_its_name_and_old_fights_fall_back(self):
        b = build()
        run = fight(b)
        self.assertTrue(any("[분쇄]" in e for e in dungeon.player_attack(run, Scripted(randoms=HIT), b)))
        old = fight(b)
        old.enemy["skill"].pop("name")                                                  # 이 업데이트 전에 시작한 싸움
        self.assertTrue(any("[강타]" in e for e in dungeon.player_attack(old, Scripted(randoms=HIT), b)))

    def test_screen_buttons_and_status(self):
        b = build(awakening={"special_name": "오버드라이브"})
        run = fight(b, skill_cd=2, awakened=True)
        labels = [c.label for c in DungeonView(object(), 7, run, b).children if getattr(c, "label", None)]
        self.assertIn("스킬 플라즈마 참격 (2턴)", labels)
        self.assertIn("궁극기 천둥 난무 (0%)", labels)
        self.assertIn("각성 특수 스킬 오버드라이브", labels)
        shown = "".join(f.value for f in run_embed(run, None, b).fields)
        self.assertIn("[플라즈마 참격] 쿨타임", shown)
        self.assertIn("특수 스킬 [오버드라이브]", shown)
        self.assertIn("[분쇄]", shown)                                                   # 다음 보스 스킬


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, cog, command, *args, **kwargs):
        i = self.w.interaction(admin=True)
        await command.callback(cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    def gear(self, name="레이저 커터"):
        return next(g for g in self.store.balance(G).gear if g.name == name)

    async def test_weapon_skill_and_ult_names(self):
        cog = WeaponSkills(self.w.bot)
        await self.run_cmd(cog, WeaponSkills.skill, "레이저 커터", Kind("power"), 300, 3, "레이저 참격")
        self.assertEqual(self.gear().skill_name, "레이저 참격")
        await self.run_cmd(cog, WeaponSkills.skill, "레이저 커터", Kind("power"), 400, 2)          # 이름을 비우면 그대로
        self.assertEqual((self.gear().skill_name, self.gear().skill_power), ("레이저 참격", 400))
        await self.run_cmd(cog, WeaponSkills.rename, "레이저 커터", SKILL, "광선 베기")    # 이름만 바꾼다
        self.assertEqual((self.gear().skill_name, self.gear().skill_power, self.gear().skill_cooldown), ("광선 베기", 400, 2))
        self.assertIn("궁극기이(가) 없어요", await self.run_cmd(cog, WeaponSkills.rename, "레이저 커터", ULT, "난무"))
        await self.run_cmd(cog, WeaponSkills.ult, "레이저 커터", Kind("multi"), 3, "레이저 폭풍")
        self.assertEqual(self.gear().ult_name, "레이저 폭풍")
        self.assertIn("[레이저 폭풍](연타)", await self.run_cmd(cog, WeaponSkills.show))
        self.assertIn("스킬 이름은", await self.run_cmd(cog, WeaponSkills.rename, "레이저 커터", SKILL, "@everyone"))
        self.assertEqual(self.gear().skill_name, "광선 베기")
        await self.run_cmd(cog, WeaponSkills.rename, "레이저 커터", SKILL, "기본")
        self.assertIsNone(self.gear().skill_name)
        await self.run_cmd(cog, WeaponSkills.ult, "레이저 커터", Kind("none"))
        await self.run_cmd(cog, WeaponSkills.ult, "레이저 커터", Kind("multi"), 3)               # 다시 붙이면 이름은 기본
        self.assertIsNone(self.gear().ult_name)

    async def test_awakening_special_name(self):
        cog = WeaponSkills(self.w.bot)
        await self.run_cmd(cog, WeaponSkills.awakening, special_name="오버드라이브")
        self.assertEqual(self.store.balance(G).special_name, "오버드라이브")
        await self.run_cmd(cog, WeaponSkills.awakening, 30)
        self.assertEqual(self.store.balance(G).special_name, "오버드라이브")
        self.assertIn("스킬 이름은", await self.run_cmd(cog, WeaponSkills.awakening, special_name="가" * 21))
        await self.run_cmd(cog, WeaponSkills.awakening, special_name="기본")
        self.assertIsNone(self.store.balance(G).special_name)

    async def test_boss_and_final_boss_names(self):
        cog = BossPatterns(self.w.bot)
        find = lambda: next(b for b in self.store.balance(G).bosses if b.name == "메인프레임 군주")
        self.assertIn("스킬이 없어요", await self.run_cmd(cog, BossPatterns.rename, "메인프레임 군주", "분쇄"))
        await self.run_cmd(cog, BossPatterns.skill, "메인프레임 군주", Kind("strike"), 2, 200, 0, "메인프레임 분쇄")
        self.assertEqual(find().skill_name, "메인프레임 분쇄")
        self.assertIn("[코어 붕괴](강타)", await self.run_cmd(cog, BossPatterns.rename, "메인프레임 군주", "코어 붕괴"))
        self.assertEqual((find().skill_name, find().skill_every), ("코어 붕괴", 2))
        await self.run_cmd(cog, BossPatterns.skill, "메인프레임 군주", Kind("none"))
        self.assertIsNone(find().skill_name)
        final = FinalBoss(self.w.bot)
        await self.run_cmd(final, FinalBoss.skill, Kind("burn"), 2, 50, 3, "종말의 불")
        self.assertEqual(self.store.balance(G).final_boss.skill_name, "종말의 불")
        await self.run_cmd(final, FinalBoss.skill_rename, "오메가 플레어")
        self.assertEqual((self.store.balance(G).final_boss.skill_name, self.store.balance(G).final_boss.skill), ("오메가 플레어", "burn"))
        await self.run_cmd(final, FinalBoss.skill_rename, "기본")
        self.assertIsNone(self.store.balance(G).final_boss.skill_name)
        await self.run_cmd(final, FinalBoss.skill, Kind("none"))
        self.assertIn("스킬이 없어요", await self.run_cmd(final, FinalBoss.skill_rename, "불"))


if __name__ == "__main__":
    unittest.main()
