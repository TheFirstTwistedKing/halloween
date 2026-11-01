"""🎟️ 히든 칭호 획득권: 던전 클리어로 얻는 별도 숫자. 물품이 아니라서 /물품지급·/물품삭제로는 못 다루고, /획득권지급으로 조정한다."""
import json
import unittest

import dungeon
import dungeon_service as service
from cogs.admin import Admin
from cogs.economy import Economy
from services import Locks
from store import TICKET_MAX, Store
from tests.helpers import Scripted, World, sent_embed
from views import bag_for

G, U, OTHER = 100, 7, 8


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_add_and_remove(self):
        self.assertEqual(self.store.add_tickets(G, U, 3), 3)
        self.assertEqual(self.store.tickets(G, U), 3)
        self.assertEqual(self.store.add_tickets(G, U, -1), -1)
        self.assertEqual(self.store.tickets(G, U), 2)

    def test_it_never_goes_below_zero_and_reports_what_really_changed(self):
        self.store.add_tickets(G, U, 2)
        self.assertEqual(self.store.add_tickets(G, U, -10), -2)               # 가진 만큼만
        self.assertEqual(self.store.tickets(G, U), 0)
        self.assertEqual(self.store.add_tickets(G, U, -1), 0)                  # 이미 0이면 아무 일도 없다
        self.assertEqual(self.store.add_tickets(G, OTHER, -5), 0)

    def test_the_maximum(self):
        self.assertEqual(self.store.add_tickets(G, U, TICKET_MAX + 500), TICKET_MAX)
        self.assertEqual(self.store.tickets(G, U), TICKET_MAX)
        self.assertEqual(self.store.add_tickets(G, U, 1), 0)

    def test_it_is_per_person_and_per_server(self):
        self.store.add_tickets(G, U, 2)
        self.store.add_tickets(G, OTHER, 5)
        self.store.add_tickets(101, U, 7)
        self.assertEqual((self.store.tickets(G, U), self.store.tickets(G, OTHER), self.store.tickets(101, U)), (2, 5, 7))
        self.store.add_tickets(G, U, -2)
        self.assertEqual((self.store.tickets(G, U), self.store.tickets(G, OTHER), self.store.tickets(101, U)), (0, 5, 7))

    def test_the_row_is_removed_at_zero_and_comes_back_when_earned_again(self):
        self.store.add_tickets(G, U, 1)
        self.store.add_tickets(G, U, -1)
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM tickets WHERE guild=? AND user=?", (G, U)).fetchone())
        self.store.add_tickets(G, U, 4)
        self.assertEqual(self.store.tickets(G, U), 4)

    def test_it_works_together_with_clearing_a_dungeon(self):
        """클리어 보상(end_dungeon)이 더한 획득권도 같은 숫자라서 같은 방법으로 줄일 수 있다."""
        with self.store.db:
            self.store.db.execute("INSERT INTO dungeons VALUES(?,?,?)", (G, U, "{}"))
        self.store.end_dungeon(G, U, tickets=1)
        self.assertEqual(self.store.tickets(G, U), 1)
        self.assertEqual(self.store.add_tickets(G, U, -1), -1)
        self.assertEqual(self.store.tickets(G, U), 0)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = Admin(self.w.bot)
        self.w.channel(400)
        self.target = self.w.member
        self.w.new_member(OTHER)

    async def asyncTearDown(self):
        await self.w.close()

    async def give(self, amount, target=None, admin=True):
        i = self.w.interaction(admin=admin)
        await Admin.give_ticket.callback(self.cog, i, target or self.target, amount)
        return i

    def text(self, i):
        return sent_embed(i)[0].description

    async def test_the_admin_can_take_a_ticket_away_and_it_disappears_from_the_bag(self):
        self.store.add_tickets(G, U, 2)
        self.assertIn("🎟️ 히든 칭호 획득권", [f.name for f in bag_for(self.w.bot, self.w.member).fields])
        i = await self.give(-1)
        self.assertIn("-1장 바뀌었어요", self.text(i))
        self.assertIn("지금 1장", self.text(i))
        self.assertEqual(self.store.tickets(G, U), 1)
        await self.give(-1)
        self.assertEqual(self.store.tickets(G, U), 0)
        self.assertNotIn("🎟️ 히든 칭호 획득권", [f.name for f in bag_for(self.w.bot, self.w.member).fields])      # 0장이면 가방에서 사라진다.

    async def test_the_admin_can_also_give_tickets(self):
        i = await self.give(3)
        self.assertIn("+3장", self.text(i))
        self.assertEqual(self.store.tickets(G, U), 3)
        fields = {f.name: f.value for f in bag_for(self.w.bot, self.w.member).fields}
        self.assertEqual(fields["🎟️ 히든 칭호 획득권"], "×3")

    async def test_taking_more_than_they_have_stops_at_zero_and_says_so(self):
        self.store.add_tickets(G, U, 1)
        i = await self.give(-5)
        self.assertIn("-1장", self.text(i))
        self.assertIn("가진 만큼만 회수했어요", self.text(i))
        self.assertEqual(self.store.tickets(G, U), 0)

    async def test_the_maximum_is_explained(self):
        i = await self.give(TICKET_MAX)
        self.assertEqual(self.store.tickets(G, U), TICKET_MAX)
        i = await self.give(5)
        self.assertIn("최대", self.text(i))
        self.assertEqual(self.store.tickets(G, U), TICKET_MAX)

    async def test_zero_only_shows_how_many_they_have(self):
        self.store.add_tickets(G, U, 4)
        i = await self.give(0)
        self.assertIn("지금 **4장**", self.text(i))
        self.assertEqual(self.store.tickets(G, U), 4)

    async def test_only_admins_and_never_bots_and_the_answer_is_public(self):
        self.store.add_tickets(G, U, 2)
        i = await self.give(-1, admin=False)
        self.assertIn("관리자만", self.text(i))
        self.assertEqual(self.store.tickets(G, U), 2)
        i = await self.give(1, target=type("Bot", (), {"bot": True, "id": 999, "mention": "<@999>"})())
        self.assertIn("봇", self.text(i))
        i = await self.give(-1)
        self.assertFalse(i.response.send_message.await_args.kwargs["ephemeral"])

    async def test_it_leaves_other_people_alone(self):
        self.store.add_tickets(G, U, 2)
        self.store.add_tickets(G, OTHER, 2)
        await self.give(-1)
        self.assertEqual((self.store.tickets(G, U), self.store.tickets(G, OTHER)), (1, 2))

    async def test_the_item_commands_do_not_touch_tickets_which_is_why_a_separate_command_exists(self):
        """관리자가 /물품지급으로 회수하려 해도 획득권은 물품이 아니라서 그대로다(그래서 /획득권지급이 필요하다)."""
        self.store.ensure_defaults(G)
        self.store.add_tickets(G, U, 2)
        for name in ("히든 칭호 획득권", "획득권"):
            i = self.w.interaction(admin=True)
            await Admin.give_item.callback(self.cog, i, self.target, name, -1)
            self.assertIn("그런 물품이 없어요", self.text(i))
        self.assertEqual(self.store.tickets(G, U), 2)
        self.assertIsNone(self.store.item_by_name(G, "히든 칭호 획득권"))

    async def test_the_action_is_written_to_the_log(self):
        self.store.add_tickets(G, U, 2)
        with self.assertLogs("halloween", level="INFO") as logs:
            await self.give(-1)
        self.assertIn("획득권 조정", "\n".join(logs.output))


class FullStoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_the_dungeon_get_a_ticket_then_the_admin_removes_it(self):
        """실제 흐름: 던전을 클리어해 획득권을 얻고(가방에 보인다) → 관리자가 회수하면 가방에서 사라진다."""
        w = World()
        try:
            store, locks = w.store, Locks()
            store.ensure_defaults(G)
            store.update_settings(G, dungeon={"보상.획득권확률": 100})                 # 반드시 나오게
            store.add_item(G, U, store.ensure_system_items(G)["key"], 1)
            out = await service.enter(store, locks, G, U)
            data = json.loads(out.run.to_json())
            data.update(floor=dungeon.FLOORS, room=dungeon.ROOMS, phase="cleared", kind="boss")
            store.save_dungeon(G, U, json.dumps(data))
            done = await service.act(store, locks, Scripted(randoms=[0.0], ints=[50]), G, U, out.run.rev, "advance")
            self.assertEqual((done.ended, done.reward.ticket, done.reward.tickets_total), ("clear", True, 1))
            bag = {f.name: f.value for f in bag_for(w.bot, w.member).fields}
            self.assertEqual(bag["🎟️ 히든 칭호 획득권"], "×1")
            admin = w.interaction(admin=True)
            await Admin.give_ticket.callback(Admin(w.bot), admin, w.member, -1)
            self.assertIn("지금 0장", sent_embed(admin)[0].description)
            self.assertNotIn("🎟️ 히든 칭호 획득권", [f.name for f in bag_for(w.bot, w.member).fields])
        finally:
            await w.close()


if __name__ == "__main__":
    unittest.main()
