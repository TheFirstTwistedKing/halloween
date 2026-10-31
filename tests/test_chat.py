"""채팅 보상 흐름 시험: 재화·패키지·쿨타임·채널 지정."""
import os
import random
import unittest
from types import SimpleNamespace as NS
from unittest import mock
from unittest.mock import AsyncMock

import discord

from cogs.economy import Economy
from packages import PackageView
from store import CANDY_MAX, DEFAULT_SETTINGS
from tests.helpers import Scripted, World


class ChatFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rng = Scripted()
        self.w = World(self.rng)
        self.cog = Economy(self.w.bot)
        self.channel = self.w.channel(400)
        self.w.store.update_settings(100, channels=[400])
        self.clock = [1000.0]
        patcher = mock.patch("cogs.economy.time.time", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.w.close()

    async def chat(self, uid=7, **kw):
        await self.cog.on_message(self.w.message(uid=uid, channel=kw.pop("channel", self.channel), **kw))

    def wallet(self, uid=7):
        return self.w.store.wallet(100, uid)

    def roll(self, candy=0.9, amount=3, package=0.9):
        """한 번의 채팅이 쓰는 난수: 재화 확률 → (성공하면 재화량) → 패키지 확률."""
        self.rng.randoms += [candy, package] if candy is not None else [package]
        if candy is not None and candy * 100 < 50:
            self.rng.ints.append(amount)


class RewardTests(ChatFixture):
    async def test_a_lucky_message_gives_candy(self):
        self.roll(candy=0.1, amount=4)
        await self.chat()
        self.assertEqual(self.wallet(), 4)

    async def test_an_unlucky_message_gives_nothing(self):
        self.roll(candy=0.7)
        await self.chat()
        self.assertEqual(self.wallet(), 0)

    async def test_exactly_fifty_percent_misses_and_just_below_hits(self):
        self.roll(candy=0.5)
        await self.chat(uid=1)
        self.roll(candy=0.4999, amount=1)
        await self.chat(uid=2)
        self.assertEqual((self.wallet(1), self.wallet(2)), (0, 1))

    async def test_only_event_channels_pay_out(self):
        other = self.w.channel(401)
        await self.chat(channel=other)                       # 난수를 뽑지 않아야 한다(Scripted가 비어 있어 뽑으면 실패)
        self.assertEqual(self.wallet(), 0)
        self.w.store.update_settings(100, channels=[])
        await self.chat()
        self.assertEqual(self.wallet(), 0)

    async def test_threads_inside_an_event_channel_count(self):
        thread = self.w.channel(777)
        thread.parent_id = 400
        self.roll(candy=0.1, amount=2)
        await self.chat(channel=thread)
        self.assertEqual(self.wallet(), 2)

    async def test_bots_webhooks_dms_and_system_messages_are_ignored(self):
        bot_author = self.w.new_member(55)
        bot_author.bot = True
        await self.chat(uid=55)
        await self.cog.on_message(self.w.message(webhook_id=123))
        await self.cog.on_message(self.w.message(type=discord.MessageType.new_member))
        await self.cog.on_message(self.w.message(guild=None))
        self.assertEqual(self.wallet(), 0)

    async def test_replies_count_like_normal_messages(self):
        self.roll(candy=0.1, amount=5)
        await self.chat(type=discord.MessageType.reply)
        self.assertEqual(self.wallet(), 5)

    async def test_other_servers_are_isolated_and_a_configured_server_list_is_respected(self):
        self.w.bot.guild_ids = frozenset({999})
        await self.chat()                                    # 허용하지 않은 서버에서는 아무 일도 없다.
        self.assertEqual(self.wallet(), 0)

    async def test_the_candy_cap_is_enforced_without_a_reaction(self):
        self.w.store.add_candy(100, 7, CANDY_MAX)
        self.roll(candy=0.1, amount=5)
        message = self.w.message()
        await self.cog.on_message(message)
        self.assertEqual(self.wallet(), CANDY_MAX)
        message.add_reaction.assert_not_awaited()
        self.w.store.add_candy(100, 7, -2)
        self.clock[0] += 10
        self.roll(candy=0.1, amount=5)
        await self.cog.on_message(message)
        self.assertEqual(self.wallet(), CANDY_MAX)         # 5를 얻어도 상한까지만


class CooldownTests(ChatFixture):
    async def test_messages_inside_the_cooldown_are_not_counted_at_all(self):
        self.roll(candy=0.1, amount=3)
        await self.chat()
        await self.chat()                                    # 같은 시각: 난수를 뽑지 않는다(Scripted가 비어 있다).
        self.clock[0] += 2.9
        await self.chat()
        self.assertEqual(self.wallet(), 3)
        self.clock[0] += 0.2                                 # 합쳐서 3.1초가 지나면 다시 센다.
        self.roll(candy=0.1, amount=2)
        await self.chat()
        self.assertEqual(self.wallet(), 5)

    async def test_zero_cooldown_counts_every_message(self):
        self.w.store.update_settings(100, candy_cooldown=0)
        for _ in range(3):                                   # 시간이 전혀 흐르지 않아도 매번 센다.
            self.roll(candy=0.1, amount=1)
            await self.chat()
        self.assertEqual(self.wallet(), 3)

    async def test_each_person_has_their_own_cooldown(self):
        self.roll(candy=0.1, amount=1)
        await self.chat(uid=1)
        self.roll(candy=0.1, amount=1)
        await self.chat(uid=2)
        self.assertEqual((self.wallet(1), self.wallet(2)), (1, 1))

    async def test_old_cooldown_records_are_forgotten(self):
        self.roll()
        await self.chat()
        self.clock[0] += 4000
        with mock.patch("cogs.economy.packages.finalize_due", new=AsyncMock()):
            await self.cog.tidy.coro(self.cog)
        self.assertEqual(self.cog.last_candy, {})
        self.assertFalse(hasattr(self.cog, "last_exp"))


class ReactionTests(ChatFixture):
    async def test_reaction_on_reward(self):
        self.roll(candy=0.1)
        message = self.w.message()
        await self.cog.on_message(message)
        message.add_reaction.assert_awaited_once_with("🍬")

    async def test_no_reaction_without_a_reward_or_when_disabled(self):
        self.roll(candy=0.9)
        message = self.w.message()
        await self.cog.on_message(message)
        message.add_reaction.assert_not_awaited()
        self.w.store.update_settings(100, reaction=False)
        self.clock[0] += 100
        self.roll(candy=0.1)
        await self.cog.on_message(message)
        message.add_reaction.assert_not_awaited()
        self.assertEqual(self.wallet(), 3)                # 반응이 없어도 재화는 들어온다.

    async def test_busy_channels_get_at_most_one_reaction_per_second(self):
        self.w.store.update_settings(100, candy_cooldown=0)
        messages = [self.w.message(uid=u) for u in (1, 2, 3)]
        for message in messages:
            self.roll(candy=0.1)
            await self.cog.on_message(message)
        self.assertEqual([m.add_reaction.await_count for m in messages], [1, 0, 0])
        self.clock[0] += 1.1
        self.roll(candy=0.1)
        await self.cog.on_message(messages[1])
        self.assertEqual(messages[1].add_reaction.await_count, 1)

    async def test_a_failed_reaction_does_not_break_the_reward(self):
        message = self.w.message()
        message.add_reaction.side_effect = discord.Forbidden(NS(status=403, reason="x"), "no")
        self.roll(candy=0.1, amount=4)
        await self.cog.on_message(message)
        self.assertEqual(self.wallet(), 4)


class PackageSpawnTests(ChatFixture):
    async def test_a_package_appears_in_the_same_channel(self):
        self.rng.randoms += [0.9, 0.0005]                    # 재화 없음, 패키지 0.05% < 0.1%
        self.rng.ints.append(20)
        await self.chat()
        (sent,) = self.channel.sent
        self.assertIn("패키지", sent["embed"].title)
        self.assertIn("한 번씩", sent["embed"].description)
        self.assertIn("120초", sent["embed"].description)
        self.assertIsInstance(sent["view"], PackageView)
        row = self.w.store.package(9001)
        self.assertEqual((row["channel"], row["mode"], row["expires"], row["closed"]), (400, "all", 1120.0, 0))

    async def test_exactly_point_one_percent_does_not_spawn(self):
        self.roll(candy=0.9, package=0.001)
        await self.chat()
        self.assertEqual(self.channel.sent, [])

    async def test_mode_and_duration_come_from_the_settings(self):
        self.w.store.update_settings(100, package_mode="first", package_seconds=30)
        self.rng.randoms += [0.9, 0.0]
        self.rng.ints.append(20)
        await self.chat()
        self.assertIn("한 명", self.channel.sent[0]["embed"].description)
        row = self.w.store.package(9001)
        self.assertEqual((row["mode"], row["expires"]), ("first", 1030.0))

    async def test_only_one_package_at_a_time_per_channel(self):
        self.w.store.create_package(100, 400, 777, expires=1100.0, mode="all")
        self.rng.randoms += [0.9]                            # 패키지 확률은 아예 뽑지 않는다.
        self.rng.ints.append(20)
        await self.chat()
        self.assertEqual(self.channel.sent, [])
        self.clock[0] = 1100.0                               # 사라진 뒤에는 다시 나올 수 있다.
        self.roll(candy=0.9, package=0.0)
        await self.chat()
        self.assertEqual(len(self.channel.sent), 1)

    async def test_a_package_in_another_channel_does_not_block(self):
        other = self.w.channel(401)
        self.w.store.update_settings(100, channels=[400, 401])
        self.w.store.create_package(100, 401, 777, expires=1100.0, mode="all")
        self.rng.randoms += [0.9, 0.0]
        self.rng.ints.append(20)
        await self.chat()
        self.assertEqual(len(self.channel.sent), 1)

    async def test_a_message_that_is_not_counted_never_rolls_for_a_package(self):
        self.roll(candy=0.9)
        await self.chat()
        await self.chat()                                    # 쿨타임 안: 난수를 뽑지 않는다.
        self.assertEqual(self.channel.sent, [])

    async def test_a_send_failure_leaves_no_record(self):
        self.channel.send.side_effect = discord.Forbidden(NS(status=403, reason="x"), "no")
        self.rng.randoms += [0.9, 0.0]
        self.rng.ints.append(20)
        with self.assertLogs("halloween", level="WARNING"):
            await self.chat()
        self.assertIsNone(self.w.store.active_package(400, 1000.0))

    async def test_package_can_be_turned_off(self):
        self.w.store.update_settings(100, package_chance=0)
        self.roll(candy=0.9)
        await self.chat()
        self.assertEqual(self.channel.sent, [])


@unittest.skipIf(os.getenv("HW_FAST"), "변이 검사 때는 느린 통계 시험을 건너뛴다")
class StatisticalTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_random_numbers_through_the_whole_flow(self):
        """진짜 난수로 채팅을 많이 흘려서, 사용자가 정한 확률이 실제 흐름에서도 지켜지는지 본다."""
        w = World(random.Random(20241031))
        try:
            cog, channel = Economy(w.bot), w.channel(400)
            w.store.update_settings(100, channels=[400], candy_cooldown=0, package_chance=0.1, reaction=False)
            messages = [w.message(uid=1 + n % 50) for n in range(20000)]
            counter = [0]
            with mock.patch("cogs.economy.time.time", side_effect=lambda: counter[0]):
                for message in messages:
                    counter[0] += 1000                       # 패키지가 매번 사라져서 독립 시행이 된다.
                    await cog.on_message(message)
            total = sum(w.store.wallet(100, u) for u in range(1, 51))
            self.assertAlmostEqual(total / 20000, 0.5 * 3, delta=0.08)       # 평균: 50% × (1~5의 평균 3) = 1.5
            self.assertTrue(8 <= len(channel.sent) <= 40, len(channel.sent))      # 0.1% × 20000 = 기대 20
        finally:
            await w.close()


if __name__ == "__main__":
    unittest.main()
