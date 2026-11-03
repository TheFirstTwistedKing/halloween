"""긴 목록 나누기: 장비·소모품이 많아져도 목록이 잘리지 않고 ◀ ▶ 버튼으로 다음 쪽을 볼 수 있다(관리자 목록, 던전 판매, 아이템 메뉴)."""
import json
import unittest

import balance
import content
import dungeon
import dungeon_service as service
from balance import Balance, check_entity, entity_dict
from cogs.content_admin import ContentAdmin
from cogs.dungeon import ITEM_PAGE, SELL_PAGE, DungeonView, ItemSelect, SellView, run_embed, sell_embed
from dungeon import Run
from paging import PagedView, chunks, send_paged, split_pages
from tests.helpers import World, sent_embed

G, U = 100, 7
Choice = type("Choice", (), {"__init__": lambda self, value: setattr(self, "value", value)})


def embed_size(embed) -> int:
    return len(embed.title or "") + len(embed.description or "") + sum(len(f.name) + len(f.value) for f in embed.fields) + len(embed.footer.text or "")


class SplitTests(unittest.TestCase):
    def test_nothing_is_lost_or_cut_and_every_page_fits(self):
        lines = [f"줄 {i} " + "가" * (i % 40) for i in range(200)]
        for limit in (60, 100, 1000):
            pages = split_pages(lines, limit)
            self.assertTrue(all(len(p) <= limit for p in pages), limit)
            self.assertEqual("\n".join(pages).split("\n"), lines)                        # 순서·내용 그대로
        self.assertEqual(split_pages([], 100), [])

    def test_the_boundary_counts_the_newlines(self):
        self.assertEqual(split_pages(["aaaa", "bbbb"], 9), ["aaaa\nbbbb"])               # 4+1+4 = 9: 딱 들어간다
        self.assertEqual(split_pages(["aaaa", "bbbb"], 8), ["aaaa", "bbbb"])
        self.assertEqual(split_pages(["aaaa"], 4), ["aaaa"])

    def test_one_huge_line_is_shortened_not_dropped(self):
        pages = split_pages(["짧음", "가" * 50, "끝"], 10)
        self.assertEqual(pages[0], "짧음")
        self.assertEqual(pages[1], "가" * 9 + "…")
        self.assertEqual(pages[2], "끝")

    def test_chunks(self):
        self.assertEqual(chunks(list(range(7)), 3), [[0, 1, 2], [3, 4, 5], [6]])
        self.assertEqual(chunks([], 3), [])
        self.assertEqual(chunks(list(range(25)), 25), [list(range(25))])
        self.assertEqual(len(chunks(list(range(26)), 25)), 2)


class PagedViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.w = World()

    async def asyncTearDown(self):
        await self.w.close()

    def build(self, i):
        import discord
        return discord.Embed(title=f"쪽 {i}")

    def test_buttons_follow_the_page(self):
        view = PagedView(U, 3, self.build)
        self.assertEqual([c.label for c in view.children], ["이전", "1 / 3", "다음"])
        self.assertEqual([c.disabled for c in view.children], [True, True, False])
        view.page = 1
        view._sync()
        self.assertEqual([c.disabled for c in view.children], [False, True, False])
        view.page = 2
        view._sync()
        self.assertEqual([c.disabled for c in view.children], [False, True, True])

    async def test_next_and_previous_edit_the_same_message_and_stay_in_range(self):
        view = PagedView(U, 3, self.build)
        i = self.w.interaction()
        await view.next.callback(i)
        self.assertEqual(i.response.edit_message.await_args.kwargs["embed"].title, "쪽 1")
        self.assertIs(i.response.edit_message.await_args.kwargs["view"], view)
        await view.next.callback(i)
        await view.next.callback(i)                                                   # 끝에서 더 눌러도 범위를 벗어나지 않는다
        self.assertEqual((view.page, i.response.edit_message.await_args.kwargs["embed"].title), (2, "쪽 2"))
        await view.prev.callback(i)
        self.assertEqual(view.page, 1)
        for _ in range(5):
            await view.prev.callback(i)
        self.assertEqual(view.page, 0)

    async def test_only_the_person_who_opened_it_can_turn_pages(self):
        view = PagedView(U, 2, self.build)
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=8)))
        self.assertTrue(await view.interaction_check(self.w.interaction(uid=U)))

    async def test_a_single_page_has_no_buttons(self):
        i = self.w.interaction()
        await send_paged(i, U, 1, self.build)
        self.assertNotIn("view", i.response.send_message.await_args.kwargs)
        j = self.w.interaction()
        await send_paged(j, U, 2, self.build)
        self.assertIsInstance(j.response.send_message.await_args.kwargs["view"], PagedView)
        self.assertEqual(j.response.send_message.await_args.kwargs["embed"].title, "쪽 0")


class PackTests(unittest.TestCase):
    def test_fields_are_packed_without_passing_the_embed_character_limit(self):
        blocks = [(f"칸{i}", "가" * 1000) for i in range(5)]
        pages = ContentAdmin._pack_fields(blocks)
        self.assertEqual([len(p) for p in pages], [4, 1])                                # 1000자 칸 5개는 4200자 안에 4개까지
        for page in pages:
            self.assertLessEqual(sum(len(n) + len(v) for n, v in page), 4200)
        self.assertEqual([n for page in pages for n, _ in page], [n for n, _ in blocks])   # 순서 그대로, 하나도 안 빠진다
        small = ContentAdmin._pack_fields([(f"칸{i}", "가") for i in range(5)])
        self.assertEqual([len(p) for p in small], [5])                                   # 짧으면 5칸이 한 쪽
        self.assertEqual([len(p) for p in ContentAdmin._pack_fields([(f"칸{i}", "가") for i in range(6)])], [5, 1])
        self.assertEqual(ContentAdmin._pack_fields([]), [[("없음", "없음")]])


class AdminListTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = ContentAdmin(self.w.bot)
        self.w.channel(400)
        for n in range(42):                                                            # 장비는 최대 50개
            content.add(self.store, G, "gear", dict(name=f"테스트로 만든 아주 긴 이름의 무기 {n:02d}", emoji="🗡️", slot="weapon", tier="일반", weight=1, attack=n + 1))
        for n in range(43):                                                            # 소모품도 최대 50개(재료 하나 자리를 남긴다)
            content.add(self.store, G, "consumables", dict(name=f"테스트로 만든 아주 긴 이름의 약 {n:02d}", emoji="💊", tier="일반", weight=1, heal=5 + n))

    async def asyncTearDown(self):
        await self.w.close()

    async def call(self, command, *args):
        i = self.w.interaction(admin=True)
        await command.callback(self.cog, i, *args)
        return i

    async def test_fifty_gear_are_all_listed_not_cut_at_1000_characters(self):
        i = await self.call(ContentAdmin.dungeon_list, Choice("gear"))
        kwargs = i.response.send_message.await_args.kwargs
        view = kwargs.get("view")
        embeds = [view.build(p) for p in range(view.count)] if view else [kwargs["embed"]]
        seen, all_text = set(), ""
        for embed in embeds:
            self.assertLessEqual(embed_size(embed), 6000)
            for field in embed.fields:
                self.assertLessEqual(len(field.value), 1024)                           # 한 칸이 1024자를 넘으면 디스코드가 거절한다
                all_text += field.value + "\n"
        for gear in self.store.balance(G).gear:
            self.assertIn(f"**{gear.name}**", all_text, gear.name)                     # 하나도 빠지지 않는다
            self.assertTrue(all(f.name.startswith("장비 50개") for f in embed.fields))
        self.assertGreater(sum(len(e.fields) for e in embeds), 1)                       # 1000자를 넘어서 여러 칸으로 나눠 담았다

    async def test_pressing_next_shows_the_following_entries(self):
        i = await self.call(ContentAdmin.dungeon_list)                                   # 모든 분류를 한꺼번에 보면 여러 쪽이 된다
        view = i.response.send_message.await_args.kwargs["view"]
        self.assertGreater(view.count, 1)
        self.assertIn("1/", view.build(0).title)
        first = view.build(0).fields[0].value
        press = self.w.interaction(admin=True)
        await view.next.callback(press)
        second = press.response.edit_message.await_args.kwargs["embed"].fields[0].value
        self.assertNotEqual(first, second)
        self.assertEqual(len(set(first.split("\n")) & set(second.split("\n"))), 0)

    async def test_a_short_list_stays_on_one_page_without_buttons(self):
        i = self.w.interaction(admin=True)
        w2 = World()
        try:
            cog = ContentAdmin(w2.bot)
            j = w2.interaction(admin=True)
            await ContentAdmin.dungeon_list.callback(cog, j)
            kwargs = j.response.send_message.await_args.kwargs
            self.assertNotIn("view", kwargs)
            self.assertEqual({f.name for f in kwargs["embed"].fields}, {"몬스터 4개", "보스 3개", "장비 8개", "소모품 6개", "시크릿 보스 1개"})
        finally:
            await w2.close()

    async def test_the_all_sections_view_also_pages_instead_of_cutting(self):
        i = await self.call(ContentAdmin.dungeon_list)
        view = i.response.send_message.await_args.kwargs["view"]
        names = [f.name for page in range(view.count) for f in view.build(page).fields]
        self.assertTrue(any(n.startswith("장비 50개") for n in names))
        all_text = "\n".join(f.value for page in range(view.count) for f in view.build(page).fields)
        for gear in self.store.balance(G).gear:
            self.assertIn(f"**{gear.name}**", all_text, gear.name)
        for page in range(view.count):
            self.assertLessEqual(embed_size(view.build(page)), 6000)

    @staticmethod
    def embeds_of(interaction):
        """보낸 목록의 모든 쪽(쪽 나눔 버튼이 있으면 쪽마다, 없으면 보낸 임베드 하나)."""
        kwargs = interaction.response.send_message.await_args.kwargs
        view = kwargs.get("view")
        return [view.build(p) for p in range(view.count)] if view else [kwargs["embed"]]

    async def test_recipe_and_condition_lists_lose_nothing(self):
        content.add(self.store, G, "consumables", dict(name="숫돌", emoji="🪨", tier="희귀", weight=1, material=True))
        weapons = [g.name for g in self.store.balance(G).gear if g.slot == "weapon"]
        for n in range(40):
            content.add_mod(self.store, G, weapons[n], "숫돌", weapons[n + 1])
        i = await self.call(ContentAdmin.mod_list)
        embeds = self.embeds_of(i)
        joined = "\n".join(e.description for e in embeds)
        self.assertEqual(joined.count("`") // 2, 40)                                    # 레시피가 하나도 빠지지 않았다
        self.assertTrue(all(len(e.description) <= 3500 for e in embeds))
        for item in self.store.balance(G).consumables:                                  # 조건이 붙은 소모품을 아주 많이 만든다
            if item.name.startswith("테스트로 만든 아주 긴 이름의 약") and not item.conditions:
                content.edit(self.store, G, ("consumables",), item.name, {"conditions": [["hp_le", 50]]})
        j = await self.call(ContentAdmin.cond_list)
        embeds = self.embeds_of(j)
        joined = "\n".join(e.description for e in embeds)
        for n in range(43):
            self.assertIn(f"테스트로 만든 아주 긴 이름의 약 {n:02d}", joined)
        self.assertTrue(all(len(e.description) <= 3500 for e in embeds))


class DungeonScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.bot, self.store = self.w.bot, self.w.store
        for n in range(40):
            content.add(self.store, G, "consumables", dict(name=f"매우 긴 이름을 가진 회복약 번호 {n:02d}", emoji="💊", tier="일반", weight=1, heal=10 + n))
        self.bal = self.store.balance(G)
        self.keys = [c.key for c in self.bal.consumables]
        self.assertGreater(len(self.keys), 25)
        self.store.add_item(G, U, self.store.ensure_system_items(G)["key"], 1)
        out = await service.enter(self.store, self.bot.locks, G, U)
        data = json.loads(out.run.to_json())
        data.update(phase="cleared", room=2, weapon="club", items={k: 2 for k in self.keys})
        self.store.save_dungeon(G, U, json.dumps(data))
        self.run = Run.from_json(self.store.dungeon_run(G, U))

    async def asyncTearDown(self):
        await self.w.close()

    def test_the_sell_screen_pages_by_25_and_never_overflows(self):
        cur = self.store.currency(G)
        entries = dungeon.sellable(self.run, self.bal)
        self.assertGreater(len(entries), SELL_PAGE)
        pages = -(-len(entries) // SELL_PAGE)
        seen = []
        for page in range(pages):
            view = SellView(self.bot, U, self.run, self.bal, cur, page)
            view.to_components()                                                       # 디스코드 규칙(선택 25개·줄 5개)을 어기면 여기서 오류
            select = view.children[0]
            self.assertLessEqual(len(select.options), 25)
            seen += [o.value for o in select.options]
            embed = sell_embed(self.run, self.bal, cur, 0, page=page)
            self.assertLessEqual(embed_size(embed), 6000)
            self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))
            self.assertIn(f"{pages}쪽", select.placeholder)
        self.assertEqual(seen, [e["id"] for e in entries])                              # 모든 항목이 어느 쪽에선가 고를 수 있다
        self.assertNotIn("앞의 25개만", " ".join(f.value for f in sell_embed(self.run, self.bal, cur, 0).fields))

    async def test_picks_are_remembered_across_pages_and_sold_together(self):
        cur = self.store.currency(G)
        view = SellView(self.bot, U, self.run, self.bal, cur)
        first_ids = [i for i in view.page_ids if i.startswith("item:")]                   # 소모품 항목만 고른다(맨 앞은 장착한 무기)
        entries = dungeon.sellable(self.run, self.bal)
        i = self.w.interaction()
        i.data = {"values": [first_ids[0]]}
        await view.on_select(i)
        turn = self.w.interaction()
        await view.turn(+1)(turn)
        second = turn.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(second.page, 1)
        self.assertEqual(second.picks, [first_ids[0]])                                  # 앞 쪽에서 고른 것을 기억한다
        pick2 = next(i for i in second.page_ids if i.startswith("item:"))
        j = self.w.interaction()
        j.data = {"values": [pick2]}
        await second.on_select(j)
        self.assertEqual(set(second.picks), {first_ids[0], pick2})
        back = self.w.interaction()
        await second.turn(-1)(back)
        again = back.response.edit_message.await_args.kwargs["view"]
        self.assertTrue(next(o for o in again.children[0].options if o.value == first_ids[0]).default)   # 돌아오면 고른 표시가 남는다
        sold = self.w.interaction()
        again.picks = list(second.picks)
        await again.sell_picked(sold)
        run = Run.from_json(self.store.dungeon_run(G, U))
        gone = {first_ids[0].split(":", 1)[1], pick2.split(":", 1)[1]}                    # 둘 다 소모품 항목(item:키)
        self.assertTrue(all(k not in run.items for k in gone))
        self.assertEqual(len(run.items), len(self.keys) - 2)

    async def test_the_last_page_and_first_page_buttons_are_disabled_at_the_ends(self):
        cur = self.store.currency(G)
        view = SellView(self.bot, U, self.run, self.bal, cur, 0)
        prev, nxt = [c for c in view.children if getattr(c, "emoji", None) and str(c.emoji) in ("◀️", "▶️")]
        self.assertTrue(prev.disabled)
        self.assertFalse(nxt.disabled)
        last = SellView(self.bot, U, self.run, self.bal, cur, 99)                      # 범위를 넘는 쪽은 마지막 쪽으로
        prev, nxt = [c for c in last.children if getattr(c, "emoji", None) and str(c.emoji) in ("◀️", "▶️")]
        self.assertTrue(nxt.disabled)
        self.assertFalse(prev.disabled)

    def test_few_items_have_no_page_buttons_and_the_old_behaviour(self):
        run = Run.from_json(self.run.to_json())
        run.items = {self.keys[0]: 1}
        view = SellView(self.bot, U, run, self.bal, self.store.currency(G))
        self.assertFalse(any(str(getattr(c, "emoji", "")) in ("◀️", "▶️") for c in view.children))
        self.assertNotIn("쪽", view.children[0].placeholder)

    async def test_the_item_menu_pages_when_there_are_more_than_25_kinds(self):
        view = DungeonView(self.bot, U, self.run, self.bal)
        view.to_components()
        select = next(c for c in view.children if isinstance(c, ItemSelect))
        self.assertEqual(len(select.options), ITEM_PAGE)
        self.assertIn("1/2쪽", select.placeholder)
        buttons = [c for c in view.children if "아이템" in (getattr(c, "label", "") or "")]
        self.assertEqual(len(buttons), 2)
        self.assertTrue(buttons[0].disabled)
        self.assertFalse(buttons[1].disabled)
        press = self.w.interaction()
        await buttons[1].callback(press)
        page2 = press.response.edit_message.await_args.kwargs["view"]
        page2.to_components()
        select2 = next(c for c in page2.children if isinstance(c, ItemSelect))
        self.assertEqual(len(select2.options), len(self.keys) - ITEM_PAGE)
        self.assertEqual({o.value for o in select.options} & {o.value for o in select2.options}, set())
        self.assertIn("2/2쪽", select2.placeholder)
        back_buttons = [c for c in page2.children if "아이템" in (getattr(c, "label", "") or "")]
        self.assertFalse(back_buttons[0].disabled)
        self.assertTrue(back_buttons[1].disabled)

    async def test_the_belongings_field_never_exceeds_the_discord_limit(self):
        embed = run_embed(self.run, None, self.bal)
        field = next(f for f in embed.fields if "소지품" in f.name)
        self.assertLessEqual(len(field.value), 1024)
        self.assertIn("외", field.value)
        self.assertLessEqual(embed_size(embed), 6000)

    async def test_a_stale_page_button_is_refused(self):
        view = DungeonView(self.bot, U, self.run, self.bal)
        button = next(c for c in view.children if "아이템 다음" in (getattr(c, "label", "") or ""))
        await service.act(self.store, self.bot.locks, self.w.bot.rng, G, U, self.run.rev, "advance")      # 화면 번호가 바뀐다
        press = self.w.interaction()
        await button.callback(press)
        self.assertIn("지난 화면", press.response.send_message.await_args.kwargs["embed"].description)
