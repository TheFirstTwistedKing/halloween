"""패키지를 한 번 뜯으면 쿨타임(기본 30초) 동안 다른 패키지를 못 뜯는다."""
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import packages
from cogs.admin import Admin, settings_embed
from packages import open_package
from store import DEFAULT_SETTINGS, Store, validate_settings
from tests.helpers import World, pin_first_table, sent_embed

G = 100
NOW = 1_000_000.0


class StoreRuleTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        for message in (1, 2, 3):
            self.store.create_package(G, 400, message, NOW + 600, "all")

    def tearDown(self):
        self.store.close()

    def test_the_default_is_30_seconds_and_it_is_validated(self):
        self.assertEqual(DEFAULT_SETTINGS["package_cooldown"], 30)
        self.assertEqual(self.store.settings(G)["package_cooldown"], 30)
        for good in (0, 30, 3600):
            self.store.update_settings(G, package_cooldown=good)
        for bad in (-1, 3601, 1.5, True, "30"):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.store.update_settings(G, package_cooldown=bad)
        self.assertEqual(self.store.settings(G)["package_cooldown"], 3600)

    def test_a_second_package_inside_the_cooldown_is_refused_and_records_nothing(self):
        self.assertEqual(self.store.open_package(1, 7, "none", NOW, 30), "ok")
        self.assertEqual(self.store.open_package(2, 7, "none", NOW + 10, 30), "wait")
        self.assertEqual(self.store.package_openers(2), [])                       # 아무 기록도 남지 않는다.
        self.assertAlmostEqual(self.store.package_wait(G, 7, NOW + 10, 30), 20)

    def test_it_opens_again_once_the_time_is_up_and_the_clock_restarts_from_that_opening(self):
        self.store.open_package(1, 7, "none", NOW, 30)
        self.assertEqual(self.store.open_package(2, 7, "none", NOW + 29.9, 30), "wait")
        self.assertEqual(self.store.open_package(2, 7, "none", NOW + 30, 30), "ok")
        self.assertEqual(self.store.open_package(3, 7, "none", NOW + 59, 30), "wait")      # 두 번째 뜯은 시각부터 다시 센다.
        self.assertEqual(self.store.open_package(3, 7, "none", NOW + 60, 30), "ok")

    def test_zero_means_no_limit(self):
        for message in (1, 2, 3):
            self.assertEqual(self.store.open_package(message, 7, "none", NOW, 0), "ok")
        self.assertEqual(self.store.package_wait(G, 7, NOW, 0), 0)

    def test_it_is_per_person_and_per_server(self):
        self.store.open_package(1, 7, "none", NOW, 30)
        self.assertEqual(self.store.open_package(2, 8, "none", NOW + 1, 30), "ok")          # 다른 사람은 상관없다.
        self.store.create_package(101, 400, 50, NOW + 600, "all")
        self.assertEqual(self.store.open_package(50, 7, "none", NOW + 1, 30), "ok")          # 다른 서버도 따로 센다.

    def test_the_old_answers_win_over_the_wait(self):
        self.store.open_package(1, 7, "none", NOW, 30)
        self.assertEqual(self.store.open_package(1, 7, "none", NOW + 1, 30), "again")        # 같은 패키지를 또 누르면 '이미 뜯었어요'
        self.assertEqual(self.store.open_package(99, 7, "none", NOW + 1, 30), "gone")        # 없는 패키지는 없다고
        self.store.create_package(G, 400, 4, NOW + 600, "first")
        self.store.open_package(4, 8, "none", NOW, 30)
        self.assertEqual(self.store.open_package(4, 7, "none", NOW + 1, 30), "closed")        # 이미 뜯긴 선착순 패키지는 '늦었어요'

    def test_a_refused_person_does_not_use_up_a_first_come_package(self):
        self.store.open_package(1, 7, "none", NOW, 30)
        self.store.create_package(G, 400, 5, NOW + 600, "first")
        self.assertEqual(self.store.open_package(5, 7, "none", NOW + 5, 30), "wait")
        self.assertEqual(self.store.package(5)["closed"], 0)                                  # 패키지는 그대로 남아 있다.
        self.assertEqual(self.store.open_package(5, 8, "none", NOW + 6, 30), "ok")             # 다른 사람이 뜯을 수 있다.

    def test_the_cooldown_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "db.sqlite3"
            store = Store(path)
            store.create_package(G, 400, 1, NOW + 600, "all")
            store.create_package(G, 400, 2, NOW + 600, "all")
            store.open_package(1, 7, "none", NOW, 30)
            store.close()
            again = Store(path)
            self.assertEqual(again.open_package(2, 7, "none", NOW + 5, 30), "wait")
            again.close()

    def test_a_database_from_before_this_feature_gets_the_table(self):
        from tests.test_seed import _old_database
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            _old_database(path)
            store = Store(path)
            tables = {r["name"] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("package_cooldowns", tables)
            self.assertEqual(store.settings(100)["package_cooldown"], 30)                      # 저장된 설정에 없으면 기본값
            store.close()


class OpenFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.w.channel(400)
        pin_first_table(self.store)
        self.clock = mock.patch.object(packages.time, "time", return_value=NOW)
        self.time = self.clock.start()
        self.addCleanup(self.clock.stop)
        for message in (9001, 9002):
            self.store.create_package(G, 400, message, NOW + 600, "all")

    async def asyncTearDown(self):
        self.clock.stop()
        await self.w.close()

    async def press(self, message, roll=0.0, uid=7):
        self.w.bot.rng.randoms.append(roll)
        i = self.w.interaction(uid=uid, message_id=message)
        await open_package(i)
        return i, i.response.send_message.await_args.kwargs

    def advance(self, seconds):
        self.time.return_value = NOW + seconds

    async def test_the_second_package_inside_30_seconds_gives_nothing_and_says_how_long_to_wait(self):
        first, _ = await self.press(9001, 0.65)                                               # 열쇠
        self.assertEqual(self.store.qty(G, 7, self.store.ensure_system_items(G)["key"]), 1)
        self.advance(12)
        i, kwargs = await self.press(9002, 0.65)
        embed = kwargs["embed"]
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("방금 패키지를 뜯었어요", embed.description)
        self.assertIn("18초", embed.description)
        self.assertEqual(self.store.qty(G, 7, self.store.ensure_system_items(G)["key"]), 1)   # 열쇠가 더 늘지 않았다.
        self.assertEqual(self.store.package_openers(9002), [])
        self.assertEqual(self.w.bot.rng.randoms, [0.65])                                      # 주사위도 굴리지 않았다.

    async def test_after_30_seconds_it_works_again(self):
        await self.press(9001)
        self.advance(30)
        _, kwargs = await self.press(9002, 0.65)
        self.assertEqual(kwargs["embed"].title, "🗝️ 열쇠!")

    async def test_even_an_empty_package_starts_the_cooldown(self):
        _, kwargs = await self.press(9001, 0.0)
        self.assertEqual(kwargs["embed"].title, "👻 꽝!")
        self.advance(1)
        _, kwargs = await self.press(9002)
        self.assertIn("29초", kwargs["embed"].description)

    async def test_seconds_are_rounded_up(self):
        await self.press(9001)
        self.advance(10.2)
        _, kwargs = await self.press(9002)
        self.assertIn(f"{math.ceil(30 - 10.2)}초", kwargs["embed"].description)               # 20초
        self.advance(29.5)
        _, kwargs = await self.press(9002)
        self.assertIn("1초", kwargs["embed"].description)

    async def test_pressing_the_same_package_again_says_already_opened_not_wait(self):
        await self.press(9001)
        self.advance(3)
        _, kwargs = await self.press(9001)
        self.assertIn("이미 이 패키지를 뜯었어요", kwargs["embed"].description)

    async def test_other_people_are_not_affected(self):
        await self.press(9001)
        _, kwargs = await self.press(9001, 0.65, uid=8)
        self.assertEqual(kwargs["embed"].title, "🗝️ 열쇠!")

    async def test_the_setting_can_turn_it_off_or_change_it(self):
        self.store.update_settings(G, package_cooldown=0)
        await self.press(9001)
        _, kwargs = await self.press(9002, 0.65)
        self.assertEqual(kwargs["embed"].title, "🗝️ 열쇠!")
        self.store.update_settings(G, package_cooldown=100)
        self.store.create_package(G, 400, 9003, NOW + 600, "all")
        self.advance(40)
        _, kwargs = await self.press(9003)
        self.assertIn("60초", kwargs["embed"].description)                                     # 100초 중 40초가 지났다.

    async def test_a_waiting_person_does_not_use_up_a_first_come_package(self):
        await self.press(9001)
        self.store.create_package(G, 400, 9004, NOW + 600, "first")
        self.advance(5)
        await self.press(9004)
        self.w.bot.rng.randoms.clear()                                                         # 거절된 사람은 주사위를 안 굴려서 값이 남아 있다.
        self.assertEqual(self.store.package(9004)["closed"], 0)
        _, kwargs = await self.press(9004, 0.65, uid=8)
        self.assertEqual(kwargs["embed"].title, "🗝️ 열쇠!")


class SettingsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Admin(self.w.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def test_the_command_changes_and_shows_the_cooldown(self):
        i = self.w.interaction(admin=True)
        await Admin.settings.callback(self.cog, i)
        self.assertIn("패키지 쿨타임: 30초", "\n".join(f.value for f in sent_embed(i)[0].fields))
        i = self.w.interaction(admin=True)
        await Admin.settings.callback(self.cog, i, package_cooldown=45)
        self.assertEqual(self.store.settings(G)["package_cooldown"], 45)
        self.assertIn("패키지 쿨타임: 45초", "\n".join(f.value for f in sent_embed(i)[0].fields))
        i = self.w.interaction(admin=True)
        await Admin.settings.callback(self.cog, i, package_cooldown=0)
        self.assertIn("패키지 쿨타임: 없음", "\n".join(f.value for f in sent_embed(i)[0].fields))

    def test_the_embed_helper_shows_it_too(self):
        text = "\n".join(f.value for f in settings_embed({**DEFAULT_SETTINGS, "package_cooldown": 30}).fields)
        self.assertIn("패키지 쿨타임: 30초", text)


if __name__ == "__main__":
    unittest.main()
