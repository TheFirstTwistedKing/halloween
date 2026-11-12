"""공개 화면과 조작 제한, 가방에서 팔기.

- 슬래시 명령어의 응답은 전부 모두가 볼 수 있다(패키지와 상점 패널 버튼의 응답은 제외).
- 명령어로 연 화면은 연 사람만 조작할 수 있고, 다른 사람이 누르면 그 사람에게만 안내가 간다.
- 가방에서 팔 수 있는 물품을 골라 수량을 적으면 바로 팔린다."""
import ast
import importlib
import inspect
import unittest
from pathlib import Path

import discord

import dungeon
from cogs.admin import Admin, ConfirmRoleGrant
from cogs.dungeon import ConfirmQuit, DungeonView, SellView
from cogs.dungeon_admin import ConfirmResetAll
from cogs.economy import Economy
from cogs.rules_admin import RulesAdmin
from cogs.shop import ConfirmDelete, Shop
from confirm import Confirm
from owner import owner_only
from panel import ShopPanelView
from packages import PackageView
from store import QTY_MAX
from tests.helpers import Scripted, World, sent_embed
from views import BagView, DetailView, QuantityModal, send_bag

ROOT = Path(__file__).resolve().parent.parent
COMMAND_MODULES = ["access", "cogs/admin", "cogs/announce", "cogs/elements", "cogs/boss_patterns", "cogs/weapon_skills", "cogs/final_boss", "cogs/vault", "cogs/base", "cogs/content_admin", "cogs/dungeon_admin", "cogs/economy", "cogs/rules_admin", "cogs/shop"]
G, U, OTHER = 100, 7, 8


class PublicByDefaultTests(unittest.TestCase):
    def test_no_command_module_answers_privately(self):
        """명령어 모듈에는 본인에게만 보이는 응답(ephemeral=True)이 하나도 없다."""
        for name in COMMAND_MODULES:
            tree = ast.parse((ROOT / f"{name}.py").read_text(encoding="utf-8"))
            private = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg == "ephemeral"
                       and isinstance(node.value, ast.Constant) and node.value.value is True]
            self.assertEqual(private, [], f"{name}.py {private}")

    def test_only_the_dungeons_in_screen_buttons_and_the_shop_flows_and_packages_stay_private(self):
        """남아 있는 비공개 응답은 버튼·선택창을 눌렀을 때의 개인 안내(던전·연금술 화면 안), 공용 상점 패널 쪽, 패키지뿐이다."""
        allowed = {"cogs/dungeon.py", "cogs/alchemy.py", "packages.py", "panel.py", "views.py", "owner.py", "bulk.py"}
        found = set()
        for path in list(ROOT.glob("*.py")) + list((ROOT / "cogs").glob("*.py")):
            if "ephemeral=True" in path.read_text(encoding="utf-8"):
                found.add(str(path.relative_to(ROOT)))
        self.assertEqual(found - allowed, set(), found)
        self.assertIn("packages.py", found)                                         # 패키지는 그대로다.

    def test_every_screen_a_command_opens_is_locked_to_who_opened_it(self):
        open_to_all = {ShopPanelView, PackageView}                                   # 공용 패널과 패키지: 누구나 누른다(각자 자기 몫).
        views = []
        for module in ("confirm", "views", "panel", "packages", "paging", "bulk", "cogs.admin", "cogs.dungeon", "cogs.dungeon_admin", "cogs.shop", "cogs.vault", "cogs.base"):
            mod = importlib.import_module(module)
            views += [cls for _, cls in inspect.getmembers(mod, inspect.isclass)
                      if issubclass(cls, discord.ui.View) and cls.__module__ == mod.__name__]
        self.assertGreaterEqual(len(views), 9)
        for cls in views:
            locked = cls.interaction_check is not discord.ui.View.interaction_check
            self.assertEqual(locked, cls not in open_to_all, cls.__name__)


class FakeWorld(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World(Scripted())
        self.store, self.bot = self.w.store, self.w.bot
        self.w.channel(400)
        self.w.new_member(OTHER)
        self.ids = self.store.ensure_system_items(G)

    async def asyncTearDown(self):
        await self.w.close()


class PublicCommandTests(FakeWorld):
    async def test_sample_commands_answer_publicly_even_when_refused(self):
        i = self.w.interaction(admin=False)
        await Economy.bag.callback(Economy(self.bot), i)
        i2 = self.w.interaction(admin=False)
        await Economy.help.callback(Economy(self.bot), i2)
        i3 = self.w.interaction(admin=False)
        await Admin.add_channel.callback(Admin(self.bot), i3, self.w.channels[400])           # 관리자가 아니라서 거절
        i4 = self.w.interaction(admin=True)
        await Shop.list_items.callback(Shop(self.bot), i4)
        i5 = self.w.interaction(admin=True)
        await RulesAdmin.package_list.callback(RulesAdmin(self.bot), i5)
        for number, interaction in enumerate((i, i2, i3, i4, i5), 1):
            self.assertFalse(interaction.response.send_message.await_args.kwargs["ephemeral"], number)
        self.assertIn("관리자만", sent_embed(i3)[0].description)

    async def test_a_command_that_defers_also_answers_publicly(self):
        i = self.w.interaction(admin=True)
        await Shop.register_shop.callback(Shop(self.bot), i, self.w.channels[400])
        i.response.defer.assert_awaited_once_with(ephemeral=False)

    async def test_the_dungeon_screen_is_private_to_the_player_and_still_locked_to_them(self):
        """던전은 개인 화면이다: 본인에게만 보이고(비공개), 혹시 보이더라도 다른 사람은 조작할 수 없다."""
        from cogs.dungeon import Dungeon
        self.store.add_item(G, U, self.ids["key"], 1)
        i = self.w.interaction()
        await Dungeon.enter.callback(Dungeon(self.bot), i)
        kwargs = i.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        view = kwargs["view"]
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=OTHER)))
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)))


class OwnerOnlyTests(FakeWorld):
    async def test_the_owner_passes_silently(self):
        i = self.w.interaction(uid=U)
        self.assertTrue(await owner_only(i, U))
        i.response.send_message.assert_not_awaited()

    async def test_someone_else_is_told_privately_and_nothing_runs(self):
        i = self.w.interaction(uid=OTHER)
        self.assertFalse(await owner_only(i, U))
        embed, kwargs = sent_embed(i)
        self.assertTrue(kwargs["ephemeral"])                                         # 안내는 누른 사람에게만 보인다.
        self.assertIn("연 사람만", embed.description)

    async def test_every_locked_screen_turns_other_people_away_with_the_notice(self):
        run = dungeon.new_run()
        run.phase, run.room = "empty", 1
        run.items = {"candy": 1}
        screens = {
            "던전": DungeonView(self.bot, U, run, self.store.balance(G)),
            "던전 판매": SellView(self.bot, U, run, self.store.balance(G), self.store.currency(G)),
            "나가기 확인": ConfirmQuit(self.bot, U, 0),
            "확인 버튼": Confirm(U, lambda: "x"),
            "역할 재화": ConfirmRoleGrant(self.bot, U, "역할", [1], 1),
            "던전 설정 초기화": ConfirmResetAll(self.bot, U),
            "물품 삭제": ConfirmDelete(self.bot, U, 1, "물품"),
            "물품 상세": DetailView(self.bot, self.ids["key"], self.w.member),
            "가방": BagView(self.bot, self.w.member),
        }
        for name, view in screens.items():
            stranger = self.w.interaction(uid=OTHER)
            self.assertFalse(await view.interaction_check(stranger), name)
            self.assertIn("연 사람만", sent_embed(stranger)[0].description, name)
            self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)), name)

    async def test_a_strangers_press_does_not_change_the_game(self):
        self.store.add_item(G, U, self.ids["key"], 1)
        from cogs.dungeon import Dungeon
        i = self.w.interaction()
        await Dungeon.enter.callback(Dungeon(self.bot), i)
        view = i.response.send_message.await_args.kwargs["view"]
        before = self.store.dungeon_run(G, U)
        stranger = self.w.interaction(uid=OTHER)
        self.assertFalse(await view.interaction_check(stranger))                      # discord.py는 False면 버튼 콜백을 부르지 않는다.
        self.assertEqual(self.store.dungeon_run(G, U), before)
        self.assertIsNone(self.store.dungeon_run(G, OTHER))


class PackageStaysOpenTests(FakeWorld):
    async def test_anyone_can_press_the_package_and_still_gets_a_private_result(self):
        from packages import open_package
        from tests.helpers import pin_first_table
        pin_first_table(self.store)
        import time
        self.store.create_package(G, 400, 9001, time.time() + 600, "all")
        for uid in (U, OTHER):
            self.bot.rng.randoms.append(0.0)
            i = self.w.interaction(uid=uid, message_id=9001)
            await open_package(i)
            self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"], uid)       # 결과는 뜯은 사람에게만
        self.assertTrue(await PackageView().interaction_check(self.w.interaction(uid=OTHER)))   # 아무나 누를 수 있다.


class BagSellTests(FakeWorld):
    def give(self, name, qty, **fields):
        item = self.store.item_by_name(G, name) or self.store.create_item(G, name, **fields)
        self.store.add_item(G, U, item["id"], qty)
        return item

    async def open_bag(self):
        i = self.w.interaction()
        await send_bag(i)
        return i

    def select(self, view):
        return next(c for c in view.children if isinstance(c, discord.ui.Select))

    async def sell_through(self, view, item_id, text):
        """선택창에서 물품을 고르고(입력창이 열린다), 수량을 적어 제출한다. (입력창, 제출 상호작용)을 돌려준다."""
        pick = self.w.interaction(data={"values": [str(item_id)]})
        await self.select(view).callback(pick)
        modal = pick.response.send_modal.await_args.args[0]
        modal.amount._value = text
        submit = self.w.interaction()
        await modal.on_submit(submit)
        return modal, submit

    async def test_the_selector_lists_only_sellable_items_with_counts_and_prices(self):
        self.give("구형 CPU", 3, sell_price=100, sellable=True)
        self.give("깨진 디스플레이 파편", 2, sell_price=-150, sellable=True)
        self.give("장식품", 5)                                                        # 팔 수 없는 물품은 안 나온다.
        i = await self.open_bag()
        kwargs = i.response.send_message.await_args.kwargs
        self.assertFalse(kwargs["ephemeral"])
        options = self.select(kwargs["view"]).options
        self.assertEqual([o.label for o in options], ["구형 CPU ×3", "깨진 디스플레이 파편 ×2"])
        self.assertEqual([o.description for o in options], ["개당 판매 💾 100", "개당 판매 💾 -150"])
        self.assertNotIn("장식품", [o.label for o in options])

    async def test_no_selector_when_nothing_can_be_sold(self):
        self.give("장식품", 1)
        i = await self.open_bag()
        self.assertNotIn("view", i.response.send_message.await_args.kwargs)

    async def test_picking_opens_a_quantity_box_for_that_item(self):
        hat = self.give("구형 CPU", 3, sell_price=100, sellable=True)
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        pick = self.w.interaction(data={"values": [str(hat["id"])]})
        await self.select(view).callback(pick)
        modal = pick.response.send_modal.await_args.args[0]
        self.assertIsInstance(modal, QuantityModal)
        self.assertEqual((modal.item_id, modal.action, modal.bag, modal.title), (hat["id"], "sell", True, "구형 CPU 판매"))

    async def test_selling_pays_removes_the_items_and_redraws_the_same_bag_message(self):
        hat = self.give("구형 CPU", 3, sell_price=100, sellable=True)
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        _, submit = await self.sell_through(view, hat["id"], "2")
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, hat["id"])), (200, 1))
        submit.response.send_message.assert_not_awaited()                              # 새 메시지가 아니라
        kwargs = submit.response.edit_message.await_args.kwargs                         # 그 가방 메시지를 고친다.
        self.assertIn("✅", kwargs["embed"].description)
        self.assertIn("💾 200을 받았어요", kwargs["embed"].description)
        fields = {f.name: f.value for f in kwargs["embed"].fields}
        self.assertIn("**200**", fields["💾 RAM"])
        self.assertIn("구형 CPU ×1", fields["🎒 물품"])
        self.assertEqual(self.select(kwargs["view"]).options[0].label, "구형 CPU ×1")

    async def test_selling_the_last_one_removes_the_selector(self):
        hat = self.give("구형 CPU", 1, sell_price=100, sellable=True)
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        _, submit = await self.sell_through(view, hat["id"], "1")
        self.assertIsNone(submit.response.edit_message.await_args.kwargs["view"])
        self.assertEqual(self.store.qty(G, U, hat["id"]), 0)

    async def test_a_negative_item_charges_you_and_refuses_when_you_cannot_pay(self):
        glass = self.give("깨진 디스플레이 파편", 2, sell_price=-150, sellable=True)
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        _, submit = await self.sell_through(view, glass["id"], "1")                     # 지갑이 비어 있다
        embed = submit.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("❌", embed.description)
        self.assertIn("모자라서", embed.description)
        self.assertEqual(self.store.qty(G, U, glass["id"]), 2)
        self.store.add_candy(G, U, 500)
        _, submit = await self.sell_through(view, glass["id"], "2")
        self.assertIn("💾 300을 냈어요", submit.response.edit_message.await_args.kwargs["embed"].description)
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, glass["id"])), (200, 0))

    async def test_the_price_range_uses_the_bots_dice(self):
        bottle = self.give("수상한 병", 1, sell_price=-300, sell_max=300, sellable=True)
        self.store.add_candy(G, U, 1000)
        self.bot.rng = type("R", (), {"randint": lambda s, a, b: 123})()
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        await self.sell_through(view, bottle["id"], "1")
        self.assertEqual(self.store.wallet(G, U), 1123)

    async def test_bad_quantities_and_vanished_items_are_reported_not_applied(self):
        hat = self.give("구형 CPU", 2, sell_price=100, sellable=True)
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        for text in ("abc", "0", "-1", "1.5", str(QTY_MAX + 1)):
            _, submit = await self.sell_through(view, hat["id"], text)
            reply = submit.response.send_message.await_args if submit.response.send_message.await_args else submit.response.edit_message.await_args
            self.assertIsNotNone(reply, text)
        self.assertEqual((self.store.wallet(G, U), self.store.qty(G, U, hat["id"])), (0, 2))
        _, submit = await self.sell_through(view, hat["id"], "5")                       # 가진 것보다 많이
        self.assertIn("모자라요", submit.response.edit_message.await_args.kwargs["embed"].description)
        self.assertEqual(self.store.qty(G, U, hat["id"]), 2)
        self.store.update_item(G, hat["id"], sellable=False, sell_price=100)             # 그 사이 판매가 꺼졌다
        pick = self.w.interaction(data={"values": [str(hat["id"])]})
        await self.select(view).callback(pick)
        pick.response.send_modal.assert_not_awaited()
        self.assertIn("팔 수 없는", sent_embed(pick)[0].description)

    async def test_the_panels_bag_button_gives_a_private_bag_with_the_same_selector(self):
        self.give("구형 CPU", 1, sell_price=100, sellable=True)
        click = self.w.interaction()
        await ShopPanelView().on_bag(click)
        kwargs = click.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(len(self.select(kwargs["view"]).options), 1)

    async def test_the_selector_is_limited_to_25_options(self):
        with self.store.db:                                                            # 상점 25칸 한도를 거치지 않고 30개를 직접 넣는다.
            for n in range(30):
                item_id = self.store.db.execute("INSERT INTO items(guild,name,emoji,kind,sell_price,sellable) VALUES(?,?,?,?,?,?)",
                                                (G, f"물건{n:02}", "⚙️", "item", 1, 1)).lastrowid
                self.store.db.execute("INSERT INTO inventory VALUES(?,?,?,?)", (G, U, item_id, 1))
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        self.assertEqual(len(self.select(view).options), 25)

    async def test_nobody_else_can_use_the_bag(self):
        self.give("구형 CPU", 1, sell_price=100, sellable=True)
        view = (await self.open_bag()).response.send_message.await_args.kwargs["view"]
        stranger = self.w.interaction(uid=OTHER)
        self.assertFalse(await view.interaction_check(stranger))
        self.assertEqual(self.store.wallet(G, OTHER), 0)


if __name__ == "__main__":
    unittest.main()
