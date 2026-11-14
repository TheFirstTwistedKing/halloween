"""관리자 /보관함지급: 다른 사람이나 자신의 보관함에 장비를 넣어 준다(장비 한도 안에서)."""
import unittest

from balance import VAULT_GEAR_MAX
from cogs.admin import Admin
from store import Store, StoreError
from tests.helpers import World, sent_embed

G = 100
ADMIN, OTHER = 7, 8


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_give_adds_and_respects_the_limit(self):
        s = self.store
        self.assertEqual(s.vault_give(G, OTHER, "dagger", 2), 2)
        self.assertEqual(s.vault_give(G, OTHER, "dagger", 1), 3)
        self.assertEqual(s.vault(G, OTHER)["gear"], {"dagger": 3})
        s.vault_put(G, OTHER, [("gear", "club", VAULT_GEAR_MAX - 4)])
        self.assertEqual(s.vault_give(G, OTHER, "shield", 1), VAULT_GEAR_MAX)          # 딱 한도까지는 된다
        with self.assertRaisesRegex(StoreError, "자리가 모자라요"):
            s.vault_give(G, OTHER, "shield", 1)
        self.assertEqual(s.vault(G, OTHER)["gear"].get("shield"), 1)                    # 거절되면 아무것도 넣지 않는다
        self.assertEqual(s.vault(G, ADMIN)["gear"], {})                                 # 다른 사람의 보관함은 그대로


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Admin(self.bot)
        self.w.new_member(OTHER)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, *args, admin=True, uid=ADMIN, **kwargs):
        i = self.w.interaction(admin=admin, uid=uid)
        await Admin.give_vault_gear.callback(self.cog, i, *args, **kwargs)
        return sent_embed(i)[0].description

    async def test_give_to_others_and_to_myself(self):
        text = await self.run_cmd(self.w.members[OTHER], "레이저 커터", 3)
        self.assertIn("보관함에", text)
        self.assertIn("×3", text)
        self.assertIn(f"3/{VAULT_GEAR_MAX}", text)
        self.assertEqual(self.store.vault(G, OTHER)["gear"], {"dagger": 3})
        me = self.w.members.get(ADMIN) or self.w.new_member(ADMIN)
        await self.run_cmd(me, "에너지 실드")                                            # 나 자신에게도, 개수는 기본 1
        self.assertEqual(self.store.vault(G, ADMIN)["gear"], {"shield": 1})

    async def test_refusals(self):
        self.assertIn("관리자만", await self.run_cmd(self.w.members[OTHER], "레이저 커터", admin=False, uid=OTHER))
        self.assertIn("그런 장비가 없어요", await self.run_cmd(self.w.members[OTHER], "없는 장비"))
        bot_member = self.w.new_member(99)
        bot_member.bot = True
        self.assertIn("봇에게는", await self.run_cmd(bot_member, "레이저 커터"))
        self.store.vault_put(G, OTHER, [("gear", "club", VAULT_GEAR_MAX)])
        self.assertIn("자리가 모자라요", await self.run_cmd(self.w.members[OTHER], "레이저 커터"))
        self.assertNotIn("dagger", self.store.vault(G, OTHER)["gear"])
        self.assertNotIn("dagger", self.store.vault(G, 99)["gear"])

    async def test_given_gear_can_be_displayed_at_the_base(self):
        await self.run_cmd(self.w.members[OTHER], "플라즈마 블레이드")
        self.store.set_display(G, OTHER, "weapon", "longsword")
        self.assertEqual(self.store.base(G, OTHER), {"weapon": "longsword"})

    async def test_autocomplete_lists_gear(self):
        names = [c.value for c in await self.cog.give_gear_autocomplete(self.w.interaction(admin=True), "커터")]
        self.assertIn("레이저 커터", names)


if __name__ == "__main__":
    unittest.main()
