"""롤플레잉: COM(재화이자 경험치)·레벨(사진 계산식)·직업·일하기·환전(10 RAM = 1 COM)·룰렛·레벨 역할 보상·
채팅/음성/출석체크 경험치·COM 상점(텍스트·역할), 명령어."""
import datetime
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord

import rp
from cogs.rp import ComShopView, Roleplay, today_kst
from rp import RpError
from store import DEFAULT_SETTINGS, Store, validate_settings
from tests.helpers import Scripted, World, sent_embed

G, A, B = 100, 7, 8
T0 = 1_000_000.0
Choice = discord.app_commands.Choice


def job(**kw):
    data = {"id": 1, "name": "정비공", "emoji": "🔧", "pay": 10, "cooldown": 60, "desc": "", "next": None, "promote_level": 0}
    data.update(kw)
    return data


class LevelTests(unittest.TestCase):
    def test_photo_formula_defaults(self):
        cfg = rp.config({})
        self.assertEqual(cfg["formula"], {"mul": 1, "offset": 1, "div": 10})
        self.assertEqual([rp.total_for(n, cfg) for n in range(5)], [0, 100, 400, 900, 1600])      # ((10L)² − 1) ÷ 1 + 1 = 100L²
        self.assertEqual(rp.formula_text(cfg), "누적 경험치 = ((10 × 현재 레벨)² - 1) ÷ 1 + 1")
        self.assertEqual(rp.level_of(0, cfg), (0, 0, 100))
        self.assertEqual(rp.level_of(99, cfg), (0, 99, 100))
        self.assertEqual(rp.level_of(100, cfg), (1, 0, 300))
        self.assertEqual(rp.level_of(399, cfg), (1, 299, 300))
        self.assertEqual(rp.level_of(400, cfg), (2, 0, 500))
        self.assertEqual(rp.level_of(10 ** 13, cfg)[0], rp.LV_MAX)
        self.assertEqual(rp.level_of(10 ** 13, cfg)[2], 0)

    def test_other_coefficients(self):
        cfg = rp.config({"formula": {"mul": 2, "offset": 10, "div": 5}})
        self.assertEqual(rp.total_for(2, cfg), ((5 * 2) ** 2 - 10) // 2 + 10)              # 55
        self.assertEqual(rp.level_of(55, cfg)[0], 2)
        self.assertEqual(rp.level_of(54, cfg)[0], 1)
        bad = rp.config({"formula": {"mul": 0, "offset": -1, "div": 99999}})
        self.assertEqual(bad["formula"], rp.DEFAULT_FORMULA)

    def test_config_cleaning(self):
        cfg = rp.config({"roulette": [{"name": "a", "times": 1, "chance": 50}], "rewards": [{"level": 5, "role": 1}, {"level": 1, "role": 2},
                                                                                              {"level": 0, "role": 3}], "label": "  ", "mute_mode": "x",
                         "shop": [{"id": 1, "name": "a", "kind": "food", "price": 1}], "chat_xp": -1})
        self.assertEqual([s["chance"] for s in cfg["roulette"]], [70, 25, 5])
        self.assertEqual([r["level"] for r in cfg["rewards"]], [1, 5])
        self.assertEqual((cfg["label"], cfg["mute_mode"], cfg["shop"], cfg["chat_xp"]), ("LV.", "block", [], 30))
        self.assertEqual((cfg["voice_xp"], cfg["attend_xp"], cfg["chat_cooldown"], cfg["mute_percent"]), (0, 1000, 1, 50))    # 사진의 기본값
        validate_settings(dict(DEFAULT_SETTINGS, rp={"jobs": []}))
        with self.assertRaises(ValueError):
            validate_settings(dict(DEFAULT_SETTINGS, rp=[]))


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.cfg = rp.config({"jobs": [job(), job(id=2, name="기술자", pay=30)]})

    def tearDown(self):
        self.store.close()


class WorkTests(Base):
    def test_no_job_then_pay_cooldown_and_level_pay(self):
        with self.assertRaisesRegex(RpError, "직업이 없어요"):
            rp.work(self.store, G, A, self.cfg, now=T0)
        rp.assign(self.store, G, A, 1)
        done = rp.work(self.store, G, A, self.cfg, now=T0)
        self.assertEqual((done["pay"], done["com"]), (10, 10))                           # 레벨 0: 기본 급여
        self.assertEqual(rp.row(self.store, G, A)["exp"], 10)
        with self.assertRaisesRegex(RpError, "쉬는 중"):
            rp.work(self.store, G, A, self.cfg, now=T0 + 59 * 60)
        rp.give(self.store, G, A, 390)                                                   # 누적 400 → 레벨 2
        done = rp.work(self.store, G, A, self.cfg, now=T0 + 3600)
        self.assertEqual(done["pay"], 10 * (100 + 5 * 2) // 100)                        # 레벨 2: +10%
        self.assertEqual(done["before"], 2)

    def test_level_up_is_reported(self):
        rp.assign(self.store, G, A, 2)
        rp.give(self.store, G, A, 80)
        done = rp.work(self.store, G, A, self.cfg, now=T0)
        self.assertEqual((done["before"], done["after"]), (0, 1))

    def test_assign_resets_cooldown_and_deleted_job(self):
        rp.assign(self.store, G, A, 1)
        rp.work(self.store, G, A, self.cfg, now=T0)
        rp.assign(self.store, G, A, 2)
        rp.work(self.store, G, A, self.cfg, now=T0 + 1)
        rp.assign(self.store, G, A, 99)
        with self.assertRaises(RpError):
            rp.work(self.store, G, A, self.cfg, now=T0 + 7200)


class ExchangeTests(Base):
    def test_both_ways_at_ten_to_one(self):
        self.store.add_candy(G, A, 1000)
        done = rp.exchange_to_com(self.store, G, A, 50, 10 ** 10)
        self.assertEqual((done["com"], done["cost"], self.store.wallet(G, A), rp.row(self.store, G, A)["exp"]), (50, 500, 500, 50))
        with self.assertRaisesRegex(RpError, "RAM이 모자라요"):
            rp.exchange_to_com(self.store, G, A, 51, 10 ** 10)
        done = rp.exchange_to_ram(self.store, G, A, 20, 10 ** 10)
        self.assertEqual((done["com"], done["gain"], self.store.wallet(G, A)), (30, 200, 700))
        self.assertEqual(rp.row(self.store, G, A)["exp"], 50)
        with self.assertRaises(RpError):
            rp.exchange_to_ram(self.store, G, A, 31, 10 ** 10)

    def test_round_trips_do_not_farm_exp(self):
        self.store.add_candy(G, A, 1000)
        rp.exchange_to_com(self.store, G, A, 100, 10 ** 10)
        for _ in range(5):
            rp.exchange_to_ram(self.store, G, A, 100, 10 ** 10)
            self.assertEqual(rp.exchange_to_com(self.store, G, A, 100, 10 ** 10)["exp_gain"], 0)
        self.assertEqual(rp.row(self.store, G, A)["exp"], 100)
        rp.exchange_to_ram(self.store, G, A, 30, 10 ** 10)
        self.store.add_candy(G, A, 1000)
        self.assertEqual(rp.exchange_to_com(self.store, G, A, 50, 10 ** 10)["exp_gain"], 20)
        self.assertEqual(rp.row(self.store, G, A)["credit"], 0)

    def test_ram_cap_and_com_cap(self):
        rp.give(self.store, G, A, 100)
        self.store.add_candy(G, A, 995)
        with self.assertRaisesRegex(RpError, "최대"):
            rp.exchange_to_ram(self.store, G, A, 1, 1000)
        self.assertEqual(rp.row(self.store, G, A)["com"], 100)
        with self.store.db:
            self.store.db.execute("UPDATE rp SET com=? WHERE guild=? AND user=?", (rp.COM_MAX, G, A))
        with self.assertRaises(RpError):
            rp.give(self.store, G, A, 1)
        self.assertIsNone(rp.gain(self.store, G, A, 30, self.cfg))                       # 활동 경험치는 막히면 조용히 안 준다


class RouletteTests(Base):
    def spin(self, bet, roll, t=T0):
        return rp.spin(self.store, G, A, bet, self.cfg, Scripted(randoms=[roll]), now=t)

    def test_outcomes_payouts_and_exp(self):
        rp.give(self.store, G, A, 100, exp=False)
        miss = self.spin(10, 0.69)
        self.assertEqual((miss["slot"]["name"], miss["payout"], miss["com"]), ("꽝", 0, 90))
        win = self.spin(10, 0.70, T0 + 10)
        self.assertEqual((win["slot"]["name"], win["payout"], win["com"]), ("당첨", 20, 100))
        jackpot = self.spin(10, 0.95, T0 + 20)
        self.assertEqual((jackpot["slot"]["name"], jackpot["payout"], jackpot["com"]), ("초당첨", 50, 140))
        self.assertEqual(rp.row(self.store, G, A)["exp"], 10 + 40)

    def test_limits(self):
        rp.give(self.store, G, A, 5, exp=False)
        with self.assertRaisesRegex(RpError, "모자라요"):
            self.spin(10, 0.5)
        with self.assertRaisesRegex(RpError, "까지 걸 수"):
            self.spin(1001, 0.5)
        self.spin(1, 0.5)
        with self.assertRaisesRegex(RpError, "초 뒤"):
            self.spin(1, 0.5, T0 + 9)
        self.cfg["roulette"] = [{"name": "꽝", "times": 0, "chance": 0}, {"name": "당첨", "times": 2, "chance": 0},
                                {"name": "초당첨", "times": 5, "chance": 100}]
        self.assertEqual(self.spin(1, 0.0, T0 + 20)["slot"]["name"], "초당첨")


class ActivityTests(Base):
    def test_attendance_once_a_day(self):
        amount, before, after = rp.attend(self.store, G, A, self.cfg, "2026-11-15")
        self.assertEqual((amount, before, after), (1000, 0, 3))                          # 1000 → 레벨 3(900)
        self.assertEqual(rp.row(self.store, G, A)["com"], 1000)                          # 경험치 = COM
        with self.assertRaisesRegex(RpError, "이미 출석체크"):
            rp.attend(self.store, G, A, self.cfg, "2026-11-15")
        rp.attend(self.store, G, A, self.cfg, "2026-11-16")
        self.assertEqual(rp.row(self.store, G, A)["exp"], 2000)

    def test_today_is_korean_time(self):
        late = datetime.datetime(2026, 11, 15, 15, 30, tzinfo=datetime.timezone.utc)    # 한국 시간 16일 0시 30분
        self.assertEqual(today_kst(late), "2026-11-16")

    def test_voice_amount_and_mute_modes(self):
        cfg = rp.config({"voice_xp": 20, "mute_percent": 50})
        self.assertEqual(rp.voice_amount(cfg, False, False), 20)
        self.assertEqual(rp.voice_amount(cfg, True, False), 0)                            # 기본: XP 차단
        cfg["mute_mode"] = "reduce"
        self.assertEqual(rp.voice_amount(cfg, False, True), 10)
        cfg["mute_percent"] = 33
        self.assertEqual(rp.voice_amount(dict(cfg, voice_xp=7), True, True), 4)           # 소수점은 버린다(7×67%=4.69)
        cfg["mute_mode"] = "off"
        self.assertEqual(rp.voice_amount(cfg, True, True), 20)

    def test_gain_reports_levels(self):
        self.assertEqual(rp.gain(self.store, G, A, 150, self.cfg), (0, 1))
        self.assertIsNone(rp.gain(self.store, G, A, 0, self.cfg))


class TransferTests(Base):
    def test_only_jobs_with_the_ability_can_give_and_no_exp_moves(self):
        self.cfg["jobs"].append(job(id=3, name="은행원", give=True))
        rp.give(self.store, G, A, 100)
        with self.assertRaisesRegex(RpError, "능력이 있는 직업"):
            rp.transfer(self.store, G, A, B, 10, self.cfg)                               # 무직
        rp.assign(self.store, G, A, 1)
        with self.assertRaisesRegex(RpError, "능력이 있는 직업"):
            rp.transfer(self.store, G, A, B, 10, self.cfg)                               # 능력 없는 직업
        rp.assign(self.store, G, A, 3)
        self.assertEqual(rp.transfer(self.store, G, A, B, 30, self.cfg), (70, 30))
        self.assertEqual((rp.row(self.store, G, A)["exp"], rp.row(self.store, G, B)["exp"]), (100, 0))     # 경험치는 옮기지 않는다
        with self.assertRaisesRegex(RpError, "모자라요"):
            rp.transfer(self.store, G, A, B, 71, self.cfg)
        with self.assertRaisesRegex(RpError, "나에게는"):
            rp.transfer(self.store, G, A, A, 1, self.cfg)
        with self.store.db:
            self.store.db.execute("UPDATE rp SET com=? WHERE guild=? AND user=?", (rp.COM_MAX, G, B))
        with self.assertRaisesRegex(RpError, "최대"):
            rp.transfer(self.store, G, A, B, 1, self.cfg)
        self.assertEqual(rp.row(self.store, G, A)["com"], 70)                            # 거절되면 그대로


class ShopTests(Base):
    def test_spend_and_refund_keep_exp(self):
        rp.give(self.store, G, A, 100)
        self.assertEqual(rp.spend(self.store, G, A, 30), 70)
        with self.assertRaisesRegex(RpError, "모자라요"):
            rp.spend(self.store, G, A, 71)
        rp.refund(self.store, G, A, 30)
        self.assertEqual((rp.row(self.store, G, A)["com"], rp.row(self.store, G, A)["exp"]), (100, 100))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.w = World(Scripted(randoms=[0.99] * 5))
        self.store, self.bot = self.w.store, self.w.bot
        self.cog = Roleplay(self.bot)
        for uid in (A, B):
            self.w.new_member(uid)
        self.role = NS(id=555, mention="<@&555>", managed=False, is_default=lambda: False)
        self.channel = self.w.channel(401)
        self.channel.send = AsyncMock()
        self.w.guild.get_role = lambda rid: self.role if rid == 555 else None
        self.w.guild.get_channel = lambda cid: self.channel if cid == 401 else None

    async def asyncTearDown(self):
        await self.w.close()

    async def run_cmd(self, command, *args, uid=A, admin=True, **kwargs):
        i = self.w.interaction(uid=uid, admin=admin)
        await command.callback(self.cog, i, *args, **kwargs)
        embed, _ = sent_embed(i)
        return (embed.title or "") + (embed.description or "") + "".join(f.name + f.value for f in embed.fields)

    def cfg(self):
        return rp.config(self.store.settings(G)["rp"])

    async def test_admin_job_flow_and_work(self):
        self.assertIn("만들었어요", await self.run_cmd(Roleplay.job_add, "정비공", "🔧", 10, 60, "기계를 고쳐요"))
        await self.run_cmd(Roleplay.job_add, "기술자", "⚙️", 30)
        self.assertIn("이미 있어요", await self.run_cmd(Roleplay.job_add, "정비공", "🔧", 10))
        self.assertIn("이모지", await self.run_cmd(Roleplay.job_add, "이상", "abc", 10))
        self.assertIn("승급", await self.run_cmd(Roleplay.job_edit, "정비공", promote_to="기술자", promote_level=3))
        self.assertIn("직업이 없어요", await self.run_cmd(Roleplay.work, admin=False))
        self.assertIn("정비공", await self.run_cmd(Roleplay.appoint, self.w.members[A], "정비공"))
        self.assertIn("10 COM", await self.run_cmd(Roleplay.work, admin=False))
        profile = await self.run_cmd(Roleplay.profile, admin=False)
        self.assertIn("기계를 고쳐요", profile)
        self.assertIn("LV. 0", profile)
        self.assertIn("LV 3이 필요해요", await self.run_cmd(Roleplay.promote, self.w.members[A]))
        await self.run_cmd(Roleplay.give_com, self.w.members[A], 900)
        self.assertIn("승급했어요", await self.run_cmd(Roleplay.promote, self.w.members[A]))
        self.assertEqual(rp.row(self.store, G, A)["job"], 2)
        self.assertIn("기술자", await self.run_cmd(Roleplay.job_list, admin=False))
        self.assertIn("1명이 무직", await self.run_cmd(Roleplay.job_delete, "기술자"))
        self.assertIsNone(rp.row(self.store, G, A)["job"])
        self.assertIsNone(self.cfg()["jobs"][0]["next"])
        self.assertIn("관리자만", await self.run_cmd(Roleplay.job_add, "몰래", "🔧", 1, admin=False))
        self.assertEqual([c.value for c in await self.cog.job_names(self.w.interaction(admin=True), "")], ["정비공"])

    async def test_level_rewards_are_given_on_level_up(self):
        self.assertIn("LV 1", await self.run_cmd(Roleplay.level_reward, 1, self.role, Choice(name="추가", value="add")))
        member = self.w.members[A]
        member.add_roles = AsyncMock()
        text = await self.run_cmd(Roleplay.give_com, member, 100)
        self.assertIn("LV. 0 → LV. 1", text)
        member.add_roles.assert_awaited_once()
        member.add_roles.side_effect = discord.HTTPException(NS(status=403, reason="x"), "no")
        await self.run_cmd(Roleplay.level_reward, 2, self.role, Choice(name="추가", value="add"))
        self.assertIn("주지 못했어요", await self.run_cmd(Roleplay.give_com, member, 300))
        self.assertIn("그런 보상이 없어요", await self.run_cmd(Roleplay.level_reward, 9, self.role, Choice(name="삭제", value="remove")))

    async def test_exchange_roulette_and_attendance_commands(self):
        self.store.add_candy(G, A, 1000)
        self.assertIn("50 COM", await self.run_cmd(Roleplay.exchange, Choice(name="", value="to_com"), 50, admin=False))
        self.assertIn("LV은 그대로", await self.run_cmd(Roleplay.exchange, Choice(name="", value="to_ram"), 10, admin=False))
        self.assertIn("EXP로 치지 않아요", await self.run_cmd(Roleplay.exchange, Choice(name="", value="to_com"), 10, admin=False))
        self.assertIn("초당첨", await self.run_cmd(Roleplay.roulette, 10, admin=False))
        self.assertIn("1,000 COM", await self.run_cmd(Roleplay.attendance, admin=False))
        self.assertIn("이미 출석체크", await self.run_cmd(Roleplay.attendance, admin=False))

    async def test_give_command_and_job_ability_setting(self):
        await self.run_cmd(Roleplay.job_add, "은행원", "🏦", 10, give=True)
        self.assertTrue(rp.job_by_name(self.cfg(), "은행원")["give"])                       # 만들 때 고른 능력이 저장된다
        await self.run_cmd(Roleplay.job_add, "정비공", "🔧", 10)
        await self.run_cmd(Roleplay.appoint, self.w.members[A], "정비공")
        rp.give(self.store, G, A, 100)
        self.assertIn("능력이 있는 직업", await self.run_cmd(Roleplay.give, self.w.members[B], 10, admin=False))
        self.assertIn("COM 주기 능력", await self.run_cmd(Roleplay.job_edit, "정비공", give=True))
        self.assertIn("줬어요", await self.run_cmd(Roleplay.give, self.w.members[B], 10, admin=False))
        self.assertEqual(rp.row(self.store, G, B)["com"], 10)
        self.assertIn("직업 능력", await self.run_cmd(Roleplay.profile, admin=False))
        self.assertIn("🤝 COM 주기", await self.run_cmd(Roleplay.job_list, admin=False))
        robot = self.w.new_member(77)
        robot.bot = True
        self.assertIn("봇에게는", await self.run_cmd(Roleplay.give, robot, 10, admin=False))
        await self.run_cmd(Roleplay.job_edit, "정비공", give=False)
        self.assertIn("능력이 있는 직업", await self.run_cmd(Roleplay.give, self.w.members[B], 10, admin=False))

    async def test_admin_settings(self):
        self.assertIn("합이 100", await self.run_cmd(Roleplay.roulette_rates, 60, 30, 5))
        self.assertIn("꽝 x0 50%", await self.run_cmd(Roleplay.roulette_rates, 50, 40, 10))
        await self.run_cmd(Roleplay.roulette_limits, 500, 30)
        self.assertEqual((self.cfg()["bet_max"], self.cfg()["spin_cooldown"]), (500, 30))
        text = await self.run_cmd(Roleplay.level_formula, 2, 10, 5, 20)
        self.assertIn("((5 × 현재 레벨)² - 10) ÷ 2 + 10", text)
        self.assertEqual((self.cfg()["formula"], self.cfg()["pay_step"]), ({"mul": 2, "offset": 10, "div": 5}, 20))
        self.assertIn("((10 × 현재 레벨)² - 1) ÷ 1 + 1", await self.run_cmd(Roleplay.level_formula, reset=True))
        text = await self.run_cmd(Roleplay.xp_settings, "레벨", 5, 7, 300, 10)
        self.assertEqual((self.cfg()["label"], self.cfg()["chat_xp"], self.cfg()["voice_xp"], self.cfg()["attend_xp"], self.cfg()["chat_cooldown"]),
                         ("레벨", 5, 7, 300, 10))
        await self.run_cmd(Roleplay.xp_settings, chat=0)
        self.assertEqual((self.cfg()["chat_xp"], self.cfg()["voice_xp"]), (0, 7))           # 0도 정할 수 있고 비운 칸은 그대로
        self.assertIn("40% 줄어듦", await self.run_cmd(Roleplay.voice_limit, Choice(name="", value="reduce"), 40))
        self.assertIn("관리자만", await self.run_cmd(Roleplay.roulette_rates, 70, 25, 5, admin=False))

    async def test_chat_xp_with_cooldown_and_level_message(self):
        self.store.update_settings(G, rp={"chat_xp": 60, "chat_cooldown": 10})
        channel = NS(send=AsyncMock())
        author = self.w.members[A]
        author.add_roles = AsyncMock()
        msg = NS(guild=self.w.guild, author=author, webhook_id=None, channel=channel)
        await self.cog.on_message(msg)
        await self.cog.on_message(msg)                                                   # 쿨타임 안: 안 준다
        self.assertEqual(rp.row(self.store, G, A)["com"], 60)
        self.cog.last_chat[(G, A)] -= 11
        await self.cog.on_message(msg)                                                   # 120 → 레벨 1
        channel.send.assert_awaited_once()
        bot_msg = NS(guild=self.w.guild, author=NS(id=B, bot=True), webhook_id=None, channel=channel)
        await self.cog.on_message(bot_msg)
        self.assertEqual(rp.row(self.store, G, B)["com"], 0)

    async def test_voice_tick(self):
        self.store.update_settings(G, rp={"voice_xp": 10})
        talk = NS(id=A, bot=False, voice=NS(self_mute=False, self_deaf=False))
        muted = NS(id=B, bot=False, voice=NS(self_mute=True, self_deaf=False))
        robot = NS(id=9, bot=True, voice=NS(self_mute=False, self_deaf=False))
        afk_member = NS(id=10, bot=False, voice=NS(self_mute=False, self_deaf=False))
        guild = NS(id=G, afk_channel=NS(id=2), voice_channels=[NS(id=1, members=[talk, muted, robot]), NS(id=2, members=[afk_member])],
                   stage_channels=[])
        self.cog.bot = NS(store=self.store, guilds=[guild], allowed_guild=lambda gid: True)    # 봇이 들어간 서버 목록을 흉내 낸다
        self.assertEqual(await self.cog.voice_tick(), 1)
        self.assertEqual([rp.row(self.store, G, u)["com"] for u in (A, B, 9, 10)], [10, 0, 0, 0])

    async def test_com_shop_text_and_role_items(self):
        self.assertIn("알림 채널", await self.run_cmd(Roleplay.shop_add, "닉네임 변경권", 50, Choice(name="", value="text"), desc="닉네임을 바꿔 줘요"))
        self.assertIn("역할을 골라", await self.run_cmd(Roleplay.shop_add, "VIP", 100, Choice(name="", value="role")))
        await self.run_cmd(Roleplay.shop_add, "VIP", 100, Choice(name="", value="role"), self.role, "👑")
        rp.give(self.store, G, A, 200, exp=False)
        i = self.w.interaction(uid=A)
        await Roleplay.com_shop.callback(self.cog, i)
        view = i.response.send_message.call_args.kwargs["view"]
        self.assertIsInstance(view, ComShopView)
        self.assertFalse(await view.interaction_check(self.w.interaction(uid=B)))
        buyer = self.w.interaction(uid=A)
        await view.finish(buyer, {"id": 1}, "새 닉네임: 고양이")                           # 알림 채널이 없으면 살 수 없다
        self.assertIn("알림 채널을 정하지", buyer.response.edit_message.call_args.kwargs["embed"].description)
        self.assertEqual(rp.row(self.store, G, A)["com"], 200)
        await self.run_cmd(Roleplay.shop_channel, self.channel)
        buyer = self.w.interaction(uid=A)
        await view.finish(buyer, {"id": 1}, "새 닉네임: 고양이")
        sent = self.channel.send.call_args.kwargs["embed"]
        self.assertIn("닉네임 변경권", sent.description)
        self.assertIn("새 닉네임: 고양이", "".join(f.value for f in sent.fields))
        self.assertEqual(rp.row(self.store, G, A)["com"], 150)
        member = self.w.members[A]
        member.add_roles = AsyncMock()
        await view.finish(self.w.interaction(uid=A), {"id": 2}, "")
        member.add_roles.assert_awaited_once()
        self.assertEqual(rp.row(self.store, G, A)["com"], 50)
        member.roles = [self.role]
        buyer = self.w.interaction(uid=A)
        rp.give(self.store, G, A, 100, exp=False)
        await view.finish(buyer, {"id": 2}, "")                                          # 이미 가진 역할은 못 산다
        self.assertIn("이미", buyer.response.edit_message.call_args.kwargs["embed"].description)
        member.roles = []
        member.add_roles.side_effect = discord.HTTPException(NS(status=403, reason="x"), "no")
        await view.finish(self.w.interaction(uid=A), {"id": 2}, "")                      # 못 주면 돌려준다
        self.assertEqual(rp.row(self.store, G, A)["com"], 150)

    async def test_shop_picking_text_opens_request_box_and_admin_edits(self):
        await self.run_cmd(Roleplay.shop_add, "닉네임 변경권", 50, Choice(name="", value="text"))
        view = ComShopView(self.cog, A, G)
        pick = self.w.interaction(uid=A, data={"values": ["1"]})
        await view.pick(pick)
        pick.response.send_modal.assert_awaited()                                        # 텍스트 물품은 요청 내용을 받는다
        await self.run_cmd(Roleplay.shop_edit, "닉네임 변경권", price=70, name="닉변권")
        self.assertEqual((self.cfg()["shop"][0]["name"], self.cfg()["shop"][0]["price"]), ("닉변권", 70))
        self.assertIn("역할은 역할 물품에만", await self.run_cmd(Roleplay.shop_edit, "닉변권", role=self.role))
        self.assertEqual([c.value for c in await self.cog.shop_names(self.w.interaction(admin=True), "")], ["닉변권"])
        await self.run_cmd(Roleplay.shop_delete, "닉변권")
        self.assertEqual(self.cfg()["shop"], [])
        self.assertIn("관리자만", await self.run_cmd(Roleplay.shop_add, "몰래", 1, Choice(name="", value="text"), admin=False))


if __name__ == "__main__":
    unittest.main()
