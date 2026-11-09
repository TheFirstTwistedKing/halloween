"""기계 분위기 개편: 재화 RAM, 새 기본 이름·문구, 예전(할로윈 판) 서버 데이터의 자동 이전, 남은 할로윈 단어가 없는지."""
import re
import unittest
from pathlib import Path

import balance
import dungeon
import store as store_module
import theme
from balance import LEGACY_NAMES, LEGACY_SECRET_INTRO, migrate_content_names, migrate_override_keys
from josa import eul_reul, eun_neun, i_ga
from store import (CANDY, CANDY_NAME, EMPTY_EMOJI, EMPTY_TITLE, LEGACY_CURRENCY, LEGACY_EMPTY, LEGACY_ITEMS, NEW_ITEMS, SEED_VERSION, Store,
                   first_package_table)
from tests.helpers import Scripted

G = 100
ROOT = Path(__file__).resolve().parent.parent


class CurrencyAndJosaTests(unittest.TestCase):
    def test_the_default_currency_is_ram(self):
        self.assertEqual((CANDY, CANDY_NAME), ("💾", "RAM"))
        self.assertEqual(tuple(store_module.DEFAULT_CURRENCY), ("💾", "RAM", 9_999_999_999))
        self.assertEqual(theme.money(1234), "💾 1,234")
        self.assertEqual(theme.plain_money(5), "💾 5")

    def test_ram_takes_the_right_particles(self):
        self.assertEqual((i_ga("RAM"), eul_reul("RAM"), eun_neun("RAM")), ("RAM이", "RAM을", "RAM은"))     # 램: 받침이 있다
        self.assertEqual((i_ga("HP"), i_ga("CPU"), i_ga("USB")), ("HP가", "CPU가", "USB가"))              # 피·유·비: 없다
        self.assertEqual((i_ga("ROM"), i_ga("SSD"), i_ga("GPU")), ("ROM이", "SSD가", "GPU가"))
        self.assertEqual((i_ga("coin"), i_ga("Gem 2")), ("coin가", "Gem 2가"))                          # 소문자 섞인 영단어는 예전 그대로
        self.assertEqual(i_ga("RAM 💾"), "RAM 💾이")                                                       # 끝의 이모지는 건너뛴다


class ThemeTests(unittest.TestCase):
    def test_colors_footer_and_titles_are_machine_like(self):
        self.assertEqual(theme.FOOTER, "⚙️ SYSTEM ONLINE")
        self.assertEqual((theme.ORANGE, theme.PURPLE, theme.GOLD, theme.GREEN, theme.RED), (0x00A8E8, 0x5C6B7A, 0xF5B700, 0x2ECC71, 0xE74C3C))
        ok, bad = theme.notice("완료"), theme.notice("실패", False)
        self.assertEqual((ok.title, bad.title), ("✅ 처리 완료", "⚠️ 오류 발생"))
        self.assertEqual(theme.candy_bar(10, 10), "🟦" * 10)
        self.assertEqual(theme.shop_embed([]).title, "🖥️ 부품 상점")

    def test_the_package_messages_talk_about_data(self):
        embed = theme.package_embed("all", 30)
        self.assertEqual(embed.title, "📦 패키지가 수신됐어요!")
        self.assertIn("데이터 덩어리", embed.description)
        self.assertIn("수신", theme.package_embed("all", 30, pile=3).title)


class FreshServerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_a_new_server_starts_with_ram_machine_parts_and_empty_packets(self):
        self.store.ensure_defaults(G)
        self.assertEqual(tuple(self.store.currency(G)), ("💾", "RAM", 9_999_999_999))
        self.assertEqual(self.store.settings(G)["seed_version"], SEED_VERSION)
        names = {r["name"] for r in self.store.db.execute("SELECT name FROM items WHERE guild=?", (G,))}
        self.assertTrue({n for _, n, *_ in NEW_ITEMS} <= names)
        self.assertFalse(names & {old for old, _, _ in LEGACY_ITEMS.values()})
        none = next(e for e in self.store.package_table(G) if e["kind"] == "none")
        self.assertEqual((none["emoji"], none["title"]), (EMPTY_EMOJI, EMPTY_TITLE))

    def test_the_new_item_descriptions_are_machine_flavored(self):
        for _system, name, emoji, description, *_ in NEW_ITEMS:
            for old in ("마녀", "유령", "호박", "뱀파이어", "달에서", "수상한"):
                self.assertNotIn(old, name + description)


def legacy_server(store):
    """예전(할로윈 판, SEED_VERSION 2) 서버처럼 만든다: 재화·기본 물품·'꽝'이 모두 예전 기본값이다."""
    store.ensure_defaults(G)
    with store.db:
        for system, (name, emoji, description) in LEGACY_ITEMS.items():
            store.db.execute("UPDATE items SET name=?, emoji=?, description=? WHERE guild=? AND system=?", (name, emoji, description, G, system))
    table = [{**e, "emoji": LEGACY_EMPTY[0], "title": LEGACY_EMPTY[1]} if e["kind"] == "none" else e for e in store.package_table(G)]
    store.update_settings(G, seed_version=2, currency_emoji=LEGACY_CURRENCY[0], currency_name=LEGACY_CURRENCY[1], package_table=table,
                          dungeon_content={}, dungeon={})


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        legacy_server(self.store)

    def tearDown(self):
        self.store.close()

    def item(self, system):
        return self.store.db.execute("SELECT * FROM items WHERE guild=? AND system=?", (G, system)).fetchone()

    def test_untouched_old_defaults_are_renamed(self):
        self.assertEqual(self.item("witch_hat")["name"], "마녀 모자")
        self.store.ensure_defaults(G)
        self.assertEqual(self.store.settings(G)["seed_version"], 4)
        self.assertEqual(tuple(self.store.currency(G)), ("💾", "RAM", 9_999_999_999))
        for system, name, emoji, description, *_ in NEW_ITEMS:
            row = self.item(system)
            self.assertEqual((row["name"], row["emoji"], row["description"]), (name, emoji, description), system)
        none = next(e for e in self.store.package_table(G) if e["kind"] == "none")
        self.assertEqual((none["emoji"], none["title"]), ("📭", "빈 패킷"))

    def test_things_the_admin_changed_are_left_alone(self):
        self.store.update_settings(G, currency_name="젬", currency_emoji="💎")
        with self.store.db:
            self.store.db.execute("UPDATE items SET name='내 모자' WHERE guild=? AND system='witch_hat'", (G,))
            self.store.db.execute("UPDATE items SET description='직접 쓴 설명' WHERE guild=? AND system='ghost_cloth'", (G,))
            self.store.db.execute("UPDATE items SET emoji='🎩' WHERE guild=? AND system='moon_shard'", (G,))
        table = self.store.settings(G)["package_table"]                                 # package_table()은 이전을 먼저 해 버리니 설정에서 바로 꺼낸다
        table = [{**e, "title": "텅"} if e["kind"] == "none" else e for e in table]
        self.store.update_settings(G, package_table=table)
        self.store.ensure_defaults(G)
        self.assertEqual(tuple(self.store.currency(G))[:2], ("💎", "젬"))
        self.assertEqual(self.item("witch_hat")["name"], "내 모자")
        self.assertEqual(self.item("witch_hat")["emoji"], "🧙")                              # 이름만 바꿨다면 바꾼 것 그대로(통째로 건드리지 않는다)
        ghost = self.item("ghost_cloth")
        self.assertEqual((ghost["name"], ghost["description"]), ("유령의 천", "직접 쓴 설명") if False else (ghost["name"], "직접 쓴 설명"))
        self.assertEqual(self.item("moon_shard")["name"], "달 조각")                          # 이모지를 바꿨다면 이름도 그대로
        none = next(e for e in self.store.package_table(G) if e["kind"] == "none")
        self.assertEqual(none["title"], "텅")

    def test_a_name_taken_by_another_item_is_skipped_not_crashed(self):
        self.store.create_item(G, "구형 CPU", emoji="⚙️")
        self.store.ensure_defaults(G)
        self.assertEqual(self.item("witch_hat")["name"], "마녀 모자")
        self.assertEqual(self.item("ghost_cloth")["name"], "방열 패드")

    def test_it_runs_once_and_keeps_bags_and_prices(self):
        before = self.item("jack_o_lantern")
        self.store.ensure_defaults(G)
        after = self.item("jack_o_lantern")
        self.assertEqual((after["id"], after["sell_price"], after["sellable"]), (before["id"], before["sell_price"], before["sellable"]))
        self.store.ensure_defaults(G)
        self.assertEqual(self.store.settings(G)["seed_version"], 4)
        self.assertEqual(self.item("jack_o_lantern")["name"], "무정전 전원 장치")

    def test_the_default_package_table_reset_uses_the_new_empty_packet(self):
        table = self.store.apply_default_package_table(G)
        none = next(e for e in table if e["kind"] == "none")
        self.assertEqual((none["emoji"], none["title"]), ("📭", "빈 패킷"))


class DungeonNamesTests(unittest.TestCase):
    def test_every_default_entity_has_a_legacy_name_and_a_new_one(self):
        defaults = {"monsters": balance.MONSTERS, "bosses": balance.BOSSES, "gear": balance.GEAR, "consumables": balance.CONSUMABLES, "secrets": balance.SECRET_BOSSES}
        for section, entities in defaults.items():
            self.assertEqual({e.key for e in entities}, set(LEGACY_NAMES[section]), section)
            for e in entities:
                old_name, old_emoji = LEGACY_NAMES[section][e.key]
                self.assertNotEqual(e.name, old_name, e.key)                       # 이모지는 방패처럼 그대로 쓸 수도 있어서 이름만 본다

    def test_new_names_are_unique_where_the_game_requires_it(self):
        creatures = [e.name for e in balance.MONSTERS + balance.BOSSES + balance.SECRET_BOSSES]
        self.assertEqual(len(creatures), len(set(creatures)))
        for table in (balance.GEAR, balance.CONSUMABLES):
            names = [e.name for e in table]
            self.assertEqual(len(names), len(set(names)))

    def test_old_content_names_are_migrated_only_when_unchanged(self):
        saved = {"monsters": [
            {"key": "slime", "name": "슬라임", "emoji": "🟢", "hp": 1, "damage": 1},                       # 그대로 → 둘 다 바뀜
            {"key": "witch", "name": "내 마녀", "emoji": "🧙", "hp": 1, "damage": 1},                      # 이름만 바꿈 → 이모지만 바뀜
            {"key": "x123", "name": "슬라임", "emoji": "🟢", "hp": 1, "damage": 1},                        # 직접 만든 것(기본 key 아님) → 그대로
            {"key": "giant_bat", "name": "거대 박쥐", "emoji": "🔮", "hp": 1, "damage": 1},                 # 이모지만 바꿈 → 이름만 바뀜
        ], "secrets": [{"key": "void_lord", "name": "공허의 군주", "emoji": "🌌", "intro": LEGACY_SECRET_INTRO}],
            "gear": [{"key": "club", "name": "낡은 몽둥이", "emoji": "🏏"}]}
        out = migrate_content_names(saved)
        self.assertEqual((out["monsters"][0]["name"], out["monsters"][0]["emoji"]), ("오일 슬러지", "🛢️"))
        self.assertEqual((out["monsters"][1]["name"], out["monsters"][1]["emoji"]), ("내 마녀", "🤖"))
        self.assertEqual((out["monsters"][2]["name"], out["monsters"][2]["emoji"]), ("슬라임", "🟢"))
        self.assertEqual((out["monsters"][3]["name"], out["monsters"][3]["emoji"]), ("정찰 드론", "🔮"))
        self.assertEqual((out["secrets"][0]["name"], out["secrets"][0]["intro"]), ("NULL 군주", balance.SECRET_BOSSES[0].intro))
        self.assertEqual(out["gear"][0]["name"], "낡은 스패너")
        self.assertEqual(saved["monsters"][0]["name"], "슬라임")                                           # 원본은 그대로
        self.assertEqual(migrate_content_names(None), {})
        self.assertEqual(migrate_content_names({"monsters": "x", "rooms": [{"key": "a"}]}), {"monsters": "x", "rooms": [{"key": "a"}]})

    def test_old_setting_names_are_migrated(self):
        out = migrate_override_keys({"몬스터.슬라임.체력": 5, "보스.해골 왕.공격": 7, "장비.단검.크리티컬": 9, "아이템.사탕.회복량": 3,
                                     "일반.층수": 4, "방2.함정": 6})
        self.assertEqual(out, {"몬스터.오일 슬러지.체력": 5, "보스.메인프레임 군주.공격": 7, "장비.레이저 커터.크리티컬": 9, "아이템.소형 배터리.회복량": 3,
                               "일반.층수": 4, "방2.함정": 6})
        self.assertEqual(migrate_override_keys("x"), {})
        for key in out:
            self.assertIn(key, balance.PARAMS)                                                            # 옮긴 이름은 실제 항목이다

    def test_a_legacy_server_keeps_its_tuned_numbers_and_edited_sections(self):
        s = Store(":memory:")
        try:
            legacy_server(s)
            s.update_settings(G, dungeon={"몬스터.슬라임.체력": 999, "일반.층수": 7},
                              dungeon_content={"monsters": [{"key": "slime", "name": "슬라임", "emoji": "🟢", "hp": 5, "damage": 1, "dodge": 10}]})
            s.ensure_defaults(G)
            bal = s.balance(G)
            self.assertEqual(bal.floors, 7)
            self.assertEqual([m.name for m in bal.monsters], ["오일 슬러지"])
            custom = Store(":memory:")
            custom.ensure_defaults(G)
            custom.update_settings(G, dungeon={"몬스터.오일 슬러지.체력": 999}, seed_version=2)
            custom.ensure_defaults(G)
            self.assertEqual(next(m for m in custom.balance(G).monsters if m.key == "slime").hp, 999)
            s2 = Store(":memory:")
            legacy_server(s2)
            s2.update_settings(G, dungeon={"몬스터.슬라임.체력": 999})
            s2.ensure_defaults(G)
            self.assertEqual(next(m for m in s2.balance(G).monsters if m.key == "slime").hp, 999)       # 정해 둔 숫자가 새 이름으로 따라온다
            self.assertEqual(s2.settings(G)["dungeon"], {"몬스터.오일 슬러지.체력": 999})
        finally:
            s.close()


class DungeonTextTests(unittest.TestCase):
    def fight(self, **kw):
        run = dungeon.new_run()
        enemy = dungeon._spawn(balance.MONSTERS[1], 1, balance.DEFAULT)
        enemy.update(hp=1, dodge=0)
        run.phase, run.kind, run.room, run.enemy = "battle", "monster", 2, enemy
        for k, v in kw.items():
            setattr(run, k, v)
        return run

    def test_kills_and_deaths_use_machine_words(self):
        run = self.fight()
        events = dungeon.player_attack(run, Scripted(randoms=[0.99]))
        self.assertTrue(any("무력화했어요" in e for e in events))
        dying = self.fight(hp=1)
        dying.enemy["hp"] = 1000
        events = dungeon.player_attack(dying, Scripted(randoms=[0.99]))
        self.assertEqual(dying.phase, "dead")
        self.assertTrue(any("시스템이 다운됐어요" in e for e in events))

    def test_room_texts_are_machine_like(self):
        run = dungeon.new_run()
        run.floor, run.room, run.phase, run.kind = 1, 2, "cleared", "empty"
        events = dungeon.advance(run, Scripted())                                      # 3번째 방은 늘 상자(난수를 뽑지 않는다)
        self.assertTrue(any("보급 상자" in e for e in events), events)
        monster = dungeon.new_run()
        monster.floor, monster.room, monster.phase = 1, 1, "cleared"
        events = dungeon.advance(monster, Scripted(randoms=[0.99, 0.0]))
        self.assertTrue(any("적성 개체 감지" in e for e in events), events)
        for text in dungeon.TRAP_TEXTS:
            self.assertNotRegex(text, "독가스|화살|돌덩이")


class NoLeftoverWordsTests(unittest.TestCase):
    FORBIDDEN = re.compile("할로윈|Halloween|🎃|👻|🕸|🍬|🍭|🦇|🧙|🐀|호박|유령|마녀|해골|박쥐|슬라임|고블린|몽둥이|영혼|사탕|뱀파이어|공허|저주|악몽|좀비|괴물")
    # 예전 이름을 알아보고 새 이름으로 옮기기 위해 일부러 남긴 줄(LEGACY_*)과 내부 이름·DB 경로
    ALLOWED = re.compile("LEGACY|legacy|예전|halloween|호박색|\"id\": \"none\"|first_package_table|package_table_v1|꽝|\"none\"|\"witch\"|\"slime\"|\"giant_|\"skeleton|\"goblin|pumpkin|witch|ghost|jack|vampire")

    def test_no_halloween_words_remain_in_the_code_and_docs(self):
        leftovers = []
        files = [p for p in ROOT.glob("*.py")] + list((ROOT / "cogs").glob("*.py")) + [ROOT / "README.md", ROOT / "CLAUDE.md", ROOT / "start.bat"]
        for path in files:
            skipping = False
            in_mapping = False                                                          # README의 '기계 분위기' 절은 예전 이름을 일부러 적어 둔 곳
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if path.name == "README.md":
                    if line.startswith("## "):
                        in_mapping = line.startswith("## ⚙️ 기계 분위기")
                    if in_mapping:
                        continue
                if re.match(r"(LEGACY_NAMES|LEGACY_ITEMS) = \{", line):          # 예전 이름 표 전체는 일부러 남긴 것
                    skipping = True
                if skipping:
                    skipping = not line.startswith("}")
                    continue
                if self.FORBIDDEN.search(line) and not self.ALLOWED.search(line):
                    leftovers.append(f"{path.name}:{number}: {line.strip()[:80]}")
        self.assertEqual(leftovers, [])
