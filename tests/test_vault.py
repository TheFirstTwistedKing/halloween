"""보관함: 클리어하면 들고 있던 장비·소모품이 들어오고, 다음 입장 때 고른 것을 가져간다. 팔기, 한도, 화면."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import content
import dungeon
import dungeon_service as service
from balance import DEFAULT, VAULT_GEAR_MAX, VAULT_ITEM_MAX, Balance
from cogs.dungeon import run_embed
from cogs.vault import NONE, Vault, VaultSellView, VaultView, sell_embed, vault_embed
from dungeon import ROOMS, Run
from services import Locks
from store import Store, StoreError
from tests.helpers import Scripted, World, sent_embed

G, U = 100, 7
WIN = dict(randoms=[0.99] * 10, ints=[100])        # 클리어 보상 100, 획득권 없음


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 5)       # 입장 물품(열쇠)

    def tearDown(self):
        self.store.close()

    def test_put_take_and_sell(self):
        s = self.store
        s.vault_put(G, U, [("gear", "dagger", 2), ("item", "potion", 3)])
        s.vault_put(G, U, [("gear", "dagger", 1)])
        self.assertEqual(s.vault(G, U), {"gear": {"dagger": 3}, "item": {"potion": 3}})
        self.assertEqual(s.vault(G, 8), {"gear": {}, "item": {}})
        self.assertEqual(s.vault_sell(G, U, [("gear", "dagger", 3), ("item", "potion", 1)], 77), 77)
        self.assertEqual(s.vault(G, U), {"gear": {}, "item": {"potion": 2}})
        with self.assertRaises(StoreError):
            s.vault_sell(G, U, [("item", "potion", 1), ("item", "potion", 5)], 10)
        self.assertEqual((s.vault(G, U)["item"], s.wallet(G, U)), ({"potion": 2}, 77))     # 실패하면 아무것도 바뀌지 않는다

    def test_selling_past_the_wallet_limit_is_refused(self):
        s = self.store
        s.update_settings(G, currency_max=100)
        s.vault_put(G, U, [("item", "potion", 1)])
        with self.assertRaises(StoreError):
            s.vault_sell(G, U, [("item", "potion", 1)], 101)
        self.assertEqual(s.vault(G, U)["item"], {"potion": 1})

    def test_entering_takes_from_the_vault_in_one_go(self):
        s = self.store
        s.vault_put(G, U, [("gear", "dagger", 1)])
        with self.assertRaises(StoreError):
            s.enter_dungeon(G, U, "{}", [("gear", "dagger", 1), ("item", "potion", 1)])
        self.assertIsNone(s.dungeon_run(G, U))
        self.assertEqual(s.vault(G, U)["gear"], {"dagger": 1})                         # 실패하면 꺼내지도 않는다
        self.assertEqual(s.qty(G, U, s.ensure_system_items(G)["key"]), 5)              # 입장 물품도 그대로
        s.enter_dungeon(G, U, "{}", [("gear", "dagger", 1)])
        self.assertEqual(s.vault(G, U)["gear"], {})
        s.enter_dungeon(G, U, "{}", [("gear", "dagger", 1)])                            # 이어서 하는 던전: 꺼내지 않는다

    def test_end_dungeon_fills_the_vault(self):
        s = self.store
        s.enter_dungeon(G, U, "{}")
        s.end_dungeon(G, U, candy=5, vault=[("gear", "club", 1), ("item", "potion", 2)])
        self.assertEqual(s.vault(G, U), {"gear": {"club": 1}, "item": {"potion": 2}})
        self.assertIsNone(s.end_dungeon(G, U, vault=[("gear", "club", 1)]))             # 이미 끝난 던전: 넣지 않는다
        self.assertEqual(s.vault(G, U)["gear"], {"club": 1})

    def test_loadout_reads_safely(self):
        s = self.store
        self.assertEqual(s.loadout(G, U), {"weapon": None, "shield": None, "armor": None, "items": []})
        s.set_loadout(G, U, {"weapon": "dagger", "armor": 5, "items": ["potion", 3]})
        self.assertEqual(s.loadout(G, U), {"weapon": "dagger", "shield": None, "armor": None, "items": ["potion"]})
        with s.db:
            s.db.execute("UPDATE vault_loadout SET data='broken'")
        self.assertEqual(s.loadout(G, U)["items"], [])

    def test_an_old_database_gets_the_new_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "old.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE dungeons(guild INTEGER, user INTEGER, data TEXT NOT NULL, PRIMARY KEY(guild, user))")
            db.execute("INSERT INTO dungeons VALUES(100, 7, '{}')")
            db.commit()
            db.close()
            s = Store(path)
            try:
                self.assertEqual(s.dungeon_run(G, U), "{}")
                s.vault_put(G, U, [("gear", "club", 1)])
                self.assertEqual(s.vault(G, U)["gear"], {"club": 1})
            finally:
                s.close()


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.bal = self.store.balance(G)

    def tearDown(self):
        self.store.close()

    def test_loadout_plan_uses_only_what_is_there(self):
        s, b = self.store, self.bal
        items = [c.key for c in b.consumables][:7]
        s.vault_put(G, U, [("gear", "dagger", 1), ("gear", "shield", 1), *[("item", k, 2) for k in items[:6]]])
        s.set_loadout(G, U, {"weapon": "dagger", "shield": "dagger", "armor": "cloth_armor", "items": [items[0], items[0], "gone", *items[1:7]]})
        gear, picked, take, names = service.loadout_plan(s, G, U, b)
        self.assertEqual(gear, {"weapon": "dagger"})                                  # 방패 칸에 무기 X, 보관함에 없는 갑옷 X
        self.assertEqual(list(picked), items[:5])                                     # 같은 것·없는 것은 건너뛰고 최대 5종
        self.assertEqual(take, [("gear", "dagger", 1), *[("item", k, 1) for k in items[:5]]])
        self.assertEqual(len(names), 6)
        limited = Balance.build({"보관함.소모품": 0})
        self.assertEqual(service.loadout_plan(s, G, U, limited)[1], {})

    def test_vault_fit_takes_equipped_spares_and_items_within_limits(self):
        s, b = self.store, self.bal
        run = dungeon.new_run(bal=b)
        run.weapon, run.spare_weapon, run.shield, run.armor, run.spare_armor = "dagger", "dagger", "shield", "cloth_armor", "gone"
        key = b.consumables[0].key
        run.items = {key: 5, "gone": 3}
        put, kept, lost = service.vault_fit(s, G, U, run, b)
        self.assertEqual(sorted(put), sorted([("gear", "dagger", 2), ("gear", "shield", 1), ("gear", "cloth_armor", 1), ("item", key, 5)]))
        self.assertEqual(lost, [])
        self.assertEqual(len(kept), 4)
        s.vault_put(G, U, [("gear", "club", VAULT_GEAR_MAX - 1), ("item", key, VAULT_ITEM_MAX - 2)])
        put, kept, lost = service.vault_fit(s, G, U, run, b)
        self.assertEqual(sorted(put), [("gear", "dagger", 1), ("item", key, 2)])       # 장비는 한 칸만, 소모품은 2개만 남았다
        self.assertEqual(sorted((n, q) for _e, n, q in lost), sorted([("레이저 커터", 1), ("에너지 실드", 1), ("방진 커버", 1), (b.consumables[0].name, 3)]))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.locks = Store(":memory:"), Locks()
        self.key = self.store.ensure_system_items(G)["key"]
        self.store.add_item(G, U, self.key, 5)
        self.item = DEFAULT.consumables[0].key

    def tearDown(self):
        self.store.close()

    async def ending(self, how, **fields):
        out = await service.enter(self.store, self.locks, G, U)
        run = out.run
        run.floor, run.room = self.store.balance(G).floors, ROOMS
        run.phase, run.kind = ("summit", "summit") if how == "finish" else ("battle", "boss")
        if how != "finish":
            run.enemy = dungeon._spawn(self.store.balance(G).bosses[0], 1, self.store.balance(G))
        for k, v in fields.items():
            setattr(run, k, v)
        self.store.save_dungeon(G, U, run.to_json())
        return await service.act(self.store, self.locks, Scripted(**WIN), G, U, run.rev, how)

    async def test_a_clear_puts_what_you_hold_into_the_vault(self):
        out = await self.ending("finish", weapon="dagger", spare_shield="shield", items={self.item: 2})
        self.assertEqual(out.ended, "clear")
        self.assertEqual(self.store.vault(G, U), {"gear": {"dagger": 1, "shield": 1}, "item": {self.item: 2}})
        self.assertEqual(len(out.reward.vault), 3)
        shown = "".join(f.value for f in run_embed(out.run, out, self.store.balance(G), self.store.currency(G)).fields)
        self.assertIn("보관함에 넣었어요", shown)

    async def test_quitting_or_going_down_keeps_nothing(self):
        out = await self.ending("quit", weapon="dagger", items={self.item: 2})
        self.assertEqual(out.ended, "quit")
        self.assertEqual(self.store.vault(G, U), {"gear": {}, "item": {}})
        out = await self.ending("attack", weapon="dagger", hp=1)
        self.assertEqual(out.ended, "dead")
        self.assertEqual(self.store.vault(G, U), {"gear": {}, "item": {}})

    async def test_the_loadout_comes_along_and_returns_after_a_clear(self):
        self.store.vault_put(G, U, [("gear", "longsword", 1), ("gear", "cloth_armor", 1), ("item", self.item, 3)])
        self.store.set_loadout(G, U, {"weapon": "longsword", "armor": "cloth_armor", "items": [self.item]})
        out = await service.enter(self.store, self.locks, G, U)
        self.assertTrue(out.started)
        self.assertEqual((out.run.weapon, out.run.armor, out.run.items), ("longsword", "cloth_armor", {self.item: 1}))
        self.assertTrue(any("보관함에서 가져왔어요" in e for e in out.run.events))
        self.assertEqual(self.store.vault(G, U), {"gear": {}, "item": {self.item: 2}})
        again = await service.enter(self.store, self.locks, G, U)                     # 이어서 하기: 더 꺼내지 않는다
        self.assertFalse(again.started)
        self.assertFalse(any("보관함에서 가져왔어요" in e for e in again.run.events))
        self.assertEqual(self.store.vault(G, U)["item"], {self.item: 2})
        run = Run.from_json(self.store.dungeon_run(G, U))
        run.floor, run.room, run.phase, run.kind = self.store.balance(G).floors, ROOMS, "summit", "summit"
        self.store.save_dungeon(G, U, run.to_json())
        await service.act(self.store, self.locks, Scripted(**WIN), G, U, run.rev, "finish")
        self.assertEqual(self.store.vault(G, U), {"gear": {"longsword": 1, "cloth_armor": 1}, "item": {self.item: 3}})
        out = await service.enter(self.store, self.locks, G, U)                       # 같은 것을 또 가져간다(고른 것은 남는다)
        self.assertEqual(out.run.weapon, "longsword")


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.item = DEFAULT.consumables[0]
        self.store.vault_put(G, U, [("gear", "dagger", 1), ("gear", "longsword", 2), ("gear", "shield", 1), ("item", self.item.key, 4)])

    async def asyncTearDown(self):
        await self.w.close()

    def selects(self, view):
        return [c for c in view.children if hasattr(c, "options")]

    def labels(self, view):
        return [c.label for c in view.children if getattr(c, "label", None)]

    async def test_command_and_screen(self):
        i = self.w.interaction(uid=U)
        await Vault.vault.callback(Vault(self.bot), i)
        embed, kwargs = sent_embed(i)
        self.assertIn("보관함", embed.title)
        shown = "".join(f.value for f in embed.fields)
        self.assertIn("플라즈마 블레이드", shown)
        self.assertIn("×2", shown)
        view = i.response.send_message.call_args.kwargs["view"]
        placeholders = [s.placeholder for s in self.selects(view)]
        self.assertEqual(len(placeholders), 3)                                         # 무기·방패·소모품(갑옷은 없다)
        self.assertIn("팔기", self.labels(view))
        view.to_components()

    async def test_choosing_what_to_bring(self):
        view = VaultView(self.bot, U, G)
        weapon = self.selects(view)[0]
        i = self.w.interaction(uid=U, data={"values": ["longsword"]})
        await weapon.callback(i)
        self.assertEqual(self.store.loadout(G, U)["weapon"], "longsword")
        embed = i.response.edit_message.call_args.kwargs["embed"]
        self.assertIn("플라즈마 블레이드", next(f.value for f in embed.fields if "가져갈 것" in f.name))
        items = self.selects(VaultView(self.bot, U, G))[2]
        self.assertEqual(items.max_values, 1)                                          # 소모품이 1종뿐
        other = DEFAULT.consumables[1].key
        self.store.vault_put(G, U, [("item", other, 1)])
        self.assertEqual(self.selects(VaultView(self.bot, U, G))[2].max_values, 2)
        self.store.update_settings(G, dungeon={"보관함.소모품": 1})                       # 가져갈 수 있는 수만큼만 고를 수 있다
        self.assertEqual(self.selects(VaultView(self.bot, U, G))[2].max_values, 1)
        self.store.update_settings(G, dungeon={})
        await items.callback(self.w.interaction(uid=U, data={"values": [self.item.key]}))
        self.assertEqual(self.store.loadout(G, U)["items"], [self.item.key])
        await self.selects(VaultView(self.bot, U, G))[0].callback(self.w.interaction(uid=U, data={"values": [NONE]}))
        self.assertIsNone(self.store.loadout(G, U)["weapon"])
        view = VaultView(self.bot, U, G)
        self.assertIn("아무것도 안 가져가기", self.labels(view))
        await view.clear(self.w.interaction(uid=U))
        self.assertEqual(self.store.loadout(G, U), {"weapon": None, "shield": None, "armor": None, "items": []})

    async def test_selling_one_or_all(self):
        view = VaultSellView(self.bot, U, G)
        await view.on_select(self.w.interaction(uid=U, data={"values": ["gear:longsword", f"item:{self.item.key}"]}))
        await view.sell_one(self.w.interaction(uid=U))
        bal = self.store.balance(G)
        one = bal.gear_by_key["longsword"].sell_price + self.item.sell_price
        self.assertEqual(self.store.wallet(G, U), one)
        self.assertEqual(self.store.vault(G, U), {"gear": {"dagger": 1, "longsword": 1, "shield": 1}, "item": {self.item.key: 3}})
        view = VaultSellView(self.bot, U, G)
        await view.on_select(self.w.interaction(uid=U, data={"values": [f"item:{self.item.key}"]}))
        i = self.w.interaction(uid=U)
        await view.sell_all(i)
        self.assertEqual(self.store.wallet(G, U), one + self.item.sell_price * 3)
        self.assertEqual(self.store.vault(G, U)["item"], {})
        self.assertIn("팔고", i.response.edit_message.call_args.kwargs["embed"].description)
        i = self.w.interaction(uid=U)
        await VaultSellView(self.bot, U, G).sell_one(i)                                # 아무것도 안 고름
        self.assertIn("먼저", i.response.edit_message.call_args.kwargs["embed"].description)

    async def test_deleted_gear_is_hidden_and_others_cannot_press(self):
        content.delete(self.store, G, ("gear",), "레이저 커터")
        shown = "".join(f.value for f in vault_embed(self.store, G, U, self.store.balance(G)).fields)
        self.assertNotIn("레이저 커터", shown)
        self.assertNotIn("레이저 커터", sell_embed(self.store, G, U).description)
        other = self.w.interaction(uid=8)
        self.assertFalse(await VaultView(self.bot, U, G).interaction_check(other))


if __name__ == "__main__":
    unittest.main()
