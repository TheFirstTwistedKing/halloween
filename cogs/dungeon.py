"""/던전 명령과 던전 화면(버튼·아이템 선택). 규칙은 dungeon.py, 진행·저장은 dungeon_service.py가 맡는다."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import dungeon
import dungeon_service as service
import theme
from owner import owner_only
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
        color, title = theme.ORANGE, f"🏚️ 던전 — {max(run.floor, 1)}/{bal.floors}층 · 방 {max(run.room, 1)}/{ROOMS}"
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
        spare = [bal.gear_by_key.get(run.spare_weapon), bal.gear_by_key.get(run.spare_shield)]
        if any(spare):                                    # 예비 칸은 비어 있으면 보여 주지 않는다.
            embed.add_field(name="📦 예비", inline=False,
                            value=" · ".join(f"{g.emoji} {g.name}" if g else f"{label} —" for g, label in zip(spare, ("⚔️", "🛡️"))))
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
        bal = bal or DEFAULT
        for_sale = dungeon.sellable(run, bal)
        if run.phase == "battle":
            self._button("공격", "⚔️", "attack", discord.ButtonStyle.danger)
            self._button("방어", "🛡️", "defend", discord.ButtonStyle.primary)
        elif run.phase == "chest":
            self._button("상자 열기", "🎁", "chest", discord.ButtonStyle.success)
        elif run.pending_gear:
            self._button("장착", "✅", "equip", discord.ButtonStyle.success)
            gear = bal.gear_by_key.get(run.pending_gear)
            if gear and dungeon._valid(getattr(run, f"spare_{'shield' if gear.slot == 'shield' else 'weapon'}"), gear.slot, bal) is None:
                self._button("예비에 넣기", "📥", "stash", discord.ButtonStyle.primary)      # 그 종류의 예비 칸이 비어 있을 때만
            self._button("버리기", "🗑️", "discard", discord.ButtonStyle.secondary)
            pending = next((e for e in for_sale if e["id"] == "pending"), None)
            if pending:                                   # 새 장비를 바로 파는 지름길
                self._button(f"판매 +{pending['price']:,}", "💰", "sell_pending", discord.ButtonStyle.primary)
        elif run.can_advance:
            self._button("앞으로" if run.room else "출발", "🚪", "advance", discord.ButtonStyle.success)
        if not run.in_battle and any(e["id"] != "pending" for e in for_sale):
            self._button("판매", "💰", "open_sell", discord.ButtonStyle.secondary)
        if not run.in_battle and (run.weapon or run.shield or run.spare_weapon or run.spare_shield):
            self._button("장비", "🎒", "open_equip", discord.ButtonStyle.secondary)
        if not run.pending_gear and any(k in bal.item_by_key for k in run.items):
            self.add_item(ItemSelect(run, bal))
        self._button("나가기", "🏃", "ask_quit", discord.ButtonStyle.secondary)

    def _button(self, label, emoji, action, style):
        button = discord.ui.Button(label=label, emoji=emoji, style=style)

        async def callback(interaction: discord.Interaction):
            if action == "ask_quit":
                await interaction.response.send_message(
                    embed=theme.notice("정말 나갈까요? 던전 진행과 입장에 쓴 물품이 사라져요.", False),
                    view=ConfirmQuit(self.client, self.user_id, self.rev), ephemeral=True)
                return
            if action == "open_sell":
                await open_sell(interaction, self.client, self.rev)
                return
            if action == "open_equip":
                await open_equip(interaction, self.client, self.rev)
                return
            if action == "sell_pending":
                await respond_sell(interaction, self.client, self.rev, ["pending"], "all", stay=False)
                return
            await self.handle(interaction, action)
        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

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
        return await owner_only(interaction, self.user_id)

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
    outcome = await service.act(client.store, client.locks, client.rng, interaction.guild_id, interaction.user.id,
                                rev, action, arg)
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


def sell_embed(run: Run, bal, cur, wallet: int, result: bool = False) -> discord.Embed:
    """판매 화면. result=True면 방금 판 내용(run.events)을 맨 위에 보여 준다."""
    entries = dungeon.sellable(run, bal)
    intro = ("팔 것을 고른 뒤 아래 버튼을 눌러요. 여러 개를 한꺼번에 고를 수 있어요.\n"
             "던전을 나가거나 쓰러지면 장비·소지품은 사라지니, 팔 수 있을 때 팔아 두세요.")
    embed = theme.make_embed("💰 던전 판매", "\n".join(run.events) if result and run.events else intro, color=theme.ORANGE)
    lines = []
    for e in entries:
        if e["kind"] == "item":
            lines.append(f"{e['emoji']} **{e['name']}** ×{e['qty']:,} — 개당 {theme.money(e['price'], cur)} · 전부 {theme.money(e['price'] * e['qty'], cur)}")
        else:
            lines.append(f"{e['emoji']} **{e['name']}** ({e['tag']}) — {theme.money(e['price'], cur)}")
    if lines:
        embed.add_field(name="🧾 팔 수 있는 것", value="\n".join(lines)[:1000], inline=False)
    if len(entries) > 25:
        embed.add_field(name="ℹ️", value="목록이 길어서 앞의 25개만 고를 수 있어요. 판 뒤에 다시 열면 나머지가 보여요.", inline=False)
    embed.add_field(name="내 재화", value=theme.money(wallet, cur), inline=False)
    return embed


class SellView(discord.ui.View):
    """판매 화면: 여러 개를 골라 한꺼번에 팔 수 있다. 던전 화면과 같은 메시지에서 바뀌어 나온다."""

    def __init__(self, client, user_id: int, run: Run, bal, cur):
        super().__init__(timeout=900)
        self.client, self.user_id, self.rev, self.picks = client, user_id, run.rev, []
        entries = dungeon.sellable(run, bal)
        options = []
        for e in entries[:25]:
            if e["kind"] == "item":
                label, hint = f"{e['name']} ×{e['qty']:,}", f"개당 {theme.plain_money(e['price'], cur)} · 전부 {theme.plain_money(e['price'] * e['qty'], cur)}"
            else:
                label, hint = f"{e['name']} ({e['tag']})", theme.plain_money(e["price"], cur)
            options.append(discord.SelectOption(label=label[:100], value=e["id"], emoji=e["emoji"], description=hint[:100]))
        select = discord.ui.Select(placeholder="💰 팔 것을 골라요 (여러 개 가능)", min_values=1, max_values=len(options), options=options, row=0)
        select.callback = self.on_select
        self.add_item(select)
        self._button("선택한 것 판매", "💰", self.sell_picked, discord.ButtonStyle.success)
        if any(e["kind"] == "item" and e["qty"] > 1 for e in entries):
            self._button("1개씩만", "☝️", self.sell_one, discord.ButtonStyle.secondary)
        if any(e["kind"] == "item" for e in entries):
            self._button("소모품 전부", "🧺", self.sell_items, discord.ButtonStyle.primary)
        self._button("돌아가기", "↩️", self.back, discord.ButtonStyle.secondary)

    def _button(self, label, emoji, callback, style):
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=1)
        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def on_select(self, interaction: discord.Interaction):
        self.picks = list(interaction.data.get("values", []))
        await interaction.response.defer()

    async def _sell(self, interaction: discord.Interaction, mode: str):
        if mode != "items" and not self.picks:
            await interaction.response.send_message(embed=theme.notice("먼저 위 목록에서 팔 것을 골라 주세요.", False), ephemeral=True)
            return
        await respond_sell(interaction, self.client, self.rev, self.picks, mode, stay=True)

    async def sell_picked(self, interaction: discord.Interaction):
        await self._sell(interaction, "all")

    async def sell_one(self, interaction: discord.Interaction):
        await self._sell(interaction, "one")

    async def sell_items(self, interaction: discord.Interaction):
        await self._sell(interaction, "items")

    async def back(self, interaction: discord.Interaction):
        run = await service.peek(self.client.store, interaction.guild_id, interaction.user.id)
        if run is None or run.rev != self.rev:
            await interaction.response.send_message(embed=theme.notice("지난 화면이에요. `/던전`으로 다시 열어 주세요.", False), ephemeral=True)
            return
        bal = self.client.store.balance(interaction.guild_id)
        await interaction.response.edit_message(
            embed=run_embed(run, None, bal, self.client.store.currency(interaction.guild_id)), view=DungeonView(self.client, interaction.user.id, run, bal))


async def open_sell(interaction: discord.Interaction, client, rev: int):
    """던전 화면의 [판매] 버튼: 같은 메시지를 판매 화면으로 바꾼다."""
    run = await service.peek(client.store, interaction.guild_id, interaction.user.id)
    if run is None or run.rev != rev:
        await interaction.response.send_message(embed=theme.notice("지난 화면이에요. `/던전`으로 다시 열어 주세요.", False), ephemeral=True)
        return
    bal, cur = client.store.balance(interaction.guild_id), client.store.currency(interaction.guild_id)
    if not dungeon.sellable(run, bal) or run.in_battle:
        await interaction.response.send_message(embed=theme.notice("지금은 팔 수 있는 게 없어요.", False), ephemeral=True)
        return
    await interaction.response.edit_message(
        embed=sell_embed(run, bal, cur, client.store.wallet(interaction.guild_id, interaction.user.id)),
        view=SellView(client, interaction.user.id, run, bal, cur))


async def respond_sell(interaction: discord.Interaction, client, rev: int, picks, mode: str, stay: bool):
    """판매를 서비스에 넘기고 화면을 바꾼다. stay이고 아직 팔 게 남았다면 판매 화면에 머물고, 아니면 던전 화면으로 돌아간다."""
    outcome = await service.sell(client.store, client.locks, interaction.guild_id, interaction.user.id, rev, picks, mode)
    if not outcome.ok:
        await interaction.response.send_message(embed=theme.notice(outcome.text, False), ephemeral=True)
        return
    run = outcome.run
    bal, cur = client.store.balance(interaction.guild_id), client.store.currency(interaction.guild_id)
    if stay and dungeon.sellable(run, bal):
        await interaction.response.edit_message(
            embed=sell_embed(run, bal, cur, client.store.wallet(interaction.guild_id, interaction.user.id), result=True),
            view=SellView(client, interaction.user.id, run, bal, cur))
        return
    await interaction.response.edit_message(embed=run_embed(run, None, bal, cur), view=DungeonView(client, interaction.user.id, run, bal))


def gear_text(gear) -> str:
    """장비 화면의 한 칸: '🏏 낡은 몽둥이 — 공격 피해량 +30'. 비어 있으면 '—'."""
    return f"{gear.emoji} **{gear.name}** — {gear.description}" if gear else "—"


def equip_embed(run: Run, bal, result: bool = False) -> discord.Embed:
    """장비 화면: 장착한 무기·방패와 예비 칸. result=True면 방금 한 교체(run.events)를 맨 위에 보여 준다."""
    intro = ("**무기 1개·방패 1개**를 예비 칸에 가지고 있다가 장착한 것과 서로 바꿀 수 있어요.\n"
             "예비 칸은 이번 던전 동안만 쓰고, 던전을 나가거나 쓰러지면 사라져요.")
    embed = theme.make_embed("🎒 장비", "\n".join(run.events) if result and run.events else intro, color=theme.ORANGE)
    for slot, name, icon, bare in (("weapon", "무기", "⚔️", "👊 맨손"), ("shield", "방패", "🛡️", "✋ 맨몸 가드")):
        on = dungeon._valid(getattr(run, slot), slot, bal)
        spare = dungeon._valid(getattr(run, f"spare_{slot}"), slot, bal)
        embed.add_field(name=f"{icon} {name}", inline=False,
                        value=f"**장착**: {gear_text(bal.gear_by_key.get(on)) if on else bare}\n**예비**: {gear_text(bal.gear_by_key.get(spare)) if spare else '비어 있어요'}")
    return embed


class EquipView(discord.ui.View):
    """장비 화면: 장착한 것과 예비를 바꾼다. 던전 화면과 같은 메시지에서 바뀌어 나오고, 연 사람만 조작할 수 있다."""

    def __init__(self, client, user_id: int, run: Run, bal):
        super().__init__(timeout=900)
        self.client, self.user_id, self.rev = client, user_id, run.rev
        for slot, name, icon in (("weapon", "무기", "⚔️"), ("shield", "방패", "🛡️")):
            on, spare = dungeon._valid(getattr(run, slot), slot, bal), dungeon._valid(getattr(run, f"spare_{slot}"), slot, bal)
            if on and spare:
                label = f"{name} 교체"
            elif on:
                label = f"{name} 예비에 넣기"
            elif spare:
                label = f"{name} 꺼내 장착"
            else:
                label = f"{name} 교체"
            button = discord.ui.Button(label=label, emoji=icon, style=discord.ButtonStyle.primary, disabled=not (on or spare), row=0)
            button.callback = self._swap(slot)
            self.add_item(button)
        back = discord.ui.Button(label="돌아가기", emoji="↩️", style=discord.ButtonStyle.secondary, row=0)
        back.callback = self.back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    def _swap(self, slot: str):
        async def callback(interaction: discord.Interaction):
            client = self.client
            outcome = await service.act(client.store, client.locks, client.rng, interaction.guild_id, interaction.user.id, self.rev, f"swap_{slot}")
            if not outcome.ok:
                await interaction.response.send_message(embed=theme.notice(outcome.text, False), ephemeral=True)
                return
            bal = client.store.balance(interaction.guild_id)
            await interaction.response.edit_message(embed=equip_embed(outcome.run, bal, result=True), view=EquipView(client, interaction.user.id, outcome.run, bal))
        return callback

    async def back(self, interaction: discord.Interaction):
        run = await service.peek(self.client.store, interaction.guild_id, interaction.user.id)
        if run is None or run.rev != self.rev:
            await interaction.response.send_message(embed=theme.notice("지난 화면이에요. `/던전`으로 다시 열어 주세요.", False), ephemeral=True)
            return
        bal = self.client.store.balance(interaction.guild_id)
        await interaction.response.edit_message(
            embed=run_embed(run, None, bal, self.client.store.currency(interaction.guild_id)), view=DungeonView(self.client, interaction.user.id, run, bal))


async def open_equip(interaction: discord.Interaction, client, rev: int):
    """던전 화면의 [🎒 장비] 버튼: 같은 메시지를 장비 화면으로 바꾼다."""
    run = await service.peek(client.store, interaction.guild_id, interaction.user.id)
    if run is None or run.rev != rev:
        await interaction.response.send_message(embed=theme.notice("지난 화면이에요. `/던전`으로 다시 열어 주세요.", False), ephemeral=True)
        return
    if run.in_battle:
        await interaction.response.send_message(embed=theme.notice("싸우는 중에는 장비를 바꿀 수 없어요.", False), ephemeral=True)
        return
    bal = client.store.balance(interaction.guild_id)
    await interaction.response.edit_message(embed=equip_embed(run, bal), view=EquipView(client, interaction.user.id, run, bal))


class Dungeon(commands.Cog):
    """🏚️ 입장 물품을 내고 들어가는 10개 방의 던전."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="던전", description="🏚️ 입장 물품을 내고 던전에 들어가요 (진행 중이면 이어서 해요)")
    @app_commands.guild_only()
    async def enter(self, interaction: discord.Interaction):
        outcome = await service.enter(self.bot.store, self.bot.locks, interaction.guild_id, interaction.user.id)
        if not outcome.ok:                                   # 던전은 개인 화면이다: 입장 불가 안내도 던전 화면도 본인에게만 보인다.
            await interaction.response.send_message(embed=theme.notice(outcome.text, False), ephemeral=True)
            return
        bal = self.bot.store.balance(interaction.guild_id)
        await interaction.response.send_message(embed=run_embed(outcome.run, None, bal, self.bot.store.currency(interaction.guild_id)),
                                                view=DungeonView(self.bot, interaction.user.id, outcome.run, bal), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Dungeon(bot))
