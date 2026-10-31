import logging

import discord
from discord import app_commands
from discord.ext import commands

import theme
from access import require_admin
from josa import eun_neun, i_ga

LOG = logging.getLogger("halloween")
NO_MEMBERS_INTENT = ("이 명령어는 봇이 서버 멤버 목록을 볼 수 있어야 해요. 설정 방법:\n"
                     "1) 개발자 포털 → 봇 → **Bot** 탭 → **Server Members Intent**를 켜요.\n"
                     "2) 봇의 `.env`에 `MEMBERS_INTENT=true`를 적어요.\n"
                     "3) 봇을 다시 시작해요.")


async def role_member_ids(guild, role) -> list[int]:
    """그 역할을 가진 사람(봇 제외)의 ID. 서버에서 멤버를 직접 가져온다(멤버 인텐트 필요). @everyone이면 서버의 모든 사람."""
    ids = []
    async for member in guild.fetch_members(limit=None):
        if not member.bot and (role.is_default() or role in member.roles):
            ids.append(member.id)
    return ids


class ConfirmRoleGrant(discord.ui.View):
    """역할 전체에게 재화를 주기(회수하기) 전에 대상과 총량을 보여주고 한 번 더 묻는다. 확인 전까지는 아무것도 바뀌지 않는다."""

    def __init__(self, bot, admin_id: int, role_name: str, member_ids: list[int], amount: int):
        super().__init__(timeout=120)
        self.bot, self.admin_id, self.role_name, self.member_ids, self.amount = bot, admin_id, role_name, member_ids, amount
        yes = discord.ui.Button(label="회수" if amount < 0 else "지급", emoji="🎁", style=discord.ButtonStyle.danger if amount < 0
                                else discord.ButtonStyle.success)
        no = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.confirm, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin_id

    async def confirm(self, interaction: discord.Interaction):
        self.stop()
        count, total, partial = self.bot.store.add_candy_many(interaction.guild_id, self.member_ids, self.amount)
        LOG.info("역할 재화 지급: 서버 %s · 관리자 %s · 역할 %s · %d명 · 1인당 %+d · 실제 합계 %+d",
                 interaction.guild_id, interaction.user.id, self.role_name, count, self.amount, total)
        verb = "회수" if self.amount < 0 else "지급"
        cur = self.bot.store.currency(interaction.guild_id)
        text = f"**{self.role_name}** 역할 {count:,}명에게 {cur.money(abs(self.amount))}씩 {verb}했어요.\n실제로 바뀐 {cur.name} 합계: **{total:+,}**"
        if partial:
            reason = "0 아래로는 내려가지 않아서" if self.amount < 0 else f"최대 {cur.max:,}개를 넘을 수 없어서"
            text += f"\n{partial:,}명은 {reason} 일부만 반영됐어요."
        await interaction.response.edit_message(embed=theme.notice(text), view=None)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("취소했어요. 아무것도 바뀌지 않았어요."), view=None)


async def item_choices(store, guild_id: int, current: str, *, items_only: bool = False):
    """물품 이름 자동완성. 25개까지만 보인다."""
    needle = current.strip().lower()
    rows = [r for r in store.items(guild_id) if needle in r["name"].lower() and not (items_only and r["kind"] == "role")]
    return [app_commands.Choice(name=f"{r['emoji']} {r['name']}"[:100], value=r["name"]) for r in rows[:25]]


def settings_embed(cfg) -> discord.Embed:
    cur_emoji, cur_name = cfg["currency_emoji"], cfg["currency_name"]
    channels = ", ".join(f"<#{c}>" for c in cfg["channels"]) or "아직 없어요 — `/이벤트채널추가`로 정해 주세요."
    cooldown = lambda sec: f"{sec}초" if sec else "없음"
    embed = theme.make_embed("🎃 이벤트 설정", color=theme.PURPLE)
    embed.add_field(name="📍 이벤트 채널", value=channels, inline=False)
    embed.add_field(name=f"{cur_emoji} {cur_name}", inline=False,
                    value=f"채팅 한 번에 **{cfg['candy_chance']:g}%** 확률로 **{cfg['candy_min']}~{cfg['candy_max']}**개\n"
                          f"같은 사람 쿨타임: {cooldown(cfg['candy_cooldown'])}")
    mode = "먼저 뜯은 한 명만" if cfg["package_mode"] == "first" else "누구나 한 번씩"
    embed.add_field(name="📦 패키지", inline=False,
                    value=f"채팅 한 번에 **{cfg['package_chance']:g}%** 확률 · {mode} · {cfg['package_seconds']}초 동안")
    embed.add_field(name=f"{cur_emoji} 반응", value="켜짐" if cfg["reaction"] else "꺼짐", inline=False)
    return embed


class Admin(commands.Cog):
    """이벤트 채널·설정과 재화·물품 지급 (관리자 전용)."""

    def __init__(self, bot):
        self.bot = bot

    # ---------------------------------------------------------------- 이벤트 채널
    @app_commands.command(name="이벤트채널추가", description="재화·패키지가 나오는 채널을 추가해요 (관리자)")
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
        await interaction.response.send_message(embed=theme.notice(f"{channel.mention}에서 재화·패키지가 나와요.{warn}"), ephemeral=True)

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
                         package_chance="패키지확률", package_mode="패키지방식", package_seconds="패키지시간", reaction="반응")
    @app_commands.describe(candy_chance="채팅 한 번당 재화를 얻을 확률(%, 기본 50)", candy_min="한 번에 얻는 재화 최소(기본 1)",
                           candy_max="한 번에 얻는 재화 최대(기본 5)", candy_cooldown="같은 사람이 다시 얻기까지(초, 기본 3, 0=제한 없음)",
                           package_chance="채팅 한 번당 패키지 확률(%, 기본 0.1)",
                           package_mode="누구나 한 번씩 / 먼저 뜯은 한 명만", package_seconds="패키지가 머무는 시간(초, 기본 120)",
                           reaction="재화를 얻으면 재화 이모지 반응 달기")
    @app_commands.choices(package_mode=[app_commands.Choice(name="누구나 한 번씩", value="all"),
                                        app_commands.Choice(name="먼저 뜯은 한 명만", value="first")])
    async def settings(self, interaction: discord.Interaction, candy_chance: app_commands.Range[float, 0, 100] | None = None,
                       candy_min: app_commands.Range[int, 0, 1000] | None = None,
                       candy_max: app_commands.Range[int, 0, 1000] | None = None,
                       candy_cooldown: app_commands.Range[int, 0, 3600] | None = None,
                       package_chance: app_commands.Range[float, 0, 100] | None = None,
                       package_mode: app_commands.Choice[str] | None = None,
                       package_seconds: app_commands.Range[int, 10, 3600] | None = None, reaction: bool | None = None):
        if not await require_admin(interaction):
            return
        changes = {k: v for k, v in dict(candy_chance=candy_chance, candy_min=candy_min, candy_max=candy_max,
                                         candy_cooldown=candy_cooldown,
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

    @app_commands.command(name="재화지급", description="재화를 주거나(+) 회수해요(−) (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(target="대상", amount="수량")
    async def give_candy(self, interaction: discord.Interaction, target: discord.Member,
                         amount: app_commands.Range[int, -1_000_000_000, 1_000_000_000]):
        if not await require_admin(interaction):
            return
        if problem := self._bad_target(target):
            await interaction.response.send_message(embed=theme.notice(problem, False), ephemeral=True)
            return
        changed = self.bot.store.add_candy(interaction.guild_id, target.id, amount)
        store, gid = self.bot.store, interaction.guild_id
        now, cur = store.wallet(gid, target.id), store.currency(gid)
        await interaction.response.send_message(
            embed=theme.notice(f"{target.mention}의 {i_ga(cur.name)} {changed:+,}만큼 바뀌었어요. (지금 {cur.money(now)})"), ephemeral=True)

    @app_commands.command(name="역할재화지급", description="그 역할을 가진 모든 사람에게 재화를 주거나(+) 회수해요(−) (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(role="역할", amount="수량")
    @app_commands.describe(role="재화를 받을 역할 (봇은 제외돼요)", amount="1인당 수량 (음수면 회수)")
    async def give_role_candy(self, interaction: discord.Interaction, role: discord.Role,
                              amount: app_commands.Range[int, -1_000_000_000, 1_000_000_000]):
        if not await require_admin(interaction):
            return
        if amount == 0:
            await interaction.response.send_message(embed=theme.notice("수량이 0이면 바뀌는 게 없어요.", False), ephemeral=True)
            return
        if not self.bot.intents.members:
            await interaction.response.send_message(embed=theme.notice(NO_MEMBERS_INTENT, False), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)                 # 멤버가 많으면 가져오는 데 시간이 걸린다.
        try:
            ids = await role_member_ids(interaction.guild, role)
        except discord.HTTPException:
            await interaction.followup.send(embed=theme.notice("서버 멤버 목록을 가져오지 못했어요. 잠시 뒤에 다시 해 주세요.", False), ephemeral=True)
            return
        if not ids:
            await interaction.followup.send(embed=theme.notice(f"{role.mention} 역할을 가진 사람이 없어요. (봇은 제외돼요)", False), ephemeral=True)
            return
        verb = "회수" if amount < 0 else "지급"
        cur = self.bot.store.currency(interaction.guild_id)
        body = (f"{role.mention} 역할 **{len(ids):,}명**에게 {cur.money(abs(amount))}씩 {verb}해요.\n"
                f"총 {cur.money(abs(amount) * len(ids))} · 봇은 제외했어요.\n"
                f"{eun_neun(cur.name)} 사람마다 0~{cur.max:,}개 안으로 맞춰져요. 버튼을 눌러야 실제로 {verb}돼요.")
        await interaction.followup.send(embed=theme.make_embed(f"🎁 역할 재화 {verb}", body, color=theme.RED if amount < 0 else theme.ORANGE),
                                        view=ConfirmRoleGrant(self.bot, interaction.user.id, role.name, ids, amount), ephemeral=True)

    @app_commands.command(name="물품지급", description="물품을 가방에 넣어 주거나(+) 회수해요(−) (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(target="대상", item="물품", amount="수량")
    async def give_item(self, interaction: discord.Interaction, target: discord.Member, item: str,
                        amount: app_commands.Range[int, -999, 999]):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        store.ensure_defaults(gid)
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
        self.bot.store.ensure_defaults(interaction.guild_id)
        return await item_choices(self.bot.store, interaction.guild_id, current, items_only=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
