"""던전 콘텐츠 편집 시험: 몬스터·보스·장비·소모품을 더하고 빼고 바꾸는 규칙, 저장 형식, 실제 게임에 미치는 영향, 명령어."""
import json
import unittest

import balance
import balance_sim
import content
import dungeon
import dungeon_service as service
from balance import Balance, check_entity, clean_content, entity_dict
from cogs.content_admin import ContentAdmin, names_for
from confirm import Confirm
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


class EntityFormatTests(unittest.TestCase):
    def test_every_default_entity_survives_a_save_and_load_round_trip(self):
        for section in balance.SECTIONS:
            for entity in getattr(balance.DEFAULT, section):
                loaded, problem = check_entity(section, json.loads(json.dumps(entity_dict(entity))))
                self.assertIsNone(problem, (section, entity.name))
                self.assertEqual(loaded, entity, (section, entity.name))

    def test_a_fully_custom_section_replaces_the_default_table(self):
        raw = {"monsters": [dict(key="a", name="허수아비", emoji="🎃", hp=10, damage=1, dodge=0)]}
        bal = Balance.build({}, raw)
        self.assertEqual([m.name for m in bal.monsters], ["허수아비"])
        self.assertEqual(bal.bosses, balance.BOSSES)                  # 손대지 않은 구역은 기본값 그대로
        self.assertEqual(bal.gear, balance.GEAR)

    def test_bad_entries_are_dropped_not_fatal(self):
        raw = {"gear": [dict(key="ok", name="막대", emoji="🪄", slot="weapon", tier="일반", weight=5, attack=10),
                        dict(key="ok", name="중복 key", emoji="🪄", slot="weapon", tier="일반", weight=5, attack=10),
                        dict(key="b", name="이상한", emoji="x", slot="hat", tier="일반", weight=5),
                        dict(key="c", name="", emoji="x", slot="weapon", tier="일반", weight=5),
                        dict(key="d", name="음수", emoji="x", slot="weapon", tier="일반", weight=-1),
                        dict(key="e", name="가짜등급", emoji="x", slot="weapon", tier="신화", weight=1),
                        dict(key="f", name="방패인데 가드 없음", emoji="x", slot="shield", tier="일반", weight=1),
                        "문자열", None, 5]}
        self.assertEqual([g.name for g in clean_content(raw)["gear"]], ["막대"])
        for junk in (None, "x", 5, [], {"gear": "x"}, {"unknown": []}):
            self.assertEqual(clean_content(junk), {})

    def test_check_entity_rules(self):
        base = dict(key="k", name="이름", emoji="🎃")
        cases = [("monsters", dict(hp=0, damage=1), "hp"), ("monsters", dict(hp=10, damage=-1), "damage"),
                 ("monsters", dict(hp=10, damage=1, dodge=96), "dodge"), ("monsters", dict(hp=10.5, damage=1), "hp"),
                 ("monsters", dict(hp=True, damage=1), "hp"),
                 ("consumables", dict(tier="일반", weight=1), "회복량"),
                 ("consumables", dict(tier="일반", weight=1, regen=5), "지속 턴")]
        for section, extra, needle in cases:
            entity, problem = check_entity(section, {**base, **extra})
            self.assertIsNone(entity)
            self.assertIn(needle, problem, (section, extra))

    def test_bosses_can_never_dodge_and_shields_and_weapons_keep_their_own_fields(self):
        boss, _ = check_entity("bosses", dict(key="b", name="보스", emoji="👑", hp=10, damage=1, dodge=50))
        self.assertEqual(boss.dodge, 0)
        self.assertTrue(boss.boss)
        shield, _ = check_entity("gear", dict(key="s", name="방패", emoji="🛡️", slot="shield", tier="일반", weight=1, guard=70, attack=500, crit=90, dodge=40))
        self.assertEqual((shield.attack, shield.crit, shield.dodge, shield.guard), (0, 0, None, 70))
        weapon, _ = check_entity("gear", dict(key="w", name="칼", emoji="🗡️", slot="weapon", tier="일반", weight=1, attack=10, guard=70, reflect=9))
        self.assertEqual((weapon.guard, weapon.reflect), (None, None))

    def test_consumable_effects_are_exclusive(self):
        item, _ = check_entity("consumables", dict(key="c", name="약", emoji="💊", tier="일반", weight=1, heal=50, full=True, regen=5, regen_turns=3))
        self.assertEqual((item.full, item.heal, item.regen, item.regen_turns), (True, 0, 0, 0))
        item, _ = check_entity("consumables", dict(key="c", name="약", emoji="💊", tier="일반", weight=1, heal=50, regen=5, regen_turns=3))
        self.assertEqual((item.full, item.heal, item.regen, item.regen_turns), (False, 0, 5, 3))
        self.assertIn("3턴", item.description)


class ContentFixture(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def monster(self, **kw):
        return dict(name="드래곤", emoji="🐉", hp=500, damage=80, dodge=5, **kw)


class ContentLogicTests(ContentFixture):
    def test_add_a_monster_and_it_joins_the_default_ones(self):
        entity = content.add(self.store, G, "monsters", self.monster())
        names = [m.name for m in self.store.balance(G).monsters]
        self.assertEqual(names, ["거대 박쥐", "슬라임", "마녀", "거대 쥐", "드래곤"])
        self.assertTrue(entity.key.startswith("x"))
        self.assertEqual(self.store.balance(G).bosses, balance.BOSSES)

    def test_only_the_edited_section_is_stored(self):
        content.add(self.store, G, "monsters", self.monster())
        self.assertEqual(set(self.store.settings(G)["dungeon_content"]), {"monsters"})

    def test_keys_never_collide_even_after_deleting(self):
        keys = set()
        for i in range(30):
            keys.add(content.add(self.store, G, "gear", dict(name=f"장비{i}", emoji="🎃", slot="weapon", tier="일반", weight=1, attack=i)).key)
            if i % 3 == 0:
                content.delete(self.store, G, ("gear",), f"장비{i}")
        self.assertEqual(len(keys), 30)

    def test_names_are_unique_per_scope_ignoring_case_and_spaces_around(self):
        content.add(self.store, G, "monsters", self.monster())
        for name in ("드래곤", " 드래곤 ", "슬라임", "거대 슬라임"):                 # 몬스터·보스는 서로의 이름도 쓸 수 없다.
            with self.assertRaisesRegex(content.ContentError, "같은 이름"):
                content.add(self.store, G, "monsters", {**self.monster(), "name": name})
        with self.assertRaisesRegex(content.ContentError, "같은 이름"):
            content.add(self.store, G, "bosses", {**self.monster(), "name": "마녀"})
        content.add(self.store, G, "gear", dict(name="마녀", emoji="🎃", slot="weapon", tier="일반", weight=1))   # 다른 구역이면 같은 이름 가능
        with self.assertRaisesRegex(content.ContentError, "같은 이름"):
            content.add(self.store, G, "gear", dict(name="단검", emoji="🎃", slot="weapon", tier="일반", weight=1))

    def test_edit_changes_only_what_is_given_and_keeps_the_key(self):
        before = next(m for m in self.store.balance(G).monsters if m.name == "슬라임")
        section, after = content.edit(self.store, G, ("monsters", "bosses"), "슬라임", {"hp": 999, "name": "초록 슬라임"})
        self.assertEqual((section, after.key, after.hp, after.damage, after.dodge), ("monsters", before.key, 999, before.damage, before.dodge))
        self.assertEqual(self.store.balance(G).monster_by_key["slime"].name, "초록 슬라임")
        self.assertIsNotNone(content.find(self.store, G, ("monsters",), "초록 슬라임"))
        self.assertIsNone(content.find(self.store, G, ("monsters",), "슬라임"))
        with self.assertRaisesRegex(content.ContentError, "바꿀 내용"):
            content.edit(self.store, G, ("monsters",), "초록 슬라임", {})
        with self.assertRaisesRegex(content.ContentError, "그런 항목"):
            content.edit(self.store, G, ("monsters",), "없는 것", {"hp": 5})
        with self.assertRaisesRegex(content.ContentError, "같은 이름"):
            content.edit(self.store, G, ("monsters",), "마녀", {"name": "초록 슬라임"})
        content.edit(self.store, G, ("monsters",), "마녀", {"name": "마녀"})            # 자기 이름 그대로는 허용

    def test_a_failed_edit_changes_nothing(self):
        snapshot = self.store.settings(G)
        for bad in ({"hp": 0}, {"emoji": "abc"}, {"name": ""}, {"dodge": 200}):
            with self.assertRaises(content.ContentError, msg=str(bad)):
                content.edit(self.store, G, ("monsters",), "슬라임", bad)
        self.assertEqual(self.store.settings(G), snapshot)

    def test_boss_stays_a_boss_and_cannot_dodge_after_edits(self):
        _, boss = content.edit(self.store, G, ("monsters", "bosses"), "해골 왕", {"dodge": 80, "hp": 5000})
        self.assertEqual((boss.boss, boss.dodge, boss.hp), (True, 0, 5000))

    def test_delete_removes_it_and_the_game_still_works(self):
        for name in ("거대 박쥐", "슬라임", "마녀"):
            content.delete(self.store, G, ("monsters",), name)
        self.assertEqual([m.name for m in self.store.balance(G).monsters], ["거대 쥐"])
        with self.assertRaisesRegex(content.ContentError, "그런 항목"):
            content.delete(self.store, G, ("monsters",), "마녀")

    def test_the_last_monster_cannot_be_deleted_while_rooms_still_spawn_them(self):
        for name in ("거대 박쥐", "슬라임", "마녀"):
            content.delete(self.store, G, ("monsters",), name)
        with self.assertRaisesRegex(content.ContentError, "잡몹이 하나도 없어요"):
            content.delete(self.store, G, ("monsters",), "거대 쥐")
        self.assertEqual(len(self.store.balance(G).monsters), 1)
        for room in (2, 4, 6, 8):                                                  # 방 배치에서 잡몹을 없애면 지울 수 있다.
            overrides = {**self.store.settings(G)["dungeon"], f"방{room}.잡몹": 0}
            self.store.update_settings(G, dungeon=overrides)
        content.delete(self.store, G, ("monsters",), "거대 쥐")
        self.assertEqual(self.store.balance(G).monsters, ())

    def test_the_last_boss_gear_or_consumable_cannot_be_deleted(self):
        for name in ("해골 왕", "거대 슬라임"):
            content.delete(self.store, G, ("bosses",), name)
        with self.assertRaisesRegex(content.ContentError, "보스가 하나도 없어요"):
            content.delete(self.store, G, ("bosses",), "고블린 왕")
        for g in list(balance.GEAR)[:-1]:
            content.delete(self.store, G, ("gear",), g.name)
        with self.assertRaisesRegex(content.ContentError, "장비"):
            content.delete(self.store, G, ("gear",), balance.GEAR[-1].name)
        for c in list(balance.CONSUMABLES)[:-1]:
            content.delete(self.store, G, ("consumables",), c.name)
        with self.assertRaisesRegex(content.ContentError, "아이템"):
            content.delete(self.store, G, ("consumables",), balance.CONSUMABLES[-1].name)

    def test_zeroing_every_drop_weight_is_refused(self):
        for g in balance.GEAR[:-1]:
            content.edit(self.store, G, ("gear",), g.name, {"weight": 0})
        with self.assertRaisesRegex(content.ContentError, "가중치"):
            content.edit(self.store, G, ("gear",), balance.GEAR[-1].name, {"weight": 0})

    def test_section_size_limits(self):
        for i in range(balance.SECTION_LIMITS["bosses"] - 3):
            content.add(self.store, G, "bosses", {**self.monster(), "name": f"보스{i}"})
        with self.assertRaisesRegex(content.ContentError, "최대 25개"):
            content.add(self.store, G, "bosses", {**self.monster(), "name": "넘치는 보스"})

    def test_bad_new_entries_are_refused(self):
        for bad, needle in ((dict(self.monster(), emoji="abc"), "이모지"), (dict(self.monster(), name="가" * 31), "이름"),
                            (dict(self.monster(), hp=0), "hp"), (dict(self.monster(), name=""), "이름")):
            with self.assertRaisesRegex(content.ContentError, needle):
                content.add(self.store, G, "monsters", bad)
        with self.assertRaisesRegex(content.ContentError, "가드"):
            content.add(self.store, G, "gear", dict(name="맨방패", emoji="🛡️", slot="shield", tier="일반", weight=1))
        self.assertEqual(self.store.settings(G)["dungeon_content"], {})

    def test_server_emoji_is_allowed(self):
        entity = content.add(self.store, G, "monsters", {**self.monster(), "emoji": "<:drake:123456789012345678>"})
        self.assertEqual(entity.emoji, "<:drake:123456789012345678>")

    def test_servers_are_independent(self):
        content.add(self.store, 1, "monsters", self.monster())
        self.assertEqual(len(self.store.balance(2).monsters), 4)
        self.assertEqual(len(self.store.balance(1).monsters), 5)

    def test_numeric_overrides_are_baked_in_on_first_edit_and_then_dropped(self):
        self.store.update_settings(G, dungeon={"몬스터.슬라임.체력": 111, "방어.회복률": 33, "장비.단검.공격력": 77})
        content.add(self.store, G, "monsters", self.monster())
        cfg = self.store.settings(G)
        self.assertEqual(cfg["dungeon"], {"방어.회복률": 33, "장비.단검.공격력": 77})      # 몬스터 항목만 정리된다
        self.assertEqual(next(m for m in self.store.balance(G).monsters if m.key == "slime").hp, 111)    # 값은 보존됐다
        self.assertEqual(next(g for g in self.store.balance(G).gear if g.key == "dagger").attack, 77)    # 장비는 아직 기본+덮어쓰기
        self.assertEqual(self.store.balance(G).guard_heal, 33)

    def test_legacy_entity_overrides_still_apply_to_untouched_sections(self):
        self.store.update_settings(G, dungeon={"보스.해골 왕.체력": 1234})
        self.assertEqual(next(b for b in self.store.balance(G).bosses if b.key == "skeleton_king").hp, 1234)
        content.add(self.store, G, "monsters", self.monster())            # 몬스터 구역을 손대도 보스 덮어쓰기는 그대로
        self.assertEqual(next(b for b in self.store.balance(G).bosses if b.key == "skeleton_king").hp, 1234)

    def test_reset_returns_to_defaults(self):
        self.store.update_settings(G, dungeon={"몬스터.슬라임.체력": 111})
        content.add(self.store, G, "monsters", self.monster())
        content.add(self.store, G, "gear", dict(name="활", emoji="🏹", slot="weapon", tier="일반", weight=1))
        self.assertEqual(content.reset(self.store, G, ("monsters",)), 1)
        self.assertEqual(self.store.balance(G).monsters, balance.MONSTERS)
        self.assertEqual(set(self.store.settings(G)["dungeon_content"]), {"gear"})
        self.assertEqual(content.reset(self.store, G), 1)
        self.assertEqual(self.store.settings(G)["dungeon_content"], {})
        self.assertEqual(content.reset(self.store, G), 0)

    def test_reset_also_clears_the_old_number_overrides_of_that_section_only(self):
        self.store.update_settings(G, dungeon={"몬스터.슬라임.체력": 111, "보스.해골 왕.체력": 222, "방어.회복률": 33})
        self.assertEqual(content.reset(self.store, G, ("monsters",)), 0)                    # 직접 만든 구역은 없었다
        self.assertEqual(self.store.settings(G)["dungeon"], {"보스.해골 왕.체력": 222, "방어.회복률": 33})
        self.assertEqual(next(m for m in self.store.balance(G).monsters if m.key == "slime").hp, 270)
        self.assertEqual(next(b for b in self.store.balance(G).bosses if b.key == "skeleton_king").hp, 222)

    def test_describe_lines(self):
        line = content.describe("monsters", entity_dict(balance.MONSTERS[3]))
        self.assertEqual(line, "🐀 **거대 쥐** — HP 90 · 공격 45 · 회피 30%")
        self.assertNotIn("회피", content.describe("bosses", entity_dict(balance.BOSSES[0])))
        self.assertIn("공격 피해량 +35", content.describe("gear", entity_dict(balance.GEAR[2])))

    def test_content_in_a_corrupt_db_row_does_not_crash_the_bot(self):
        self.store.db.execute("INSERT INTO settings VALUES(1, ?)", (json.dumps({"dungeon_content": {"monsters": "망가짐", "gear": [1, 2]}}),))
        self.store.db.commit()
        bal = self.store.balance(1)
        self.assertEqual(bal.monsters, balance.MONSTERS)
        self.assertEqual(bal.gear, ())                                  # 구역은 있으나 쓸 수 있는 칸이 없다(검사를 통과하지 못한 직접 편집)


class RuntimeEffectTests(unittest.IsolatedAsyncioTestCase):
    """편집한 내용이 실제 던전에서 나오고 동작하는지, 편집 도중에도 진행 중인 던전이 깨지지 않는지."""

    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.locks = self.w.locks
        self.store.ensure_defaults(G)

    async def asyncTearDown(self):
        await self.w.close()

    def start(self, **run_kw):
        bal = self.store.balance(G)
        run = dungeon.new_run(bal=bal)
        for k, v in run_kw.items():
            setattr(run, k, v)
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO dungeons VALUES(?,?,?)", (G, U, run.to_json()))
        return run

    def test_a_custom_monster_spawns_with_its_stats(self):
        content.add(self.store, G, "monsters", dict(name="드래곤", emoji="🐉", hp=321, damage=77, dodge=0))
        for name in ("거대 박쥐", "슬라임", "마녀", "거대 쥐"):
            content.delete(self.store, G, ("monsters",), name)
        bal = self.store.balance(G)
        run = dungeon.new_run(bal=bal)
        run.room, run.phase = 1, "cleared"                              # 다음은 2번째 방(빈방 또는 잡몹)
        dungeon.advance(run, Scripted(randoms=[0.99]), bal)               # 0.99 → 잡몹
        self.assertEqual((run.enemy["name"], run.enemy["hp"], run.enemy["damage"], run.enemy["dodge"]), ("드래곤", 321, 77, 0))

    def test_a_custom_weapon_changes_damage_and_a_custom_shield_changes_guard(self):
        content.add(self.store, G, "gear", dict(name="용검", emoji="🐲", slot="weapon", tier="전설", weight=1, attack=500, crit=0))
        content.add(self.store, G, "gear", dict(name="철벽", emoji="🧱", slot="shield", tier="전설", weight=1, guard=100, reflect=200))
        bal = self.store.balance(G)
        key = next(g.key for g in bal.gear if g.name == "용검")
        shield = next(g.key for g in bal.gear if g.name == "철벽")
        run = dungeon.new_run(weapon=key, shield=shield, bal=bal)
        self.assertEqual(dungeon.player_damage(run, bal), bal.base_damage + 500)
        self.assertEqual((dungeon.guard_percent(run, bal), dungeon.reflect_percent(run, bal)), (100, 200))

    def test_a_custom_consumable_heals_and_the_default_ones_can_be_removed(self):
        content.add(self.store, G, "consumables", dict(name="엘릭서", emoji="💊", tier="희귀", weight=1, heal=333))
        bal = self.store.balance(G)
        key = next(c.key for c in bal.consumables if c.name == "엘릭서")
        run = dungeon.new_run(bal=bal)
        run.hp, run.items = 100, {key: 1}
        dungeon.use_consumable(run, key, bal)
        self.assertEqual((run.hp, run.items), (433, {}))

    def test_a_chest_only_gives_what_exists(self):
        for g in balance.GEAR:
            if g.name != "단검":
                content.delete(self.store, G, ("gear",), g.name)
        for c in balance.CONSUMABLES:
            if c.name != "사탕":
                content.delete(self.store, G, ("consumables",), c.name)
        bal = self.store.balance(G)
        run = dungeon.new_run(bal=bal)
        run.phase = "chest"
        gear, item, _ = dungeon.open_chest(run, Scripted(randoms=[0.9, 0.9]), bal)
        self.assertEqual((gear.name, item.name), ("단검", "사탕"))

    async def test_a_gear_deleted_while_pending_does_not_trap_the_player(self):
        bal = self.store.balance(G)
        run = self.start(pending_gear="longsword", phase="chest_done", room=1)
        content.delete(self.store, G, ("gear",), "롱소드")
        self.assertFalse(run.can_advance)
        for action in ("equip", "discard"):
            self.start(pending_gear="longsword", phase="chest_done", room=1)
            data = json.loads(self.store.dungeon_run(G, U))
            out = await service.act(self.store, self.locks, Scripted(), G, U, data["rev"], action)
            self.assertTrue(out.ok, action)
            self.assertIsNone(out.run.pending_gear)
            self.assertTrue(out.run.can_advance)
            self.assertIn("사라졌", "\n".join(out.run.events))

    async def test_gear_and_items_deleted_mid_run_are_ignored_not_fatal(self):
        content.add(self.store, G, "gear", dict(name="임시 검", emoji="🗡️", slot="weapon", tier="일반", weight=1, attack=50))
        content.add(self.store, G, "consumables", dict(name="임시 약", emoji="💊", tier="일반", weight=1, heal=50))
        bal = self.store.balance(G)
        wkey = next(g.key for g in bal.gear if g.name == "임시 검")
        ikey = next(c.key for c in bal.consumables if c.name == "임시 약")
        run = self.start(weapon=wkey, items={ikey: 2}, phase="cleared", room=1)
        content.delete(self.store, G, ("gear",), "임시 검")
        content.delete(self.store, G, ("consumables",), "임시 약")
        bal = self.store.balance(G)
        self.assertEqual(dungeon.player_damage(run, bal), bal.base_damage)               # 사라진 무기는 맨손처럼
        with self.assertRaises(dungeon.DungeonError):
            dungeon.use_consumable(run, ikey, bal)
        from cogs.dungeon import DungeonView, run_embed
        embed = run_embed(run, None, bal)
        self.assertNotIn("소지품", [f.name for f in embed.fields])                       # 빈 칸을 만들지 않는다(디스코드가 거절한다)
        self.assertTrue(all(f.value for f in embed.fields))
        view = DungeonView(self.w.bot, U, run, bal)
        self.assertFalse(any(type(c).__name__ == "ItemSelect" for c in view.children))   # 고를 수 있는 게 없으면 선택창도 없다
        out = await service.act(self.store, self.locks, Scripted(randoms=[0.5]), G, U, run.rev, "advance")
        self.assertTrue(out.ok)

    def test_simulation_runs_on_custom_content(self):
        base = balance_sim.simulate(self.store.balance(G), 300, seed=3)["attack"]["clear"]
        content.add(self.store, G, "monsters", dict(name="허수아비", emoji="🎃", hp=1, damage=0, dodge=0))
        for name in ("거대 박쥐", "슬라임", "마녀", "거대 쥐"):
            content.delete(self.store, G, ("monsters",), name)
        easy = balance_sim.simulate(self.store.balance(G), 300, seed=3)["attack"]["clear"]
        self.assertGreater(easy, base)                                         # 잡몹이 허수아비뿐이라 쉬워진다


class ContentCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def run_cmd(self, command, interaction, *args, **kwargs):
        await command.callback(self.cog, interaction, *args, **kwargs)
        return interaction

    def text(self, interaction):
        embed, _ = sent_embed(interaction)
        return (embed.description or "") + "\n".join(f.value for f in embed.fields)

    async def test_everything_is_admin_only(self):
        calls = [(ContentAdmin.monster_add, ("드래곤", "🐉", 100, 10)), (ContentAdmin.monster_edit, ("슬라임",), dict(hp=5)),
                 (ContentAdmin.monster_delete, ("슬라임",)),
                 (ContentAdmin.gear_add, ("활", "🏹", Choice("weapon"), Choice("일반"), 5)), (ContentAdmin.gear_edit, ("단검",), dict(attack=1)),
                 (ContentAdmin.gear_delete, ("단검",)),
                 (ContentAdmin.potion_add, ("약", "💊", Choice("일반"), 5), dict(heal=10)), (ContentAdmin.potion_edit, ("사탕",), dict(heal=5)),
                 (ContentAdmin.potion_delete, ("사탕",)), (ContentAdmin.dungeon_list, ()), (ContentAdmin.content_reset, ())]
        for command, args, *rest in calls:
            interaction = self.ask(admin=False)
            await self.run_cmd(command, interaction, *args, **(rest[0] if rest else {}))
            self.assertIn("관리자만", self.text(interaction), command.name)
        self.assertEqual(self.store.settings(G)["dungeon_content"], {})

    async def test_add_edit_delete_a_monster_through_the_commands(self):
        i = await self.run_cmd(ContentAdmin.monster_add, self.ask(), "드래곤", "🐉", 500, 80, 5, False)
        self.assertIn("🐉 **드래곤** — HP 500 · 공격 80 · 회피 5%", self.text(i))
        i = await self.run_cmd(ContentAdmin.monster_edit, self.ask(), "드래곤", hp=900)
        self.assertIn("HP 900", self.text(i))
        i = await self.run_cmd(ContentAdmin.monster_add, self.ask(), "마왕", "😈", 1500, 200, 77, True)      # 보스는 회피 입력이 무시된다
        self.assertNotIn("회피", self.text(i).split("\n")[1])
        self.assertEqual(next(b for b in self.store.balance(G).bosses if b.name == "마왕").dodge, 0)
        i = await self.run_cmd(ContentAdmin.monster_delete, self.ask(), "드래곤")
        self.assertIn("지웠어요", self.text(i))
        i = await self.run_cmd(ContentAdmin.monster_delete, self.ask(), "드래곤")
        self.assertIn("그런 항목이 없어요", self.text(i))

    async def test_gear_edit_minus_one_clears_optional_fields(self):
        i = await self.run_cmd(ContentAdmin.gear_edit, self.ask(), "석궁", dodge=-1)
        self.assertNotIn("회피 확률", self.text(i))
        self.assertIsNone(next(g for g in self.store.balance(G).gear if g.name == "석궁").dodge)
        await self.run_cmd(ContentAdmin.gear_edit, self.ask(), "방패", reflect=-1)
        shield = next(g for g in self.store.balance(G).gear if g.name == "방패")
        self.assertIsNone(shield.reflect)
        self.assertEqual(dungeon.reflect_percent(dungeon.new_run(shield=shield.key, bal=self.store.balance(G)), self.store.balance(G)),
                         self.store.balance(G).reflect_base)                           # 해제하면 기본 반사로 돌아간다

    async def test_gear_commands_validate(self):
        i = await self.run_cmd(ContentAdmin.gear_add, self.ask(), "맨방패", "🛡️", Choice("shield"), Choice("일반"), 3)
        self.assertIn("가드", self.text(i))
        i = await self.run_cmd(ContentAdmin.gear_add, self.ask(), "철방패", "🛡️", Choice("shield"), Choice("희귀"), 3, guard=95, reflect=60)
        self.assertIn("가드 피해 감소 95%", self.text(i))
        i = await self.run_cmd(ContentAdmin.gear_add, self.ask(), "이상한 칼", "ㅁ", Choice("weapon"), Choice("일반"), 3, attack=5)
        self.assertIn("이모지", self.text(i))

    async def test_changing_a_consumables_effect_removes_the_old_one(self):
        await self.run_cmd(ContentAdmin.potion_edit, self.ask(), "영혼 에센스", heal=300)
        soul = next(c for c in self.store.balance(G).consumables if c.name == "영혼 에센스")
        self.assertEqual((soul.heal, soul.regen, soul.regen_turns, soul.full), (300, 0, 0, False))
        await self.run_cmd(ContentAdmin.potion_edit, self.ask(), "영혼 에센스", regen=40, turns=5)
        soul = next(c for c in self.store.balance(G).consumables if c.name == "영혼 에센스")
        self.assertEqual((soul.heal, soul.regen, soul.regen_turns, soul.full), (0, 40, 5, False))
        await self.run_cmd(ContentAdmin.potion_edit, self.ask(), "영혼 에센스", full=True)
        soul = next(c for c in self.store.balance(G).consumables if c.name == "영혼 에센스")
        self.assertEqual((soul.heal, soul.regen, soul.full), (0, 0, True))
        await self.run_cmd(ContentAdmin.potion_edit, self.ask(), "황금 호박죽", heal=100)               # 전체 회복 → 고정 회복
        self.assertEqual(next(c for c in self.store.balance(G).consumables if c.name == "황금 호박죽").full, False)

    async def test_potion_add_requires_one_effect(self):
        i = await self.run_cmd(ContentAdmin.potion_add, self.ask(), "빈 약병", "🧴", Choice("일반"), 5)
        self.assertIn("회복량", self.text(i))
        i = await self.run_cmd(ContentAdmin.potion_add, self.ask(), "서서히", "🧴", Choice("일반"), 5, regen=10)
        self.assertIn("지속 턴", self.text(i))
        i = await self.run_cmd(ContentAdmin.potion_add, self.ask(), "서서히", "🧴", Choice("일반"), 5, regen=10, turns=4)
        self.assertIn("4턴", self.text(i))

    async def test_list_shows_sections_and_marks_edited_ones(self):
        await self.run_cmd(ContentAdmin.monster_add, self.ask(), "드래곤", "🐉", 500, 80)
        i = await self.run_cmd(ContentAdmin.dungeon_list, self.ask())
        embed, _ = sent_embed(i)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(set(fields), {"몬스터 5개 ✏️", "보스 3개", "장비 8개", "소모품 6개"})
        self.assertIn("🐉 **드래곤**", fields["몬스터 5개 ✏️"])
        for value in fields.values():
            self.assertLessEqual(len(value), 1024)
        i = await self.run_cmd(ContentAdmin.dungeon_list, self.ask(), Choice("gear"))
        self.assertEqual([f.name for f in sent_embed(i)[0].fields], ["장비 8개"])

    async def test_a_full_list_stays_within_discord_limits(self):
        for n in range(balance.SECTION_LIMITS["gear"] - 8):
            content.add(self.store, G, "gear", dict(name="아주아주 긴 이름의 장비" + str(n), emoji="🗡️", slot="weapon", tier="전설", weight=1, attack=99999, crit=100, dodge=95))
        i = await self.run_cmd(ContentAdmin.dungeon_list, self.ask(), Choice("gear"))
        self.assertLessEqual(len(sent_embed(i)[0].fields[0].value), 1000)

    async def test_reset_asks_first_and_only_the_admin_can_confirm(self):
        i = await self.run_cmd(ContentAdmin.content_reset, self.ask())
        self.assertIn("직접 편집한 게 없어요", self.text(i))
        content.add(self.store, G, "monsters", dict(name="드래곤", emoji="🐉", hp=5, damage=1))
        i = await self.run_cmd(ContentAdmin.content_reset, self.ask())
        view = i.response.send_message.await_args.kwargs["view"]
        self.assertIsInstance(view, Confirm)
        self.assertEqual(len(self.store.balance(G).monsters), 5)                       # 확인 전에는 그대로
        self.assertFalse(await view.interaction_check(self.ask(uid=99)))
        await view.cancel(self.ask())
        self.assertEqual(len(self.store.balance(G).monsters), 5)
        click = self.ask()
        await view.confirm(click)
        self.assertEqual(len(self.store.balance(G).monsters), 4)
        self.assertIn("기본 목록", click.response.edit_message.await_args.kwargs["embed"].description)

    def test_name_autocomplete(self):
        content.add(self.store, G, "monsters", dict(name="아주 센 슬라임", emoji="🟢", hp=5, damage=1))
        found = names_for(self.store, G, ("monsters", "bosses"), "슬라임")
        self.assertEqual([c.value for c in found], ["슬라임", "아주 센 슬라임", "거대 슬라임"])
        self.assertEqual(found[0].name, "🟢 슬라임")
        self.assertEqual(len(names_for(self.store, G, ("gear",), "")), 8)


if __name__ == "__main__":
    unittest.main()
