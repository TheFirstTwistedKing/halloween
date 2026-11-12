"""속성 상성: 관리자가 속성과 상성표(피해 배율 %)를 만들고 무기·갑옷·몬스터·보스에 붙인다. 내 공격과 몬스터의 공격 양쪽에 적용된다."""
import json
import unittest

import balance
import content
import dungeon
from balance import Balance, check_entity, clean_elements, entity_dict
from cogs.dungeon import run_embed
from cogs.elements import ElementError, Elements, add_element, assign, delete_element, set_affinity
from store import Store, validate_settings
from tests.helpers import Scripted, World, sent_embed

G = 100
ELEMENTS = [{"key": "e", "name": "전기", "emoji": "⚡"}, {"key": "c", "name": "냉각", "emoji": "❄️"}, {"key": "h", "name": "열", "emoji": "🔥"}]
TABLE = [{"atk": "e", "def": "c", "percent": 150}, {"atk": "c", "def": "e", "percent": 50}, {"atk": "h", "def": "h", "percent": 0}]


def bal(**content_):
    raw = {k: [entity_dict(check_entity(k, d)[0]) for d in v] for k, v in content_.items()}
    return Balance.build({}, raw, None, ELEMENTS, TABLE)


def weapon(**kw):
    data = dict(key="w", name="전기 커터", emoji="🔪", slot="weapon", tier="일반", weight=1, attack=80)
    data.update(kw)
    return data


def mob(**kw):
    data = dict(key="m", name="냉각 드론", emoji="🛸", hp=1000, damage=100, dodge=0)
    data.update(kw)
    return data


def fight(b, **kw):
    run = dungeon.new_run(bal=b)
    run.enemy = dungeon._spawn(b.monsters[0], 1, b)
    run.enemy["dodge"] = 0
    run.phase, run.kind, run.room = "battle", "monster", 2
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class RuleTests(unittest.TestCase):
    def test_the_table_and_defaults(self):
        b = Balance.build({}, None, None, ELEMENTS, TABLE)
        self.assertEqual((b.element_percent("e", "c"), b.element_percent("c", "e"), b.element_percent("h", "h")), (150, 50, 0))
        self.assertEqual((b.element_percent("e", "h"), b.element_percent(None, "c"), b.element_percent("e", None), b.element_percent("gone", "c")),
                         (100, 100, 100, 100))
        self.assertEqual((b.element_label("e"), b.element_label("gone"), b.element_label(None)), ("⚡ 전기", "", ""))
        self.assertEqual(Balance.build({}).elements, ())

    def test_bad_saved_entries_are_dropped(self):
        out = clean_elements(ELEMENTS + [{"key": "e", "name": "중복", "emoji": "x"}, {"key": 3}, "x"],
                             TABLE + [{"atk": "e", "def": "zz", "percent": 10}, {"atk": "e", "def": "c", "percent": 999},
                                      {"atk": "c", "def": "h", "percent": 1001}, {"atk": "c", "def": "h", "percent": True}, None])
        self.assertEqual([k for k, *_ in out["elements"]], ["e", "c", "h"])
        self.assertEqual(out["affinity"], (("e", "c", 150), ("c", "e", 50), ("h", "h", 0)))
        self.assertEqual(clean_elements(None, None), {"elements": (), "affinity": ()})

    def test_entities_carry_an_element_and_shields_never_do(self):
        self.assertEqual(check_entity("gear", weapon(element="e"))[0].element, "e")
        self.assertIsNone(check_entity("gear", dict(weapon(element="e"), slot="shield", guard=50))[0].element)
        self.assertEqual(check_entity("gear", dict(weapon(element="c"), slot="armor", reduction=5))[0].element, "c")
        self.assertEqual(check_entity("monsters", mob(element="c"))[0].element, "c")
        self.assertEqual(check_entity("bosses", mob(element="h"))[0].element, "h")
        secret = dict(key="s", name="널", emoji="🕳️", hp=5, damage=1, drop_item="candy", element="e")
        self.assertEqual(check_entity("secrets", secret)[0].element, "e")
        for bad in (3, "x" * 41, True):
            self.assertIsNotNone(check_entity("monsters", mob(element=bad))[1], bad)
        old = entity_dict(check_entity("monsters", mob(element="c"))[0])
        old.pop("element")
        self.assertIsNone(check_entity("monsters", old)[0].element)

    def test_the_spawned_enemy_keeps_its_element(self):
        b = bal(monsters=[mob(element="c")])
        self.assertEqual(dungeon._spawn(b.monsters[0], 1, b)["element"], "c")


class CombatTests(unittest.TestCase):
    def test_an_advantage_multiplies_my_damage_and_says_so(self):
        b = bal(monsters=[mob(element="c")], gear=[weapon(element="e")])
        run = fight(b, weapon="w")
        events = dungeon.player_attack(run, Scripted(randoms=[0.99]), b)
        self.assertEqual(run.enemy["hp"], 1000 - (20 + 80) * 3 // 2)
        self.assertIn("상성 우위", events[0])
        self.assertIn("×1.5", events[0])

    def test_a_disadvantage_and_immunity(self):
        b = bal(monsters=[mob(key="m", element="e")], gear=[weapon(element="c")])
        run = fight(b, weapon="w")
        events = dungeon.player_attack(run, Scripted(randoms=[0.99]), b)
        self.assertEqual(run.enemy["hp"], 1000 - 50)
        self.assertIn("상성 열세", events[0])
        immune = bal(monsters=[mob(element="h")], gear=[weapon(element="h")])
        run = fight(immune, weapon="w")
        dungeon.player_attack(run, Scripted(randoms=[0.99]), immune)
        self.assertEqual(run.enemy["hp"], 1000)

    def test_crit_comes_first_then_the_element(self):
        b = bal(monsters=[mob(element="c")], gear=[weapon(element="e", crit=100)])
        run = fight(b, weapon="w")
        dungeon.player_attack(run, Scripted(randoms=[0.99, 0.0]), b)
        self.assertEqual(run.enemy["hp"], 1000 - 300)                                  # (20+80)×2 = 200 → ×1.5 = 300

    def test_no_element_on_either_side_is_unchanged(self):
        b = bal(monsters=[mob()], gear=[weapon(element="e")])
        run = fight(b, weapon="w")
        events = dungeon.player_attack(run, Scripted(randoms=[0.99]), b)
        self.assertEqual(run.enemy["hp"], 900)
        self.assertNotIn("상성", events[0])

    def test_the_monster_hits_my_armor_by_the_same_table(self):
        b = bal(monsters=[mob(element="e")], gear=[dict(weapon(element="c"), key="a", name="냉각 장갑", slot="armor", reduction=1)])
        run = fight(b, armor="a")
        run.enemy["hp"] = 10 ** 6
        events = dungeon.player_attack(run, Scripted(randoms=[0.99]), b)
        self.assertEqual(run.hp, b.max_hp - 149)                                        # 전기 → 냉각 150% = 150, 갑옷 1% 감소 → 149
        self.assertTrue(any("상성 우위" in e and "공격!" in e for e in events))

    def test_guard_applies_after_the_element(self):
        b = bal(monsters=[mob(element="e", damage=1000)], gear=[dict(weapon(element="c"), key="a", name="냉각 장갑", slot="armor", reduction=1)])
        run = fight(b, armor="a", hp=1000)
        dungeon.player_defend(run, True, b)
        self.assertEqual(run.hp, 1000 - 600 + 500)                                      # 1500 × (100-60)% = 600, 가드 회복은 원래 공격(1000)의 50%

    def test_a_deleted_element_counts_as_none(self):
        b = Balance.build({}, {"monsters": [entity_dict(check_entity("monsters", mob(element="gone"))[0])]}, None, ELEMENTS, TABLE)
        run = fight(b)
        dungeon.player_attack(run, Scripted(randoms=[0.99]), b)
        self.assertEqual(run.enemy["hp"], 980)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_settings_are_checked_and_reach_the_balance(self):
        cfg = self.store.settings(G)
        self.assertEqual((cfg["elements"], cfg["affinity"]), ([], []))
        for bad in (dict(elements="x"), dict(elements=[{"key": 1}]), dict(affinity=[{"atk": "a", "def": "b", "percent": 1001}]),
                    dict(affinity=[{"atk": "a", "def": "b", "percent": True}]), dict(elements=[{"key": "a", "name": "a", "emoji": "a"}] * 21)):
            with self.assertRaises(ValueError, msg=bad):
                validate_settings({**cfg, **bad})
        self.store.update_settings(G, elements=ELEMENTS, affinity=TABLE)
        self.assertEqual(self.store.balance(G).element_percent("e", "c"), 150)


class CommandLogicTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_add_validate_and_delete(self):
        add_element(self.store, G, "전기", "⚡")
        add_element(self.store, G, "냉각", "❄️")
        for args, text in ((("전기", "🔥"), "같은 이름"), (("가" * 21, "🔥"), "1~20자"), (("열", "abc"), "이모지")):
            with self.assertRaisesRegex(ElementError, text):
                add_element(self.store, G, *args)
        set_affinity(self.store, G, "전기", "냉각", 150)
        set_affinity(self.store, G, "냉각", "전기", 50)
        assign(self.store, G, "오일 슬러지", "전기")
        entry, users = delete_element(self.store, G, "전기")
        self.assertEqual((entry["name"], users), ("전기", 1))
        self.assertEqual(self.store.settings(G)["affinity"], [])                         # 그 속성이 든 칸은 다 지운다
        with self.assertRaisesRegex(ElementError, "그런 속성이 없어요"):
            delete_element(self.store, G, "전기")
        bal = self.store.balance(G)
        slime = next(m for m in bal.monsters if m.name == "오일 슬러지")
        self.assertEqual((bal.element_label(slime.element), bal.element_percent(slime.element, "x")), ("", 100))   # 지운 속성은 무속성으로 친다

    def test_the_limit(self):
        for n in range(balance.MAX_ELEMENTS):
            add_element(self.store, G, f"속성{n}", "⚙️")
        with self.assertRaisesRegex(ElementError, "20개까지"):
            add_element(self.store, G, "하나 더", "⚙️")

    def test_affinity_set_reset_and_old_value(self):
        add_element(self.store, G, "전기", "⚡")
        add_element(self.store, G, "냉각", "❄️")
        self.assertEqual(set_affinity(self.store, G, "전기", "냉각", 200)[2], 100)
        self.assertEqual(set_affinity(self.store, G, "전기", "냉각", 150)[2], 200)
        self.assertEqual(len(self.store.settings(G)["affinity"]), 1)
        set_affinity(self.store, G, "전기", "냉각", 100)
        self.assertEqual(self.store.settings(G)["affinity"], [])
        with self.assertRaisesRegex(ElementError, "그런 속성"):
            set_affinity(self.store, G, "전기", "없음", 120)

    def test_assign_to_each_kind_and_refusals(self):
        add_element(self.store, G, "전기", "⚡")
        key = self.store.settings(G)["elements"][0]["key"]
        for target in ("레이저 커터", "방진 커버", "오일 슬러지", "메인프레임 군주", "NULL 군주"):
            section, data, chosen = assign(self.store, G, target, "전기")
            self.assertEqual(data["element"], key, target)
        with self.assertRaisesRegex(ElementError, "방패에는"):
            assign(self.store, G, "에너지 실드", "전기")
        with self.assertRaisesRegex(ElementError, "그런 속성"):
            assign(self.store, G, "레이저 커터", "없는 속성")
        with self.assertRaisesRegex(ElementError, "그런 장비·몬스터"):
            assign(self.store, G, "없는 것", "전기")
        _, data, chosen = assign(self.store, G, "레이저 커터", "-")
        self.assertEqual((data["element"], chosen), (None, None))
        section, data, _ = assign(self.store, G, "monsters:slime", "전기")                # 자동완성 값(구역:key)
        self.assertEqual((section, data["name"]), ("monsters", "오일 슬러지"))

    def test_the_list_describes_elements(self):
        add_element(self.store, G, "전기", "⚡")
        assign(self.store, G, "오일 슬러지", "전기")
        line = content.describe("monsters", next(d for d in content.current(self.store, G, "monsters") if d["name"] == "오일 슬러지"), self.store.balance(G))
        self.assertIn("[⚡ 전기]", line)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Elements(self.w.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args)
        embed, _ = sent_embed(i)
        return (embed.description or "") + "".join(f.value for f in embed.fields)

    async def test_the_whole_flow_through_the_commands(self):
        self.assertIn("만들었어요", await self.run_cmd(Elements.element_add, "전기", "⚡"))
        await self.run_cmd(Elements.element_add, "냉각", "❄️")
        self.assertIn("**150%**", await self.run_cmd(Elements.element_affinity, "전기", "냉각", 150))
        self.assertIn("공격 속성: **⚡ 전기**", await self.run_cmd(Elements.element_assign, "레이저 커터", "전기"))
        self.assertIn("방어 속성: **❄️ 냉각**", await self.run_cmd(Elements.element_assign, "방진 커버", "냉각"))
        listed = await self.run_cmd(Elements.element_list)
        self.assertIn("⚡ 전기 → ❄️ 냉각: **150%** 🔺", listed)
        self.assertIn("붙은 장비·몬스터 1개", listed)
        self.assertIn("방패에는", await self.run_cmd(Elements.element_assign, "에너지 실드", "전기"))
        self.assertIn("지웠어요", await self.run_cmd(Elements.element_delete, "냉각"))

    async def test_admin_only_and_autocomplete(self):
        self.assertIn("관리자만", await self.run_cmd(Elements.element_add, "전기", "⚡", admin=False))
        self.assertEqual(self.store.settings(G)["elements"], [])
        await self.run_cmd(Elements.element_add, "전기", "⚡")
        i = self.w.interaction(admin=True)
        names = [c.value for c in await self.cog.element_names(i, "")]
        self.assertEqual(names, ["전기", "-"])
        targets = {c.value: c.name for c in await self.cog.target_names(i, "")}
        self.assertIn("gear:dagger", targets)
        self.assertNotIn("gear:shield", targets)                                       # 방패는 빠진다
        self.assertTrue(any(v.startswith("secrets:") for v in targets))

    async def test_the_battle_screen_shows_elements(self):
        self.store.update_settings(G, elements=ELEMENTS, affinity=TABLE)
        assign(self.store, G, "오일 슬러지", "냉각")
        assign(self.store, G, "레이저 커터", "전기")
        b = self.store.balance(G)
        run = dungeon.new_run(bal=b)
        run.enemy = dungeon._spawn(next(m for m in b.monsters if m.name == "오일 슬러지"), 1, b)
        run.phase, run.weapon = "battle", "dagger"
        names = [f.name for f in run_embed(run, None, b).fields]
        self.assertTrue(any("오일 슬러지" in n and "[❄️ 냉각]" in n for n in names))
        gear = {f.name: f.value for f in run_embed(run, None, b).fields}["🎒 장비"]
        self.assertIn("레이저 커터 [⚡ 전기]", gear)
