"""레이드에서 내 체력 보기: 공용 화면에 참가자 50명 모두의 HP, 버튼을 누르면 나에게만 보이는 내 상태 창(맞으면 자동으로 고침)."""
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

import raid
from cogs.raid import PANEL_EDITS, PANEL_TTL, Raid, member_chunks, member_entry, panel_embed, raid_embed
from store import Store
from tests.helpers import World

G, CH = 100, 400
A, B = 1, 2
T0 = 1_000_000.0


class Dice:
    def random(self):
        return 0.99

    def sample(self, population, k):
        return list(population)[:k]


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_all_fifty_members_show_their_hp(self):
        raid_id = raid.open_raid(self.store, G, CH, raid.check_boss(name="코어", emoji="🐉", hp=10 ** 6, damage=10), now=T0)
        users = list(range(10 ** 17, 10 ** 17 + raid.MAX_MEMBERS))
        for u in users:
            raid.join(self.store, G, u, now=T0)
        raid.start(self.store, G, now=T0)
        data = raid.boss(raid.get(self.store, raid_id))
        data["log"] = ["가" * 200] * raid.LOG_KEEP                                         # 기록이 길어도
        raid._save(self.store, raid_id, data)
        embed = raid_embed(self.store, raid.get(self.store, raid_id), now=T0)
        member_text = "".join(f.value for f in embed.fields if f.name.startswith("👥"))
        for u in users:
            self.assertIn(f"<@{u}> ❤️1,000", member_text)                                # 31번째 이후도 빠지지 않는다
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
        self.assertLessEqual(len(embed), 6000)                                            # 디스코드 임베드 한도
        self.assertIn("❤️ HP", embed.fields[0].name)

    def test_entries(self):
        self.assertEqual(member_entry({"user": 5, "hp": 820, "down": False}), "<@5> ❤️820")
        self.assertEqual(member_entry({"user": 5, "hp": 0, "down": True}), "<@5> 💀")
        self.assertEqual(member_entry({"user": 5, "hp": 9, "down": False, "awakened": True, "ult": "power", "ult_gauge": 100}), "<@5> ❤️9⚡🟡")
        chunks = member_chunks([{"user": 10 ** 17 + n, "hp": 1000, "down": False} for n in range(50)], limit=300)
        self.assertTrue(all(len(", ".join(c)) <= 300 for c in chunks))
        self.assertEqual(sum(len(c) for c in chunks), 50)
        self.assertEqual(member_chunks([]), [])

    def test_panel_shows_my_hp(self):
        raid_id = raid.open_raid(self.store, G, CH, raid.check_boss(name="코어", emoji="🐉", hp=100, damage=10), now=T0)
        raid.join(self.store, G, A, now=T0)
        text = panel_embed(self.store, G, raid.get(self.store, raid_id), A, now=T0).description
        self.assertIn("❤️ **1,000/1,000**", text)
        self.assertIn("🟩", text)


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.w.channel(CH)
        self.cog = Raid(self.bot, rng=Dice())
        self.message = NS(id=777)
        self.raid_id = raid.open_raid(self.store, G, CH, raid.check_boss(name="코어", emoji="🐉", hp=10 ** 6, damage=100), now=time.time(),
                                      attack_every=3, cooldown=1)
        raid.set_message(self.store, self.raid_id, 777)

    async def asyncTearDown(self):
        await self.w.close()

    def press(self, uid=A):
        i = self.w.interaction(uid=uid)
        i.message = self.message
        panel = NS(edit=AsyncMock())
        i.followup = NS(send=AsyncMock(return_value=panel))
        return i, panel

    async def test_join_opens_my_panel_and_later_presses_edit_it(self):
        i, panel = self.press()
        await self.cog.press(i, "join")
        kwargs = i.followup.send.call_args.kwargs
        self.assertTrue(kwargs["ephemeral"])                                             # 나에게만
        self.assertIn("1,000/1,000", kwargs["embed"].description)
        raid.start(self.store, G)
        i2, _ = self.press()
        await self.cog.press(i2, "attack")
        i2.followup.send.assert_not_awaited()                                             # 새 창을 또 띄우지 않고
        panel.edit.assert_awaited()                                                       # 처음 창을 고친다
        i3, _ = self.press()
        await self.cog.press(i3, "status")                                                # 내 상태 버튼은 새 창
        i3.followup.send.assert_awaited()

    async def test_expired_or_broken_panel_is_replaced(self):
        i, panel = self.press()
        await self.cog.press(i, "join")
        raid.start(self.store, G)
        self.cog.panels[(self.raid_id, A)]["made"] -= PANEL_TTL + 1                     # 15분이 지났다
        i2, _ = self.press()
        await self.cog.press(i2, "attack")
        i2.followup.send.assert_awaited()
        self.cog.panels[(self.raid_id, A)]["message"].edit.side_effect = discord.HTTPException(NS(status=404, reason="x"), "gone")
        i3, _ = self.press()
        await self.cog.show_panel(i3, raid.get(self.store, self.raid_id), A)
        i3.followup.send.assert_awaited()

    async def test_refresh_updates_panels_of_people_who_got_hit(self):
        for uid in (A, B):
            i, _ = self.press(uid)
            await self.cog.press(i, "join")
        raid.start(self.store, G)
        row = raid.get(self.store, self.raid_id)
        self.assertEqual(await self.cog.refresh_panels(row), 2)                         # 시작하면서 회복 아이템을 받았다
        self.assertEqual(await self.cog.refresh_panels(row), 0)                         # 바뀐 게 없으면 고치지 않는다
        raid.tick(self.store, G, Dice(), now=time.time() + 3)                             # 보스가 A를 때렸다
        row = raid.get(self.store, self.raid_id)
        self.assertEqual(await self.cog.refresh_panels(row), 1)
        self.cog.panels[(self.raid_id, A)]["message"].edit.assert_awaited()
        self.assertIn("900/1,000", self.cog.panels[(self.raid_id, A)]["message"].edit.call_args.kwargs["embed"].description)
        self.assertEqual(await self.cog.refresh_panels(row), 0)

    async def test_the_three_second_refresh_updates_panels(self):
        i, panel = self.press()
        await self.cog.press(i, "join")
        raid.start(self.store, G)
        panel.edit.reset_mock()
        await self.cog.refresh_now(now=time.time() + 3)                                   # 보스가 때리고 정리가 돈다
        panel.edit.assert_awaited()
        self.assertIn("900/1,000", panel.edit.call_args.kwargs["embed"].description)

    async def test_refresh_is_capped_and_forgets_ended_raids(self):
        users = list(range(100, 100 + PANEL_EDITS + 3))
        for uid in users:
            i, _ = self.press(uid)
            await self.cog.press(i, "join")
        for entry in self.cog.panels.values():
            entry["seen"] = "old"                                                         # 모두 바뀐 것처럼
        row = raid.get(self.store, self.raid_id)
        self.assertEqual(await self.cog.refresh_panels(row), PANEL_EDITS)
        raid.cancel(self.store, G)
        await self.cog.refresh_panels(raid.get(self.store, self.raid_id))
        self.assertEqual([k for k in self.cog.panels if k[0] == self.raid_id], [])


if __name__ == "__main__":
    unittest.main()
