import discord
from discord import app_commands
from discord.ext import commands

import theme
from access import require_admin
from store import CANDY, CANDY_NAME


async def item_choices(store, guild_id: int, current: str, *, items_only: bool = False):
    """물품 이름 자동완성. 25개까지만 보인다."""
    needle = current.strip().lower()
    rows = [r for r in store.items(guild_id) if needle in r["name"].lower() and not (items_only and r["kind"] == "role")]
    return [app_commands.Choice(name=f"{r['emoji']} {r['name']}"[:100], value=r["name"]) for r in rows[:25]]


def settings_embed(cfg) -> discord.Embed:
    channels = ", ".join(f"<#{c}>" for c in cfg["channels"]) or "아직 없어요 — `/이벤트채널추가`로 정해 주세요."
    cooldown = lambda sec: f"{sec}초" if sec else "없음"
    embed = theme.make_embed("🎃 이벤트 설정", color=theme.PURPLE)
    embed.add_field(name="📍 이벤트 채널", value=channels, inline=False)
    embed.add_field(name=f"{CANDY} 재화", inline=False,
                    value=f"채팅 한 번에 **{cfg['candy_chance']:g}%** 확률로 **{cfg['candy_min']}~{cfg['candy_max']}**개\n"
                          f"같은 사람 쿨타임: {cooldown(cfg['candy_cooldown'])}")
    embed.add_field(name="✨ 경험치", inline=False,
                    value=f"채팅 한 번에 **{cfg['exp_min']}~{cfg['exp_max']}** (쿨타임 {cooldown(cfg['exp_cooldown'])})")
    mode = "먼저 뜯은 한 명만" if cfg["package_mode"] == "first" else "누구나 한 번씩"
    embed.add_field(name="📦 패키지", inline=False,
                    value=f"채팅 한 번에 **{cfg['package_chance']:g}%** 확률 · {mode} · {cfg['package_seconds']}초 동안")
    embed.add_field(name="🍬 반응", value="켜짐" if cfg["reaction"] else "꺼짐", inline=False)
    return embed


class Admin(commands.Cog):
    """이벤트 채널·설정과 재화·EXP·물품 지급 (관리자 전용)."""

    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------------- 이벤트 채널
    @app_commands.command(name="이벤트채널추가", description="재화·EXP·패키지가 나오는 채널을 추가해요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(channel="채널")
    async def add_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        channels = store.settings(gid)["channels"]
        if channel.id in channels:
            await interaction.response.send_message(embed=theme.notice(f"{channel.mention}은(는) 이미 이벤트 채널이에요.", False), ephemeral=True)
            return
        if len(channels) >= 100:
            await interaction.response.send_message(embed=theme.notice("이벤트 채널은 최대 100개예요.", False), ephemeral=True)
            return
        store.update_settings(gid, channels=[*channels, channel.id])
        perms = channel.permissions_for(interaction.guild.me)
        warn = "" if perms.view_channel and perms.send_messages and perms.embed_links else (
            "\n⚠️ 봇이 이 채널에서 메시지를 보내거나 임베드를 올릴 수 없어요. **패키지가 나오려면** 채널 권한을 확인해 주세요.")
        await interaction.response.send_message(embed=theme.notice(f"{channel.mention}에서 재화·EXP·패키지가 나와요.{warn}"), ephemeral=True)

    @app_commands.command(name="이벤트채널제거", description="이벤트 채널에서 뺍니다 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(channel="채널")
    async def remove_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        channels = store.settings(gid)["channels"]
        if channel.id not in channels:
            await interaction.response.send_message(embed=theme.notice(f"{channel.mention}은(는) 이벤트 채널이 아니에요.", False), ephemeral=True)
            return
        store.update_settings(gid, channels=[c for c in channels if c != channel.id])
        await interaction.response.send_message(embed=theme.notice(f"{channel.mention}을(를) 이벤트 채널에서 뺐어요."), ephemeral=True)

    @app_commands.command(name="이벤트채널목록", description="이벤트 채널 목록을 봅니다 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def list_channels(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.send_message(embed=settings_embed(self.bot.store.settings(interaction.guild_id)), ephemeral=True)

    # ---------------------------------------------------------------- 설정
    @app_commands.command(name="이벤트설정", description="확률·쿨타임·패키지 방식을 바꿔요. 비워 두면 현재 설정을 보여줘요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(candy_chance="재화확률", candy_min="재화최소", candy_max="재화최대", candy_cooldown="재화쿨타임",
                         exp_min="경험치최소", exp_max="경험치최대", exp_cooldown="경험치쿨타임",
                         package_chance="패키지확률", package_mode="패키지방식", package_seconds="패키지시간", reaction="반응")
    @app_commands.describe(candy_chance="채팅 한 번당 재화를 얻을 확률(%, 기본 50)", candy_min="한 번에 얻는 재화 최소(기본 1)",
                           candy_max="한 번에 얻는 재화 최대(기본 5)", candy_cooldown="같은 사람이 다시 얻기까지(초, 기본 3, 0=제한 없음)",
                           exp_min="채팅당 EXP 최소(기본 15)", exp_max="채팅당 EXP 최대(기본 25, 0이면 EXP 없음)",
                           exp_cooldown="EXP 쿨타임(초, 기본 60)", package_chance="채팅 한 번당 패키지 확률(%, 기본 0.1)",
                           package_mode="누구나 한 번씩 / 먼저 뜯은 한 명만", package_seconds="패키지가 머무는 시간(초, 기본 120)",
                           reaction="재화를 얻으면 🍬 반응 달기")
    @app_commands.choices(package_mode=[app_commands.Choice(name="누구나 한 번씩", value="all"),
                                        app_commands.Choice(name="먼저 뜯은 한 명만", value="first")])
    async def settings(self, interaction: discord.Interaction, candy_chance: app_commands.Range[float, 0, 100] | None = None,
                       candy_min: app_commands.Range[int, 0, 1000] | None = None,
                       candy_max: app_commands.Range[int, 0, 1000] | None = None,
                       candy_cooldown: app_commands.Range[int, 0, 3600] | None = None,
                       exp_min: app_commands.Range[int, 0, 100000] | None = None,
                       exp_max: app_commands.Range[int, 0, 100000] | None = None,
                       exp_cooldown: app_commands.Range[int, 0, 3600] | None = None,
                       package_chance: app_commands.Range[float, 0, 100] | None = None,
                       package_mode: app_commands.Choice[str] | None = None,
                       package_seconds: app_commands.Range[int, 10, 3600] | None = None, reaction: bool | None = None):
        if not await require_admin(interaction):
            return
        changes = {k: v for k, v in dict(candy_chance=candy_chance, candy_min=candy_min, candy_max=candy_max,
                                         candy_cooldown=candy_cooldown, exp_min=exp_min, exp_max=exp_max, exp_cooldown=exp_cooldown,
                                         package_chance=package_chance, package_seconds=package_seconds, reaction=reaction).items()
                   if v is not None}
        if package_mode is not None:
            changes["package_mode"] = package_mode.value
        gid = interaction.guild_id
        if changes:
            try:
                self.bot.store.update_settings(gid, **changes)
            except ValueError as exc:
                await interaction.response.send_message(embed=theme.notice(str(exc), False), ephemeral=True)
                return
        await interaction.response.send_message(embed=settings_embed(self.bot.store.settings(gid)), ephemeral=True)

    # ---------------------------------------------------------------- 지급
    @staticmethod
    def _bad_target(member) -> str | None:
        return "봇에게는 지급할 수 없어요." if member.bot else None

    @app_commands.command(name="재화지급", description="할로윈 재화를 주거나(+) 회수해요(−) (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(target="대상", amount="수량")
    async def give_candy(self, interaction: discord.Interaction, target: discord.Member,
                         amount: app_commands.Range[int, -99999, 99999]):
        if not await require_admin(interaction):
            return
        if problem := self._bad_target(target):
            await interaction.response.send_message(embed=theme.notice(problem, False), ephemeral=True)
            return
        changed = self.bot.store.add_candy(interaction.guild_id, target.id, amount)
        now = self.bot.store.wallet(interaction.guild_id, target.id)[0]
        await interaction.response.send_message(
            embed=theme.notice(f"{target.mention}의 {CANDY_NAME}이 {changed:+,}만큼 바뀌었어요. (지금 {theme.money(now)})"), ephemeral=True)

    @app_commands.command(name="경험치지급", description="경험치를 주거나(+) 회수해요(−) (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(target="대상", amount="수량")
    async def give_exp(self, interaction: discord.Interaction, target: discord.Member,
                       amount: app_commands.Range[int, -10_000_000, 10_000_000]):
        if not await require_admin(interaction):
            return
        if problem := self._bad_target(target):
            await interaction.response.send_message(embed=theme.notice(problem, False), ephemeral=True)
            return
        changed = self.bot.store.add_exp(interaction.guild_id, target.id, amount)
        now = self.bot.store.wallet(interaction.guild_id, target.id)[1]
        await interaction.response.send_message(
            embed=theme.notice(f"{target.mention}의 경험치가 {changed:+,}만큼 바뀌었어요. (지금 ✨ {now:,})"), ephemeral=True)

    @app_commands.command(name="물품지급", description="물품을 가방에 넣어 주거나(+) 회수해요(−) (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(target="대상", item="물품", amount="수량")
    async def give_item(self, interaction: discord.Interaction, target: discord.Member, item: str,
                        amount: app_commands.Range[int, -999, 999]):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        store.ensure_system_items(gid)
        row = store.item_by_name(gid, item)
        problem = self._bad_target(target) or ("그런 물품이 없어요." if row is None else
                                               "역할은 지급할 수 없어요. 상점에서 사거나 직접 부여해 주세요." if row["kind"] == "role" else None)
        if problem:
            await interaction.response.send_message(embed=theme.notice(problem, False), ephemeral=True)
            return
        changed = store.add_item(gid, target.id, row["id"], amount)
        await interaction.response.send_message(
            embed=theme.notice(f"{target.mention}의 {row['emoji']} {row['name']}이(가) {changed:+,}개 바뀌었어요. "
                               f"(지금 ×{store.qty(gid, target.id, row['id'])})"), ephemeral=True)

    @give_item.autocomplete("item")
    async def give_item_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        self.bot.store.ensure_system_items(interaction.guild_id)
        return await item_choices(self.bot.store, interaction.guild_id, current, items_only=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
