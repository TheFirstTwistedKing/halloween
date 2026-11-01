"""자동완성 전수 점검: 자동완성이 걸린 모든 옵션을 실제 상호작용 경로(tree._call)로 돌려서,
응답이 나가는지(오류 없이)와 디스코드 규칙(선택지 25개 이하, 이름·값 1~100자)을 지키는지 확인한다.
규칙을 어기면 디스코드는 선택지를 통째로 버려서 사용자 눈에는 '자동완성이 안 뜨는' 것으로 보인다."""
import asyncio
import sys
import unittest
from unittest import mock
from unittest.mock import AsyncMock

import discord
from discord import app_commands

from main import HalloweenBot
from store import Store

ADMIN = str(discord.Permissions(administrator=True).value)
G = 100
LONG_EMOJI = "<a:" + "e" * 32 + ":" + "1" * 25 + ">"          # 서버 이모지로 가능한 가장 긴 모양
EXTENSIONS = ("cogs.economy", "cogs.admin", "cogs.shop", "cogs.dungeon", "cogs.dungeon_admin", "cogs.rules_admin", "cogs.content_admin")


class SweepBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 확장을 불러오고 내리면 sys.modules의 cogs.* 모듈이 새 객체로 바뀐다. 다른 시험 파일이 먼저 가져온 모듈과 어긋나지 않게 끝나면 되돌린다.
        self._modules = {k: v for k, v in sys.modules.items() if k == "cogs" or k.startswith("cogs.")}
        self.store = Store(":memory:")
        self.bot = HalloweenBot(self.store)
        self.state = self.bot._connection
        self.state.user = discord.ClientUser(state=self.state, data={"id": "999", "username": "bot", "discriminator": "0", "avatar": None, "bot": True})
        self.bot._ready = asyncio.Event()
        for extension in EXTENSIONS:
            await self.bot.load_extension(extension)
        patcher = mock.patch.object(discord.InteractionResponse, "autocomplete", new_callable=AsyncMock)
        self.answer = patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()
        for name in [k for k in sys.modules if k == "cogs" or k.startswith("cogs.")]:
            del sys.modules[name]
        sys.modules.update(self._modules)

    def targets(self):
        """(명령어 이름 목록, 옵션 이름) — 자동완성이 걸린 모든 옵션."""
        out = []
        for command in self.bot.tree.walk_commands():
            if not isinstance(command, app_commands.Command):
                continue
            for parameter in command.parameters:
                if parameter.autocomplete:
                    out.append((command.qualified_name.split(" "), parameter.display_name))
        return out

    def interaction(self, path, option, text):
        focused = {"name": option, "type": 3, "value": text, "focused": True}
        nested = [focused]
        for name in reversed(path[1:]):                              # 묶음 명령어(/몬스터 수정 …)는 한 겹씩 감싼다.
            nested = [{"name": name, "type": 1, "options": nested}]
        payload = {"id": "1", "application_id": "2", "type": 4, "token": "tok", "version": 1, "guild_id": str(G), "channel_id": "5",
                   "locale": "ko", "guild_locale": "ko", "app_permissions": "0", "attachment_size_limit": 10485760, "entitlements": [],
                   "authorizing_integration_owners": {}, "context": 0,
                   "data": {"id": "3", "name": path[0], "type": 1, "options": nested},
                   "member": {"user": {"id": "20", "username": "u", "discriminator": "0", "avatar": None}, "roles": [],
                              "joined_at": "2026-01-01T00:00:00+00:00", "deaf": False, "mute": False, "flags": 0, "permissions": ADMIN}}
        return discord.Interaction(data=payload, state=self.state)

    async def ask(self, path, option, text):
        self.answer.reset_mock()
        await self.bot.tree._call(self.interaction(path, option, text))
        self.assertTrue(self.answer.await_count >= 1, f"{' '.join(path)} · {option} · '{text}': 자동완성 응답이 나가지 않았어요")
        call = self.answer.await_args
        return list(call.args[0] if call.args else call.kwargs["choices"])

    def check(self, label, choices):
        self.assertLessEqual(len(choices), 25, label)
        for choice in choices:
            self.assertTrue(1 <= len(choice.name) <= 100, f"{label}: 이름 길이 {len(choice.name)} {choice.name!r}")
            self.assertTrue(1 <= len(str(choice.value)) <= 100, f"{label}: 값 길이 {choice.value!r}")

    async def sweep(self):
        found = self.targets()
        self.assertGreaterEqual(len(found), 21)
        for path, option in found:
            for text in ("", "열", "a", "일반", "💎"):
                label = f"{' '.join(path)} · {option} · '{text}'"
                self.check(label, await self.ask(path, option, text))
        return found


class FreshServerTests(SweepBase):
    async def test_every_autocomplete_answers_on_a_brand_new_server(self):
        found = await self.sweep()
        names = {" ".join(p) + ":" + o for p, o in found}
        for expected in ("물품수정:물품", "물품삭제:물품", "상점진열:물품", "물품지급:물품", "던전입장물품:물품", "던전설정:항목", "던전설정초기화:항목",
                         "몬스터 수정:대상", "장비 삭제:대상", "소모품 수정:대상", "패키지결과 수정:결과", "패키지결과 추가:물품", "던전보상 수정:보상"):
            self.assertIn(expected, names)

    async def test_the_item_lists_are_not_empty_on_a_new_server(self):
        """처음 쓰는 서버에서도 기본 물품(열쇠·쓰레기·패키지 물품)이 자동완성에 나온다."""
        for path, option in (("물품수정", "물품"), ("상점진열", "물품"), ("물품지급", "물품")):
            choices = await self.ask([path], option, "")
            self.assertIn("열쇠", [c.value for c in choices], path)


class BusyServerTests(SweepBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        store = self.store
        store.ensure_defaults(G)
        for n in range(40):                                                  # 25개를 넘는 물품, 가장 긴 이름과 이모지
            store.create_item(G, f"{n:02}" + "가" * 28, emoji=LONG_EMOJI if n % 2 else "🎃", sell_price=5 if n < 10 else 0, sellable=n < 10)
        ids = [r["id"] for r in store.items(G)]
        table = [{"id": f"t{n}", "kind": "item", "item": ids[n], "weight": 1, "amount": 1, "emoji": LONG_EMOJI, "title": "나" * 30} for n in range(20)]
        store.update_settings(G, package_table=table,
                              clear_rewards=[{"id": f"r{n}", "item": ids[n], "amount": 1, "chance": 10} for n in range(10)])

    async def test_every_autocomplete_obeys_discords_limits_on_a_busy_server(self):
        await self.sweep()

    async def test_lists_are_capped_at_25_and_typing_narrows_them(self):
        choices = await self.ask(["물품수정"], "물품", "")
        self.assertEqual(len(choices), 25)
        narrowed = await self.ask(["물품수정"], "물품", "39")
        self.assertEqual([c.value[:2] for c in narrowed], ["39"])


if __name__ == "__main__":
    unittest.main()
