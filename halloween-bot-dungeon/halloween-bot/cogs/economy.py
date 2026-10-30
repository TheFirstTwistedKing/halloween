import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import packages
import services
import theme
from store import (CANDY, CANDY_MAX, CANDY_NAME, EXCHANGE_CANDY, EXCHANGE_EXP, roll_candy, roll_exp,
                   roll_package_spawn)
from views import send_bag

LOG = logging.getLogger("halloween")
FORGET_AFTER = 3600       # 이 시간 넘게 말이 없던 사람의 쿨타임 기록은 지운다(메모리 관리).


class Economy(commands.Cog):
    """채팅 보상(재화·EXP·패키지), 가방, 경험치 교환."""

    def __init__(self, bot):
        self.bot = bot
        self.last_candy: dict[tuple[int, int], float] = {}
        self.last_exp: dict[tuple[int, int], float] = {}
        self.last_reaction: dict[int, float] = {}

    async def cog_load(self):
        self.tidy.start()

    async def cog_unload(self):
        self.tidy.cancel()

    @tasks.loop(seconds=15)
    async def tidy(self):
        """시간이 지난 패키지를 요약으로 바꾸고 정리한다. 한 번 실패해도 계속 돈다."""
        try:
            await packages.finalize_due(self.bot)
            now = time.time()
            for table in (self.last_candy, self.last_exp):
                for key in [k for k, t in table.items() if now - t > FORGET_AFTER]:
                    del table[key]
        except Exception:
            LOG.exception("패키지 정리 중 오류")

    @tidy.before_loop
    async def before_tidy(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------------- 채팅 보상
    def counted(self, table: dict, key, now: float, cooldown: int) -> bool:
        """쿨타임이 지났으면 이 메시지를 센다. 같은 사람이 연달아 보낸 메시지는 한 번으로 친다(매크로·도배 방지)."""
        if now - table.get(key, 0.0) < cooldown:
            return False
        table[key] = now
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (message.guild is None or message.author.bot or message.webhook_id is not None
                or message.type not in (discord.MessageType.default, discord.MessageType.reply)
                or not self.bot.allowed_guild(message.guild.id)):
            return
        store, guild = self.bot.store, message.guild
        cfg = store.settings(guild.id)
        channel = message.channel
        if channel.id not in cfg["channels"] and getattr(channel, "parent_id", None) not in cfg["channels"]:
            return
        now, key = time.time(), (guild.id, message.author.id)
        if self.counted(self.last_candy, key, now, cfg["candy_cooldown"]):
            gained = roll_candy(cfg, self.bot.rng)
            if gained and store.add_candy(guild.id, message.author.id, gained) and cfg["reaction"]:
                await self.react(message, now)
            if not store.active_package(channel.id, now) and roll_package_spawn(cfg, self.bot.rng):
                await packages.spawn(self.bot, message, cfg)
        if cfg["exp_max"] and self.counted(self.last_exp, key, now, cfg["exp_cooldown"]):
            store.add_exp(guild.id, message.author.id, roll_exp(cfg, self.bot.rng))

    async def react(self, message, now: float):
        """재화를 얻으면 🍬 반응을 단다. 바쁜 채널에서 요청 제한에 걸리지 않게 채널마다 1초에 한 번만."""
        if now - self.last_reaction.get(message.channel.id, 0.0) < 1.0:
            return
        self.last_reaction[message.channel.id] = now
        try:
            await message.add_reaction(CANDY)
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------------- 명령어
    @app_commands.command(name="가방", description="내 할로윈 재화, 경험치, 물품을 확인해요")
    @app_commands.guild_only()
    async def bag(self, interaction: discord.Interaction):
        await send_bag(interaction)

    @app_commands.command(name="도움말", description="할로윈 이벤트 사용법을 알려줘요")
    @app_commands.guild_only()
    async def help(self, interaction: discord.Interaction):
        cfg = self.bot.store.settings(interaction.guild_id)
        where = ", ".join(f"<#{c}>" for c in cfg["channels"][:10]) or "아직 정해지지 않았어요 (관리자가 `/이벤트채널추가`로 정해요)"
        mode = "먼저 뜯은 한 명만" if cfg["package_mode"] == "first" else "누구나 한 번씩"
        embed = theme.make_embed("🎃 할로윈 이벤트 안내", "채팅하고, 패키지를 뜯고, 상점에서 쇼핑해 보세요!", color=theme.ORANGE)
        embed.add_field(name=f"{CANDY} 모으는 법", inline=False,
                        value=f"📍 {where}\n채팅하면 **{cfg['candy_chance']:g}%** 확률로 **{cfg['candy_min']}~{cfg['candy_max']}**개를 얻어요.\n"
                              f"✨ 채팅으로 EXP도 쌓여요. **EXP {EXCHANGE_EXP:,} → {CANDY} {EXCHANGE_CANDY}** (`/경험치교환`)")
        embed.add_field(name="📦 패키지", inline=False,
                        value=f"채팅하다 보면 **{cfg['package_chance']:g}%** 확률로 패키지가 나타나요. **{mode}** 뜯을 수 있어요.\n"
                              "꽝 60% · 🗝️ 열쇠 20% · 🍬 50개 10% · 🗑️ 쓰레기 10%")
        embed.add_field(name="🛒 쓰는 법", inline=False,
                        value=f"상점 채널에서 물품을 골라 사고팔 수 있어요. `/가방`으로 내 {CANDY_NAME}·경험치·물품을 봐요.\n"
                              f"{CANDY_NAME}은 **최대 {CANDY_MAX:,}개**까지 가질 수 있어요.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="경험치교환", description="경험치 15,000으로 할로윈 재화 15개를 사요")
    @app_commands.guild_only()
    @app_commands.rename(count="횟수")
    @app_commands.describe(count="몇 번 교환할지 (한 번에 EXP 15,000 → 🍬 15)")
    async def exchange(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 6666] = 1):
        result = await services.exchange(self.bot.store, self.bot.locks, interaction.user, count)
        await interaction.response.send_message(embed=theme.notice(result.text, result.ok), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Economy(bot))
