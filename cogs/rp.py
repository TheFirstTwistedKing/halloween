"""롤플레잉 명령어: /프로필 /일하기 /환전 /룰렛 /직업목록, 관리자 /롤플관리 …. 규칙은 rp.py에 있다."""
from __future__ import annotations

import datetime
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import rp
import theme
from access import require_admin
from rp import COM_NAME, RpError, money
from owner import owner_only
from store import valid_emoji

LOG = logging.getLogger("halloween")
Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
KST = datetime.timezone(datetime.timedelta(hours=9))
VOICE_MINUTES = 5                 # 음성 경험치를 주는 간격(분)


def today_kst(now: datetime.datetime | None = None) -> str:
    return (now or datetime.datetime.now(KST)).astimezone(KST).strftime("%Y-%m-%d")


def bar(value: int, maximum: int, width: int = 10) -> str:
    filled = 0 if maximum <= 0 else max(0, min(width, value * width // maximum))
    return "🟦" * filled + "⬛" * (width - filled)


class Roleplay(commands.Cog):
    """🎭 롤플레잉."""

    bank = app_commands.Group(name="금고", description=f"내 COM을 보관하는 개인 금고(최대 {rp.BANK_MAX:,} COM)", guild_only=True)
    admin = app_commands.Group(name="롤플관리", description="롤플레잉의 직업·레벨·룰렛·레벨 보상을 정해요 (관리자)", **PERMS)

    def __init__(self, bot):
        self.bot = bot
        self.last_chat: dict[tuple[int, int], float] = {}

    async def cog_load(self):
        self.voice_loop.start()

    async def cog_unload(self):
        self.voice_loop.cancel()

    # ---------------------------------------------------------------- 활동 경험치(= COM): 채팅·음성
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or message.webhook_id is not None:
            return
        if hasattr(self.bot, "allowed_guild") and not self.bot.allowed_guild(message.guild.id):
            return
        cfg = self.cfg(message.guild.id)
        if cfg["chat_xp"] <= 0:
            return
        key, now = (message.guild.id, message.author.id), time.time()
        if now - self.last_chat.get(key, 0.0) < cfg["chat_cooldown"]:
            return
        self.last_chat[key] = now
        result = rp.gain(self.bot.store, message.guild.id, message.author.id, cfg["chat_xp"], cfg)
        if result and result[1] > result[0]:                       # 레벨이 오르면 그 채널에 알리고 레벨 보상을 준다
            text = await self.level_up(message.author, cfg, *result)
            try:
                await message.channel.send(embed=theme.notice(f"{message.author.mention} {text}"), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    @tasks.loop(minutes=VOICE_MINUTES)
    async def voice_loop(self):
        try:
            await self.voice_tick()
        except Exception:
            LOG.exception("음성 경험치 지급 중 오류")

    @voice_loop.before_loop
    async def before_voice(self):
        await self.bot.wait_until_ready()

    async def voice_tick(self) -> int:
        """음성 채널(잠수 채널 제외)에 있는 사람(봇 제외)에게 경험치(=COM)를 준다. 준 사람 수."""
        given = 0
        for guild in list(getattr(self.bot, "guilds", [])):
            if hasattr(self.bot, "allowed_guild") and not self.bot.allowed_guild(guild.id):
                continue
            cfg = self.cfg(guild.id)
            if cfg["voice_xp"] <= 0:
                continue
            afk = getattr(guild, "afk_channel", None)
            for channel in list(guild.voice_channels) + list(getattr(guild, "stage_channels", [])):
                if afk is not None and channel.id == afk.id:
                    continue
                for member in channel.members:
                    voice = member.voice
                    if member.bot or voice is None:
                        continue
                    amount = rp.voice_amount(cfg, bool(voice.self_mute), bool(voice.self_deaf))
                    result = rp.gain(self.bot.store, guild.id, member.id, amount, cfg)
                    if result:
                        given += 1
                        if result[1] > result[0]:
                            await self.level_up(member, cfg, *result)
        return given

    # ---------------------------------------------------------------- 공통
    def cfg(self, guild_id: int) -> dict:
        """롤플레잉 설정. 채팅마다 불리므로 설정 전체를 복사하지 않고 캐시를 읽는다(rp.config가 새 사전·목록을 만들어서 캐시는 바뀌지 않는다)."""
        store = self.bot.store
        raw = getattr(store, "_settings", {}).get(guild_id) or store.settings(guild_id)
        return rp.config(raw["rp"])

    def save(self, guild_id: int, cfg: dict) -> None:
        self.bot.store.update_settings(guild_id, rp=cfg)

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    async def level_up(self, member, cfg: dict, before: int, after: int) -> str:
        """LV이 올랐으면 그 사이 레벨 보상 역할을 주고 안내 문장을 돌려준다."""
        if after <= before:
            return ""
        lines = [f"🎉 **{cfg['label']} {before} → {cfg['label']} {after}**!"]
        guild = getattr(member, "guild", None)
        for reward in rp.rewards_between(cfg, before, after):
            role = guild.get_role(reward["role"]) if guild else None
            if role is None:
                lines.append(f"⚠️ LV {reward['level']} 보상 역할이 지워졌어요(관리자에게 알려 주세요).")
                continue
            try:
                await member.add_roles(role, reason=f"롤플레잉 LV {reward['level']} 보상")
                lines.append(f"🎁 LV {reward['level']} 보상: {role.mention} 역할을 받았어요!")
            except (discord.HTTPException, AttributeError):
                lines.append(f"⚠️ LV {reward['level']} 보상 {role.mention}을(를) 주지 못했어요(봇 권한·역할 순서를 확인해 주세요).")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 사람들이 쓰는 명령어
    @app_commands.command(name="프로필", description="롤플레잉 프로필(직업·LV·COM)을 봐요")
    @app_commands.guild_only()
    @app_commands.rename(member="사람")
    @app_commands.describe(member="볼 사람(비우면 나)")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        store, gid = self.bot.store, interaction.guild_id
        who = member or interaction.user
        cfg, me = self.cfg(gid), rp.row(store, gid, who.id)
        level, have, need = rp.level_of(me["exp"], cfg)
        job = rp.job_by_id(cfg, me["job"])
        title = f"{job['emoji']} {job['name']}" if job else "🙂 무직"
        embed = theme.make_embed(f"🎭 {getattr(who, 'display_name', None) or getattr(who, 'name', None) or who.id}의 프로필",
                                 f"**{title}** · **{cfg['label']} {level}**", color=theme.PURPLE)
        if job and job.get("desc"):
            embed.add_field(name="📜 직업 소개", value=job["desc"][:1024], inline=False)
        if job and job.get("give"):
            embed.add_field(name="🧰 직업 능력", value="🤝 COM 주기(`/com주기`)", inline=False)
        embed.add_field(name="✨ 경험치", inline=False,
                        value=f"{bar(have, need)} {have:,}/{need:,} (누적 {me['exp']:,})" if need else f"최고 레벨이에요! (누적 {me['exp']:,})")
        embed.add_field(name=f"{rp.COM} {COM_NAME}", value=f"{me['com']:,}", inline=True)
        embed.add_field(name="🏦 금고", value=f"{me['bank']:,}", inline=True)
        if job:
            embed.add_field(name="💼 급여", value=f"{money(rp.pay_for(job, level, cfg))} · {job['cooldown']}분마다", inline=True)
            _job, nxt, need_lv = rp.can_promote(store, gid, who.id, cfg)
            if nxt:
                embed.add_field(name="⬆️ 승급", inline=True,
                                value=f"{cfg['label']} {need_lv}에 {nxt['emoji']} {nxt['name']}" + (" — **승급할 수 있어요!**(관리자에게)" if level >= need_lv else ""))
        upcoming = next((r for r in cfg["rewards"] if r["level"] > level), None)
        if upcoming:
            embed.add_field(name="🎁 다음 레벨 보상", value=f"{cfg['label']} {upcoming['level']}: <@&{upcoming['role']}>", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="일하기", description="내 직업으로 일해서 급여(COM)를 받아요. 받은 COM은 EXP로도 쌓여요")
    @app_commands.guild_only()
    async def work(self, interaction: discord.Interaction):
        store, gid = self.bot.store, interaction.guild_id
        cfg = self.cfg(gid)
        try:
            done = rp.work(store, gid, interaction.user.id, cfg)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        job = done["job"]
        text = f"{job['emoji']} **{job['name']}**(으)로 일하고 {money(done['pay'])}을(를) 받았어요! (가진 것 {done['com']:,})"
        extra = await self.level_up(interaction.user, cfg, done["before"], done["after"])
        await self._say(interaction, text + ("\n" + extra if extra else "") + f"\n다음 일은 {job['cooldown']}분 뒤에 할 수 있어요.")

    @app_commands.command(name="환전", description=f"RAM과 COM을 바꿔요. {rp.EXCHANGE_RATE} RAM = 1 COM (양방향)")
    @app_commands.guild_only()
    @app_commands.rename(direction="방향", amount="수량")
    @app_commands.describe(direction="바꾸는 방향", amount="받거나 낼 COM 수량")
    @app_commands.choices(direction=[Choice(name=f"RAM → COM ({rp.EXCHANGE_RATE} RAM당 1 COM)", value="to_com"),
                                     Choice(name=f"COM → RAM (1 COM당 {rp.EXCHANGE_RATE} RAM)", value="to_ram")])
    async def exchange(self, interaction: discord.Interaction, direction: Choice[str], amount: app_commands.Range[int, 1, rp.COM_MAX]):
        store, gid = self.bot.store, interaction.guild_id
        cfg, cur = self.cfg(gid), store.currency(gid)
        before = rp.level_of(rp.row(store, gid, interaction.user.id)["exp"], cfg)[0]
        try:
            if direction.value == "to_com":
                done = rp.exchange_to_com(store, gid, interaction.user.id, amount, cur.max)
            else:
                done = rp.exchange_to_ram(store, gid, interaction.user.id, amount, cur.max)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        if direction.value == "to_com":
            text = f"💱 {cur.money(done['cost'])}을(를) {money(amount)}(으)로 바꿨어요. (가진 {COM_NAME} {done['com']:,})"
            if done["exp_gain"] < amount:
                text += f"\n(전에 RAM으로 바꿨던 {amount - done['exp_gain']:,} {COM_NAME}만큼은 EXP로 치지 않아요)"
            text += "\n" + (await self.level_up(interaction.user, cfg, before, rp.level_of(done["exp"], cfg)[0]))
        else:
            text = f"💱 {money(amount)}을(를) {cur.money(done['gain'])}(으)로 바꿨어요. (가진 {COM_NAME} {done['com']:,}, LV은 그대로예요)"
        await self._say(interaction, text.strip())

    @app_commands.command(name="룰렛", description="COM을 걸고 룰렛을 돌려요. 꽝 x0 · 당첨 x2 · 초당첨 x5")
    @app_commands.guild_only()
    @app_commands.rename(bet="걸기")
    @app_commands.describe(bet="걸 COM")
    async def roulette(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1, rp.COM_MAX]):
        store, gid = self.bot.store, interaction.guild_id
        cfg = self.cfg(gid)
        try:
            done = rp.spin(store, gid, interaction.user.id, bet, cfg, self.bot.rng)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        slot = done["slot"]
        icon = "💥" if slot["times"] == 0 else ("🌟" if slot["times"] >= 5 else "🎉")
        text = (f"🎰 {money(bet)}을(를) 걸고 돌렸어요… {icon} **{slot['name']}** (x{slot['times']})\n"
                + (f"{money(done['payout'])}을(를) 받았어요!" if done["payout"] else "아무것도 못 받았어요.") + f" (가진 {COM_NAME} {done['com']:,})")
        extra = await self.level_up(interaction.user, cfg, done["before"], done["after"])
        await self._say(interaction, text + ("\n" + extra if extra else ""), slot["times"] > 0)

    @app_commands.command(name="출석체크", description="하루 한 번 출석체크하고 경험치(=COM)를 받아요")
    @app_commands.guild_only()
    async def attendance(self, interaction: discord.Interaction):
        store, gid = self.bot.store, interaction.guild_id
        cfg = self.cfg(gid)
        try:
            amount, before, after = rp.attend(store, gid, interaction.user.id, cfg, today_kst())
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        extra = await self.level_up(interaction.user, cfg, before, after)
        await self._say(interaction, f"📅 출석체크 완료! 경험치 {money(amount)}을(를) 받았어요." + ("\n" + extra if extra else ""))

    @app_commands.command(name="com상점", description="COM으로 물품(텍스트·역할)을 사는 상점을 열어요")
    @app_commands.guild_only()
    async def com_shop(self, interaction: discord.Interaction):
        cfg = self.cfg(interaction.guild_id)
        await interaction.response.send_message(embed=shop_embed(self.bot.store, interaction.guild_id, interaction.user.id, cfg),
                                                view=ComShopView(self, interaction.user.id, interaction.guild_id), ephemeral=False)

    async def buy(self, interaction: discord.Interaction, item_id: int, request: str = "") -> str:
        """물품을 산다. 텍스트는 알림 채널에 올리고, 역할은 준다. 주지 못하면 COM을 돌려준다. 결과 문장(실패는 RpError)."""
        store, gid, user = self.bot.store, interaction.guild_id, interaction.user
        cfg = self.cfg(gid)
        item = next((i for i in cfg["shop"] if i["id"] == item_id), None)
        if item is None:
            raise RpError("이제 상점에 없는 물품이에요.")
        role = channel = None
        if item["kind"] == "role":
            role = interaction.guild.get_role(item.get("role") or 0)
            if role is None:
                raise RpError("이 물품의 역할이 지워졌어요. 관리자에게 알려 주세요.")
            if any(r.id == role.id for r in getattr(user, "roles", [])):
                raise RpError(f"이미 {role.mention} 역할이 있어요.")
        else:
            channel = interaction.guild.get_channel(cfg["shop_channel"] or 0)
            if channel is None or not hasattr(channel, "send"):
                raise RpError("관리자가 아직 알림 채널을 정하지 않아서 텍스트 물품을 살 수 없어요(`/롤플관리 알림채널`).")
        left = rp.spend(store, gid, user.id, item["price"])
        try:
            if role is not None:
                await user.add_roles(role, reason=f"COM 상점: {item['name']}")
                result = f"🎭 {role.mention} 역할을 받았어요!"
            else:
                embed = theme.make_embed("🛒 COM 상점 구매", f"{user.mention}님이 {item['emoji']} **{item['name']}**을(를) 샀어요. ({money(item['price'])})",
                                         color=theme.GOLD)
                if item.get("desc"):
                    embed.add_field(name="📦 물품 내용", value=item["desc"][:1024], inline=False)
                if request.strip():
                    embed.add_field(name="✍️ 요청 내용", value=request.strip()[:1024], inline=False)
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                result = f"📨 관리자에게 알렸어요({channel.mention}). 곧 처리해 줄 거예요."
        except (discord.HTTPException, AttributeError):
            rp.refund(store, gid, user.id, item["price"])
            raise RpError("물품을 주지 못해서 COM을 돌려줬어요(봇 권한을 확인해 달라고 관리자에게 알려 주세요).")
        LOG.info("COM 상점: %s → %s (%s COM, 서버 %s)", user.id, item["id"], item["price"], gid)
        return f"{item['emoji']} **{item['name']}**을(를) {money(item['price'])}에 샀어요! (남은 {COM_NAME} {left:,})\n{result}"

    @app_commands.command(name="com주기", description="다른 사람에게 COM을 줘요. 'COM 주기' 능력이 있는 직업만 쓸 수 있어요")
    @app_commands.guild_only()
    @app_commands.rename(member="사람", amount="수량")
    @app_commands.describe(member="COM을 받을 사람", amount="줄 COM(받은 사람의 경험치는 늘지 않아요)")
    async def give(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, rp.COM_MAX]):
        store, gid = self.bot.store, interaction.guild_id
        if member.bot:
            await self._say(interaction, "봇에게는 줄 수 없어요.", False)
            return
        try:
            left, got = rp.transfer(store, gid, interaction.user.id, member.id, amount, self.cfg(gid))
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        LOG.info("COM 주기: %s → %s, %d (서버 %s)", interaction.user.id, member.id, amount, gid)
        await self._say(interaction, f"🤝 {member.mention}님에게 {money(amount)}을(를) 줬어요. (남은 {COM_NAME} {left:,})\n"
                                     f"받은 COM은 경험치(레벨)로 치지 않아요.")

    @app_commands.command(name="com선물", description="다른 사람에게 내 지갑의 COM을 선물해요. 누구나 쓸 수 있어요")
    @app_commands.guild_only()
    @app_commands.rename(member="사람", amount="수량")
    @app_commands.describe(member="선물 받을 사람", amount="선물할 COM(받은 사람의 경험치는 늘지 않아요)")
    async def gift(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, rp.COM_MAX]):
        store, gid = self.bot.store, interaction.guild_id
        if member.bot:
            await self._say(interaction, "봇에게는 선물할 수 없어요.", False)
            return
        try:
            left, _got = rp.gift(store, gid, interaction.user.id, member.id, amount)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        LOG.info("COM 선물: %s → %s, %d (서버 %s)", interaction.user.id, member.id, amount, gid)
        await self._say(interaction, f"🎁 {member.mention}님에게 {money(amount)}을(를) 선물했어요. (남은 {COM_NAME} {left:,})\n"
                                     f"받은 COM은 경험치(레벨)로 치지 않아요.")

    # ---------------------------------------------------------------- 개인 금고
    @bank.command(name="보기", description="내 금고와 지갑의 COM을 봐요")
    async def bank_show(self, interaction: discord.Interaction):
        me = rp.row(self.bot.store, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(embed=theme.make_embed(
            "🏦 내 금고", f"🏦 금고 **{me['bank']:,}** / {rp.BANK_MAX:,} {COM_NAME}\n{bar(me['bank'], rp.BANK_MAX)}\n"
                         f"{rp.COM} 지갑 **{me['com']:,}** {COM_NAME}\n`/금고 넣기`·`/금고 빼기`로 옮겨요(수량을 비우면 전부). 경험치·레벨은 그대로예요.",
            color=theme.GOLD), ephemeral=False)

    @bank.command(name="넣기", description="지갑의 COM을 금고에 넣어요. 수량을 비우면 넣을 수 있는 만큼 전부")
    @app_commands.rename(amount="수량")
    async def bank_deposit(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, rp.BANK_MAX] | None = None):
        try:
            moved, wallet, safe = rp.deposit(self.bot.store, interaction.guild_id, interaction.user.id, amount)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"🏦 금고에 {money(moved)}을(를) 넣었어요. (금고 {safe:,}/{rp.BANK_MAX:,} · 지갑 {wallet:,})")

    @bank.command(name="빼기", description="금고의 COM을 지갑으로 꺼내요. 수량을 비우면 전부")
    @app_commands.rename(amount="수량")
    async def bank_withdraw(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, rp.BANK_MAX] | None = None):
        try:
            moved, wallet, safe = rp.withdraw(self.bot.store, interaction.guild_id, interaction.user.id, amount)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"🏦 금고에서 {money(moved)}을(를) 꺼냈어요. (금고 {safe:,}/{rp.BANK_MAX:,} · 지갑 {wallet:,})")

    @app_commands.command(name="직업목록", description="이 서버의 직업들을 봐요")
    @app_commands.guild_only()
    async def job_list(self, interaction: discord.Interaction):
        cfg = self.cfg(interaction.guild_id)
        lines = []
        for job in cfg["jobs"]:
            nxt = rp.job_by_id(cfg, job.get("next"))
            lines.append(f"{job['emoji']} **{job['name']}** — 기본 급여 {money(job['pay'])} · {job['cooldown']}분마다"
                         + (" · 🤝 COM 주기" if job.get("give") else "")
                         + (f" · LV {job['promote_level']}에 {nxt['emoji']} {nxt['name']}(으)로 승급" if nxt else "")
                         + (f"\n  {job['desc']}" if job.get("desc") else ""))
        rate = cfg["roulette"]
        footer = (f"\n\n급여는 LV마다 +{cfg['pay_step']}%. 직업은 관리자가 정해 줘요. 룰렛: "
                  + " · ".join(f"{s['name']} x{s['times']} {s['chance']}%" for s in rate))
        await interaction.response.send_message(embed=theme.make_embed("💼 직업 목록", ("\n".join(lines) or "아직 직업이 없어요.") + footer, color=theme.PURPLE)
                                                , ephemeral=False)

    # ---------------------------------------------------------------- 관리자: 직업
    def _job_or_error(self, cfg, name):
        job = rp.job_by_name(cfg, name)
        if job is None:
            raise RpError("그런 직업이 없어요. 이름은 자동완성에서 골라 주세요.")
        return job

    @admin.command(name="직업추가", description="새 직업을 만들어요 (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", pay="급여", cooldown="쿨타임", desc="소개", give="com주기")
    @app_commands.describe(name="직업 이름", emoji="이모지", pay="레벨 0일 때 한 번 일하면 받는 COM", cooldown="다시 일할 수 있을 때까지(분, 1~10080, 기본 60)",
                           desc="프로필에 보일 직업 소개(롤플레이 설명)", give="직업 능력: 다른 사람에게 COM을 줄 수 있게(/com주기, 기본 아니요)")
    async def job_add(self, interaction: discord.Interaction, name: str, emoji: str, pay: app_commands.Range[int, 1, 1_000_000],
                      cooldown: app_commands.Range[int, 1, 10080] = 60, desc: app_commands.Range[str, 0, 300] = "", give: bool = False):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        try:
            name = rp.clean_job_name(name)
            if rp.job_by_name(cfg, name):
                raise RpError(f"**{name}** 직업이 이미 있어요.")
            if len(cfg["jobs"]) >= rp.JOBS_MAX:
                raise RpError(f"직업은 {rp.JOBS_MAX}개까지예요.")
            if not valid_emoji(emoji.strip()):
                raise RpError("이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요.")
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        job = {"id": max((j["id"] for j in cfg["jobs"]), default=0) + 1, "name": name, "emoji": emoji.strip(), "pay": pay, "cooldown": cooldown,
               "desc": desc.strip(), "next": None, "promote_level": 0, "give": give}
        cfg["jobs"].append(job)
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, f"💼 {job['emoji']} **{name}** 직업을 만들었어요. (급여 {money(pay)}, {cooldown}분마다"
                        + (", 🤝 COM 주기 능력" if give else "") + ") `/롤플관리 임명`으로 사람에게 줘요.")

    @admin.command(name="직업수정", description="직업의 이름·이모지·급여·쿨타임·소개·승급을 고쳐요. 비운 칸은 그대로 (관리자)")
    @app_commands.rename(target="직업", name="새이름", emoji="이모지", pay="급여", cooldown="쿨타임", desc="소개", promote_to="승급직업",
                         promote_level="승급레벨", give="com주기")
    @app_commands.describe(target="고칠 직업(자동완성)", promote_to="이 직업에서 승급할 직업('없음'이면 승급 없음)", promote_level="승급에 필요한 레벨(1~999)")
    async def job_edit(self, interaction: discord.Interaction, target: str, name: str | None = None, emoji: str | None = None,
                       pay: app_commands.Range[int, 1, 1_000_000] | None = None, cooldown: app_commands.Range[int, 1, 10080] | None = None,
                       desc: app_commands.Range[str, 0, 300] | None = None, promote_to: str | None = None,
                       promote_level: app_commands.Range[int, 1, rp.LV_MAX] | None = None, give: bool | None = None):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        try:
            job = self._job_or_error(cfg, target)
            if name is not None:
                clean = rp.clean_job_name(name)
                if clean.lower() != job["name"].lower() and rp.job_by_name(cfg, clean):
                    raise RpError(f"**{clean}** 직업이 이미 있어요.")
                job["name"] = clean
            if emoji is not None:
                if not valid_emoji(emoji.strip()):
                    raise RpError("이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요.")
                job["emoji"] = emoji.strip()
            if promote_to is not None:
                if promote_to.strip() == "없음":
                    job["next"], job["promote_level"] = None, 0
                else:
                    nxt = self._job_or_error(cfg, promote_to)
                    if nxt["id"] == job["id"]:
                        raise RpError("자기 자신으로는 승급할 수 없어요.")
                    job["next"] = nxt["id"]
                    job["promote_level"] = promote_level or job.get("promote_level") or 10
            elif promote_level is not None:
                if not job.get("next"):
                    raise RpError("먼저 승급직업을 정해 주세요.")
                job["promote_level"] = promote_level
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        for key, value in (("pay", pay), ("cooldown", cooldown), ("desc", desc.strip() if desc is not None else None), ("give", give)):
            if value is not None:
                job[key] = value
        self.save(interaction.guild_id, cfg)
        nxt = rp.job_by_id(cfg, job.get("next"))
        await self._say(interaction, f"💼 {job['emoji']} **{job['name']}**: 급여 {money(job['pay'])} · {job['cooldown']}분마다"
                        + (" · 🤝 COM 주기 능력" if job.get("give") else "")
                        + (f" · LV {job['promote_level']}에 {nxt['emoji']} {nxt['name']}(으)로 승급" if nxt else " · 승급 없음"))

    @admin.command(name="직업삭제", description="직업을 지워요. 그 직업이던 사람은 무직이 돼요 (관리자)")
    @app_commands.rename(target="직업")
    async def job_delete(self, interaction: discord.Interaction, target: str):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        cfg = self.cfg(gid)
        try:
            job = self._job_or_error(cfg, target)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        cfg["jobs"] = [j for j in cfg["jobs"] if j["id"] != job["id"]]
        for other in cfg["jobs"]:                                       # 이 직업으로 승급하던 길도 끊는다
            if other.get("next") == job["id"]:
                other["next"], other["promote_level"] = None, 0
        self.save(gid, cfg)
        with store.db:
            gone = store.db.execute("UPDATE rp SET job=NULL WHERE guild=? AND job=?", (gid, job["id"])).rowcount
        await self._say(interaction, f"🗑️ {job['emoji']} **{job['name']}** 직업을 지웠어요." + (f" {gone}명이 무직이 됐어요." if gone else ""))

    @admin.command(name="임명", description="사람에게 직업을 줘요 (관리자)")
    @app_commands.rename(member="사람", target="직업")
    async def appoint(self, interaction: discord.Interaction, member: discord.Member, target: str):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        try:
            if member.bot:
                raise RpError("봇에게는 직업을 줄 수 없어요.")
            job = self._job_or_error(cfg, target)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        rp.assign(self.bot.store, interaction.guild_id, member.id, job["id"])
        LOG.info("직업 임명: 관리자 %s → %s, 직업 %s (서버 %s)", interaction.user.id, member.id, job["id"], interaction.guild_id)
        await self._say(interaction, f"💼 {member.mention}님이 {job['emoji']} **{job['name']}**이(가) 됐어요! `/일하기`로 급여를 받아요.")

    @admin.command(name="해임", description="사람의 직업을 없애요(무직) (관리자)")
    @app_commands.rename(member="사람")
    async def dismiss(self, interaction: discord.Interaction, member: discord.Member):
        if not await require_admin(interaction):
            return
        rp.assign(self.bot.store, interaction.guild_id, member.id, None)
        await self._say(interaction, f"🙂 {member.mention}님은 이제 무직이에요.")

    @admin.command(name="승급", description="승급 LV에 닿은 사람을 다음 직업으로 승급시켜요 (관리자)")
    @app_commands.rename(member="사람")
    async def promote(self, interaction: discord.Interaction, member: discord.Member):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        cfg = self.cfg(gid)
        job, nxt, need = rp.can_promote(store, gid, member.id, cfg)
        level = rp.level_of(rp.row(store, gid, member.id)["exp"], cfg)[0]
        if job is None:
            await self._say(interaction, f"{member.mention}님은 직업이 없어요.", False)
            return
        if nxt is None:
            await self._say(interaction, f"{job['emoji']} {job['name']}에는 승급할 직업이 없어요(`/롤플관리 직업수정`의 승급직업).", False)
            return
        if level < need:
            await self._say(interaction, f"{member.mention}님은 LV {level}이에요. 승급하려면 LV {need}이 필요해요.", False)
            return
        rp.assign(store, gid, member.id, nxt["id"])
        await self._say(interaction, f"⬆️ {member.mention}님이 {job['emoji']} {job['name']}에서 {nxt['emoji']} **{nxt['name']}**(으)로 승급했어요!")

    # ---------------------------------------------------------------- 관리자: 레벨·룰렛·보상·지급
    @admin.command(name="레벨공식", description="레벨업 경험치 계산식(곱셈·오프셋·나눗셈 계수)과 레벨별 급여 증가를 정해요. 비운 칸은 그대로 (관리자)")
    @app_commands.rename(mul="곱셈계수", offset="오프셋", div="나눗셈계수", pay_step="급여증가", reset="초기화")
    @app_commands.describe(mul="곱셈 계수(1~1,000,000, 기본 1)", offset="오프셋(0~1,000,000,000, 기본 1)", div="나눗셈 계수(1~10,000, 기본 10)",
                           pay_step="레벨이 하나 오를 때마다 늘어나는 급여 %(기본 5)", reset="계산식을 기본값(1·1·10)으로 되돌릴까요")
    async def level_formula(self, interaction: discord.Interaction, mul: app_commands.Range[int, 1, 1_000_000] | None = None,
                            offset: app_commands.Range[int, 0, 1_000_000_000] | None = None, div: app_commands.Range[int, 1, 10_000] | None = None,
                            pay_step: app_commands.Range[int, 0, 1000] | None = None, reset: bool = False):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        formula = dict(rp.DEFAULT_FORMULA) if reset else dict(cfg["formula"])
        for key, value in (("mul", mul), ("offset", offset), ("div", div)):
            if value is not None:
                formula[key] = value
        cfg["formula"] = formula
        if pay_step is not None:
            cfg["pay_step"] = pay_step
        self.save(interaction.guild_id, cfg)
        examples = " · ".join(f"{cfg['label']} {n} {rp.total_for(n, cfg):,}" for n in (1, 2, 5, 10, 50))
        await self._say(interaction, f"📈 **{rp.formula_text(cfg)}**\n예: {examples}\n급여는 레벨마다 +{cfg['pay_step']}%")

    @admin.command(name="경험치설정", description="경험치 이름과 채팅·음성·출석체크로 받는 경험치(=COM), 채팅 쿨타임을 정해요. 비운 칸은 그대로 (관리자)")
    @app_commands.rename(label="경험치이름", chat="채팅", voice="음성", attend="출석체크", chat_cooldown="채팅쿨타임")
    @app_commands.describe(label="레벨 앞에 붙는 이름(예: LV.)", chat="채팅 한 번에 받는 경험치(=COM)", voice="음성 채널에 있을 때 5분마다 받는 경험치(=COM)",
                           attend="/출석체크로 받는 경험치(=COM)", chat_cooldown="채팅 경험치를 다시 받을 때까지(초)")
    async def xp_settings(self, interaction: discord.Interaction, label: app_commands.Range[str, 1, 10] | None = None,
                          chat: app_commands.Range[int, 0, 1_000_000] | None = None, voice: app_commands.Range[int, 0, 1_000_000] | None = None,
                          attend: app_commands.Range[int, 0, 1_000_000_000] | None = None, chat_cooldown: app_commands.Range[int, 0, 3600] | None = None):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        for key, value in (("label", label.strip() if label else None), ("chat_xp", chat), ("voice_xp", voice), ("attend_xp", attend),
                           ("chat_cooldown", chat_cooldown)):
            if value:
                cfg[key] = value
            elif value == 0 and key != "label":
                cfg[key] = 0
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, self.xp_text(cfg))

    def xp_text(self, cfg) -> str:
        mute = {"off": "음소거해도 그대로", "block": "음소거하면 받지 않음", "reduce": f"음소거하면 {cfg['mute_percent']}% 줄어듦"}[cfg["mute_mode"]]
        return (f"✨ 경험치 이름 **{cfg['label']}** · 채팅 {cfg['chat_xp']:,}(쿨타임 {cfg['chat_cooldown']}초) · 음성 5분마다 {cfg['voice_xp']:,}({mute}) · "
                f"출석체크 {cfg['attend_xp']:,}\n받은 경험치는 그대로 {COM_NAME}(으)로 들어와요(경험치 = {COM_NAME}).")

    @admin.command(name="음성제한", description="마이크·헤드셋을 스스로 끈 사람의 음성 경험치를 막거나 줄여요(서버 뮤트는 상관없어요) (관리자)")
    @app_commands.rename(mode="모드", percent="감소비율")
    @app_commands.describe(mode="비활성 · XP 차단 · XP 감소", percent="XP 감소일 때 줄일 %(소수점은 버려요, 기본 50)")
    @app_commands.choices(mode=[Choice(name="비활성", value="off"), Choice(name="XP 차단", value="block"), Choice(name="XP 감소", value="reduce")])
    async def voice_limit(self, interaction: discord.Interaction, mode: Choice[str], percent: app_commands.Range[int, 0, 100] | None = None):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        cfg["mute_mode"] = mode.value
        if percent is not None:
            cfg["mute_percent"] = percent
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, self.xp_text(cfg))

    @admin.command(name="룰렛확률", description="룰렛의 꽝·당첨·초당첨 확률(%)을 정해요. 합이 100이어야 해요 (관리자)")
    @app_commands.rename(miss="꽝", win="당첨", jackpot="초당첨")
    async def roulette_rates(self, interaction: discord.Interaction, miss: app_commands.Range[int, 0, 100], win: app_commands.Range[int, 0, 100],
                             jackpot: app_commands.Range[int, 0, 100]):
        if not await require_admin(interaction):
            return
        if miss + win + jackpot != 100:
            await self._say(interaction, f"세 확률의 합이 100이어야 해요(지금 {miss + win + jackpot}).", False)
            return
        cfg = self.cfg(interaction.guild_id)
        wheel = [dict(s) for s in cfg["roulette"]]
        if len(wheel) != 3:
            wheel = [dict(s) for s in rp.DEFAULT_ROULETTE]
        for slot, chance in zip(wheel, (miss, win, jackpot)):
            slot["chance"] = chance
        cfg["roulette"] = wheel
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, "🎰 룰렛: " + " · ".join(f"{s['name']} x{s['times']} {s['chance']}%" for s in wheel))

    @admin.command(name="룰렛한도", description="룰렛에 한 번에 걸 수 있는 최대 COM과 쿨타임(초)을 정해요 (관리자)")
    @app_commands.rename(bet_max="최대", cooldown="쿨타임")
    async def roulette_limits(self, interaction: discord.Interaction, bet_max: app_commands.Range[int, 1, rp.COM_MAX] | None = None,
                              cooldown: app_commands.Range[int, 0, 86400] | None = None):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        if bet_max is not None:
            cfg["bet_max"] = bet_max
        if cooldown is not None:
            cfg["spin_cooldown"] = cooldown
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, f"🎰 한 번에 최대 {money(cfg['bet_max'])} · {cfg['spin_cooldown']}초마다")

    @admin.command(name="레벨보상", description="정한 LV에 닿으면 줄 역할 보상을 넣거나 빼요 (관리자)")
    @app_commands.rename(level="레벨", role="역할", action="동작")
    @app_commands.choices(action=[Choice(name="추가", value="add"), Choice(name="삭제", value="remove")])
    async def level_reward(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, rp.LV_MAX], role: discord.Role, action: Choice[str]):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        if action.value == "add":
            if role.is_default() or role.managed:
                await self._say(interaction, "@everyone이나 봇·연동 역할은 보상으로 줄 수 없어요.", False)
                return
            if any(r["level"] == level and r["role"] == role.id for r in cfg["rewards"]):
                await self._say(interaction, "이미 있는 보상이에요.", False)
                return
            if len(cfg["rewards"]) >= rp.REWARDS_MAX:
                await self._say(interaction, f"레벨 보상은 {rp.REWARDS_MAX}개까지예요.", False)
                return
            cfg["rewards"].append({"level": level, "role": role.id})
        else:
            before = len(cfg["rewards"])
            cfg["rewards"] = [r for r in cfg["rewards"] if not (r["level"] == level and r["role"] == role.id)]
            if len(cfg["rewards"]) == before:
                await self._say(interaction, "그런 보상이 없어요.", False)
                return
        self.save(interaction.guild_id, cfg)
        cfg = self.cfg(interaction.guild_id)
        await self._say(interaction, "🎁 레벨 보상: " + (", ".join(f"LV {r['level']} → <@&{r['role']}>" for r in cfg["rewards"]) or "없음")
                        + "\n(이미 그 LV을 넘은 사람은 다음에 LV이 오를 때 받지 않아요. 직접 주거나 `/롤플관리 com지급`으로 LV을 올려 주세요)")

    @admin.command(name="com지급", description="사람에게 COM을 줘요. EXP로도 쌓을지 고를 수 있어요 (관리자)")
    @app_commands.rename(member="사람", amount="수량", with_exp="경험치포함")
    @app_commands.describe(with_exp="받은 COM을 EXP(LV)로도 쌓을까요(기본 예)")
    async def give_com(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, rp.COM_MAX],
                       with_exp: bool = True):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        cfg = self.cfg(gid)
        before = rp.level_of(rp.row(store, gid, member.id)["exp"], cfg)[0]
        try:
            com, exp = rp.give(store, gid, member.id, amount, exp=with_exp)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        extra = await self.level_up(member, cfg, before, rp.level_of(exp, cfg)[0])
        await self._say(interaction, f"{rp.COM} {member.mention}님에게 {money(amount)}을(를) 줬어요. (가진 {COM_NAME} {com:,})" + ("\n" + extra if extra else ""))

    @admin.command(name="com회수", description="사람의 COM을 가져가요. 수량을 비우면 전부, 지갑·금고 중 고를 수 있어요 (관리자)")
    @app_commands.rename(member="사람", amount="수량", where="위치", with_exp="경험치포함")
    @app_commands.describe(amount="가져갈 COM(비우면 그곳의 전부)", where="어디서 가져갈까요(기본 지갑)",
                           with_exp="EXP(LV)도 같은 양만큼 뺄까요(기본 아니요)")
    @app_commands.choices(where=[Choice(name="지갑", value="wallet"), Choice(name="금고", value="bank")])
    async def take_com(self, interaction: discord.Interaction, member: discord.Member,
                       amount: app_commands.Range[int, 1, rp.COM_MAX] | None = None, where: Choice[str] | None = None,
                       with_exp: bool = False):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        cfg = self.cfg(gid)
        place = where.value if where else "wallet"
        before = rp.level_of(rp.row(store, gid, member.id)["exp"], cfg)[0]
        try:
            taken, left, exp = rp.take(store, gid, member.id, amount, where=place, exp=with_exp)
        except RpError as exc:
            await self._say(interaction, str(exc), False)
            return
        name = "금고" if place == "bank" else "지갑"
        text = f"{rp.COM} {member.mention}님의 {name}에서 {money(taken)}을(를) 가져갔어요. ({name} {left:,} {COM_NAME} 남음)"
        after = rp.level_of(exp, cfg)[0]
        if after < before:
            text += (f"\n📉 {cfg['label']} {before} → {cfg['label']} {after}"
                     "\n(이미 받은 레벨 보상 역할은 자동으로 빼지 않아요. 필요하면 직접 빼 주세요)")
        elif with_exp:
            text += f"\nEXP도 뺐어요. (지금 {cfg['label']} {after})"
        await self._say(interaction, text)

    # ---------------------------------------------------------------- 관리자: COM 상점
    @admin.command(name="상점추가", description="COM 상점에 물품을 더해요. 종류는 텍스트(알림 채널에 알림) 또는 역할 (관리자)")
    @app_commands.rename(name="이름", price="가격", kind="종류", role="역할", emoji="이모지", desc="내용")
    @app_commands.describe(name="물품 이름", price="가격(COM)", kind="텍스트: 사면 알림 채널에 알려요 / 역할: 사면 역할을 줘요",
                           role="역할 물품일 때 줄 역할", emoji="이모지(기본 📦)", desc="물품 내용·설명(텍스트 물품은 알림에 같이 나가요)")
    @app_commands.choices(kind=[Choice(name="텍스트", value="text"), Choice(name="역할", value="role")])
    async def shop_add(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 30], price: app_commands.Range[int, 0, rp.COM_MAX],
                       kind: Choice[str], role: discord.Role | None = None, emoji: str = "📦", desc: app_commands.Range[str, 0, 300] = ""):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        problem = None
        if rp.shop_item(cfg, name):
            problem = f"**{name}** 물품이 이미 있어요."
        elif len(cfg["shop"]) >= rp.SHOP_MAX:
            problem = f"물품은 {rp.SHOP_MAX}개까지예요."
        elif not valid_emoji(emoji.strip()):
            problem = "이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요."
        elif kind.value == "role" and (role is None or role.is_default() or role.managed):
            problem = "역할 물품에는 줄 역할을 골라 주세요(@everyone·봇 역할은 안 돼요)."
        if problem:
            await self._say(interaction, problem, False)
            return
        item = {"id": max((i["id"] for i in cfg["shop"]), default=0) + 1, "name": name.strip(), "emoji": emoji.strip(), "price": price,
                "kind": kind.value, "role": role.id if kind.value == "role" else None, "desc": desc.strip()}
        cfg["shop"].append(item)
        self.save(interaction.guild_id, cfg)
        note = "" if kind.value == "role" or cfg["shop_channel"] else "\n⚠️ 텍스트 물품을 팔려면 `/롤플관리 알림채널`로 알림 채널을 정해 주세요."
        await self._say(interaction, f"🛒 {item['emoji']} **{item['name']}**({'역할 ' + role.mention if role and kind.value == 'role' else '텍스트'}, {money(price)})을(를) 더했어요.{note}")

    @admin.command(name="상점수정", description="COM 상점 물품의 이름·가격·이모지·내용·역할을 고쳐요. 비운 칸은 그대로 (관리자)")
    @app_commands.rename(target="물품", name="새이름", price="가격", emoji="이모지", desc="내용", role="역할")
    async def shop_edit(self, interaction: discord.Interaction, target: str, name: app_commands.Range[str, 1, 30] | None = None,
                        price: app_commands.Range[int, 0, rp.COM_MAX] | None = None, emoji: str | None = None,
                        desc: app_commands.Range[str, 0, 300] | None = None, role: discord.Role | None = None):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        item = rp.shop_item(cfg, target)
        problem = None
        if item is None:
            problem = "그런 물품이 없어요. 이름은 자동완성에서 골라 주세요."
        elif name and name.strip().lower() != item["name"].lower() and rp.shop_item(cfg, name):
            problem = f"**{name}** 물품이 이미 있어요."
        elif emoji is not None and not valid_emoji(emoji.strip()):
            problem = "이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요."
        elif role is not None and (item["kind"] != "role" or role.is_default() or role.managed):
            problem = "역할은 역할 물품에만, @everyone·봇 역할이 아닌 것으로 정할 수 있어요."
        if problem:
            await self._say(interaction, problem, False)
            return
        for key, value in (("name", name.strip() if name else None), ("price", price), ("emoji", emoji.strip() if emoji else None),
                           ("desc", desc.strip() if desc is not None else None), ("role", role.id if role else None)):
            if value is not None:
                item[key] = value
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, f"🛒 {item['emoji']} **{item['name']}** — {money(item['price'])}을(를) 고쳤어요.")

    @admin.command(name="상점삭제", description="COM 상점 물품을 지워요 (관리자)")
    @app_commands.rename(target="물품")
    async def shop_delete(self, interaction: discord.Interaction, target: str):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        item = rp.shop_item(cfg, target)
        if item is None:
            await self._say(interaction, "그런 물품이 없어요.", False)
            return
        cfg["shop"] = [i for i in cfg["shop"] if i["id"] != item["id"]]
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, f"🗑️ {item['emoji']} **{item['name']}**을(를) 지웠어요.")

    @admin.command(name="알림채널", description="COM 상점에서 텍스트 물품을 사면 알림이 올라갈 채널을 정해요 (관리자)")
    @app_commands.rename(channel="채널")
    async def shop_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await require_admin(interaction):
            return
        cfg = self.cfg(interaction.guild_id)
        cfg["shop_channel"] = channel.id
        self.save(interaction.guild_id, cfg)
        await self._say(interaction, f"📨 텍스트 물품을 사면 {channel.mention}에 알림이 올라가요.")

    # ---------------------------------------------------------------- 자동완성
    @job_edit.autocomplete("target")
    @job_edit.autocomplete("promote_to")
    @job_delete.autocomplete("target")
    @appoint.autocomplete("target")
    async def job_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        return [Choice(name=f"{j['emoji']} {j['name']}"[:100], value=j["name"]) for j in self.cfg(interaction.guild_id)["jobs"] if needle in j["name"].lower()][:25]


    @shop_edit.autocomplete("target")
    @shop_delete.autocomplete("target")
    async def shop_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        return [Choice(name=f"{i['emoji']} {i['name']}"[:100], value=i["name"]) for i in self.cfg(interaction.guild_id)["shop"] if needle in i["name"].lower()][:25]


def shop_embed(store, guild_id: int, user_id: int, cfg: dict, note: str | None = None) -> discord.Embed:
    lines = ([note, ""] if note else [])
    for item in cfg["shop"]:
        what = f"역할 <@&{item['role']}>" if item["kind"] == "role" else "텍스트(관리자에게 알림)"
        lines.append(f"{item['emoji']} **{item['name']}** — {money(item['price'])} · {what}" + (f"\n  {item['desc']}" if item.get("desc") else ""))
    if not cfg["shop"]:
        lines.append("아직 물품이 없어요. 관리자가 `/롤플관리 상점추가`로 더해요.")
    embed = theme.make_embed("🪙 COM 상점", "\n".join(lines)[:4000], color=theme.GOLD)
    embed.set_footer(text=f"가진 {COM_NAME}: {rp.row(store, guild_id, user_id)['com']:,} · 아래에서 골라 사요")
    return embed


class RequestModal(discord.ui.Modal):
    """텍스트 물품을 살 때 관리자에게 보낼 요청 내용(예: 바꿀 닉네임)."""

    def __init__(self, view: "ComShopView", item: dict):
        super().__init__(title=f"{item['name']} 구매"[:45], timeout=600)
        self.view, self.item = view, item
        self.request = discord.ui.TextInput(label="요청 내용(관리자에게 같이 보내요, 비워도 돼요)", style=discord.TextStyle.paragraph,
                                            required=False, max_length=500)
        self.add_item(self.request)

    async def on_submit(self, interaction: discord.Interaction):
        await self.view.finish(interaction, self.item, self.request.value)


class ComShopView(discord.ui.View):
    """COM 상점 화면: 물품을 골라 [사기]. 연 사람만 누를 수 있다."""

    def __init__(self, cog: Roleplay, user_id: int, guild_id: int):
        super().__init__(timeout=600)
        self.cog, self.user_id, self.guild_id = cog, user_id, guild_id
        items = cog.cfg(guild_id)["shop"][:25]
        if items:
            select = discord.ui.Select(placeholder="🛒 살 물품을 골라요", options=[
                discord.SelectOption(label=f"{i['name']} — {i['price']:,} {COM_NAME}"[:100], value=str(i["id"]), emoji=i["emoji"],
                                     description=(i.get("desc") or ("역할" if i["kind"] == "role" else "텍스트"))[:100]) for i in items])
            select.callback = self.pick
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def pick(self, interaction: discord.Interaction):
        item_id = int((interaction.data.get("values") or ["0"])[0])
        item = next((i for i in self.cog.cfg(self.guild_id)["shop"] if i["id"] == item_id), None)
        if item is not None and item["kind"] == "text":
            await interaction.response.send_modal(RequestModal(self, item))       # 텍스트 물품은 요청 내용을 받는다
            return
        await self.finish(interaction, item or {"id": item_id}, "")

    async def finish(self, interaction: discord.Interaction, item: dict, request: str):
        try:
            text = await self.cog.buy(interaction, item["id"], request)
            ok = True
        except RpError as exc:
            text, ok = f"⚠️ {exc}", False
        cfg = self.cog.cfg(self.guild_id)
        await interaction.response.edit_message(embed=shop_embed(self.cog.bot.store, self.guild_id, self.user_id, cfg, text),
                                                view=ComShopView(self.cog, self.user_id, self.guild_id))
        if not ok:
            LOG.info("COM 상점 구매 실패: %s (%s)", interaction.user.id, text)


async def setup(bot):
    await bot.add_cog(Roleplay(bot))
