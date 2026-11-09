"""/장비지급: 관리자가 진행 중인 던전에 있는 사람에게 장비를 준다(상자에서 나온 것처럼 장착/예비/버리기를 고른다)."""
import json
import unittest

import balance
import dungeon
import dungeon_service as service
from cogs.admin import Admin
from cogs.dungeon import DungeonView
from dungeon import Run
from services import Locks
from store import Store
from tests.helpers import Scripted, World, sent_embed

G, U, OTHER = 100, 7, 8


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 2)

    def tearDown(self):
        self.store.close()

    async def enter(self, **changes):
        out = await service.enter(self.store, self.locks, G, U)
        if changes:
            data = json.loads(out.run.to_json())
            data.update(changes)
            self.store.save_dungeon(G, U, json.dumps(data))
        return Run.from_json(self.store.dungeon_run(G, U))

    def saved(self):
        return Run.from_json(self.store.dungeon_run(G, U))

    async def test_the_gear_waits_for_the_player_to_decide(self):
        before = await self.enter(phase="cleared", room=3)
        out = await service.grant_gear(self.store, self.locks, G, U, "longsword")
        self.assertTrue(out.ok)
        run = self.saved()
        self.assertEqual((run.pending_gear, run.loot), ("longsword", ["플라즈마 블레이드"]))
        self.assertGreater(run.rev, before.rev)                                           # 예전 화면은 지난 화면이 된다
        self.assertFalse(run.can_advance)                                                 # 정하기 전에는 앞으로 못 간다
        self.assertIn("관리자가 장비를 줬어요", "\n".join(run.events))
        self.assertIn("플라즈마 블레이드", "\n".join(run.events))

    async def test_the_player_can_equip_stash_or_discard_it_like_a_chest_drop(self):
        for action, check in (("equip", lambda r: (r.weapon, r.pending_gear) == ("longsword", None)),
                              ("stash", lambda r: (r.spare_weapon, r.pending_gear) == ("longsword", None)),
                              ("discard", lambda r: (r.weapon, r.spare_weapon, r.pending_gear) == (None, None, None))):
            store = Store(":memory:")
            store.add_item(G, U, store.ensure_system_items(G)["key"], 1)
            out = await service.enter(store, self.locks, G, U)
            data = json.loads(out.run.to_json())
            data.update(phase="cleared", room=3)
            store.save_dungeon(G, U, json.dumps(data))
            await service.grant_gear(store, self.locks, G, U, "longsword")
            rev = Run.from_json(store.dungeon_run(G, U)).rev
            done = await service.act(store, self.locks, Scripted(), G, U, rev, action)
            self.assertTrue(done.ok, action)
            self.assertTrue(check(Run.from_json(store.dungeon_run(G, U))), action)
            store.close()

    async def test_the_new_screen_shows_the_equip_buttons(self):
        await self.enter(phase="cleared", room=3)
        await service.grant_gear(self.store, self.locks, G, U, "shield")
        view = DungeonView(object(), U, self.saved(), self.store.balance(G))
        labels = [getattr(c, "label", None) for c in view.children]
        self.assertIn("장착", labels)
        self.assertIn("버리기", labels)
        self.assertNotIn("앞으로", labels)

    async def test_it_works_on_an_empty_room_and_a_cleared_boss_room_too(self):
        for changes in (dict(phase="empty", kind="empty", room=2), dict(phase="chest_done", kind="chest", room=3),
                        dict(phase="cleared", kind="boss", room=10)):
            store = Store(":memory:")
            store.add_item(G, U, store.ensure_system_items(G)["key"], 1)
            out = await service.enter(store, self.locks, G, U)
            data = json.loads(out.run.to_json())
            data.update(changes)
            store.save_dungeon(G, U, json.dumps(data))
            result = await service.grant_gear(store, self.locks, G, U, "crossbow")
            self.assertTrue(result.ok, changes)
            store.close()

    async def test_it_is_refused_when_it_would_clash_with_something_in_progress(self):
        await self.enter(phase="battle", kind="monster", room=2, enemy=dungeon._spawn(balance.MONSTERS[0], 1, balance.DEFAULT))
        out = await service.grant_gear(self.store, self.locks, G, U, "longsword")
        self.assertFalse(out.ok)
        self.assertIn("싸우는 중", out.text)
        self.assertIsNone(self.saved().pending_gear)
        data = json.loads(self.store.dungeon_run(G, U))
        data.update(phase="chest", kind="chest", enemy=None)
        self.store.save_dungeon(G, U, json.dumps(data))
        out = await service.grant_gear(self.store, self.locks, G, U, "longsword")
        self.assertIn("상자", out.text)
        data.update(phase="chest_done", pending_gear="club")                              # 상자에서 나온 장비를 아직 못 정했다
        self.store.save_dungeon(G, U, json.dumps(data))
        out = await service.grant_gear(self.store, self.locks, G, U, "longsword")
        self.assertFalse(out.ok)
        self.assertIn("정하지 않은 장비", out.text)
        self.assertEqual(self.saved().pending_gear, "club")                              # 덮어쓰지 않는다

    async def test_nobody_in_a_dungeon_and_unknown_gear(self):
        out = await service.grant_gear(self.store, self.locks, G, U, "longsword")
        self.assertFalse(out.ok)
        self.assertIn("던전에 들어가 있지 않아요", out.text)
        await self.enter(phase="cleared", room=3)
        out = await service.grant_gear(self.store, self.locks, G, U, "no_such")
        self.assertFalse(out.ok)
        self.assertIn("그런 장비가 없어요", out.text)
        self.assertIsNone(self.saved().pending_gear)

    async def test_a_gift_is_not_sellable_by_accident_and_a_sale_does_not_clash(self):
        await self.enter(phase="cleared", room=3)
        await service.grant_gear(self.store, self.locks, G, U, "gun")
        run = self.saved()
        entries = {e["id"]: e for e in dungeon.sellable(run, self.store.balance(G))}
        self.assertIn("pending", entries)                                                  # 상자 장비처럼 바로 팔 수도 있다
        sold = await service.sell(self.store, self.locks, G, U, run.rev, ["pending"], "all")
        self.assertTrue(sold.ok)
        self.assertIsNone(self.saved().pending_gear)

    async def test_quick_play_decides_by_itself(self):
        run = await self.enter(phase="cleared", room=3)
        await service.set_auto(self.store, self.locks, G, U, run.rev, True)
        await service.grant_gear(self.store, self.locks, G, U, "gun")
        step = await service.auto_step(self.store, self.locks, Scripted(), G, U)
        self.assertTrue(step.ok)
        self.assertEqual((self.saved().weapon, self.saved().pending_gear), ("gun", None))      # 맨손보다 훨씬 강해서 장착한다


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Admin(self.bot)
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        out = await service.enter(self.store, self.bot.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(phase="cleared", room=3)
        self.store.save_dungeon(G, U, json.dumps(data))

    async def asyncTearDown(self):
        await self.w.close()

    async def call(self, member, gear, admin=True):
        i = self.w.interaction(admin=admin)
        await Admin.give_gear.callback(self.cog, i, member, gear)
        embed, kwargs = sent_embed(i)
        return embed.description, kwargs

    async def test_giving_gear_tells_everyone_and_mentions_the_player(self):
        text, kwargs = await self.call(self.w.member, "플라즈마 블레이드")
        self.assertIn("<@7>", text)
        self.assertIn("플라즈마 블레이드", text)
        self.assertIn("/던전", text)
        self.assertFalse(kwargs["ephemeral"])
        self.assertEqual(self.store.dungeon_run(G, U) is not None and Run.from_json(self.store.dungeon_run(G, U)).pending_gear, "longsword")

    async def test_problems_are_explained_and_nothing_changes(self):
        for member, gear, expect in ((self.w.new_member(OTHER), "플라즈마 블레이드", "던전에 들어가 있지 않아요"), (self.w.member, "없는장비", "그런 장비가 없어요")):
            text, _ = await self.call(member, gear)
            self.assertIn(expect, text)
        bot_member = self.w.new_member(9)
        bot_member.bot = True
        text, _ = await self.call(bot_member, "플라즈마 블레이드")
        self.assertIn("봇에게는", text)
        self.assertIsNone(Run.from_json(self.store.dungeon_run(G, U)).pending_gear)

    async def test_admin_only(self):
        text, _ = await self.call(self.w.member, "플라즈마 블레이드", admin=False)
        self.assertIn("관리자만", text)
        self.assertIsNone(Run.from_json(self.store.dungeon_run(G, U)).pending_gear)

    async def test_custom_gear_can_be_given_and_autocomplete_lists_gear(self):
        import content
        content.add(self.store, G, "gear", dict(name="특별한 검", emoji="🗡️", slot="weapon", tier="전설", weight=0, attack=999))
        text, _ = await self.call(self.w.member, "특별한 검")
        self.assertIn("특별한 검", text)
        i = self.w.interaction(admin=True)
        names = [c.value for c in await self.cog.give_gear_autocomplete(i, "검")]
        self.assertIn("특별한 검", names)
        self.assertNotIn("에너지 실드", names)
        self.assertEqual([c.value for c in await self.cog.give_gear_autocomplete(i, "실드")], ["에너지 실드"])
        self.assertEqual([c.value for c in await self.cog.give_gear_autocomplete(i, "방열")], ["구리 방열판"])

    def test_options_are_declared(self):
        names = {p.name: p.type.name for p in Admin.give_gear.parameters}
        self.assertEqual(names, {"target": "user", "gear": "string"})
