"""던전은 개인 화면이다: /던전의 응답은 본인에게만 보이고, 그 메시지를 고쳐 쓰는 층·장비·판매 화면도 같은 메시지라서 계속 본인에게만 보인다."""
import random
import unittest

import dungeon
from cogs.dungeon import Dungeon, DungeonView, open_equip, open_sell
from tests.helpers import World, sent_embed

G, U = 100, 7


class DungeonIsPrivateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World(random.Random(3))
        self.store, self.bot = self.w.store, self.w.bot
        self.w.channel(400)
        self.store.ensure_defaults(G)
        self.key = self.store.ensure_system_items(G)["key"]

    async def asyncTearDown(self):
        await self.w.close()

    async def enter(self):
        i = self.w.interaction()
        await Dungeon.enter.callback(Dungeon(self.bot), i)
        return i, i.response.send_message.await_args.kwargs

    async def test_entering_shows_the_dungeon_only_to_the_player(self):
        self.store.add_item(G, U, self.key, 1)
        i, kwargs = await self.enter()
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("던전", kwargs["embed"].title)
        self.assertIsInstance(kwargs["view"], DungeonView)

    async def test_the_cannot_enter_notice_is_private_too(self):
        i, kwargs = await self.enter()                                         # 열쇠가 없다
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("열쇠", sent_embed(i)[0].description)
        self.assertIsNone(self.store.dungeon_run(G, U))

    async def test_resuming_an_unfinished_dungeon_is_private_too(self):
        self.store.add_item(G, U, self.key, 1)
        await self.enter()
        i, kwargs = await self.enter()                                         # 두 번째 /던전 = 이어하기
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("이어서", "\n".join(kwargs["embed"].description.splitlines()))

    async def test_pressing_buttons_edits_that_same_private_message_and_never_posts_a_public_one(self):
        """버튼은 그 (비공개) 메시지를 고쳐 쓰므로 층이 오르고 장비·판매 화면으로 바뀌어도 계속 본인에게만 보인다."""
        self.store.add_item(G, U, self.key, 1)
        _, kwargs = await self.enter()
        view = kwargs["view"]
        for step in range(80):
            run = dungeon.Run.from_json(self.store.dungeon_run(G, U))
            btn = lambda label: next((c for c in view.children if getattr(c, "label", None) == label), None)
            if run.phase == "battle":
                pick = btn("공격")
            elif run.phase == "chest":
                pick = btn("상자 열기")
            elif run.pending_gear:
                pick = btn("예비에 넣기") or btn("장착")
            elif step % 9 == 0 and btn("장비"):
                s = self.w.interaction()
                await btn("장비").callback(s)                                   # 장비 화면
                s.response.send_message.assert_not_awaited()
                view = s.response.edit_message.await_args.kwargs["view"]
                back = self.w.interaction()
                await next(c for c in view.children if getattr(c, "label", "") == "돌아가기").callback(back)
                view = back.response.edit_message.await_args.kwargs["view"]
                continue
            elif step % 11 == 0 and btn("판매"):
                s = self.w.interaction()
                await btn("판매").callback(s)                                   # 판매 화면
                s.response.send_message.assert_not_awaited()
                view = s.response.edit_message.await_args.kwargs["view"]
                back = self.w.interaction()
                await next(c for c in view.children if getattr(c, "label", "") == "돌아가기").callback(back)
                view = back.response.edit_message.await_args.kwargs["view"]
                continue
            else:
                pick = btn("앞으로") or btn("출발")
            press = self.w.interaction()
            await pick.callback(press)
            press.response.send_message.assert_not_awaited()                    # 새 공개 메시지를 만들지 않고 이 메시지를 고친다.
            view = press.response.edit_message.await_args.kwargs["view"]
            if view is None:
                break

    async def test_the_admin_dungeon_commands_are_not_affected(self):
        """/던전설정 같은 관리자 명령어는 예전 규칙(모두가 볼 수 있음) 그대로다."""
        from cogs.dungeon_admin import DungeonAdmin
        i = self.w.interaction(admin=True)
        await DungeonAdmin.show.callback(DungeonAdmin(self.bot), i, None)
        self.assertFalse(i.response.send_message.await_args.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
