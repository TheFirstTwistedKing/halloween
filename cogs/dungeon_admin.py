"""던전 밸런스 관리 명령어(관리자 전용): 값 바꾸기·보기·되돌리기·시뮬레이션. 값의 정의와 검사는 balance.py가 맡는다."""
from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import balance
import balance_sim
import theme
from access import require_admin
from balance import PARAMS, ROOM_KINDS, fmt

Choice = app_commands.Choice


def param_choices(store, guild_id: int, current: str) -> list[Choice]:
    """항목 이름 자동완성. 입력한 글자가 이름에 들어 있는 항목을 25개까지, 지금 값과 기본값을 같이 보여 준다."""
    needle = current.replace(" ", "").lower()
    changed = balance.clean(store.settings(guild_id)["dungeon"])
    out = []
    for param in PARAMS.values():
        if needle not in param.key.replace(" ", "").lower():
            continue
        now = changed.get(param.key, param.default)
        mark = "✏️ " if param.key in changed else ""
        out.append(Choice(name=f"{mark}{param.key} = {fmt(now)} (기본 {fmt(param.default)})"[:100], value=param.key))
        if len(out) >= 25:
            break
    return out


def settings_lines(group: str, changed: dict) -> list[str]:
    """분류 하나의 항목들을 '이름 값 (바꿨으면 ✏️ 기본값)' 줄로 만든다. 방 배치는 방마다 한 줄."""
    if group == "방 배치":
        lines = []
        for room in range(1, balance.ROOMS + 1):
            parts, edited = [], False
            for _, label in ROOM_KINDS:
                key = f"방{room}.{label}"
                value = changed.get(key, PARAMS[key].default)
                edited = edited or key in changed
                if value:
                    parts.append(f"{label} {fmt(value)}")
            lines.append(f"`방{room}` " + " · ".join(parts) + (" ✏️" if edited else ""))
        return lines
    lines = []
    for param in PARAMS.values():
        if param.group != group:
            continue
        now = changed.get(param.key, param.default)
        lines.append(f"`{param.key}` **{fmt(now)}**" + (f" ✏️ 기본 {fmt(param.default)}" if param.key in changed else ""))
    return lines


class ConfirmResetAll(discord.ui.View):
    """모든 던전 밸런스 값을 기본값으로 되돌리기 전에 한 번 더 묻는다."""

    def __init__(self, bot, admin_id: int):
        super().__init__(timeout=60)
        self.bot, self.admin_id = bot, admin_id
        yes = discord.ui.Button(label="모두 기본값으로", emoji="♻️", style=discord.ButtonStyle.danger)
        no = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.confirm, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin_id

    async def confirm(self, interaction: discord.Interaction):
        self.stop()
        store = self.bot.store
        count = len(balance.clean(store.settings(interaction.guild_id)["dungeon"]))
        store.update_settings(interaction.guild_id, dungeon={})
        await interaction.response.edit_message(embed=theme.notice(f"던전 밸런스 {count}개 항목을 모두 기본값으로 되돌렸어요."), view=None)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("되돌리기를 취소했어요."), view=None)


class DungeonAdmin(commands.Cog):
    """던전 밸런스를 관리자가 직접 바꾸고 확인한다."""

    def __init__(self, bot):
        self.bot = bot

    async def _deny(self, interaction, text):
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=True)

    def _changed(self, guild_id: int) -> dict:
        return balance.clean(self.bot.store.settings(guild_id)["dungeon"])

    # ---------------------------------------------------------------- 값 바꾸기
    @app_commands.command(name="던전설정", description="던전 밸런스 값을 바꿔요. 항목은 글자를 입력하면 자동완성돼요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(key="항목", value="값")
    @app_commands.describe(key="바꿀 항목 (예: 몬스터.슬라임.체력, 방어.회복률, 방3.잡몹)", value="새 값 (숫자)")
    async def set_param(self, interaction: discord.Interaction, key: str, value: float):
        if not await require_admin(interaction):
            return
        new_value, problem = balance.check_value(key, value)
        if problem:
            await self._deny(interaction, problem)
            return
        store, gid, param = self.bot.store, interaction.guild_id, PARAMS[key]
        changed = self._changed(gid)
        old = changed.get(key, param.default)
        if new_value == old:
            await self._deny(interaction, f"**{key}**은(는) 이미 {fmt(old)}이에요.")
            return
        updated = dict(changed)
        if new_value == param.default:
            updated.pop(key, None)                       # 기본값으로 돌아오면 기록을 지운다.
        else:
            updated[key] = new_value
        problem = balance.validate(updated)
        if problem:
            await self._deny(interaction, problem)
            return
        store.update_settings(gid, dungeon=updated)
        lines = [f"✏️ **{key}**: {fmt(old)} → **{fmt(new_value)}** (기본 {fmt(param.default)})"]
        if param.note:
            lines.append(f"┕ {param.note}")
        lines += balance.warnings(store.balance(gid))
        lines.append("지금 진행 중인 던전에도 바로 적용돼요(이미 나온 몬스터의 체력·공격은 그대로). `/던전시뮬`로 결과를 확인해 보세요.")
        await interaction.response.send_message(embed=theme.notice("\n".join(lines)), ephemeral=True)

    @app_commands.command(name="던전설정확인", description="던전 밸런스의 현재 값을 봐요. 분류를 고르면 그 분류만 보여줘요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(group="분류")
    @app_commands.choices(group=[Choice(name=g, value=g) for g in balance.GROUPS])
    async def show(self, interaction: discord.Interaction, group: Choice[str] | None = None):
        if not await require_admin(interaction):
            return
        changed = self._changed(interaction.guild_id)
        if group is None:
            if changed:
                lines = [f"`{key}` **{fmt(value)}** (기본 {fmt(PARAMS[key].default)})" for key, value in changed.items()]
                body = f"기본값에서 바꾼 항목 **{len(changed)}개**\n" + "\n".join(lines)
            else:
                body = "바꾼 항목이 없어요. 모두 기본값이에요."
            body += "\n\n분류를 고르면 전체 값을 볼 수 있어요: " + ", ".join(balance.GROUPS)
            title = "🏚️ 던전 밸런스 — 바꾼 항목"
        else:
            title, body = f"🏚️ 던전 밸런스 — {group.value}", "\n".join(settings_lines(group.value, changed))
            body += "\n\n✏️ = 기본값에서 바꾼 항목. 바꾸려면 `/던전설정`"
        embed = theme.make_embed(title, body[:4000], color=theme.PURPLE)
        for warning in balance.warnings(self.bot.store.balance(interaction.guild_id)):
            embed.add_field(name="주의", value=warning, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------------------------------------------------------- 되돌리기
    @app_commands.command(name="던전설정초기화", description="던전 밸런스를 기본값으로 되돌려요. 항목을 비우면 전부 되돌려요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(key="항목")
    @app_commands.describe(key="되돌릴 항목 (비우면 전부)")
    async def reset(self, interaction: discord.Interaction, key: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        changed = self._changed(gid)
        if key is None:
            if not changed:
                await self._deny(interaction, "바꾼 항목이 없어요. 이미 모두 기본값이에요.")
                return
            await interaction.response.send_message(
                embed=theme.make_embed("♻️ 모두 기본값으로 되돌릴까요?", f"바꾼 항목 **{len(changed)}개**가 모두 기본값으로 돌아가요.", color=theme.RED),
                view=ConfirmResetAll(self.bot, interaction.user.id), ephemeral=True)
            return
        if key not in PARAMS:
            await self._deny(interaction, "그런 항목이 없어요. 항목 이름은 자동완성에서 골라 주세요.")
            return
        if key not in changed:
            await self._deny(interaction, f"**{key}**은(는) 이미 기본값({fmt(PARAMS[key].default)})이에요.")
            return
        old = changed.pop(key)
        store.update_settings(gid, dungeon=changed)
        await interaction.response.send_message(
            embed=theme.notice(f"♻️ **{key}**: {fmt(old)} → 기본값 **{fmt(PARAMS[key].default)}**"), ephemeral=True)

    # ---------------------------------------------------------------- 시뮬레이션
    @app_commands.command(name="던전시뮬", description="지금 밸런스로 던전을 컴퓨터가 대신 돌려 클리어율을 어림잡아요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(runs="판수")
    @app_commands.describe(runs="방식마다 몇 판 돌릴지 (기본 1000)")
    async def simulate(self, interaction: discord.Interaction, runs: app_commands.Range[int, 100, 3000] = 1000):
        if not await require_admin(interaction):
            return
        bal = self.bot.store.balance(interaction.guild_id)
        await interaction.response.defer(ephemeral=True)
        result = await asyncio.to_thread(balance_sim.simulate, bal, runs)
        embed = theme.make_embed(f"🧪 던전 시뮬레이션 — 방식마다 {runs:,}판", color=theme.PURPLE)
        for entry in result.values():
            deaths = sorted(entry["deaths"].items(), key=lambda kv: -kv[1])[:3]
            where = " · ".join(f"{room}번째 방 {rate * 100:.0f}%" for room, rate in deaths if rate >= 0.005) or "거의 없음"
            embed.add_field(name=entry["label"], inline=False,
                            value=f"**클리어 {entry['clear'] * 100:.1f}%** · 평균 도달 방 {entry['avg_room']:.1f}\n쓰러진 곳: {where}")
        for warning in balance.warnings(bal):
            embed.add_field(name="주의", value=warning, inline=False)
        embed.add_field(name="참고", inline=False,
                        value="컴퓨터가 정해진 방식으로 플레이한 어림값이에요. 실제 유저는 장비·아이템을 덜 알뜰하게 써서 보통 더 낮게 나와요.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @set_param.autocomplete("key")
    @reset.autocomplete("key")
    async def key_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        return param_choices(self.bot.store, interaction.guild_id, current)


async def setup(bot):
    await bot.add_cog(DungeonAdmin(bot))
