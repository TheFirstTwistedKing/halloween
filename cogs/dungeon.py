"""/던전 명령과 던전 화면(버튼·아이템 선택). 규칙은 dungeon.py, 진행·저장은 dungeon_service.py가 맡는다."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import dungeon
import dungeon_service as service
import theme
from balance import DEFAULT
from dungeon import ROOMS, Run

BAR = 10


def bar(value: int, maximum: int, full: str, width: int = BAR) -> str:
    filled = 0 if maximum <= 0 else max(0, min(width, -(-value * width // maximum)))   # 남아 있으면 최소 한 칸
    return full * filled + "⬛" * (width - filled)


def run_embed(run: Run, outcome: service.Outcome | None = None, bal=None, cur=None) -> discord.Embed:
    """지금 화면. 끝난 던전이면 결과(보상)를 보여 준다."""
    bal = bal or DEFAULT
    ended = outcome.ended if outcome else None
    if ended == "clear":
        color, title = theme.GREEN, "🏆 던전 클리어!"
    elif ended == "dead":
        color, title = theme.RED, "💀 쓰러졌어요…"
    elif ended == "quit":
        color, title = theme.PURPLE, "🚪 던전에서 나왔어요"
    else:
        color, title = theme.ORANGE, f"🏚️ 던전 — 방 {max(run.room, 1)}/{ROOMS}"
    embed = theme.make_embed(title, "\n".join(run.events) or None, color=color)
    if run.enemy and not ended:
        enemy = run.enemy
        embed.add_field(name=f"{enemy['emoji']} {enemy['name']}" + (" 👑" if enemy["boss"] else "") + (" 💫 기절(다음 공격 못 함)" if enemy.get("stunned") else ""), inline=False,
                        value=f"{bar(enemy['hp'], enemy['max_hp'], '🟥')} {enemy['hp']}/{enemy['max_hp']}")
    embed.add_field(name="❤️ HP", inline=False, value=f"{bar(run.hp, bal.max_hp, '🟩')} {run.hp}/{bal.max_hp}")
    if not ended:
        weapon, shield = bal.gear_by_key.get(run.weapon), bal.gear_by_key.get(run.shield)
        gear = [f"{weapon.emoji} {weapon.name}" if weapon else "👊 맨손", f"{shield.emoji} {shield.name}" if shield else "✋ 맨몸 가드"]
        embed.add_field(name="🎒 장비", value=" · ".join(gear), inline=False)
        held = [f"{bal.item_by_key[k].emoji} {bal.item_by_key[k].name} ×{n}" for k, n in run.items.items() if k in bal.item_by_key]
        if held:                                          # 관리자가 지운 아이템만 남았다면 칸 자체를 숨긴다(빈 칸은 디스코드가 거절한다).
            embed.add_field(name="🧪 소지품", value="\n".join(held), inline=False)
        if run.regen_turns:
            embed.add_field(name="👻 회복 중", value=f"턴마다 HP +{run.regen_amount} (남은 {run.regen_turns}턴)", inline=False)
    reward = outcome.reward if outcome else None
    if ended == "clear" and reward:
        lines = [f"{theme.money(reward.candy, cur)} 을(를) 받았어요."]
        lines += [f"{emoji} **{name}** ×{amount:,}" for emoji, name, amount in reward.items]
        if reward.ticket:
            lines.append(f"🎟️ **히든 칭호 획득권**이 나왔어요! (지금 {reward.tickets_total}장)")
        embed.add_field(name="🎁 보상", value="\n".join(lines), inline=False)
    elif ended in ("dead", "quit"):
        embed.add_field(name="입장 물품", value="입장에 쓴 물품은 돌아오지 않아요. 새로 도전하려면 입장 물품이 다시 필요해요.", inline=False)
    if run.loot and ended:
        embed.add_field(name="🧾 이번에 얻은 것", value=", ".join(run.loot[-10:]), inline=False)
    return embed


class DungeonView(discord.ui.View):
    """한 화면(rev)에 붙는 버튼들. 눌린 뒤의 화면은 새 View로 바뀐다. 봇이 꺼지면 사라지지만 /던전으로 이어서 할 수 있다."""

    def __init__(self, client, user_id: int, run: Run, bal=None):
        super().__init__(timeout=900)
        self.client, self.user_id, self.rev = client, user_id, run.rev
        if run.phase == "battle":
            self._button("공격", "⚔️", "attack", discord.ButtonStyle.danger)
            self._button("방어", "🛡️", "defend", discord.ButtonStyle.primary)
        elif run.phase == "chest":
            self._button("상자 열기", "🎁", "chest", discord.ButtonStyle.success)
        elif run.pending_gear:
            self._button("장착", "✅", "equip", discord.ButtonStyle.success)
            self._button("버리기", "🗑️", "discard", discord.ButtonStyle.secondary)
        elif run.can_advance:
            self._button("앞으로" if run.room else "출발", "🚪", "advance", discord.ButtonStyle.success)
        if not run.pending_gear and any(k in (bal or DEFAULT).item_by_key for k in run.items):
            self.add_item(ItemSelect(run, bal or DEFAULT))
        self._button("나가기", "🏃", "ask_quit", discord.ButtonStyle.secondary)

    def _button(self, label, emoji, action, style):
        button = discord.ui.Button(label=label, emoji=emoji, style=style)

        async def callback(interaction: discord.Interaction):
            if action == "ask_quit":
                await interaction.response.send_message(
                    embed=theme.notice("정말 나갈까요? 던전 진행과 입장에 쓴 물품이 사라져요.", False),
                    view=ConfirmQuit(self.client, self.user_id, self.rev), ephemeral=True)
                return
            await self.handle(interaction, action)
        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def handle(self, interaction: discord.Interaction, action: str, arg: str | None = None):
        await respond(interaction, self.client, self.rev, action, arg)


class ItemSelect(discord.ui.Select):
    def __init__(self, run: Run, bal):
        items = bal.item_by_key
        options = [discord.SelectOption(label=f"{items[k].name} ×{n}", value=k, emoji=items[k].emoji,
                                        description=items[k].description[:100])
                   for k, n in run.items.items() if k in items]
        super().__init__(placeholder="🧪 아이템 사용 (전투 중엔 한 턴을 써요)", options=options[:25], row=1)

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle(interaction, "item", self.values[0])


class ConfirmQuit(discord.ui.View):
    def __init__(self, client, user_id: int, rev: int):
        super().__init__(timeout=60)
        self.client, self.user_id, self.rev = client, user_id, rev
        yes = discord.ui.Button(label="나가기", emoji="🏃", style=discord.ButtonStyle.danger)
        no = discord.ui.Button(label="계속하기", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.confirm, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def confirm(self, interaction: discord.Interaction):
        self.stop()
        await respond(interaction, self.client, self.rev, "quit", None, from_confirm=True)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("계속 진행해요."), view=None)


async def respond(interaction: discord.Interaction, client, rev: int, action: str, arg: str | None = None,
                  from_confirm: bool = False):
    """버튼 하나를 서비스에 넘기고, 결과에 맞게 화면을 바꾼다."""
    bal = client.store.balance(interaction.guild_id)
    guard_ok = dungeon.roll_guard(client.rng, bal) if action == "defend" else False
    outcome = await service.act(client.store, client.locks, client.rng, interaction.guild_id, interaction.user.id,
                                rev, action, arg, guard_ok)
    if not outcome.ok:                                   # 지난 화면·할 수 없는 행동: 화면은 그대로 두고 알려 준다.
        await interaction.response.send_message(embed=theme.notice(outcome.text, False), ephemeral=True)
        return
    run = outcome.run
    view = None if outcome.ended else DungeonView(client, interaction.user.id, run, bal)
    if from_confirm:
        # 확인창(별도 메시지)에서 눌렀다. 던전 화면은 그 메시지에서 따로 바꿀 수 없으니 결과만 알린다.
        await interaction.response.edit_message(embed=run_embed(run, outcome, bal, client.store.currency(interaction.guild_id)), view=None)
        return
    await interaction.response.edit_message(embed=run_embed(run, outcome, bal, client.store.currency(interaction.guild_id)), view=view)


class Dungeon(commands.Cog):
    """🏚️ 입장 물품을 내고 들어가는 10개 방의 던전."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="던전", description="🏚️ 입장 물품을 내고 던전에 들어가요 (진행 중이면 이어서 해요)")
    @app_commands.guild_only()
    async def enter(self, interaction: discord.Interaction):
        outcome = await service.enter(self.bot.store, self.bot.locks, interaction.guild_id, interaction.user.id)
        if not outcome.ok:
            await interaction.response.send_message(embed=theme.notice(outcome.text, False), ephemeral=True)
            return
        bal = self.bot.store.balance(interaction.guild_id)
        await interaction.response.send_message(embed=run_embed(outcome.run, None, bal, self.bot.store.currency(interaction.guild_id)),
                                                view=DungeonView(self.bot, interaction.user.id, outcome.run, bal), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Dungeon(bot))
