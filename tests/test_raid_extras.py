"""레이드 궁극기·각성 특수 스킬, 시작할 때 주는 회복 아이템(관리자가 추가·수정·삭제), 내 상태."""
import unittest
from unittest.mock import AsyncMock

import content
import raid
from balance import DEFAULT
from cogs.raid import Raid, RaidItems, RaidView
from raid import RaidError
from store import DEFAULT_SETTINGS, Store, validate_settings
from tests.helpers import World, sent_embed

G, CH = 100, 400
A, B, C = 1, 2, 3
T0 = 1_000_000.0
BASE = DEFAULT.base_damage + DEFAULT.gear_by_key["longsword"].attack     # 플라즈마 블레이드를 든 공격력


class Dice:
    def random(self):
        return 0.99

    def sample(self, population, k):
        return list(population)[:k]


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def arm(self, user, **weapon):
        if weapon:
            content.edit(self.store, G, ("gear",), "플라즈마 블레이드", weapon)
        self.store.vault_put(G, user, [("gear", "longsword", 1)])
        self.store.set_display(G, user, "weapon", "longsword")

    def started(self, users=(A,), hp=10 ** 6, damage=0, **kw):
        raid_id = raid.open_raid(self.store, G, CH, raid.check_boss(name="코어", emoji="🐉", hp=hp, damage=damage), now=T0,
                                 attack_every=kw.pop("attack_every", 120), cooldown=1, **kw)
        for u in users:
            raid.join(self.store, G, u, now=T0)
        raid.start(self.store, G, now=T0)
        return raid_id

    def me(self, raid_id, user=A):
        return next(m for m in raid.members(self.store, raid_id) if m["user"] == user)

    def boss_hp(self, raid_id):
        return raid.boss(raid.get(self.store, raid_id))["hp"]

    def act(self, action, t, user=A):
        return raid.act(self.store, G, user, action, Dice(), now=T0 + t)


class UltimateTests(Base):
    def test_gauge_fills_by_attack_and_defend_only(self):
        self.arm(A, ult="power", ult_power=500, skill="power", skill_power=200, skill_cooldown=1)
        raid_id = self.started()
        self.act("attack", 1)
        self.act("defend", 2)
        self.act("skill", 3)                                                             # 스킬은 게이지를 채우지 않는다
        self.assertEqual(self.me(raid_id)["ult_gauge"], 40)
        with self.assertRaisesRegex(RaidError, "40%"):
            self.act("ult", 4)
        for t in (5, 6, 7):
            self.act("attack", t)
        self.assertEqual(self.me(raid_id)["ult_gauge"], 100)
        self.act("attack", 8)
        self.assertEqual(self.me(raid_id)["ult_gauge"], 100)                             # 넘치지 않는다
        before = self.boss_hp(raid_id)
        line = self.act("ult", 9)
        self.assertIn("궁극기", line)
        self.assertEqual(before - self.boss_hp(raid_id), BASE * 5)
        self.assertEqual((self.me(raid_id)["ult_gauge"], self.me(raid_id)["awaken_gauge"]), (0, 10))

    def test_gauge_caps_at_100_with_uneven_charge(self):
        self.store.update_settings(G, awakening={"ult_charge": 30})
        self.arm(A, ult="power", ult_power=100)
        raid_id = self.started()
        for t in range(1, 6):
            self.act("attack", t)
        self.assertEqual(self.me(raid_id)["ult_gauge"], 100)                             # 30·60·90 → 100에서 멈춘다

    def test_no_ultimate_no_gauge(self):
        self.arm(A)
        raid_id = self.started()
        self.act("attack", 1)
        self.assertEqual(self.me(raid_id)["ult_gauge"], 0)
        with self.assertRaisesRegex(RaidError, "궁극기가 없어요"):
            self.act("ult", 2)

    def test_awakening_follows_the_awakening_settings(self):
        self.store.update_settings(G, awakening={"ult_charge": 100, "awaken_charge": 50, "special": "multi", "special_power": 3,
                                                 "special_ends": True, "awaken_bonus": 100, "special_name": "오버드라이브"})
        self.arm(A, ult="power", ult_power=100)
        raid_id = self.started()
        with self.assertRaisesRegex(RaidError, "각성 중에만"):
            self.act("special", 1)
        for t in (2, 4):
            self.act("attack", t)
            self.act("ult", t + 1)
        self.assertTrue(self.me(raid_id)["awakened"])
        before = self.boss_hp(raid_id)
        self.act("attack", 6)
        self.assertEqual(before - self.boss_hp(raid_id), BASE * 2)                       # 각성 중 피해 +100%
        before = self.boss_hp(raid_id)
        line = self.act("special", 7)
        self.assertIn("[오버드라이브]", line)
        self.assertEqual(before - self.boss_hp(raid_id), BASE * 2 * 3)
        self.assertEqual((self.me(raid_id)["awakened"], self.me(raid_id)["awaken_gauge"]), (False, 0))

    def test_special_can_repeat_when_set_not_to_end(self):
        self.store.update_settings(G, awakening={"ult_charge": 100, "awaken_charge": 100, "special_ends": False})
        self.arm(A, ult="power", ult_power=100)
        raid_id = self.started()
        self.act("attack", 1)
        self.act("ult", 2)
        self.act("special", 3)
        self.act("special", 4)
        self.assertTrue(self.me(raid_id)["awakened"])


class ItemTests(Base):
    def test_everyone_gets_the_items_at_start_and_late_joiners_too(self):
        self.store.update_settings(G, raid_items=[{"id": 1, "name": "배터리", "emoji": "🔋", "heal": 300, "amount": 2},
                                                  {"id": 4, "name": "냉각수", "emoji": "🧊", "heal": 50, "amount": 1}])
        raid_id = raid.open_raid(self.store, G, CH, raid.check_boss(name="코어", emoji="🐉", hp=10 ** 6, damage=0), now=T0)
        raid.join(self.store, G, A, now=T0)
        self.assertEqual(self.me(raid_id)["items"], {})                                   # 모집 중에는 아직 없다
        raid.start(self.store, G, now=T0)
        self.assertEqual(self.me(raid_id)["items"], {"1": 2, "4": 1})
        self.store.update_settings(G, raid_items=[])                                     # 도중에 설정을 바꿔도 이 레이드는 그대로
        raid.join(self.store, G, B, now=T0 + 5)
        self.assertEqual(self.me(raid_id, B)["items"], {"1": 2, "4": 1})

    def test_using_an_item(self):
        raid_id = self.started(damage=500, attack_every=3)
        with self.assertRaisesRegex(RaidError, "가득"):
            self.act("item:1", 1)
        raid.tick(self.store, G, Dice(), now=T0 + 3)
        self.assertEqual(self.me(raid_id)["hp"], 500)
        line = self.act("item:1", 4)
        self.assertIn("HP +300", line)
        self.assertEqual((self.me(raid_id)["hp"], self.me(raid_id)["items"]["1"]), (800, 1))
        with self.assertRaisesRegex(RaidError, "초 뒤"):
            self.act("item:1", 4.5)                                                      # 아이템도 행동 쿨타임을 쓴다
        self.act("item:1", 5)
        self.assertEqual(self.me(raid_id)["hp"], 1000)                                    # 최대 HP까지만
        raid.tick(self.store, G, Dice(), now=T0 + 6)
        with self.assertRaisesRegex(RaidError, "남아 있지 않아요"):
            self.act("item:1", 7)
        with self.assertRaisesRegex(RaidError, "없는 아이템"):
            self.act("item:9", 8)

    def test_down_cannot_use_items(self):
        raid_id = self.started(users=(A, B), damage=1000, attack_every=3)
        raid.tick(self.store, G, Dice(), now=T0 + 3)
        self.assertTrue(self.me(raid_id)["down"])
        with self.assertRaisesRegex(RaidError, "다운"):
            self.act("item:1", 4)

    def test_item_settings_are_checked(self):
        good = {"id": 1, "name": "배터리", "emoji": "🔋", "heal": 300, "amount": 2}
        validate_settings(dict(DEFAULT_SETTINGS, raid_items=[good]))
        for broken in ([dict(good, heal=0)], [dict(good, amount=100)], [dict(good, emoji="abc")], [good, dict(good)],
                       [good, dict(good, id=2)], [dict(good, id=i, name=f"n{i}") for i in range(1, 7)], "x"):
            with self.assertRaises(ValueError, msg=broken):
                validate_settings(dict(DEFAULT_SETTINGS, raid_items=broken))
        self.assertEqual(DEFAULT_SETTINGS["raid_items"][0]["name"], "응급 배터리")         # 기본으로 하나 들어 있다

    def test_status_text(self):
        self.arm(A, ult="power", ult_power=100)
        raid_id = self.started()
        text = raid.status_text(self.store, G, raid.get(self.store, raid_id), A, now=T0)
        self.assertIn("궁극기", text)
        self.assertIn("응급 배터리 ×2", text)
        with self.assertRaises(RaidError):
            raid.status_text(self.store, G, raid.get(self.store, raid_id), B, now=T0)


class OldRaidTests(Base):
    def test_a_raid_saved_before_this_update_still_works(self):
        raid_id = self.started()
        row = raid.get(self.store, raid_id)
        data = raid.boss(row)
        data.pop("items")                                                                # 예전 레이드에는 아이템 칸이 없다
        raid._save(self.store, raid_id, data)
        member = self.me(raid_id)
        for key in ("ult", "ult_power", "ult_name", "ult_gauge", "awaken_gauge", "awakened", "items"):
            member.pop(key)
        raid._save_member(self.store, raid_id, member)
        self.act("attack", 1)
        self.assertIn("HP", raid.status_text(self.store, G, raid.get(self.store, raid_id), A, now=T0 + 2))
        with self.assertRaises(RaidError):
            self.act("item:1", 3)
        with self.assertRaisesRegex(RaidError, "궁극기가 없어요"):
            self.act("ult", 4)


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store, self.bot = self.w.store, self.w.bot
        self.w.channel(CH)
        self.cog = Raid(self.bot, rng=Dice())
        self.message = type("M", (), {"id": 777})()

    async def asyncTearDown(self):
        await self.w.close()

    def press(self, uid, data=None):
        i = self.w.interaction(uid=uid, data=data)
        i.message = self.message
        return i

    async def open_and_join(self):
        i = self.w.interaction(admin=True)
        i.original_response = AsyncMock(return_value=self.message)
        await Raid.open.callback(self.cog, i, "코어", 10 ** 6, 0)
        await self.cog.press(self.press(A), "join")
        return raid.current(self.store, G)

    async def test_buttons_fit_and_the_item_menu_uses_this_raids_items(self):
        five = [{"id": n, "name": f"아이템{n}", "emoji": "🔋", "heal": 10 * n, "amount": 1} for n in range(1, 6)]
        self.store.update_settings(G, raid_items=five)
        row = await self.open_and_join()
        view = self.cog.view_for(row)
        view.to_components()                                                            # 한 줄 5개 등 디스코드 한도 안
        labels = [c.label for c in view.children if getattr(c, "label", None)]
        self.assertTrue({"참가", "공격", "방어", "스킬", "궁극기", "각성", "내 상태"} <= set(labels))
        menu = next(c for c in view.children if getattr(c, "custom_id", "") == "raid:item")
        self.assertEqual(len(menu.options), 5)
        i = self.w.interaction(admin=True)
        await Raid.begin.callback(self.cog, i)
        self.store.update_settings(G, raid_items=five[:1])                               # 시작한 뒤에 바꾸면 이 레이드 메뉴는 그대로
        menu = next(c for c in self.cog.view_for(raid.current(self.store, G)).children if getattr(c, "custom_id", "") == "raid:item")
        self.assertEqual(len(menu.options), 5)
        self.assertTrue(RaidView(self.cog, ended=True).children[0].disabled)

    async def test_status_is_private_and_the_menu_uses_an_item(self):
        await self.open_and_join()
        i = self.press(A)
        await self.cog.press(i, "status")
        self.assertTrue(i.response.send_message.call_args.kwargs["ephemeral"])
        self.assertIn("HP", i.response.send_message.call_args.kwargs["embed"].description)
        i = self.w.interaction(admin=True)
        await Raid.begin.callback(self.cog, i)
        row = raid.current(self.store, G)
        member = next(m for m in raid.members(self.store, row["id"]) if m["user"] == A)
        member["hp"] = 100
        raid._save_member(self.store, row["id"], member)
        registered = RaidView(self.cog)                                                 # 봇이 다시 켜졌을 때 등록되는 화면으로 눌러도
        menu = next(c for c in registered.children if getattr(c, "custom_id", "") == "raid:item")
        pick = self.press(A, data={"values": ["1"]})
        self.cog.last_edit[row["id"]] = 0
        await menu.callback(pick)
        member = next(m for m in raid.members(self.store, row["id"]) if m["user"] == A)
        self.assertEqual((member["hp"], member["items"]["1"]), (400, 1))


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World()
        self.store = self.w.store
        self.cog = RaidItems(self.w.bot)

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, admin=True, **kwargs):
        i = self.w.interaction(admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        return sent_embed(i)[0].description

    def items(self):
        return self.store.settings(G)["raid_items"]

    async def test_add_edit_delete_list(self):
        self.assertIn("더했어요", await self.run_cmd(RaidItems.add, "냉각수", "🧊", 150, 3))
        self.assertEqual(self.items()[-1], {"id": 2, "name": "냉각수", "emoji": "🧊", "heal": 150, "amount": 3})
        self.assertIn("이미 있어요", await self.run_cmd(RaidItems.add, "냉각수", "🧊", 1))
        self.assertIn("고쳤어요", await self.run_cmd(RaidItems.edit, "냉각수", heal=500))
        self.assertEqual((self.items()[-1]["heal"], self.items()[-1]["amount"]), (500, 3))          # 비운 칸은 그대로
        await self.run_cmd(RaidItems.edit, "응급 배터리", name="대형 배터리", emoji="⚡", amount=5)
        self.assertEqual(self.items()[0], {"id": 1, "name": "대형 배터리", "emoji": "⚡", "heal": 300, "amount": 5})
        self.assertIn("이미 있어요", await self.run_cmd(RaidItems.edit, "냉각수", name="대형 배터리"))
        self.assertIn("바꿀 칸", await self.run_cmd(RaidItems.edit, "냉각수"))
        self.assertIn("유니코드 이모지", await self.run_cmd(RaidItems.edit, "냉각수", emoji="abc"))
        self.assertIn("없어요", await self.run_cmd(RaidItems.edit, "없는것", heal=1))
        self.assertIn("냉각수", await self.run_cmd(RaidItems.show))
        self.assertIn("지웠어요", await self.run_cmd(RaidItems.delete, "냉각수"))
        self.assertEqual([i["name"] for i in self.items()], ["대형 배터리"])
        names = [c.value for c in await self.cog.item_names(self.w.interaction(admin=True), "")]
        self.assertEqual(names, ["대형 배터리"])

    async def test_limits_and_admin_only(self):
        for n in range(4):
            await self.run_cmd(RaidItems.add, f"아이템{n}", "🔋", 10)
        self.assertIn("최대 5종", await self.run_cmd(RaidItems.add, "여섯째", "🔋", 10))
        self.assertEqual(len(self.items()), 5)
        self.assertIn("관리자만", await self.run_cmd(RaidItems.add, "몰래", "🔋", 10, admin=False))
        self.assertIn("관리자만", await self.run_cmd(RaidItems.delete, "아이템0", admin=False))
        self.assertIn("이모지", await self.run_cmd(RaidItems.add, "이상", "abc", 10))


if __name__ == "__main__":
    unittest.main()
