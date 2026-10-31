"""역할 재화 지급 시험: 대상(역할 보유자·봇 제외), 확인 버튼, 상한/하한, 인텐트 안내, 관리자 전용, 한 번에 저장."""
import unittest
from types import SimpleNamespace as NS

import discord

import main
from cogs.admin import NO_MEMBERS_INTENT, Admin, ConfirmRoleGrant, role_member_ids
from main import HalloweenBot, env_flag
from store import CANDY_MAX, Store
from tests.helpers import FakeRole, World, sent_embed

G = 100


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_everyone_gets_the_same_amount_and_totals_are_reported(self):
        self.store.add_candy(G, 2, 10)
        self.assertEqual(self.store.add_candy_many(G, [1, 2, 3], 5), (3, 15, 0))
        self.assertEqual([self.store.wallet(G, u) for u in (1, 2, 3)], [5, 15, 5])

    def test_cap_and_floor_are_per_person_and_counted_as_partial(self):
        self.store.add_candy(G, 1, CANDY_MAX - 2)
        self.store.add_candy(G, 2, 3)
        self.assertEqual(self.store.add_candy_many(G, [1, 2], 10), (2, 2 + 10, 1))            # 1번은 2만 들어간다
        self.assertEqual(self.store.wallet(G, 1), CANDY_MAX)
        # -20: 1번은 그대로 -20, 2번은 13뿐이라 0까지만(-13), 3번은 이미 0이라 변화 없음(0) → 일부만 반영된 사람 2명
        self.assertEqual(self.store.add_candy_many(G, [1, 2, 3], -20), (3, -20 - 13, 2))
        self.assertEqual([self.store.wallet(G, u) for u in (1, 2, 3)], [CANDY_MAX - 20, 0, 0])

    def test_duplicates_count_once_and_servers_are_separate(self):
        self.assertEqual(self.store.add_candy_many(G, [7, 7, 7], 4), (1, 4, 0))
        self.assertEqual((self.store.wallet(G, 7), self.store.wallet(101, 7)), (4, 0))

    def test_a_failure_in_the_middle_changes_nobody(self):
        self.store.add_candy(G, 1, 5)
        with self.assertRaises(OverflowError):
            self.store.add_candy_many(G, [1, 2, 2 ** 70, 4], 5)       # 세 번째(64비트를 넘는 ID)에서 실패하면 앞사람도 반영되지 않는다
        self.assertEqual([self.store.wallet(G, u) for u in (1, 2, 4)], [5, 0, 0])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM wallets WHERE guild=? AND user=2", (G,)).fetchone()[0], 0)

    def test_a_few_thousand_people_in_one_go(self):
        count, total, partial = self.store.add_candy_many(G, range(1, 5001), 7)
        self.assertEqual((count, total, partial), (5000, 35000, 0))


class BotOptionTests(unittest.TestCase):
    def test_members_intent_is_off_unless_asked_and_never_preloads_everyone(self):
        for flag in (False, True):
            store = Store(":memory:")
            bot = HalloweenBot(store, members_intent=flag)
            self.assertEqual(bot.intents.members, flag)
            self.assertFalse(bot._connection._chunk_guilds)
            store.close()

    def test_env_flag(self):
        for raw in ("1", "true", "TRUE", " yes ", "On"):
            self.assertTrue(env_flag(raw), raw)
        for raw in ("", "0", "false", "no", "off", "아니오", "truee"):
            self.assertFalse(env_flag(raw), raw)

    def test_env_example_documents_the_option(self):
        text = open(main.BASE / ".env.example", encoding="utf-8").read()
        self.assertIn("MEMBERS_INTENT=", text)
        self.assertIn("Server Members Intent", text)


class RoleGrantFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.bot = HalloweenBot(self.store, members_intent=True)        # 같은 저장소를 쓰는, 멤버 인텐트가 켜진 봇
        self.cog = Admin(self.bot)
        self.role = self.w.add_role(60, name="손님")
        self.other = self.w.add_role(61, name="다른 역할")
        self.guest1, self.guest2 = self.w.new_member(11), self.w.new_member(12)
        self.plain = self.w.new_member(13)
        self.robot = self.w.new_member(14)
        self.robot.bot = True
        self.guest1.roles, self.guest2.roles, self.robot.roles = [self.role], [self.role, self.other], [self.role]
        self.plain.roles = [self.other]
        self.w.channel(400)

        async def fetch_members(limit=None):
            for member in list(self.w.members.values()):
                yield member
        self.w.guild.fetch_members = fetch_members

    async def asyncTearDown(self):
        await self.bot.close()
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def run_cmd(self, interaction, role, amount):
        await Admin.give_role_candy.callback(self.cog, interaction, role, amount)
        return interaction

    def preview(self, interaction):
        embed, kwargs = sent_embed(interaction, "followup")
        return embed, kwargs

    def candy(self, *users):
        return [self.store.wallet(G, u.id) for u in users]


class CommandTests(RoleGrantFixture):
    async def test_preview_lists_the_count_and_changes_nothing_until_confirmed(self):
        i = await self.run_cmd(self.ask(), self.role, 30)
        i.response.defer.assert_awaited_once()
        embed, kwargs = self.preview(i)
        self.assertIn("**2명**", embed.description)                     # 봇과 다른 역할 사람은 빠진다
        self.assertIn("🍬 60", embed.description)                        # 총 60
        self.assertIsInstance(kwargs["view"], ConfirmRoleGrant)
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(self.candy(self.guest1, self.guest2, self.plain, self.robot), [0, 0, 0, 0])

    async def test_confirming_pays_only_role_holders_and_reports(self):
        i = await self.run_cmd(self.ask(), self.role, 30)
        view = self.preview(i)[1]["view"]
        click = self.ask()
        await view.confirm(click)
        self.assertEqual(self.candy(self.guest1, self.guest2, self.plain, self.robot), [30, 30, 0, 0])
        text = click.response.edit_message.await_args.kwargs["embed"].description
        self.assertIn("2명", text)
        self.assertIn("+60", text)
        self.assertIsNone(click.response.edit_message.await_args.kwargs["view"])

    async def test_cancel_and_strangers_change_nothing(self):
        view = self.preview(await self.run_cmd(self.ask(), self.role, 30))[1]["view"]
        self.assertFalse(await view.interaction_check(self.ask(uid=99)))
        self.assertTrue(await view.interaction_check(self.ask(uid=7)))
        await view.cancel(self.ask())
        self.assertEqual(self.candy(self.guest1, self.guest2), [0, 0])

    async def test_revoking_floors_at_zero_and_cap_is_explained(self):
        self.store.add_candy(G, 11, 5)
        self.store.add_candy(G, 12, CANDY_MAX - 1)
        view = self.preview(await self.run_cmd(self.ask(), self.role, -50))[1]["view"]
        click = self.ask()
        await view.confirm(click)
        self.assertEqual(self.candy(self.guest1, self.guest2), [0, CANDY_MAX - 1 - 50])
        text = click.response.edit_message.await_args.kwargs["embed"].description
        self.assertIn("1명은 0 아래로는 내려가지 않아서 일부만 반영됐어요", text)      # 11번은 5만 줄었다
        self.assertIn("합계: **-55**", text)                                           # 11번 −5, 12번 −50
        view = self.preview(await self.run_cmd(self.ask(), self.role, 99999))[1]["view"]
        click = self.ask()
        await view.confirm(click)
        self.assertEqual(self.candy(self.guest1, self.guest2), [CANDY_MAX, CANDY_MAX])
        self.assertIn(f"최대 {CANDY_MAX:,}개를 넘을 수 없어서", click.response.edit_message.await_args.kwargs["embed"].description)

    async def test_the_list_is_the_one_shown_in_the_preview(self):
        view = self.preview(await self.run_cmd(self.ask(), self.role, 10))[1]["view"]
        self.guest1.roles = []                                           # 미리보기 뒤에 역할을 잃어도 보여 준 명단대로 처리한다
        newcomer = self.w.new_member(15)
        newcomer.roles = [self.role]
        await view.confirm(self.ask())
        self.assertEqual(self.candy(self.guest1, self.guest2, newcomer), [10, 10, 0])

    async def test_everyone_role_means_every_non_bot_member(self):
        everyone = FakeRole(100, default=True, name="@everyone")
        i = await self.run_cmd(self.ask(), everyone, 5)
        view = self.preview(i)[1]["view"]
        await view.confirm(self.ask())
        members = [self.w.members[u] for u in (7, 11, 12, 13)]
        self.assertEqual(self.candy(*members), [5, 5, 5, 5])
        self.assertEqual(self.candy(self.robot), [0])

    async def test_zero_empty_role_and_fetch_errors_are_explained(self):
        i = await self.run_cmd(self.ask(), self.role, 0)
        self.assertIn("0이면", sent_embed(i)[0].description)
        i.response.defer.assert_not_awaited()
        empty = self.w.add_role(70, name="아무도 없는 역할")
        i = await self.run_cmd(self.ask(), empty, 5)
        self.assertIn("가진 사람이 없어요", self.preview(i)[0].description)

        async def broken(limit=None):
            raise discord.HTTPException(NS(status=500, reason="x"), "오류")
            yield
        self.w.guild.fetch_members = broken
        i = await self.run_cmd(self.ask(), self.role, 5)
        self.assertIn("가져오지 못했어요", self.preview(i)[0].description)
        self.assertEqual(self.candy(self.guest1), [0])

    async def test_regular_members_are_refused_and_nothing_is_fetched(self):
        called = []

        async def spy(limit=None):
            called.append(1)
            return
            yield
        self.w.guild.fetch_members = spy
        i = await self.run_cmd(self.ask(admin=False), self.role, 30)
        self.assertIn("관리자만", sent_embed(i)[0].description)
        i.response.defer.assert_not_awaited()
        self.assertEqual((called, self.candy(self.guest1)), ([], [0]))

    async def test_role_member_ids_skips_bots_and_other_roles(self):
        self.assertEqual(sorted(await role_member_ids(self.w.guild, self.role)), [11, 12])


class WithoutIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_explains_how_to_turn_it_on_and_does_nothing(self):
        w = World()                                                       # 기본 봇: 멤버 인텐트 꺼짐
        try:
            w.guild.fetch_members = None
            role = w.add_role(60)
            interaction = w.interaction(admin=True)
            await Admin.give_role_candy.callback(Admin(w.bot), interaction, role, 10)
            embed, _ = sent_embed(interaction)
            self.assertEqual(embed.description, NO_MEMBERS_INTENT)
            self.assertIn("Server Members Intent", embed.description)
            self.assertIn("MEMBERS_INTENT=true", embed.description)
            interaction.response.defer.assert_not_awaited()
        finally:
            await w.close()


if __name__ == "__main__":
    unittest.main()
