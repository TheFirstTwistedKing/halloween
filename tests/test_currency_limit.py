"""재화(RAM) 최대 보유량 9,999,999,999: 기본값, 설정 한도, 큰 수 처리, 예전 서버(99,999)의 이전."""
import json
import unittest

import dungeon
import dungeon_service as service
from cogs.admin import Admin
from cogs.rules_admin import RulesAdmin
from services import Locks
from store import CANDY_MAX, LEGACY_CANDY_MAX, SEED_VERSION, Store, StoreError, validate_settings
from tests.helpers import World
from views import bag_for

G, U = 100, 7
BIG = 9_999_999_999


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.ensure_defaults(G)

    def tearDown(self):
        self.store.close()

    def test_the_default_and_the_settings_limit(self):
        self.assertEqual((CANDY_MAX, LEGACY_CANDY_MAX), (BIG, 99999))
        self.assertEqual(self.store.currency(G).max, BIG)
        cfg = self.store.settings(G)
        validate_settings({**cfg, "currency_max": BIG})
        for bad in (BIG + 1, 0, -1, 1.5, True):
            with self.assertRaises(ValueError, msg=bad):
                validate_settings({**cfg, "currency_max": bad})

    def test_a_wallet_holds_and_caps_at_the_new_limit(self):
        self.assertEqual(self.store.add_candy(G, U, BIG - 5), BIG - 5)
        self.assertEqual(self.store.wallet(G, U), BIG - 5)
        self.assertEqual(self.store.add_candy(G, U, 100), 5)                       # 상한까지만
        self.assertEqual((self.store.wallet(G, U), self.store.add_candy(G, U, 1)), (BIG, 0))
        self.assertEqual(self.store.add_candy(G, U, -BIG), -BIG)
        self.assertEqual(self.store.wallet(G, U), 0)

    def test_many_users_at_once_near_the_limit(self):
        self.store.add_candy(G, 1, BIG - 10)
        count, total, partial = self.store.add_candy_many(G, [1, 2], 50)
        self.assertEqual((count, total, partial), (2, 60, 1))
        self.assertEqual((self.store.wallet(G, 1), self.store.wallet(G, 2)), (BIG, 50))

    def test_lowering_the_limit_still_trims_and_raising_it_keeps_what_people_have(self):
        self.store.add_candy(G, U, 5_000_000_000)
        self.store.set_currency(G, max=1_000)
        self.assertEqual(self.store.wallet(G, U), 1_000)
        self.store.set_currency(G, max=BIG)
        self.assertEqual((self.store.currency(G).max, self.store.wallet(G, U)), (BIG, 1_000))
        with self.assertRaises(ValueError):
            self.store.set_currency(G, max=BIG + 1)

    def test_selling_in_the_dungeon_respects_the_new_limit(self):
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        self.store.enter_dungeon(G, U, '{"a": 1}')
        self.store.add_candy(G, U, BIG - 5)
        with self.assertRaisesRegex(StoreError, "최대"):
            self.store.sell_in_dungeon(G, U, '{"a": 2}', 10)
        self.assertEqual(self.store.sell_in_dungeon(G, U, '{"a": 2}', 5), BIG)

    def test_buying_in_the_dungeon_from_a_huge_wallet(self):
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        self.store.enter_dungeon(G, U, '{"a": 1}')
        self.store.add_candy(G, U, BIG)
        self.assertEqual(self.store.spend_in_dungeon(G, U, '{"a": 2}', 4_000_000_000), BIG - 4_000_000_000)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.ensure_defaults(G)

    def tearDown(self):
        self.store.close()

    def old_server(self, max_):
        self.store.update_settings(G, seed_version=3, currency_max=max_)
        self.store._settings.pop(G, None)

    def test_a_server_still_on_the_old_default_gets_the_new_limit(self):
        self.store.add_candy(G, U, 12_345)
        self.old_server(LEGACY_CANDY_MAX)
        self.store.ensure_defaults(G)
        self.assertEqual((self.store.settings(G)["seed_version"], self.store.currency(G).max), (SEED_VERSION, BIG))
        self.assertEqual(self.store.wallet(G, U), 12_345)                         # 가진 재화는 그대로

    def test_a_limit_the_admin_chose_is_left_alone(self):
        for chosen in (5_000, 99_998, 123_456_789):
            self.old_server(chosen)
            self.store.ensure_defaults(G)
            self.assertEqual(self.store.currency(G).max, chosen)
            self.assertEqual(self.store.settings(G)["seed_version"], SEED_VERSION)

    def test_it_runs_once(self):
        self.old_server(LEGACY_CANDY_MAX)
        self.store.ensure_defaults(G)
        self.store.set_currency(G, max=77_777)                                    # 새 한도를 관리자가 다시 줄였다
        self.store.ensure_defaults(G)
        self.assertEqual(self.store.currency(G).max, 77_777)


class CommandAndScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.store.ensure_defaults(G)

    async def asyncTearDown(self):
        await self.w.close()

    def limits(self, command):
        return {p.display_name: (p.min_value, p.max_value) for p in command.parameters}

    def test_the_commands_accept_amounts_up_to_the_limit(self):
        self.assertEqual(self.limits(RulesAdmin.currency)["최대"], (1, BIG))
        self.assertEqual(self.limits(Admin.give_candy)["수량"], (-BIG, BIG))
        self.assertEqual(self.limits(Admin.give_role_candy)["수량"], (-BIG, BIG))

    def test_the_bag_shows_huge_amounts_with_commas_and_a_full_bar(self):
        self.store.add_candy(G, 7, BIG)
        embed = bag_for(self.w.bot, self.w.member)
        value = {f.name: f.value for f in embed.fields}["💾 RAM"]
        self.assertIn("**9,999,999,999** / 9,999,999,999", value)
        self.assertTrue(value.endswith("🟦" * 10))

    async def test_a_dungeon_purchase_and_bonus_work_with_big_numbers(self):
        locks = Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        out = await service.enter(self.store, locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(floor=5, room=dungeon.ROOMS, phase="cleared", kind="boss")
        self.store.save_dungeon(G, U, json.dumps(data))
        self.store.add_candy(G, U, BIG - 100)
        from tests.helpers import Scripted
        opened = await service.act(self.store, locks, Scripted(randoms=[0.99, 0.0, 0.0, 0.9, 0.0, 0.0, 0.0]), G, U, out.run.rev, "advance")
        self.assertEqual(opened.run.phase, "shop")
        bought = await service.act(self.store, locks, Scripted(), G, U, opened.run.rev, "buy", "1")
        self.assertTrue(bought.ok, bought.text)
        self.assertEqual(self.store.wallet(G, U), BIG - 100 - opened.run.shop[1]["price"])
