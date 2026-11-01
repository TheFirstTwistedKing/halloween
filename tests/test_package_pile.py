"""패키지 더미: 패키지가 나타날 때 35% 확률로 변이해서 나오고, 뜯으면 결과가 3개 나온다."""
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock

import discord

import packages
import theme
from cogs.admin import Admin, settings_embed
from packages import PackageView, finalize_due, open_package, spawn, summary_embed
from store import DEFAULT_SETTINGS, Store, roll_package_pile, split_results
from tests.helpers import Scripted, World, pin_first_table, sent_embed

G, U = 100, 7
NOW = 1_000_000.0


class RuleTests(unittest.TestCase):
    def test_the_defaults_are_35_percent_and_three_results(self):
        self.assertEqual((DEFAULT_SETTINGS["package_pile_chance"], DEFAULT_SETTINGS["package_pile_size"]), (35.0, 3))

    def test_the_35_percent_edge(self):
        cfg = {"package_pile_chance": 35.0}
        self.assertTrue(roll_package_pile(cfg, Scripted([0.0])))
        self.assertTrue(roll_package_pile(cfg, Scripted([0.3499])))
        self.assertFalse(roll_package_pile(cfg, Scripted([0.35])))                    # 35% 미만만 변이한다.
        self.assertFalse(roll_package_pile(cfg, Scripted([0.99])))

    def test_zero_never_and_one_hundred_always(self):
        self.assertFalse(roll_package_pile({"package_pile_chance": 0}, Scripted([])))     # 0%면 주사위도 안 굴린다.
        self.assertTrue(roll_package_pile({"package_pile_chance": 100}, Scripted([0.9999])))

    def test_the_sample_rate_is_about_35_percent(self):
        import random
        rng, n = random.Random(7), 200_000
        hits = sum(roll_package_pile({"package_pile_chance": 35.0}, rng) for _ in range(n))
        self.assertAlmostEqual(hits / n, 0.35, delta=0.01)

    def test_settings_are_validated(self):
        store = Store(":memory:")
        for good in (dict(package_pile_chance=0), dict(package_pile_chance=100), dict(package_pile_chance=12.5), dict(package_pile_size=1), dict(package_pile_size=10)):
            store.update_settings(G, **good)
        for bad in (dict(package_pile_chance=-1), dict(package_pile_chance=100.1), dict(package_pile_size=0), dict(package_pile_size=11),
                    dict(package_pile_size=2.5), dict(package_pile_size=True)):
            with self.assertRaises(ValueError, msg=str(bad)):
                store.update_settings(G, **bad)
        store.close()

    def test_split_results(self):
        self.assertEqual(split_results("none"), ["none"])
        self.assertEqual(split_results("key,none,candy"), ["key", "none", "candy"])
        self.assertEqual(split_results(""), [])


class StoreTests(unittest.TestCase):
    def test_a_pile_is_remembered_and_normal_packages_are_not_piles(self):
        store = Store(":memory:")
        store.create_package(G, 400, 1, NOW + 60, "all", 3)
        store.create_package(G, 401, 2, NOW + 60, "all")
        self.assertEqual((store.package(1)["pile"], store.package(2)["pile"]), (3, 0))
        store.close()

    def test_several_results_are_stored_and_read_back_for_one_opener(self):
        store = Store(":memory:")
        store.create_package(G, 400, 1, NOW + 60, "all", 3)
        self.assertEqual(store.open_package(1, U, "key,none,key", NOW), "ok")
        self.assertEqual(split_results(store.package_openers(1)[0]["result"]), ["key", "none", "key"])
        store.close()

    def test_the_pile_survives_a_restart_and_an_old_database_gets_the_column(self):
        from tests.test_seed import _old_database
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db.sqlite3"
            store = Store(path)
            store.create_package(G, 400, 1, NOW + 600, "all", 3)
            store.close()
            self.assertEqual(Store(path).package(1)["pile"], 3)                      # 재시작해도 더미다.
            old = Path(folder) / "old.sqlite3"
            _old_database(old)
            with __import__("sqlite3").connect(str(old)) as db:                        # 더미가 생기기 전의 packages 표
                db.executescript("CREATE TABLE packages(message INTEGER PRIMARY KEY, guild INTEGER NOT NULL, channel INTEGER NOT NULL,"
                                 " expires REAL NOT NULL, mode TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0);"
                                 "INSERT INTO packages VALUES(5, 100, 400, 9999999999, 'all', 0);")
            migrated = Store(old)
            self.assertEqual(migrated.package(5)["pile"], 0)                             # 이미 나와 있던 패키지는 일반으로 이어진다.
            migrated.create_package(100, 400, 6, NOW + 60, "all", 3)
            self.assertEqual(migrated.package(6)["pile"], 3)
            migrated.close()


class SpawnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.channel = self.w.channel(400)
        self.store = self.w.store

    async def asyncTearDown(self):
        await self.w.close()

    async def spawn(self, roll):
        self.w.bot.rng.randoms.append(roll)
        message = NS(guild=self.w.guild, channel=self.channel)
        await spawn(self.w.bot, message, self.store.settings(G))
        return self.channel.sent[-1]

    async def test_35_percent_of_packages_appear_as_a_pile_with_its_own_look(self):
        sent = await self.spawn(0.3499)
        self.assertEqual(sent["embed"].title, "📦📦📦 패키지 더미가 나타났어요!")
        self.assertIn("3개", sent["embed"].description)
        self.assertEqual(sent["embed"].color.value, theme.GOLD)
        self.assertIsInstance(sent["view"], PackageView)
        self.assertEqual(self.store.package(9001)["pile"], 3)

    async def test_the_rest_appear_as_ordinary_packages(self):
        sent = await self.spawn(0.35)
        self.assertEqual(sent["embed"].title, "📦 패키지가 나타났어요!")
        self.assertEqual(self.store.package(9001)["pile"], 0)

    async def test_the_settings_change_the_chance_and_the_size(self):
        self.store.update_settings(G, package_pile_chance=100, package_pile_size=5)
        sent = await self.spawn(0.99)
        self.assertIn("5개", sent["embed"].description)
        self.assertEqual(self.store.package(9001)["pile"], 5)
        self.store.update_settings(G, package_pile_chance=0)
        sent = await self.spawn(0.0)                                                # 확률 0이면 주사위가 0이어도 일반이다.
        self.assertEqual(sent["embed"].title, "📦 패키지가 나타났어요!")


class OpenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.w.channel(400)
        self.w.bot.get_channel = lambda cid: self.w.channels.get(cid)
        self.store = self.w.store
        pin_first_table(self.store)                    # 꽝 60 · 열쇠 20 · 재화(🍬50) 10 · 쓰레기 10 (누적 경계 0.6 / 0.8 / 0.9)
        self.ids = self.store.ensure_system_items(G)
        self.clock = mock.patch.object(packages.time, "time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def asyncTearDown(self):
        self.clock.stop()
        await self.w.close()

    def package(self, message=9001, pile=3, mode="all"):
        self.store.create_package(G, 400, message, NOW + 600, mode, pile)

    async def press(self, rolls, message=9001, uid=U):
        self.w.bot.rng.randoms += rolls
        i = self.w.interaction(uid=uid, message_id=message)
        await open_package(i)
        return i, i.response.send_message.await_args.kwargs

    async def test_a_pile_gives_three_results_and_every_one_is_paid(self):
        self.package()
        i, kwargs = await self.press([0.65, 0.85, 0.95])                    # 열쇠, 재화 50, 쓰레기
        self.assertTrue(kwargs["ephemeral"])
        embed = kwargs["embed"]
        self.assertEqual(embed.title, "📦📦📦 패키지 더미! (3개)")
        for needle in ("`1` 🗝️ 열쇠", "`2` 🍬 할로윈 재화 50", "`3` 🗑️ 쓰레기", "재화 합계 **+50**"):
            self.assertIn(needle, embed.description)
        self.assertEqual((self.store.qty(G, U, self.ids["key"]), self.store.qty(G, U, self.ids["trash"]), self.store.wallet(G, U)), (1, 1, 50))
        self.assertEqual(self.w.bot.rng.randoms, [])                         # 정확히 3번만 굴렸다.

    async def test_the_same_result_can_come_up_more_than_once(self):
        self.package()
        await self.press([0.65, 0.65, 0.65])
        self.assertEqual(self.store.qty(G, U, self.ids["key"]), 3)

    async def test_empty_results_are_shown_as_empty(self):
        self.package()
        _, kwargs = await self.press([0.0, 0.0, 0.65])
        text = kwargs["embed"].description
        self.assertEqual(text.count("꽝"), 2)
        self.assertIn("텅 비어 있었어요", text)
        self.assertEqual(self.store.qty(G, U, self.ids["key"]), 1)

    async def test_candy_beyond_the_cap_is_cut_and_says_so(self):
        self.store.add_candy(G, U, self.store.currency(G).max - 70)
        self.package()
        _, kwargs = await self.press([0.85, 0.85, 0.85])                    # 재화 50 × 3, 70까지만 들어간다(50 + 20 + 0)
        self.assertEqual(self.store.wallet(G, U), self.store.currency(G).max)
        self.assertIn("20개만 받았어요", kwargs["embed"].description)
        self.assertIn("0개만 받았어요", kwargs["embed"].description)
        self.assertIn("재화 합계 **+70**", kwargs["embed"].description)

    async def test_an_ordinary_package_still_gives_one_result_in_the_old_format(self):
        self.package(pile=0)
        _, kwargs = await self.press([0.65])
        self.assertEqual(kwargs["embed"].title, "🗝️ 열쇠!")
        self.assertEqual(self.w.bot.rng.randoms, [])

    async def test_a_package_that_was_a_pile_before_a_restart_is_still_opened_as_a_pile(self):
        self.package()
        self.w.bot.rng.randoms.clear()
        restarted = World(Scripted([0.65, 0.65, 0.65]))
        restarted.store.db.close()
        restarted.store, restarted.bot.store = self.store, self.store
        i = restarted.interaction(uid=U, message_id=9001)
        await PackageView().on_open(i)
        self.assertEqual(i.response.send_message.await_args.kwargs["embed"].title, "📦📦📦 패키지 더미! (3개)")
        self.assertEqual(self.store.qty(G, U, self.ids["key"]), 3)

    async def test_each_person_gets_their_own_three_and_a_second_press_is_refused(self):
        self.package()
        await self.press([0.65, 0.65, 0.65])
        self.store.update_settings(G, package_cooldown=0)
        _, kwargs = await self.press([0.0, 0.0, 0.0])
        self.assertIn("이미 이 패키지를 뜯었어요", kwargs["embed"].description)
        _, kwargs = await self.press([0.85, 0.85, 0.85], uid=8)               # 다른 사람은 자기 몫 3개
        self.assertIn("재화 합계 **+150**", kwargs["embed"].description)
        self.assertEqual((self.store.qty(G, U, self.ids["key"]), self.store.wallet(G, 8)), (3, 150))

    async def test_the_cooldown_applies_to_a_pile_like_any_other_package(self):
        self.package(9001)
        self.package(9002)
        await self.press([0.65, 0.65, 0.65], 9001)
        self.w.bot.rng.randoms.clear()
        _, kwargs = await self.press([], 9002)
        self.assertIn("방금 패키지를 뜯었어요", kwargs["embed"].description)
        self.assertEqual(self.store.package_openers(9002), [])

    async def test_a_first_come_pile_goes_entirely_to_the_first_person(self):
        self.package(mode="first")
        await self.press([0.65, 0.65, 0.65])
        self.store.update_settings(G, package_cooldown=0)
        _, kwargs = await self.press([0.65, 0.65, 0.65], uid=8)
        self.assertIn("이미 다른 분이 뜯었어요", kwargs["embed"].description)
        self.assertEqual((self.store.qty(G, U, self.ids["key"]), self.store.qty(G, 8, self.ids["key"])), (3, 0))


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.channel = self.w.channel(400)
        self.w.bot.get_channel = lambda cid: self.w.channels.get(cid)
        self.store = self.w.store
        pin_first_table(self.store)

    async def asyncTearDown(self):
        await self.w.close()

    def summary(self, row_pile, mode="all"):
        self.store.create_package(G, 400, 9001, NOW + 5, mode, row_pile)
        return self.store.package(9001)

    async def test_the_summary_counts_every_result_of_every_person(self):
        row = self.summary(3)
        self.store.open_package(9001, 1, "key,none,key", NOW, 0)
        self.store.open_package(9001, 2, "none,none,trash", NOW, 0)
        embed = summary_embed(row, self.store.package_openers(9001), self.store)
        self.assertEqual(embed.title, "📦 패키지 더미가 사라졌어요")
        self.assertIn("2명이 뜯었어요", embed.description)
        self.assertIn("🗝️ 열쇠 ×2", embed.description)
        self.assertIn("👻 꽝 ×3", embed.description)
        self.assertIn("🗑️ 쓰레기 ×1", embed.description)
        self.assertEqual(embed.color.value, theme.GOLD)

    async def test_the_first_come_summary_lists_all_three(self):
        row = self.summary(3, "first")
        self.store.open_package(9001, 1, "key,none,trash", NOW, 0)
        embed = summary_embed(row, self.store.package_openers(9001), self.store)
        self.assertEqual(embed.title, "📦 패키지 더미가 열렸어요!")
        for needle in ("<@1> 님이 뜯었어요", "**🗝️ 열쇠**", "**👻 꽝**", "**🗑️ 쓰레기**"):
            self.assertIn(needle, embed.description)

    async def test_an_ordinary_summary_is_unchanged(self):
        row = self.summary(0)
        self.store.open_package(9001, 1, "key", NOW, 0)
        embed = summary_embed(row, self.store.package_openers(9001), self.store)
        self.assertEqual((embed.title, embed.color.value), ("📦 패키지가 사라졌어요", theme.PURPLE))
        self.assertIn("🗝️ 열쇠 ×1", embed.description)

    async def test_nobody_opened_a_pile(self):
        row = self.summary(3)
        self.assertEqual(summary_embed(row, [], self.store).title, "📦 패키지 더미가 사라졌어요")

    async def test_the_expiry_cleanup_shows_the_pile_summary(self):
        self.summary(3)
        self.store.open_package(9001, 1, "key,key,key", NOW, 0)
        self.assertEqual(await finalize_due(self.w.bot, NOW + 10), 1)
        shown = self.channel.partial[9001].edit.await_args.kwargs["embed"]
        self.assertIn("🗝️ 열쇠 ×3", shown.description)


class SettingsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Admin(self.w.bot)

    async def asyncTearDown(self):
        await self.w.close()

    def shown(self, i):
        return "\n".join(f.value for f in sent_embed(i)[0].fields)

    async def test_the_command_shows_and_changes_the_pile_settings(self):
        i = self.w.interaction(admin=True)
        await Admin.settings.callback(self.cog, i)
        self.assertIn("패키지 더미: **35%** 확률로 변이해서 나오고, 뜯으면 **3개**가 나와요", self.shown(i))
        i = self.w.interaction(admin=True)
        await Admin.settings.callback(self.cog, i, package_pile_chance=50.0, package_pile_size=4)
        self.assertEqual((self.store.settings(G)["package_pile_chance"], self.store.settings(G)["package_pile_size"]), (50.0, 4))
        self.assertIn("**50%**", self.shown(i))
        self.assertIn("**4개**", self.shown(i))
        i = self.w.interaction(admin=True)
        await Admin.settings.callback(self.cog, i, package_pile_chance=0.0)
        self.assertIn("패키지 더미: 없음", self.shown(i))

    def test_the_embed_helper(self):
        text = "\n".join(f.value for f in settings_embed({**DEFAULT_SETTINGS}).fields)
        self.assertIn("**35%**", text)

    async def test_the_help_mentions_piles_only_while_they_exist(self):
        from cogs.economy import Economy
        i = self.w.interaction()
        await Economy.help.callback(Economy(self.w.bot), i)
        self.assertIn("패키지 더미", "".join(f.value for f in sent_embed(i)[0].fields))
        self.store.update_settings(G, package_pile_chance=0)
        i = self.w.interaction()
        await Economy.help.callback(Economy(self.w.bot), i)
        self.assertNotIn("패키지 더미", "".join(f.value for f in sent_embed(i)[0].fields))


if __name__ == "__main__":
    unittest.main()
