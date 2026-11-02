"""던전 안에서 장비·소모품을 파는 기능 시험: 규칙, 저장(최대 보유량), 서비스(겹친 요청), 판매가 편집 명령어, 화면."""
import asyncio
import json
import unittest

import balance
import content
import dungeon
import dungeon_service as service
from balance import check_entity, entity_dict
from cogs.content_admin import ContentAdmin
from cogs.dungeon import DungeonView, SellView, open_sell, respond_sell, run_embed, sell_embed
from store import Store, StoreError
from tests.helpers import World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


def run_with(**kw):
    run = dungeon.new_run()
    run.phase, run.room = "cleared", 1
    for k, v in kw.items():
        setattr(run, k, v)
    return run


class RuleTests(unittest.TestCase):
    def test_default_sell_prices_follow_the_tier(self):
        by_tier = {"일반": 5, "희귀": 15, "전설": 50}
        for entity in balance.GEAR + balance.CONSUMABLES:
            self.assertEqual(entity.sell_price, by_tier[entity.tier], entity.name)

    def test_sell_price_round_trips_and_old_saved_data_gets_the_tier_default(self):
        for section in ("gear", "consumables"):
            for entity in getattr(balance.DEFAULT, section):
                loaded, problem = check_entity(section, json.loads(json.dumps(entity_dict(entity))))
                self.assertIsNone(problem)
                self.assertEqual(loaded.sell_price, entity.sell_price)
        old = entity_dict(balance.GEAR[3])                              # 판매가가 생기기 전에 저장된 데이터
        old.pop("sell_price")
        self.assertEqual(check_entity("gear", old)[0].sell_price, 15)
        for bad in (-1, 1.5, True, "5", balance.SELL_MAX + 1):
            _, problem = check_entity("gear", {**old, "sell_price": bad})
            self.assertIn("sell_price", problem or "", bad)

    def test_sellable_lists_new_gear_equipped_gear_and_items_but_not_free_or_deleted_ones(self):
        run = run_with(pending_gear="longsword", weapon="dagger", shield="wooden_shield", items={"candy": 3, "golden_soup": 1})
        entries = {e["id"]: e for e in dungeon.sellable(run)}
        self.assertEqual(list(entries), ["pending", "weapon", "shield", "item:candy", "item:golden_soup"])
        self.assertEqual((entries["pending"]["price"], entries["item:candy"]["qty"], entries["item:golden_soup"]["price"]), (15, 3, 50))
        zero = balance.Balance.build({}, {"consumables": [entity_dict(c) | {"sell_price": 0} for c in balance.CONSUMABLES]})
        self.assertFalse(any(e["kind"] == "item" for e in dungeon.sellable(run, zero)))     # 판매가 0은 팔 수 없다
        run.items["ghost"], run.weapon = 2, "removed"                                          # 지워진 것은 건너뛴다
        self.assertNotIn("weapon", {e["id"] for e in dungeon.sellable(run)})
        self.assertNotIn("item:ghost", {e["id"] for e in dungeon.sellable(run)})

    def test_sell_picked_removes_everything_chosen_and_sums_the_prices(self):
        run = run_with(pending_gear="longsword", weapon="dagger", items={"candy": 3, "pumpkin_soup": 2})
        total, sold = dungeon.sell(run, ["pending", "weapon", "item:candy", "item:pumpkin_soup"], "all")
        self.assertEqual(total, 15 + 5 + 3 * 5 + 2 * 15)
        self.assertEqual([(e["id"], q) for e, q in sold], [("pending", 1), ("weapon", 1), ("item:candy", 3), ("item:pumpkin_soup", 2)])
        self.assertEqual((run.pending_gear, run.weapon, run.items), (None, None, {}))
        self.assertTrue(run.can_advance)                                # 새 장비를 팔면 정하지 못해 막혀 있던 길이 열린다

    def test_one_at_a_time_and_all_items_modes(self):
        run = run_with(weapon="dagger", items={"candy": 3, "cookie": 2})
        total, _ = dungeon.sell(run, ["item:candy", "weapon"], "one")
        self.assertEqual((total, run.items, run.weapon), (5 + 5, {"candy": 2, "cookie": 2}, None))
        total, _ = dungeon.sell(run, [], "items")                       # 고르지 않아도 소모품 전부
        self.assertEqual((total, run.items), (2 * 5 + 2 * 5, {}))

    def test_cannot_sell_while_fighting_or_after_the_run_ended_or_with_nothing_valid(self):
        for phase in ("battle", "guard"):
            with self.assertRaises(dungeon.DungeonError):
                dungeon.sell(run_with(phase=phase, items={"candy": 1}), ["item:candy"])
        for phase in ("dead", "clear", "quit"):
            with self.assertRaises(dungeon.DungeonError):
                dungeon.sell(run_with(phase=phase, items={"candy": 1}), ["item:candy"])
        run = run_with(items={"candy": 1})
        for picks, mode in (([], "all"), (["nonsense", "item:cookie"], "all"), (["item:candy"], "weird")):
            with self.assertRaises(dungeon.DungeonError):
                dungeon.sell(run, picks, mode)
        self.assertEqual(run.items, {"candy": 1})                       # 실패하면 아무것도 빠지지 않는다

    def test_duplicate_picks_count_once(self):
        run = run_with(items={"candy": 2})
        total, _ = dungeon.sell(run, ["item:candy", "item:candy"], "all")
        self.assertEqual((total, run.items), (10, {}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def begin(self):
        with self.store.db:
            self.store.db.execute("INSERT INTO dungeons VALUES(?,?,?)", (G, U, "{}"))

    def test_pays_and_saves_together(self):
        self.begin()
        self.store.add_candy(G, U, 100)
        self.assertEqual(self.store.sell_in_dungeon(G, U, '{"x":1}', 30), 130)
        self.assertEqual((self.store.wallet(G, U), self.store.dungeon_run(G, U)), (130, '{"x":1}'))

    def test_refuses_over_the_cap_and_changes_nothing(self):
        self.begin()
        top = self.store.currency(G).max
        self.store.add_candy(G, U, top - 10)
        with self.assertRaises(StoreError) as caught:
            self.store.sell_in_dungeon(G, U, '{"x":1}', 11)
        self.assertIn("최대", str(caught.exception))
        self.assertEqual((self.store.wallet(G, U), self.store.dungeon_run(G, U)), (top - 10, "{}"))
        self.assertEqual(self.store.sell_in_dungeon(G, U, '{"x":1}', 10), top)         # 딱 맞게는 된다

    def test_refuses_without_a_run_and_pays_nothing(self):
        with self.assertRaises(StoreError):
            self.store.sell_in_dungeon(G, U, "{}", 50)
        self.assertEqual(self.store.wallet(G, U), 0)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from services import Locks
        self.store, self.locks = Store(":memory:"), Locks()

    def tearDown(self):
        self.store.close()

    def start(self, **kw):
        run = run_with(**kw)
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO dungeons VALUES(?,?,?)", (G, U, run.to_json()))
        return run

    async def test_selling_pays_now_saves_the_run_and_moves_to_a_new_screen(self):
        run = self.start(items={"candy": 3}, pending_gear="shield", phase="chest_done")
        out = await service.sell(self.store, self.locks, G, U, run.rev, ["item:candy", "pending"])
        self.assertTrue(out.ok)
        self.assertEqual(self.store.wallet(G, U), 15 + 15)
        saved = json.loads(self.store.dungeon_run(G, U))
        self.assertEqual((saved["items"], saved["pending_gear"], saved["rev"]), ({}, None, run.rev + 1))
        text = "\n".join(out.run.events)
        self.assertIn("사탕", text)
        self.assertIn("×3", text)
        self.assertIn("합계 🍬 30", text)

    async def test_an_old_screen_or_a_missing_run_pays_nothing(self):
        run = self.start(items={"candy": 1})
        stale = await service.sell(self.store, self.locks, G, U, run.rev + 5, ["item:candy"])
        self.assertEqual((stale.ok, stale.stale), (False, True))
        gone = await service.sell(self.store, self.locks, G, 99, 0, ["item:candy"])
        self.assertEqual((gone.ok, gone.stale), (False, True))
        self.assertEqual((self.store.wallet(G, U), json.loads(self.store.dungeon_run(G, U))["items"]), (0, {"candy": 1}))

    async def test_no_selling_in_a_fight_and_over_the_cap_nothing_is_sold(self):
        run = self.start(items={"candy": 1}, phase="battle", enemy={"name": "x"})
        out = await service.sell(self.store, self.locks, G, U, run.rev, ["item:candy"])
        self.assertFalse(out.ok)
        self.assertIn("싸우는 중", out.text)
        run = self.start(items={"candy": 2})
        self.store.add_candy(G, U, self.store.currency(G).max - 5)
        out = await service.sell(self.store, self.locks, G, U, run.rev, ["item:candy"], "all")
        self.assertFalse(out.ok)
        self.assertIn("최대", out.text)
        self.assertEqual(json.loads(self.store.dungeon_run(G, U))["items"], {"candy": 2})      # 물건은 그대로
        self.assertEqual(self.store.wallet(G, U), self.store.currency(G).max - 5)

    async def test_double_clicks_pay_only_once(self):
        run = self.start(items={"pumpkin_soup": 4})
        results = await asyncio.gather(*[service.sell(self.store, self.locks, G, U, run.rev, ["item:pumpkin_soup"]) for _ in range(5)])
        self.assertEqual([r.ok for r in results].count(True), 1)
        self.assertEqual(self.store.wallet(G, U), 4 * 15)

    async def test_the_admins_price_applies_to_the_next_sale(self):
        content.set_sell_price(self.store, G, ("consumables",), 40, "일반")
        run = self.start(items={"candy": 2})
        out = await service.sell(self.store, self.locks, G, U, run.rev, ["item:candy"])
        self.assertTrue(out.ok)
        self.assertEqual(self.store.wallet(G, U), 80)


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def price(self, section, name):
        return next(e for e in getattr(self.store.balance(G), section) if e.name == name).sell_price

    def test_bulk_price_by_tier_and_section(self):
        changed = content.set_sell_price(self.store, G, ("gear", "consumables"), 99, "전설")
        self.assertEqual(changed, {"gear": 2, "consumables": 1})
        self.assertEqual((self.price("gear", "총"), self.price("gear", "호박 대검"), self.price("consumables", "황금 호박죽")), (99, 99, 99))
        self.assertEqual((self.price("gear", "롱소드"), self.price("consumables", "사탕")), (15, 5))      # 다른 등급은 그대로
        content.set_sell_price(self.store, G, ("gear",), 0)
        self.assertTrue(all(g.sell_price == 0 for g in self.store.balance(G).gear))
        self.assertEqual(self.price("consumables", "사탕"), 5)

    def test_bulk_price_errors(self):
        with self.assertRaises(content.ContentError):
            content.set_sell_price(self.store, G, ("monsters",), 5)
        with self.assertRaises(content.ContentError):
            content.set_sell_price(self.store, G, ("gear",), 5, "신화")
        for name in [g.name for g in balance.GEAR if g.tier == "전설"]:
            content.delete(self.store, G, ("gear",), name)
        with self.assertRaises(content.ContentError) as caught:
            content.set_sell_price(self.store, G, ("gear",), 5, "전설")
        self.assertIn("전설", str(caught.exception))

    def test_edit_and_add_keep_or_default_the_price(self):
        content.edit(self.store, G, ("gear",), "단검", {"sell_price": 12})
        self.assertEqual(self.price("gear", "단검"), 12)
        content.edit(self.store, G, ("gear",), "단검", {"attack": 40})
        self.assertEqual(self.price("gear", "단검"), 12)                      # 다른 값을 고쳐도 판매가는 그대로
        content.edit(self.store, G, ("gear",), "단검", {"tier": "전설"})
        self.assertEqual(self.price("gear", "단검"), 12)                      # 등급을 바꿔도 직접 정한 값은 유지
        content.add(self.store, G, "consumables", dict(name="엘릭서", emoji="💊", tier="희귀", weight=1, heal=10))
        self.assertEqual(self.price("consumables", "엘릭서"), 15)             # 안 정하면 등급별 기본값
        content.add(self.store, G, "consumables", dict(name="독약", emoji="☠️", tier="일반", weight=1, heal=10, sell_price=0))
        self.assertEqual(self.price("consumables", "독약"), 0)

    def test_describe_shows_the_price_or_that_it_cannot_be_sold(self):
        self.assertIn("판매 15", content.describe("gear", entity_dict(balance.GEAR[3])))
        self.assertIn("판매 불가", content.describe("consumables", entity_dict(balance.CONSUMABLES[0]) | {"sell_price": 0}))

    def test_content_saved_before_this_feature_gets_tier_defaults(self):
        legacy = [entity_dict(g) for g in balance.GEAR]
        for data in legacy:
            data.pop("sell_price")
        self.store.update_settings(G, dungeon_content={"gear": legacy})
        self.assertEqual(self.price("gear", "롱소드"), 15)
        self.assertEqual(self.price("gear", "총"), 50)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    def ask(self, **kw):
        kw.setdefault("admin", True)
        return self.w.interaction(**kw)

    async def run_cmd(self, command, interaction, *args, **kwargs):
        await command.callback(self.cog, interaction, *args, **kwargs)
        return interaction

    def text(self, interaction):
        embed, _ = sent_embed(interaction)
        return (embed.description or "") + "\n".join(f.value for f in embed.fields)

    def price(self, section, name):
        return next(e for e in getattr(self.store.balance(G), section) if e.name == name).sell_price

    async def test_edit_commands_change_the_price_and_zero_turns_selling_off(self):
        i = await self.run_cmd(ContentAdmin.gear_edit, self.ask(), "롱소드", sell_price=77)
        self.assertIn("판매 77", self.text(i))
        i = await self.run_cmd(ContentAdmin.potion_edit, self.ask(), "사탕", sell_price=0)
        self.assertIn("판매 불가", self.text(i))
        self.assertEqual((self.price("gear", "롱소드"), self.price("consumables", "사탕")), (77, 0))
        run = run_with(items={"candy": 2}, weapon="longsword")
        self.assertEqual([e["id"] for e in dungeon.sellable(run, self.store.balance(G))], ["weapon"])

    async def test_add_commands_accept_a_price_and_default_when_it_is_left_out(self):
        await self.run_cmd(ContentAdmin.gear_add, self.ask(), "활", "🏹", Choice("weapon"), Choice("희귀"), 5, attack=10, sell_price=33)
        await self.run_cmd(ContentAdmin.potion_add, self.ask(), "붕대", "🩹", Choice("희귀"), 5, heal=100)
        self.assertEqual((self.price("gear", "활"), self.price("consumables", "붕대")), (33, 15))

    async def test_bulk_command_reports_what_changed_and_checks_for_admin(self):
        i = await self.run_cmd(ContentAdmin.sell_price_bulk, self.ask(), 20, Choice("gear"), Choice("일반"))
        self.assertIn("일반 등급 장비 3개", self.text(i))
        self.assertIn("20", self.text(i))
        self.assertEqual((self.price("gear", "단검"), self.price("gear", "롱소드"), self.price("consumables", "사탕")), (20, 15, 5))
        i = await self.run_cmd(ContentAdmin.sell_price_bulk, self.ask(), 0)
        self.assertIn("팔 수 없어요", self.text(i))
        self.assertTrue(all(e.sell_price == 0 for e in self.store.balance(G).gear + self.store.balance(G).consumables))
        i = await self.run_cmd(ContentAdmin.sell_price_bulk, self.ask(admin=False), 999)
        self.assertIn("관리자만", self.text(i))
        self.assertEqual(self.price("gear", "총"), 0)


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.w.channel(400)

    async def asyncTearDown(self):
        await self.w.close()

    def start(self, **kw):
        run = run_with(**kw)
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO dungeons VALUES(?,?,?)", (G, U, run.to_json()))
        return run

    def labels(self, view):
        return [getattr(c, "label", None) or type(c).__name__ for c in view.children]

    def edited(self, interaction):
        call = interaction.response.edit_message.await_args
        return call.kwargs["embed"], call.kwargs["view"]

    def test_the_sell_button_appears_only_when_something_can_be_sold_and_not_in_a_fight(self):
        bal = self.store.balance(G)
        self.assertNotIn("판매", self.labels(DungeonView(self.bot, U, run_with(), bal)))
        self.assertIn("판매", self.labels(DungeonView(self.bot, U, run_with(items={"candy": 1}), bal)))
        fight = run_with(items={"candy": 1}, phase="battle", enemy={"name": "x", "emoji": "x", "hp": 1, "max_hp": 1, "boss": False})
        self.assertNotIn("판매", self.labels(DungeonView(self.bot, U, fight, bal)))
        free = balance.Balance.build({}, {"consumables": [entity_dict(c) | {"sell_price": 0} for c in balance.CONSUMABLES]})
        self.assertNotIn("판매", self.labels(DungeonView(self.bot, U, run_with(items={"candy": 1}), free)))

    def test_new_gear_has_a_quick_sell_button_with_the_price(self):
        view = DungeonView(self.bot, U, run_with(pending_gear="longsword", phase="chest_done"), self.store.balance(G))
        self.assertEqual(self.labels(view), ["장착", "예비에 넣기", "버리기", "판매 +15", "빠른진행", "나가기"])      # 소지품이 없으면 [판매] 창구는 따로 없다
        view = DungeonView(self.bot, U, run_with(pending_gear="longsword", phase="chest_done", items={"candy": 1}), self.store.balance(G))
        self.assertEqual(self.labels(view)[:5], ["장착", "예비에 넣기", "버리기", "판매 +15", "판매"])
        view.to_components()                                                    # 버튼이 6개를 넘어도 두 줄로 나뉘어 규칙 안에 든다

    async def test_open_sell_swaps_the_same_message_for_the_sell_screen(self):
        run = self.start(items={"candy": 3, "pumpkin_soup": 1}, weapon="dagger")
        i = self.w.interaction()
        await open_sell(i, self.bot, run.rev)
        embed, view = self.edited(i)
        self.assertIsInstance(view, SellView)
        self.assertEqual(embed.title, "💰 던전 판매")
        options = next(c for c in view.children if hasattr(c, "options")).options
        self.assertEqual([o.value for o in options], ["weapon", "item:candy", "item:pumpkin_soup"])
        self.assertTrue(all(len(o.description) <= 100 and len(o.label) <= 100 for o in options))
        self.assertEqual(self.labels(view)[1:], ["선택한 것 판매", "1개씩만", "소모품 전부", "돌아가기"])

    async def test_selling_several_things_at_once_from_the_screen(self):
        run = self.start(items={"candy": 3, "cookie": 2, "pumpkin_soup": 1}, weapon="dagger")
        i = self.w.interaction()
        await open_sell(i, self.bot, run.rev)
        _, view = self.edited(i)
        select = next(c for c in view.children if hasattr(c, "options"))
        pick = self.w.interaction(data={"values": ["item:candy", "item:cookie", "weapon"]})
        await select.callback(pick)
        pick.response.defer.assert_awaited()
        buy = self.w.interaction()
        await next(c for c in view.children if getattr(c, "label", "") == "선택한 것 판매").callback(buy)
        self.assertEqual(self.store.wallet(G, U), 3 * 5 + 2 * 5 + 5)
        embed, new_view = self.edited(buy)
        self.assertIsInstance(new_view, SellView)                          # 남은 게 있으면 판매 화면에 머문다
        self.assertIn("합계 🍬 30", embed.description)
        remaining = next(c for c in new_view.children if hasattr(c, "options")).options
        self.assertEqual([o.value for o in remaining], ["item:pumpkin_soup"])

    async def test_selling_the_last_thing_returns_to_the_dungeon_screen(self):
        run = self.start(items={"candy": 1})
        i = self.w.interaction()
        await respond_sell(i, self.bot, run.rev, ["item:candy"], "all", stay=True)
        embed, view = self.edited(i)
        self.assertIsInstance(view, DungeonView)
        self.assertIn("합계", embed.description)

    async def test_sell_all_items_needs_no_selection_but_the_other_buttons_do(self):
        run = self.start(items={"candy": 2, "cookie": 2}, weapon="dagger")
        i = self.w.interaction()
        await open_sell(i, self.bot, run.rev)
        _, view = self.edited(i)
        none = self.w.interaction()
        await next(c for c in view.children if getattr(c, "label", "") == "선택한 것 판매").callback(none)
        self.assertIn("먼저", sent_embed(none)[0].description)
        self.assertEqual(self.store.wallet(G, U), 0)
        every = self.w.interaction()
        await next(c for c in view.children if getattr(c, "label", "") == "소모품 전부").callback(every)
        self.assertEqual(self.store.wallet(G, U), 4 * 5)
        self.assertEqual(json.loads(self.store.dungeon_run(G, U))["weapon"], "dagger")   # 장비는 안 팔린다

    async def test_old_screens_and_other_players_are_turned_away(self):
        run = self.start(items={"candy": 1})
        i = self.w.interaction()
        await open_sell(i, self.bot, run.rev)
        _, view = self.edited(i)
        await respond_sell(self.w.interaction(), self.bot, run.rev, ["item:candy"], "all", stay=True)   # 먼저 팔아 버림
        late = self.w.interaction()
        await view.sell_picked(late)                                       # 고른 게 없으면 안내
        self.assertIn("먼저", sent_embed(late)[0].description)
        view.picks = ["item:candy"]
        late = self.w.interaction()
        await view.sell_picked(late)
        self.assertIn("지난 화면", sent_embed(late)[0].description)
        self.assertEqual(self.store.wallet(G, U), 5)                       # 두 번 받지 않는다
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=8)))
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)))

    async def test_back_returns_to_the_dungeon_screen(self):
        run = self.start(items={"candy": 1})
        i = self.w.interaction()
        await open_sell(i, self.bot, run.rev)
        _, view = self.edited(i)
        back = self.w.interaction()
        await view.back(back)
        embed, new_view = self.edited(back)
        self.assertIsInstance(new_view, DungeonView)
        self.assertEqual(embed.title, run_embed(run, None, self.store.balance(G)).title)

    def test_sell_embed_fits_discord_limits_with_many_items(self):
        bal = self.store.balance(G)
        run = run_with(items={c.key: 99 for c in bal.consumables}, weapon="dagger", shield="shield", pending_gear="gun")
        embed = sell_embed(run, bal, self.store.currency(G), 123)
        self.assertLessEqual(len(embed), 6000)
        self.assertTrue(all(f.value for f in embed.fields))
        view = SellView(self.bot, U, run, bal, self.store.currency(G))
        select = next(c for c in view.children if hasattr(c, "options"))
        self.assertLessEqual(len(select.options), 25)
        self.assertEqual(select.max_values, len(select.options))


if __name__ == "__main__":
    unittest.main()
