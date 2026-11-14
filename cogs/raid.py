"""레이드 화면과 관리자 명령어(/레이드 열기·시작·취소). 규칙은 raid.py에 있다.

- 레이드 화면은 채널에 하나 올라가는 공용 메시지다. 버튼 ID가 고정이라 봇을 껐다 켜도 계속 동작한다(main.setup_hook에서 add_view).
- 버튼을 누르면 화면을 바로 고치고(너무 자주면 3초마다 모아서), 안 되는 행동(쿨타임·다운 등)만 누른 사람에게 따로 알린다.
- 3초마다 보스 공격·끝났는지를 처리하고 화면을 고친다.
"""
from __future__ import annotations

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import raid
import theme
from access import require_admin
from raid import RaidError
from store import valid_emoji

LOG = logging.getLogger("halloween")
Choice = app_commands.Choice
PERMS = dict(guild_only=True, default_permissions=discord.Permissions(administrator=True))
SKILL_CHOICES = [Choice(name=f"{name} — {meaning}", value=key) for key, (name, meaning, _r, _t) in raid.BOSS_SKILLS.items()]
PANEL_TTL = 14 * 60              # 내 상태 창(나에게만 보이는 메시지)은 디스코드에서 15분까지만 고칠 수 있다
PANEL_EDITS = 10                 # 정리 한 번에 고치는 내 상태 창 수(디스코드 한도를 넘지 않게)
EDIT_GAP = 2.5                   # 버튼을 눌러 화면을 바로 고치는 최소 간격(초). 더 잦으면 다음 정리 때 고친다
BUTTONS = (("join", "참가", "🙋", discord.ButtonStyle.success, 0), ("attack", "공격", "⚔️", discord.ButtonStyle.danger, 0),
           ("defend", "방어", "🛡️", discord.ButtonStyle.primary, 0), ("skill", "스킬", "✨", discord.ButtonStyle.secondary, 0),
           ("ult", "궁극기", "🟡", discord.ButtonStyle.secondary, 1), ("special", "각성", "⚡", discord.ButtonStyle.danger, 1),
           ("status", "내 상태", "📋", discord.ButtonStyle.secondary, 1))
ITEM_ID = "raid:item"


def bar(value: int, maximum: int, width: int = 12) -> str:
    filled = 0 if maximum <= 0 else max(0, min(width, round(value * width / maximum)))
    return "🟥" * filled + "⬛" * (width - filled)


def member_entry(m: dict) -> str:
    """참가자 한 명: '@이름 ❤️820'(다운이면 💀). 각성 중 ⚡, 궁극기 준비 🟡."""
    if m["down"]:
        return f"<@{m['user']}> 💀"
    return (f"<@{m['user']}> ❤️{m['hp']:,}" + ("⚡" if m.get("awakened") else "")
            + ("🟡" if m.get("ult") and m.get("ult_gauge", 0) >= 100 else ""))


def member_chunks(everyone, limit: int = 1000) -> list[list[str]]:
    """참가자 목록을 디스코드 칸 한도(1024자) 안에 들어가게 나눈다."""
    chunks, current, size = [], [], 0
    for m in everyone:
        entry = member_entry(m)
        if current and size + len(entry) + 2 > limit:
            chunks.append(current)
            current, size = [], 0
        current.append(entry)
        size += len(entry) + 2
    if current:
        chunks.append(current)
    return chunks


def panel_embed(store, guild_id: int, row, user: int, now=None) -> discord.Embed:
    """나에게만 보이는 내 상태 창: HP 막대 + 쿨타임·게이지·회복 아이템."""
    mine = next((m for m in raid.members(store, row["id"]) if m["user"] == user), None)
    top = f"{bar(mine['hp'], mine['max_hp'], 10).replace('🟥', '🟩')} ❤️ **{mine['hp']:,}/{mine['max_hp']:,}**\n" if mine else ""
    return theme.make_embed("📋 내 레이드 상태 (나에게만 보여요)", top + raid.status_text(store, guild_id, row, user, now), color=theme.PURPLE)


def raid_embed(store, row, now=None) -> discord.Embed:
    data = raid.boss(row)
    everyone = raid.members(store, row["id"])
    alive = sum(1 for m in everyone if not m["down"])
    cur = store.currency(row["guild"])
    head = {"open": "🙋 모집 중 — [참가]를 누르고 관리자의 `/레이드 시작`을 기다려요", "active": f"⏳ 남은 시간 {raid.time_left(row, now)}",
            "won": "🏆 레이드 성공!", "lost": "💀 레이드 실패", "cancelled": "🚫 취소된 레이드"}[row["state"]]
    color = {"open": theme.ORANGE, "active": theme.RED, "won": theme.GOLD, "lost": theme.PURPLE, "cancelled": theme.PURPLE}[row["state"]]
    phase = f" [{data['phase']}/2페이즈]" if data["phase2_hp"] or data["phase"] == 2 else ""
    lines = [head, f"{data['emoji']} **{data['name']}**{phase}", f"{bar(data['hp'], data['max_hp'])} {data['hp']:,}/{data['max_hp']:,}",
             f"⚔️ 공격 {data['damage']:,} · {data['attack_every']}초마다 · 행동 쿨타임 {data['cooldown']}초"]
    skill = raid.skill_line(data)
    if skill:
        lines.append(f"🌀 스킬 {skill}")
    if data["barrier_turns"]:
        lines.append(f"🛡️ 보스 방어막: 받는 피해 {data['barrier']}% 감소 (남은 {data['barrier_turns']}번)")
    if data["burn_turns"]:
        lines.append(f"🔥 과열: 보스가 공격할 때마다 모두 {data['burn']:,} 피해 (남은 {data['burn_turns']}번)")
    embed = theme.make_embed("🐉 레이드", "\n".join(lines), color=color)
    head = f"👥 참가 {len(everyone)}/{raid.MAX_MEMBERS} (살아 있음 {alive}) · ❤️ HP"
    for number, chunk in enumerate(member_chunks(everyone) or [["아직 없어요"]]):          # 50명 모두의 HP가 보이게 칸을 나눈다
        embed.add_field(name=head if number == 0 else "👥 (이어서)", inline=False, value=", ".join(chunk))
    top = sorted((m for m in everyone if m["damage"]), key=lambda m: -m["damage"])[:5]
    if top:
        embed.add_field(name="🏅 준 피해", inline=True, value="\n".join(f"<@{m['user']}> {m['damage']:,}" for m in top))
    reward = f"{cur.money(data['reward'])}을(를) 준 피해 비율대로" + (f" + 참가 보상 {cur.money(data['min_reward'])}" if data["min_reward"] else "")
    embed.add_field(name="🎁 승리 보상", inline=True, value=reward if data["reward"] or data["min_reward"] else "없음")
    items = data.get("items") if row["state"] != "open" else store.settings(row["guild"])["raid_items"]
    embed.add_field(name="🧪 회복 아이템 (시작할 때 모두에게)", inline=False, value=raid.item_list(items or []) or "없음")
    if row["state"] == "won" and data.get("payout"):
        paid = sorted(data["payout"].items(), key=lambda p: -p[1])[:15]
        embed.add_field(name="💰 받은 보상", inline=False, value=", ".join(f"<@{u}> +{cur.money(n)}" for u, n in paid)[:1024] or "없음")
    if data.get("log"):
        embed.add_field(name="📜 기록", inline=False, value="\n".join(data["log"])[-1024:])
    embed.set_footer(text="기지에 전시한 장비(무기·방패·갑옷)로 싸워요. 장비는 잃지 않아요. ⚡ 각성 중 · 🟡 궁극기 준비 · 📋 내 상태로 게이지·아이템 확인")
    return embed


class RaidView(discord.ui.View):
    """레이드 화면 버튼(누구나 누른다). 버튼 ID가 고정이라 봇이 다시 켜져도 동작한다."""

    def __init__(self, cog: "Raid | None" = None, ended: bool = False, items=()):
        super().__init__(timeout=None)
        self.cog = cog
        for action, label, emoji, style, row in BUTTONS:
            button = discord.ui.Button(custom_id=f"raid:{action}", label=label, emoji=emoji, style=style, disabled=ended, row=row)
            button.callback = self._callback(action)
            self.add_item(button)
        options = [discord.SelectOption(label=f"{i['name']} (HP +{i['heal']:,})"[:100], value=str(i["id"]), emoji=i["emoji"]) for i in items]
        select = discord.ui.Select(custom_id=ITEM_ID, placeholder="🧪 회복 아이템 쓰기", row=2, disabled=ended or not options,
                                   options=options or [discord.SelectOption(label="회복 아이템 없음", value="0")])
        select.callback = self._pick
        self.add_item(select)

    async def _pick(self, interaction: discord.Interaction):
        cog = self.cog or interaction.client.get_cog("Raid")
        value = (interaction.data.get("values") or ["0"])[0]
        await cog.press(interaction, f"item:{value}")

    def _callback(self, action: str):
        async def callback(interaction: discord.Interaction):
            cog = self.cog or interaction.client.get_cog("Raid")
            await cog.press(interaction, action)
        return callback


class Raid(commands.Cog):
    """🐉 레이드."""

    group = app_commands.Group(name="레이드", description="레이드를 열고 시작하고 취소해요 (관리자)", **PERMS)

    def __init__(self, bot, rng=None):
        self.bot = bot
        self.rng = rng or random.Random()
        self.last_edit: dict[int, float] = {}
        self.panels: dict[tuple[int, int], dict] = {}    # (레이드, 사람) → 내 상태 창 {message, made, seen}

    async def cog_load(self):
        self.bot.add_view(RaidView(self))
        self.refresh.start()

    async def cog_unload(self):
        self.refresh.cancel()

    # ---------------------------------------------------------------- 버튼
    def view_for(self, row) -> RaidView:
        """그 레이드 화면에 붙일 버튼(회복 아이템 메뉴는 시작했으면 그 레이드의 것, 모집 중이면 지금 설정)."""
        data = raid.boss(row)
        items = data.get("items") if row["state"] != "open" else self.bot.store.settings(row["guild"])["raid_items"]
        return RaidView(self, ended=row["state"] not in raid.OPEN, items=items or ())

    async def press(self, interaction: discord.Interaction, action: str):
        store, gid = self.bot.store, interaction.guild_id
        row = raid.by_message(store, interaction.message.id) if interaction.message else None
        if row is None or row["state"] not in raid.OPEN:
            await self._private(interaction, "이 레이드는 끝났어요.")
            return
        if action == "status":                                # 내 상태: 누른 사람에게만 보이는 창을 새로 띄운다(화면은 그대로)
            try:
                panel_embed(store, gid, row, interaction.user.id)
            except RaidError as exc:
                await self._private(interaction, str(exc))
                return
            await interaction.response.defer()
            await self.show_panel(interaction, row, interaction.user.id, new=True)
            return
        try:
            if action == "join":
                raid.join(store, gid, interaction.user.id)
            else:
                raid.act(store, gid, interaction.user.id, action, self.rng)
        except RaidError as exc:
            await self._private(interaction, str(exc))
            return
        row = raid.get(store, row["id"])
        now = time.time()
        if now - self.last_edit.get(row["id"], 0) >= EDIT_GAP or row["state"] not in raid.OPEN:
            self.last_edit[row["id"]] = now
            await interaction.response.edit_message(embed=raid_embed(store, row), view=self.view_for(row))
            raid.mark_clean(store, row["id"])
        else:
            await interaction.response.defer()                # 화면은 다음 정리 때(3초 안에) 고친다
        await self.show_panel(interaction, row, interaction.user.id)

    def _seen(self, row, user: int):
        """내 상태 창에 보여 준 값(바뀌었을 때만 다시 고친다)."""
        mine = next((m for m in raid.members(self.bot.store, row["id"]) if m["user"] == user), None)
        if mine is None:
            return None
        return (mine["hp"], mine["down"], mine.get("ult_gauge", 0), mine.get("awaken_gauge", 0), mine.get("awakened"),
                tuple(sorted(mine.get("items", {}).items())), row["state"])

    async def show_panel(self, interaction, row, user: int, new: bool = False) -> None:
        """누른 사람에게만 보이는 내 상태 창을 고친다. 없거나 15분이 지나 못 고치면(또는 new면) 새로 띄운다."""
        store = self.bot.store
        try:
            embed = panel_embed(store, row["guild"], row, user)
        except RaidError:
            return
        key, entry = (row["id"], user), self.panels.get((row["id"], user))
        if entry and not new and time.time() - entry["made"] < PANEL_TTL:
            try:
                await entry["message"].edit(embed=embed)
                entry["seen"] = self._seen(row, user)
                return
            except discord.HTTPException:
                pass
        try:
            message = await interaction.followup.send(embed=embed, ephemeral=True, wait=True)
        except discord.HTTPException:
            return
        self.panels[key] = {"message": message, "made": time.time(), "seen": self._seen(row, user)}

    async def refresh_panels(self, row) -> int:
        """보스에게 맞는 등으로 바뀐 사람의 내 상태 창을 고친다(정리 한 번에 PANEL_EDITS개까지). 고친 수."""
        done = 0
        for (raid_id, user), entry in list(self.panels.items()):
            if raid_id != row["id"] or done >= PANEL_EDITS:
                continue
            if time.time() - entry["made"] >= PANEL_TTL:
                del self.panels[(raid_id, user)]
                continue
            seen = self._seen(row, user)
            if seen == entry["seen"]:
                continue
            try:
                await entry["message"].edit(embed=panel_embed(self.bot.store, row["guild"], row, user))
                entry["seen"] = seen
                done += 1
            except (discord.HTTPException, RaidError):
                del self.panels[(raid_id, user)]
        if row["state"] not in raid.OPEN:                     # 끝난 레이드의 창은 마지막으로 고친 뒤 잊는다
            for key in [k for k in self.panels if k[0] == row["id"]]:
                del self.panels[key]
        return done

    async def _private(self, interaction, text: str):
        """레이드 화면 안에서 안 되는 행동은 누른 사람에게만 알린다(공용 화면을 어지럽히지 않게)."""
        try:
            await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=True)
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------------- 정리(3초마다)
    @tasks.loop(seconds=3)
    async def refresh(self):
        try:
            await self.refresh_now()
        except Exception:
            LOG.exception("레이드 정리 중 오류")

    @refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    async def refresh_now(self, now=None) -> list[int]:
        """보스 공격·끝났는지를 처리하고, 바뀐 화면을 고친다. 고친 레이드 id 목록."""
        store, edited = self.bot.store, []
        for row in raid.running(store):
            raid.tick(store, row["guild"], self.rng, now)
            fresh = raid.get(store, row["id"])
            await self.refresh_panels(fresh)
            if not raid.boss(fresh).get("dirty") or not fresh["message"]:
                continue
            channel = self.bot.get_channel(fresh["channel"])
            if channel is None or not hasattr(channel, "get_partial_message"):
                continue
            try:
                await channel.get_partial_message(fresh["message"]).edit(embed=raid_embed(store, fresh, now),
                                                                         view=self.view_for(fresh))
            except discord.HTTPException:
                LOG.warning("레이드 %s 화면을 고치지 못했어요", fresh["id"])
                continue
            raid.mark_clean(store, fresh["id"])
            self.last_edit[fresh["id"]] = time.time()
            edited.append(fresh["id"])
        return edited

    # ---------------------------------------------------------------- 관리자
    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    @group.command(name="열기", description="레이드를 열어요. 이 채널에 레이드 화면이 올라가고 [참가]로 모여요 (관리자)")
    @app_commands.rename(name="이름", hp="체력", damage="공격", emoji="이모지", minutes="제한시간", reward="보상", min_reward="참가보상",
                         attack_every="공격간격", cooldown="쿨타임", phase2_hp="2페이즈체력", phase2_damage="2페이즈공격", skill="스킬",
                         skill_every="스킬주기", skill_power="스킬세기", skill_turns="스킬턴", skill_name="스킬이름")
    @app_commands.describe(name="레이드 보스 이름", hp="체력(최대 10억)", damage="한 번 공격의 피해", emoji="보스 이모지(기본 🐉)",
                           minutes="싸우는 시간(분, 1~180, 기본 10)", reward="이기면 준 피해 비율대로 나눌 재화 총액",
                           min_reward="이기면 참가자 모두가 더 받는 재화", attack_every="보스가 공격하는 간격(초, 3~120, 기본 8)",
                           cooldown="참가자 행동 쿨타임(초, 1~60, 기본 3)", phase2_hp="2페이즈 체력(0이면 없음)", phase2_damage="2페이즈 공격(0이면 그대로)",
                           skill="보스 스킬(보스 공격 N번마다)", skill_every="몇 번째 공격마다 스킬(1~20)",
                           skill_power="스킬 세기: 강타 공격의 %, 자가 수리 최대 체력의 %, 방어막 감소 %, 과열 피해", skill_turns="방어막·과열이 이어지는 보스 공격 수",
                           skill_name="스킬의 보이는 이름(비우면 기본 이름)")
    @app_commands.choices(skill=SKILL_CHOICES)
    async def open(self, interaction: discord.Interaction, name: str, hp: app_commands.Range[int, 1, raid.HP_MAX],
                   damage: app_commands.Range[int, 0, 10_000_000], emoji: str = "🐉", minutes: app_commands.Range[int, 1, 180] = 10,
                   reward: app_commands.Range[int, 0, raid.REWARD_MAX] = 0, min_reward: app_commands.Range[int, 0, raid.REWARD_MAX] = 0,
                   attack_every: app_commands.Range[int, 3, 120] = 8, cooldown: app_commands.Range[int, 1, 60] = 3,
                   phase2_hp: app_commands.Range[int, 0, raid.HP_MAX] = 0, phase2_damage: app_commands.Range[int, 0, 10_000_000] = 0,
                   skill: Choice[str] | None = None, skill_every: app_commands.Range[int, 1, 20] = 3,
                   skill_power: app_commands.Range[int, 1, 100000] = 150, skill_turns: app_commands.Range[int, 0, 20] = 0,
                   skill_name: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        emoji = emoji.strip()
        if not valid_emoji(emoji):
            await self._say(interaction, "이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요.", False)
            return
        try:
            data = raid.check_boss(name=name, emoji=emoji, hp=hp, damage=damage, phase2_hp=phase2_hp, phase2_damage=phase2_damage,
                                   skill=skill.value if skill else None, skill_every=skill_every if skill else 0,
                                   skill_power=skill_power if skill else 0, skill_turns=skill_turns if skill else 0, skill_name=skill_name)
            raid_id = raid.open_raid(store, gid, getattr(interaction.channel, "id", None), data, minutes=minutes, reward=reward,
                                     min_reward=min_reward, attack_every=attack_every, cooldown=cooldown)
        except RaidError as exc:
            await self._say(interaction, str(exc), False)
            return
        row = raid.get(store, raid_id)
        await interaction.response.send_message(embed=raid_embed(store, row), view=self.view_for(row), ephemeral=False)
        try:
            message = await interaction.original_response()
            raid.set_message(store, raid_id, message.id)
        except discord.HTTPException:
            LOG.warning("레이드 %s 화면 메시지를 찾지 못했어요", raid_id)
        raid.mark_clean(store, raid_id)

    @group.command(name="시작", description="모집 중인 레이드를 시작해요. 제한 시간이 흐르기 시작해요 (관리자)")
    async def begin(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        try:
            row = raid.start(self.bot.store, interaction.guild_id)
        except RaidError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"🔥 레이드를 시작했어요! {raid.boss(row)['minutes']}분 동안 싸워요. 레이드 화면의 버튼을 눌러 주세요.")
        await self.refresh_now()

    @group.command(name="취소", description="모집 중이거나 진행 중인 레이드를 취소해요. 보상은 없어요 (관리자)")
    async def cancel(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        try:
            raid.cancel(self.bot.store, interaction.guild_id)
        except RaidError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, "🚫 레이드를 취소했어요.")
        await self.refresh_now()


class RaidItems(commands.Cog):
    """🧪 레이드 회복 아이템(관리자): 레이드를 시작할 때 참가자 모두에게 주는 회복 아이템을 만들고 고친다."""

    group = app_commands.Group(name="레이드아이템", description="레이드를 시작할 때 모두에게 주는 회복 아이템을 정해요 (관리자)", **PERMS)

    def __init__(self, bot):
        self.bot = bot

    async def _say(self, interaction, text: str, ok: bool = True):
        await interaction.response.send_message(embed=theme.notice(text, ok), ephemeral=False)

    def _items(self, gid: int) -> list[dict]:
        return [dict(i) for i in self.bot.store.settings(gid)["raid_items"]]

    def _find(self, items, name: str):
        needle = (name or "").strip().lower()
        return next((i for i in items if i["name"].lower() == needle), None)

    async def _store(self, interaction, items, text: str):
        try:
            self.bot.store.update_settings(interaction.guild_id, raid_items=items)
        except ValueError as exc:
            await self._say(interaction, str(exc), False)
            return
        await self._say(interaction, f"{text}\n지금 목록: {raid.item_list(items) or '없음'}\n다음에 시작하는 레이드부터 적용돼요(진행 중인 레이드는 그대로).")

    @group.command(name="추가", description="회복 아이템을 더해요(최대 5종) (관리자)")
    @app_commands.rename(name="이름", emoji="이모지", heal="회복량", amount="개수")
    @app_commands.describe(name="아이템 이름", emoji="이모지", heal="한 번에 회복하는 HP(1~100,000)", amount="레이드마다 한 사람이 받는 개수(1~99)")
    async def add(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 30], emoji: str,
                  heal: app_commands.Range[int, 1, 100000], amount: app_commands.Range[int, 1, 99] = 1):
        if not await require_admin(interaction):
            return
        items = self._items(interaction.guild_id)
        if self._find(items, name):
            await self._say(interaction, f"**{name}**은(는) 이미 있어요. `/레이드아이템 수정`으로 고쳐 주세요.", False)
            return
        if not valid_emoji(emoji.strip()):
            await self._say(interaction, "이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요.", False)
            return
        items.append({"id": max((i["id"] for i in items), default=0) + 1, "name": name.strip(), "emoji": emoji.strip(), "heal": heal, "amount": amount})
        await self._store(interaction, items, f"🧪 **{name.strip()}**을(를) 더했어요.")

    @group.command(name="수정", description="회복 아이템의 이름·이모지·회복량·개수를 고쳐요. 비운 칸은 그대로 (관리자)")
    @app_commands.rename(target="아이템", name="새이름", emoji="이모지", heal="회복량", amount="개수")
    @app_commands.describe(target="고칠 아이템(자동완성)", name="새 이름", emoji="새 이모지", heal="한 번에 회복하는 HP", amount="한 사람이 받는 개수")
    async def edit(self, interaction: discord.Interaction, target: str, name: app_commands.Range[str, 1, 30] | None = None,
                   emoji: str | None = None, heal: app_commands.Range[int, 1, 100000] | None = None,
                   amount: app_commands.Range[int, 1, 99] | None = None):
        if not await require_admin(interaction):
            return
        items = self._items(interaction.guild_id)
        item = self._find(items, target)
        if item is None:
            await self._say(interaction, "그런 회복 아이템이 없어요. 이름은 자동완성에서 골라 주세요.", False)
            return
        if emoji is not None and not valid_emoji(emoji.strip()):
            await self._say(interaction, "이모지는 유니코드 이모지나 이 서버의 이모지 하나로 적어 주세요.", False)
            return
        if name is not None and name.strip().lower() != item["name"].lower() and self._find(items, name):
            await self._say(interaction, f"**{name}**은(는) 이미 있어요.", False)
            return
        changes = {k: v for k, v in dict(name=name.strip() if name else None, emoji=emoji.strip() if emoji else None, heal=heal, amount=amount).items()
                   if v is not None}
        if not changes:
            await self._say(interaction, "바꿀 칸을 하나 이상 골라 주세요.", False)
            return
        item.update(changes)
        await self._store(interaction, items, f"🧪 **{item['name']}**을(를) 고쳤어요.")

    @group.command(name="삭제", description="회복 아이템을 지워요 (관리자)")
    @app_commands.rename(target="아이템")
    async def delete(self, interaction: discord.Interaction, target: str):
        if not await require_admin(interaction):
            return
        items = self._items(interaction.guild_id)
        item = self._find(items, target)
        if item is None:
            await self._say(interaction, "그런 회복 아이템이 없어요.", False)
            return
        await self._store(interaction, [i for i in items if i["id"] != item["id"]], f"🗑️ **{item['name']}**을(를) 지웠어요.")

    @group.command(name="목록", description="레이드 회복 아이템을 봐요 (관리자)")
    async def show(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        items = self._items(interaction.guild_id)
        await self._say(interaction, "🧪 레이드를 시작하면 참가자 모두에게: " + (raid.item_list(items) or "없음 (`/레이드아이템 추가`로 더해요)"))

    @edit.autocomplete("target")
    @delete.autocomplete("target")
    async def item_names(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        needle = current.strip().lower()
        return [Choice(name=f"{i['emoji']} {i['name']}"[:100], value=i["name"]) for i in self._items(interaction.guild_id) if needle in i["name"].lower()]


async def setup(bot):
    await bot.add_cog(Raid(bot))
    await bot.add_cog(RaidItems(bot))
