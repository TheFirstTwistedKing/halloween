"""레벨 옵션(/롤플관리 레벨옵션): 레벨 보상 '최고 역할만'(하위 보상 역할 자동 제거)과 '나간 유저 경험치 초기화'."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

import rp
from cogs.rp import Roleplay
from store import Store
from tests.helpers import World, sent_embed

G, A = 100, 7


def role(rid):
    return NS(id=rid, mention=f"<@&{rid}>")


REWARDS = [{"level": 1, "role": 11}, {"level": 5, "role": 15}, {"level": 10, "role": 20}, {"level": 10, "role": 21}]


class PlanTests(unittest.TestCase):
    def cfg(self, top, rewards=REWARDS):
        return rp.config({"rewards": rewards, "top_role_only": top})

    def test_config(self):
        self.assertEqual((rp.config({})["top_role_only"], rp.config({})["reset_on_leave"]), (False, False))
        self.assertEqual(rp.config({"top_role_only": True, "reset_on_leave": True})["reset_on_leave"], True)
        self.assertEqual(rp.config({"top_role_only": 1, "reset_on_leave": "yes"})["top_role_only"], False)   # 참/거짓만

    def test_normal_gives_everything_and_removes_nothing(self):
        give, drop = rp.reward_plan(self.cfg(False), 0, 7)
        self.assertEqual(([r["role"] for r in give], drop), ([11, 15], set()))

    def test_top_only(self):
        cfg = self.cfg(True)
        give, drop = rp.reward_plan(cfg, 0, 7)                                           # 건너뛴 LV 1 보상은 주지 않는다
        self.assertEqual(([r["role"] for r in give], drop), ([15], {11}))
        give, drop = rp.reward_plan(cfg, 7, 12)                                          # 같은 레벨 보상은 모두 준다
        self.assertEqual(([r["role"] for r in give], drop), ([20, 21], {11, 15}))
        self.assertEqual(rp.reward_plan(cfg, 5, 9), ([], set()))                          # 새 보상이 없으면 아무것도 안 한다

    def test_a_role_that_is_also_the_top_reward_is_kept(self):
        cfg = self.cfg(True, [{"level": 1, "role": 11}, {"level": 3, "role": 11}, {"level": 3, "role": 13}, {"level": 2, "role": 12}])
        give, drop = rp.reward_plan(cfg, 0, 3)
        self.assertEqual(({r["role"] for r in give}, drop), ({11, 13}, {12}))

    def test_reset_exp(self):
        store = Store(":memory:")
        try:
            self.assertEqual(rp.reset_exp(store, G, A), 0)
            rp.give(store, G, A, 500)
            rp.deposit(store, G, A, 100)
            self.assertEqual(rp.reset_exp(store, G, A), 500)
            me = rp.row(store, G, A)
            self.assertEqual((me["exp"], me["com"], me["bank"]), (0, 400, 100))         # COM·금고는 그대로
        finally:
            store.close()


class CogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Roleplay(self.bot)
        self.member = self.w.new_member(A)
        self.roles = {rid: role(rid) for rid in (11, 15, 20, 21, 99)}
        self.w.guild.get_role = lambda rid: self.roles.get(rid)
        self.member.remove_roles = AsyncMock()
        self.store.update_settings(G, rp={"rewards": REWARDS})

    async def asyncTearDown(self):
        await self.w.close()

    def cfg(self):
        return rp.config(self.store.settings(G)["rp"])

    async def options(self, admin=True, **kwargs):
        i = self.w.interaction(uid=A, admin=admin)
        await Roleplay.level_options.callback(self.cog, i, **kwargs)
        return sent_embed(i)[0].description or ""

    async def test_admin_command(self):
        self.assertIn("관리자만", await self.options(admin=False, top_only=True))
        self.assertFalse(self.cfg()["top_role_only"])
        text = await self.options(top_only=True)
        self.assertIn("최고 역할만: ✅ 켜짐", text)
        self.assertIn("나간 유저 경험치 초기화: ⬜ 꺼짐", text)
        await self.options(reset_on_leave=True)                                          # 비운 칸은 그대로
        self.assertEqual((self.cfg()["top_role_only"], self.cfg()["reset_on_leave"]), (True, True))
        self.assertEqual(self.cfg()["rewards"], REWARDS)                                  # 다른 설정도 그대로
        await self.options(top_only=False)
        self.assertEqual((self.cfg()["top_role_only"], self.cfg()["reset_on_leave"]), (False, True))

    async def test_intent_warning(self):
        self.bot._connection._intents.members = False                                  # bot.intents는 복사본이라 원본을 바꾼다
        self.assertIn("멤버 인텐트", await self.options(reset_on_leave=True))
        self.assertNotIn("멤버 인텐트", await self.options(reset_on_leave=False))
        self.bot._connection._intents.members = True
        self.assertNotIn("멤버 인텐트", await self.options(reset_on_leave=True))

    async def test_top_only_removes_lower_reward_roles(self):
        self.member.roles = [self.roles[11], self.roles[99]]                             # LV 1 보상과 상관없는 역할
        await self.options(top_only=True)
        text = await self.cog.level_up(self.member, self.cfg(), 1, 6)
        self.assertEqual([r.id for r in self.member.roles], [11, 99, 15])                # add_roles(가짜)는 목록에 더한다
        self.member.remove_roles.assert_awaited_once()
        self.assertEqual([r.id for r in self.member.remove_roles.await_args.args], [11])  # 보상이 아닌 역할은 안 뺀다
        self.assertIn("🧹 하위 레벨 보상 <@&11>", text)

    async def test_off_removes_nothing(self):
        self.member.roles = [self.roles[11]]
        text = await self.cog.level_up(self.member, self.cfg(), 1, 6)
        self.member.remove_roles.assert_not_awaited()
        self.assertNotIn("🧹", text)

    async def test_nothing_to_remove_and_remove_failure(self):
        await self.options(top_only=True)
        self.member.roles = []
        await self.cog.level_up(self.member, self.cfg(), 1, 6)
        self.member.remove_roles.assert_not_awaited()                                    # 가진 하위 역할이 없으면 부르지 않는다
        self.member.roles = [self.roles[11]]
        self.member.remove_roles.side_effect = discord.HTTPException(NS(status=403, reason="x"), "no")
        self.assertIn("빼지 못했어요", await self.cog.level_up(self.member, self.cfg(), 6, 10))

    async def leave(self, user, guild_id=G):
        await self.cog.on_raw_member_remove(NS(user=user, guild_id=guild_id))

    async def test_reset_on_leave(self):
        rp.give(self.store, G, A, 900)
        await self.leave(self.member)                                                    # 꺼져 있으면 그대로
        self.assertEqual(rp.row(self.store, G, A)["exp"], 900)
        await self.options(reset_on_leave=True)
        robot = NS(id=A, bot=True)
        await self.leave(robot)                                                          # 봇은 무시
        self.assertEqual(rp.row(self.store, G, A)["exp"], 900)
        self.bot.allowed_guild = lambda gid: False
        await self.leave(self.member)                                                    # 쓰지 않는 서버는 무시
        self.assertEqual(rp.row(self.store, G, A)["exp"], 900)
        self.bot.allowed_guild = lambda gid: True
        await self.leave(NS(id=A, bot=False))                                            # 캐시에 없어 User로 와도 된다
        me = rp.row(self.store, G, A)
        self.assertEqual((me["exp"], me["com"]), (0, 900))


if __name__ == "__main__":
    unittest.main()
