"""/던전 명령과 던전 화면(버튼·아이템 선택). 규칙은 dungeon.py, 진행·저장은 dungeon_service.py가 맡는다."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import dungeon
import dungeon_service as service
import theme
from owner import owner_only
from paging import chunks, split_pages
from balance import BOSS_SKILLS, DEFAULT, WEAPON_SKILLS, item_summary
from dungeon import ROOMS, Run

BAR = 10
log = logging.getLogger("halloween")
AUTO_DELAY = 1.6               # 빠른진행이 한 걸음마다 쉬는 시간(초). 디스코드의 메시지 수정 제한을 넘지 않게 한다.
SECRET_FRAME_DELAY = 1.1        # 시크릿 보스 등장 연출에서 한 장면이 머무는 시간(초)
AUTO_MAX_SECONDS = 14 * 60     # 디스코드는 버튼을 누른 뒤 15분이 지나면 그 메시지를 더 못 고친다: 그 전에 멈추고 이어서 하게 한다.


def bar(value: int, maximum: int, full: str, width: int = BAR) -> str:
    filled = 0 if maximum <= 0 else max(0, min(width, -(-value * width // maximum)))   # 남아 있으면 최소 한 칸
    return full * filled + "⬛" * (width - filled)


def _ready_mark(run: Run, item, bal) -> str:
    """조건부 소모품 표시: 조건을 만족해 지금 쓸 수 있으면 ✨, 아직이면 🔒. 조건이 없으면 빈 문자열."""
    if not item.conditions and not item.material and not item.damage and not item.dot:         # 몬스터에게 쓰는 소모품은 싸울 때만 쓸 수 있어서 표시한다.
        return ""
    return " ✨" if dungeon.can_use_now(run, item, bal) else " 🔒"


def gauge(percent: int, width: int = 10) -> str:
    """게이지 막대(10칸)."""
    filled = max(0, min(width, percent * width // 100))
    return "🟨" * filled + "⬛" * (width - filled)


def shop_text(run: Run, bal, cur=None) -> str:
    """상점 상품 목록: '1. 🔋 소형 배터리 (일반) — HP +50 · 💾 20'. 이미 산 것은 취소선."""
    lines = []
    for number, offer in enumerate(run.shop, start=1):
        thing = (bal.gear_by_key if offer["kind"] == "gear" else bal.item_by_key).get(offer["key"])
        if thing is None:
            lines.append(f"{number}. ~~(사라진 상품)~~")
            continue
        text = f"{number}. {thing.emoji} **{thing.name}** ({thing.tier}) — " + (item_summary(thing, bal) if offer["kind"] != "gear" else thing.description)
        text = text[:230]
        lines.append(f"~~{text}~~ ✅ 샀어요" if offer.get("sold") else f"{text} · {theme.money(offer['price'], cur)}")
    return "\n".join(lines)


def run_embed(run: Run, outcome: service.Outcome | None = None, bal=None, cur=None) -> discord.Embed:
    """지금 화면. 끝난 던전이면 결과(보상)를 보여 준다."""
    bal = bal or DEFAULT
    ended = outcome.ended if outcome else None
    if ended == "clear" and run.final_clear:
        color, title = theme.GOLD, "🌀 던전 완전 클리어!"
    elif ended == "clear":
        color, title = theme.GREEN, "🏆 던전 클리어!"
    elif ended == "dead":
        color, title = theme.RED, "💀 시스템이 다운됐어요…"
    elif ended == "quit":
        color, title = theme.PURPLE, "🚪 던전에서 나왔어요"
    elif run.phase == "summit":
        color, title = theme.GOLD, f"🏔️ {max(run.floor, 1)}층 꼭대기에 도착했어요!"
    elif run.phase == "shop":
        color, title = theme.GOLD, f"🛒 {max(run.floor, 1)}층 상점"
    elif run.enemy and run.enemy.get("final") and not ended:
        color, title = theme.GOLD, f"🌀 최종 관문 — 무한 {max(run.floor, 1)}층"
    elif run.kind == "secret":
        color, title = theme.GOLD, f"🌌 시크릿 방 — {max(run.floor, 1)}/{bal.floors}층"
    elif run.infinite:
        color, title = theme.PURPLE, f"♾️ 무한모드 — {max(run.floor, 1)}층 · 방 {max(run.room, 1)}/{ROOMS}"
    else:
        color, title = theme.ORANGE, f"🏭 던전 — {max(run.floor, 1)}/{bal.floors}층 · 방 {max(run.room, 1)}/{ROOMS}"
    embed = theme.make_embed(title, "\n".join(run.events) or None, color=color)
    if run.enemy and not ended:
        enemy = run.enemy
        if enemy.get("image"):
            embed.set_image(url=enemy["image"])                # 시크릿 보스의 그림(움직이는 gif도 돼요)
        embed.add_field(name=f"{enemy['emoji']} {enemy['name']}" + (f" [{bal.element_label(enemy.get('element'))}]" if bal.element_label(enemy.get("element")) else "") + (" 🌌 SECRET" if enemy.get("secret") else " 🌀 FINAL" if enemy.get("final") else " 👑" if enemy["boss"] else "")
                        + (f" [{enemy.get('phase', 1)}/2페이즈]" if enemy.get("phase2_hp") or enemy.get("phase", 1) == 2 else "") + (" 💫 기절(다음 공격 못 함)" if enemy.get("stunned") else ""), inline=False,
                        value=f"{bar(enemy['hp'], enemy['max_hp'], '🟥')} {enemy['hp']}/{enemy['max_hp']}")
    if run.phase == "shop" and run.shop and not ended:
        embed.add_field(name="🛒 상품 (재화로 사요)", inline=False, value=shop_text(run, bal, cur))
    embed.add_field(name="❤️ HP", inline=False, value=f"{bar(run.hp, bal.max_hp, '🟩')} {run.hp}/{bal.max_hp}")
    if not ended:
        weapon, shield, armor = bal.gear_by_key.get(run.weapon), bal.gear_by_key.get(run.shield), dungeon._armor(run, bal)
        tag = lambda g: f" [{bal.element_label(g.element)}]" if bal.element_label(g.element) else ""     # 무기·갑옷의 속성
        gear = [f"{weapon.emoji} {weapon.name}{tag(weapon)}" if weapon else "👊 맨손", f"{shield.emoji} {shield.name}" if shield else "✋ 맨몸 가드",
                f"{armor.emoji} {armor.name}{tag(armor)}" if armor else "🧥 맨몸"]
        embed.add_field(name="🎒 장비", value=" · ".join(gear), inline=False)
        passive = {"attack": run.base_attack, "guard": run.base_guard, "reflect": run.base_reflect, "reduction": run.base_reduction}
        if any(passive.values()):                         # 기지에 전시한 장비의 패시브(들어올 때 정해졌다)
            embed.add_field(name="🏠 기지 패시브", value=service.passive_text(passive), inline=False)
        spare = [bal.gear_by_key.get(run.spare_weapon), bal.gear_by_key.get(run.spare_shield), bal.gear_by_key.get(run.spare_armor)]
        if any(spare):                                    # 예비 칸은 비어 있으면 보여 주지 않는다.
            embed.add_field(name="📦 예비", inline=False,
                            value=" · ".join(f"{g.emoji} {g.name}" if g else f"{label} —" for g, label in zip(spare, ("⚔️", "🛡️", "🧥"))))
        held = [f"{bal.item_by_key[k].emoji} {bal.item_by_key[k].name} ×{n}" + _ready_mark(run, bal.item_by_key[k], bal)
                for k, n in run.items.items() if k in bal.item_by_key]
        if held:                                          # 관리자가 지운 아이템만 남았다면 칸 자체를 숨긴다(빈 칸은 디스코드가 거절한다).
            shown = split_pages(held, 1000)[0].split("\n")   # 칸 하나는 1024자까지라서, 넘치면 앞쪽만 보이고 나머지 수를 알려 준다.
            more = len(held) - len(shown)
            embed.add_field(name="🧪 소지품", value="\n".join(shown) + (f"\n… 외 {more}종 (아이템 메뉴에서 ◀ ▶로 넘겨 보세요)" if more else ""), inline=False)
        if run.regen_turns:
            embed.add_field(name="🧬 회복 중", value=f"턴마다 HP +{run.regen_amount} (남은 {run.regen_turns}턴)", inline=False)
        status = []
        if run.invuln_turns:
            status.append(f"✨ 무적 (남은 {run.invuln_turns}턴)")
        if run.reduce_turns and run.reduce_percent:
            status.append(f"🛡️ 받는 피해 {run.reduce_percent}% 감소 (남은 {run.reduce_turns}턴)")
        if run.drain_turns:
            status.append(f"🔥 턴마다 HP -{run.drain_amount} (남은 {run.drain_turns}턴)")
        weapon_now = bal.gear_by_key.get(run.weapon)
        if weapon_now and weapon_now.ult:
            status.append(f"🟡 궁극기 게이지 {gauge(run.ult_gauge)} {run.ult_gauge}%")
        if run.awakened:
            status.append(f"⚡ **각성 중!** 특수 스킬 [{WEAPON_SKILLS[bal.special][0]}]을(를) 쓸 수 있어요" + (f" · 피해 +{bal.awaken_bonus}%" if bal.awaken_bonus else ""))
        elif run.awaken_gauge:
            status.append(f"⚡ 각성 게이지 {gauge(run.awaken_gauge)} {run.awaken_gauge}%")
        if weapon_now and weapon_now.skill and run.skill_cd > 0:
            status.append(f"✨ 스킬 [{WEAPON_SKILLS[weapon_now.skill][0]}] 쿨타임 {run.skill_cd}턴")
        if run.burn_turns:
            status.append(f"🔥 과열: 턴마다 {run.burn_amount:,} 피해 (남은 {run.burn_turns}턴)")
        if run.enemy and run.enemy.get("barrier_turns"):
            status.append(f"🛡️ 몬스터 방어막: 받는 피해 {run.enemy['barrier']}% 감소 (남은 {run.enemy['barrier_turns']}턴)")
        skill = (run.enemy or {}).get("skill")
        if run.in_battle and skill and skill.get("every"):
            left = skill["every"] - run.enemy.get("turn", 0) % skill["every"]
            status.append(f"🌀 다음 스킬 [{BOSS_SKILLS[skill['kind']][0]}]: {left}번째 공격 뒤" if left > 1 else f"🌀 다음 공격이 스킬 [{BOSS_SKILLS[skill['kind']][0]}]이에요!")
        if run.enemy and run.enemy.get("dot_turns"):
            status.append(f"🌡️ 몬스터가 턴마다 {run.enemy['dot']} 피해 (남은 {run.enemy['dot_turns']}턴)")
        if status:
            embed.add_field(name="🌀 효과", value="\n".join(status), inline=False)
    reward = outcome.reward if outcome else None
    if ended == "clear" and reward:
        lines = [f"{theme.money(reward.candy, cur)} 을(를) 받았어요."]
        lines += [f"{emoji} **{name}** ×{amount:,}" for emoji, name, amount in reward.items]
        if reward.ticket:
            lines.append(f"🎟️ **히든 칭호 획득권**이 나왔어요! (지금 {reward.tickets_total}장)")
        if reward.final:
            lines.append("🌀 **최종보스 보상**" + (f" {theme.money(reward.final_candy, cur)}" if reward.final_candy else "")
                         + (f" · 🎟️ 획득권 +{reward.final_tickets} (지금 {reward.tickets_total}장)" if reward.final_tickets else "") + " (위 재화에 포함)")
        embed.add_field(name="🎁 보상", value="\n".join(lines), inline=False)
        if reward.vault or reward.vault_lost:                 # 클리어하면 들고 있던 장비·소모품은 보관함으로
            kept = ", ".join(f"{e} {n}" + (f" ×{q}" if q > 1 else "") for e, n, q in reward.vault)
            lost = ", ".join(f"{e} {n}" + (f" ×{q}" if q > 1 else "") for e, n, q in reward.vault_lost)
            embed.add_field(name="📦 보관함", inline=False,
                            value=((f"{kept}을(를) 보관함에 넣었어요. `/보관함`에서 다음 던전에 가져갈 것을 골라요." if kept else "")
                                   + (f"\n⚠️ 보관함이 가득 차서 버려졌어요: {lost}" if lost else "")).strip()[:1024])
        if reward.final:
            embed.add_field(name="🌀 완전 클리어", value=f"무한 **{run.floor}층**의 최종보스까지 쓰러뜨리고 던전을 완전히 클리어했어요!", inline=False)
    elif ended in ("dead", "quit"):
        embed.add_field(name="입장 물품", value="입장에 쓴 물품은 돌아오지 않아요. 새로 도전하려면 입장 물품이 다시 필요해요.", inline=False)
        if run.infinite:                                  # 클리어 보상과 층 보너스는 이미 받았다.
            embed.add_field(name="♾️ 무한모드 기록", value=f"**{run.floor}층**까지 올랐어요! (클리어 보상과 층 보너스는 이미 받았어요)", inline=False)
    if run.loot and ended:
        embed.add_field(name="🧾 이번에 얻은 것", value=", ".join(run.loot[-10:]), inline=False)
    return embed


def has_intro(run: Run) -> bool:
    """방금 시크릿 보스나 무한모드 최종보스가 나타나서 등장 연출을 보여 줘야 하나."""
    return "secret_intro" in run.last or "final_intro" in run.last


def secret_intro_frames(enemy: dict) -> list[discord.Embed]:
    """시크릿 보스 등장 연출: 같은 메시지를 장면마다 고쳐서 움직이는 것처럼 보이게 한다(어둠 → 경고 → 이름이 드러남 → 등장)."""
    name, emoji = enemy["name"], enemy["emoji"]
    cover = "？" * min(len(name), 12)
    half = name[: max(1, len(name) // 2)] + "？" * (min(len(name), 12) - max(1, len(name) // 2))
    if enemy.get("final"):                               # 무한모드 최종보스: 같은 연출에 제목·문구만 다르다
        title, quiet, warn = "🌀 F I N A L   B O S S 🌀", "던전 전체의 전력이 한 곳으로 빨려 들어간다.\n모든 층의 기계가 동시에 멈춘다…", "**던전의 중심 코어가 깨어난다!**"
    else:
        title, quiet, warn = "🌌 S E C R E T   B O S S 🌌", "서버실의 팬 소리가 갑자기 멈춘다.\n모든 모니터가 한꺼번에 꺼진다…", "**알 수 없는 거대한 신호가 감지된다!**"
    final = theme.make_embed(title, f"# {emoji} 『 {name} 』\n" + (f"*{enemy['intro']}*" if enemy.get("intro") else ""), color=theme.GOLD)
    if enemy.get("image"):
        final.set_image(url=enemy["image"])
    return [
        theme.make_embed("…", "⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛\n⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛\n⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛", color=0x050507),
        theme.make_embed("🌑 ……", quiet, color=0x2B1B4A),
        theme.make_embed("⚠️ ⚠️ ⚠️  WARNING  ⚠️ ⚠️ ⚠️", warn, color=theme.RED),
        theme.make_embed("👁️ 무언가 가동을 시작했다", f"# 『 {cover} 』", color=0x7A1F1F),
        theme.make_embed("👁️ 이름이 드러난다…", f"# 『 {half} 』", color=0xB5651D),
        final,
    ]


async def play_secret_intro(interaction: discord.Interaction, enemy: dict, *, first_is_response: bool, view=None):
    """등장 연출 장면들을 차례로 보여 준다. 마지막 장면(보스 등장)이 머문 뒤 반환하니, 호출한 쪽이 전투 화면으로 바꾼다."""
    frames = secret_intro_frames(enemy)
    for number, frame in enumerate(frames):
        if number:
            await asyncio.sleep(SECRET_FRAME_DELAY)
        if number == 0 and first_is_response:
            await interaction.response.edit_message(embed=frame, view=view)
        else:
            await interaction.edit_original_response(embed=frame, view=view)
    await asyncio.sleep(SECRET_FRAME_DELAY)


class DungeonView(discord.ui.View):
    """한 화면(rev)에 붙는 버튼들. 눌린 뒤의 화면은 새 View로 바뀐다. 봇이 꺼지면 사라지지만 /던전으로 이어서 할 수 있다."""

    def __init__(self, client, user_id: int, run: Run, bal=None, item_page: int = 0):
        super().__init__(timeout=900)
        self.client, self.user_id, self.rev = client, user_id, run.rev
        bal = bal or DEFAULT
        for_sale = dungeon.sellable(run, bal)
        common = 3 if run.phase == "shop" else None       # 상점은 버튼이 많아서(상품 3 + 나가기) 나머지 버튼은 아래 줄에 둔다(선택 메뉴는 2번째 줄)
        if run.phase == "battle":
            self._button("공격", "⚔️", "attack", discord.ButtonStyle.danger)
            self._button("방어", "🛡️", "defend", discord.ButtonStyle.primary)
            self._technique_buttons(run, bal)
        elif run.phase == "chest":
            self._button("상자 열기", "🎁", "chest", discord.ButtonStyle.success)
        elif run.pending_gear:
            self._button("장착", "✅", "equip", discord.ButtonStyle.success)
            gear = bal.gear_by_key.get(run.pending_gear)
            if gear and dungeon._valid(getattr(run, f"spare_{gear.slot}"), gear.slot, bal) is None:
                self._button("예비에 넣기", "📥", "stash", discord.ButtonStyle.primary)      # 그 종류의 예비 칸이 비어 있을 때만
            self._button("버리기", "🗑️", "discard", discord.ButtonStyle.secondary)
            pending = next((e for e in for_sale if e["id"] == "pending"), None)
            if pending:                                   # 새 장비를 바로 파는 지름길
                self._button(f"판매 +{pending['price']:,}", "💰", "sell_pending", discord.ButtonStyle.primary)
        elif run.phase == "summit":                       # 꼭대기: 클리어하고 끝낼지, 무한모드로 계속할지 고른다.
            self._button("클리어하고 끝내기", "🏆", "finish", discord.ButtonStyle.success)
            self._button("무한모드 계속", "♾️", "infinite", discord.ButtonStyle.primary)
        elif run.phase == "shop":                         # 상점: 안 산 상품마다 [사기] 버튼, 그리고 [상점 나가기]
            for number, offer in enumerate(run.shop):
                thing = (bal.gear_by_key if offer["kind"] == "gear" else bal.item_by_key).get(offer["key"])
                if thing is not None and not offer.get("sold"):
                    self._button(f"{thing.name} {offer['price']:,}"[:80], "🛒", f"buy:{number}", discord.ButtonStyle.primary, row=0)
            self._button("상점 나가기", "🚪", "advance", discord.ButtonStyle.success, row=0)
        elif run.can_advance:
            self._button("앞으로" if run.room else "출발", "🚪", "advance", discord.ButtonStyle.success)
        if not run.in_battle and any(e["id"] != "pending" for e in for_sale):
            self._button("판매", "💰", "open_sell", discord.ButtonStyle.secondary, row=common)
        if not run.in_battle and (run.weapon or run.shield or run.armor or run.spare_weapon or run.spare_shield or run.spare_armor):
            self._button("장비", "🎒", "open_equip", discord.ButtonStyle.secondary, row=common)
        if not run.pending_gear and any(k in bal.item_by_key for k in run.items):
            pages = max(1, len(chunks([k for k in run.items if k in bal.item_by_key], ITEM_PAGE)))
            self.item_page = max(0, min(item_page, pages - 1))
            self.add_item(ItemSelect(run, bal, self.item_page))
            if pages > 1:                                    # 아이템 종류가 25개를 넘으면 ◀ ▶로 넘겨 본다(선택 메뉴는 25개까지만 담는다).
                self._item_pager(self.item_page, pages)
        if not run.auto:
            self._button("빠른진행", "⏩", "ask_auto", discord.ButtonStyle.secondary, row=common)
        self._button("나가기", "🏃", "ask_quit", discord.ButtonStyle.secondary, row=common)

    def _technique_buttons(self, run: Run, bal) -> None:
        """무기 스킬·궁극기·각성 특수 스킬 버튼(3번째 줄). 무기에 없는 기술은 버튼이 없고, 아직 못 쓰면 흐리게 보인다."""
        weapon = bal.gear_by_key.get(run.weapon)
        ready = dungeon.skill_ready(run, bal)
        if weapon and weapon.skill:
            label = f"스킬 {WEAPON_SKILLS[weapon.skill][0]}" + ("" if ready["skill"] else f" ({run.skill_cd}턴)")
            self._button(label, "✨", "skill", discord.ButtonStyle.success, row=2, disabled=not ready["skill"])
        if weapon and weapon.ult:
            label = f"궁극기 {WEAPON_SKILLS[weapon.ult][0]}" + ("" if ready["ult"] else f" ({run.ult_gauge}%)")
            self._button(label, "🟡", "ult", discord.ButtonStyle.success, row=2, disabled=not ready["ult"])
        if run.awakened:
            self._button(f"각성 특수 스킬 {WEAPON_SKILLS[bal.special][0]}", "⚡", "special", discord.ButtonStyle.danger, row=2)

    def _item_pager(self, page: int, pages: int) -> None:
        for step, label, emoji in ((-1, "아이템 이전", "◀️"), (+1, "아이템 다음", "▶️")):
            button = discord.ui.Button(label=f"{label} ({page + 1}/{pages})" if step > 0 else label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                       disabled=(page + step < 0 or page + step >= pages), row=2)

            async def callback(interaction: discord.Interaction, step=step):
                run = await service.peek(self.client.store, interaction.guild_id, interaction.user.id)
                if run is None or run.rev != self.rev:
                    await interaction.response.send_message(embed=theme.notice("지난 화면이에요. `/던전`으로 다시 열어 주세요.", False), ephemeral=True)
                    return
                bal = self.client.store.balance(interaction.guild_id)
                await interaction.response.edit_message(embed=run_embed(run, None, bal, self.client.store.currency(interaction.guild_id)),
                                                        view=DungeonView(self.client, interaction.user.id, run, bal, self.item_page + step))
            button.callback = callback
            self.add_item(button)

    def _button(self, label, emoji, action, style, row=None, disabled=False):
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row, disabled=disabled)

        async def callback(interaction: discord.Interaction):
            if action == "ask_quit":
                await interaction.response.send_message(
                    embed=theme.notice("정말 나갈까요? 던전 진행과 입장에 쓴 물품이 사라져요.", False),
                    view=ConfirmQuit(self.client, self.user_id, self.rev), ephemeral=True)
                return
            if action == "ask_auto":
                await ask_auto(interaction, self.client, self.rev)
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
            if action.startswith("buy:"):                 # 상점 상품 사기: 'buy:번호'
                await self.handle(interaction, "buy", action[4:])
                return
            await self.handle(interaction, action)
        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def handle(self, interaction: discord.Interaction, action: str, arg: str | None = None):
        await respond(interaction, self.client, self.rev, action, arg)


ITEM_PAGE = 25                 # 선택 메뉴 하나에 담을 수 있는 항목 수(디스코드 한도)


class ItemSelect(discord.ui.Select):
    def __init__(self, run: Run, bal, page: int = 0):
        items = bal.item_by_key
        options = [discord.SelectOption(label=f"{items[k].name} ×{n}", value=k, emoji=items[k].emoji,
                                        description=(_ready_mark(run, items[k], bal).strip() + " " + item_summary(items[k], bal)).strip()[:100])
                   for k, n in run.items.items() if k in items]
        pages = chunks(options, ITEM_PAGE) or [[]]
        page = max(0, min(page, len(pages) - 1))
        holder = "🧪 아이템 사용 (전투 중엔 한 턴을 써요)" + (f" · {page + 1}/{len(pages)}쪽" if len(pages) > 1 else "")
        super().__init__(placeholder=holder, options=pages[page], row=1)

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
    if has_intro(run) and not outcome.ended and not from_confirm:      # 시크릿 방이 열렸다: 특별한 등장 연출을 먼저 보여 준다.
        await play_secret_intro(interaction, run.enemy, first_is_response=True)
        await interaction.edit_original_response(embed=run_embed(run, outcome, bal, client.store.currency(interaction.guild_id)), view=view)
        return
    if from_confirm:
        # 확인창(별도 메시지)에서 눌렀다. 던전 화면은 그 메시지에서 따로 바꿀 수 없으니 결과만 알린다.
        await interaction.response.edit_message(embed=run_embed(run, outcome, bal, client.store.currency(interaction.guild_id)), view=None)
        return
    await interaction.response.edit_message(embed=run_embed(run, outcome, bal, client.store.currency(interaction.guild_id)), view=view)


def auto_embed(run: Run, outcome=None, bal=None, cur=None) -> discord.Embed:
    """빠른진행 중인 화면: 평소 화면에 '컴퓨터가 진행 중'이라는 안내를 붙인다."""
    embed = run_embed(run, outcome, bal, cur)
    if not (outcome and outcome.ended):
        embed.set_footer(text="⏩ 빠른진행 중 · 컴퓨터가 알아서 진행해요 · ⏹ 멈추기를 누르면 직접 진행해요")
    return embed


class AutoView(discord.ui.View):
    """빠른진행 중에 붙는 버튼: 멈추기 하나뿐이다(그동안 다른 행동은 서버가 받지 않는다)."""

    def __init__(self, client, user_id: int):
        super().__init__(timeout=AUTO_MAX_SECONDS + 120)
        self.client, self.user_id = client, user_id
        stop = discord.ui.Button(label="멈추기", emoji="⏹", style=discord.ButtonStyle.danger)
        stop.callback = self.stop_auto
        self.add_item(stop)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def stop_auto(self, interaction: discord.Interaction):
        client = self.client
        await service.set_auto(client.store, client.locks, interaction.guild_id, interaction.user.id, 0, False)
        task = client.autos.get((interaction.guild_id, interaction.user.id))
        if task is not None and not task.done():
            # 돌리던 작업이 다음 걸음에서 멈춘 걸 알고 던전 화면으로 되돌려 놓는다(보통 2초 안).
            await interaction.response.edit_message(embed=theme.notice("⏹ 멈추는 중이에요… 잠시 뒤 던전 화면으로 돌아와요.", False), view=None)
            return
        run = await service.peek(client.store, interaction.guild_id, interaction.user.id)    # 돌리던 작업이 없다(봇이 다시 켜진 뒤 등)
        if run is None:
            await interaction.response.edit_message(embed=theme.notice("던전이 이미 끝났어요.", False), view=None)
            return
        bal = client.store.balance(interaction.guild_id)
        await interaction.response.edit_message(embed=run_embed(run, None, bal, client.store.currency(interaction.guild_id)),
                                                view=DungeonView(client, interaction.user.id, run, bal))


class AutoConfirm(discord.ui.View):
    """[⏩ 빠른진행] 첫 확인: 입장 물품을 한 번 더 낸다는 걸 알리고 켜거나 취소한다."""

    def __init__(self, client, user_id: int, rev: int):
        super().__init__(timeout=120)
        self.client, self.user_id, self.rev = client, user_id, rev
        yes = discord.ui.Button(label="켜기", emoji="⏩", style=discord.ButtonStyle.success)
        no = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.confirm, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def confirm(self, interaction: discord.Interaction):
        self.stop()
        await start_auto(interaction, self.client, self.rev)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        client = self.client
        run = await service.peek(client.store, interaction.guild_id, interaction.user.id)
        if run is None:
            await interaction.response.edit_message(embed=theme.notice("던전이 이미 끝났어요.", False), view=None)
            return
        bal = client.store.balance(interaction.guild_id)
        await interaction.response.edit_message(embed=run_embed(run, None, bal, client.store.currency(interaction.guild_id)),
                                                view=DungeonView(client, interaction.user.id, run, bal))


async def ask_auto(interaction: discord.Interaction, client, rev: int):
    """던전 화면의 [⏩ 빠른진행]: 이 던전에서 아직 비용을 안 냈으면 같은 메시지를 확인 화면으로 바꾸고, 냈거나 무료면 바로 켠다."""
    run = await service.peek(client.store, interaction.guild_id, interaction.user.id)
    if run is None or run.rev != rev:
        await interaction.response.send_message(embed=theme.notice("지난 화면이에요. `/던전`으로 다시 열어 주세요.", False), ephemeral=True)
        return
    label = service.entry_label(client.store, interaction.guild_id)
    if run.auto_paid or not label:
        await start_auto(interaction, client, rev)
        return
    text = (f"**{label}**을(를) 한 번 더 내고 이번 던전을 **컴퓨터가 알아서 진행**하게 할까요?\n"
            "• 화면이 라이브로 바뀌고, ⏹ 멈추기로 언제든 직접 진행으로 돌아올 수 있어요.\n"
            "• 이 던전에서는 **한 번만** 내요. 껐다 켜도 다시 내지 않아요.\n"
            "• 컴퓨터는 더 좋은 장비만 끼고, 위험하면 방어·회복을 하지만 **다운될 수 있어요.** 장비·소모품을 파는 일은 하지 않아요.")
    await interaction.response.edit_message(embed=theme.make_embed("⏩ 빠른진행", text, color=theme.ORANGE), view=AutoConfirm(client, interaction.user.id, rev))


async def start_auto(interaction: discord.Interaction, client, rev: int):
    """빠른진행을 켜고(비용이 필요하면 내고) 라이브로 진행하는 작업을 시작한다."""
    outcome = await service.set_auto(client.store, client.locks, interaction.guild_id, interaction.user.id, rev, True)
    if not outcome.ok:
        await interaction.response.send_message(embed=theme.notice(outcome.text, False), ephemeral=True)
        return
    bal = client.store.balance(interaction.guild_id)
    cur = client.store.currency(interaction.guild_id)
    view = AutoView(client, interaction.user.id)
    await interaction.response.edit_message(embed=auto_embed(outcome.run, None, bal, cur), view=view)
    key = (interaction.guild_id, interaction.user.id)
    task = asyncio.get_running_loop().create_task(run_auto(interaction, client, interaction.user.id, view))
    client.autos[key] = task


async def run_auto(interaction: discord.Interaction, client, user_id: int, view):
    """빠른진행 본체: 일정 간격으로 한 걸음씩 진행하며 같은 메시지를 계속 고친다. 끝나거나 멈추거나 시간이 다 되면 끝낸다."""
    store, gid, key = client.store, interaction.guild_id, (interaction.guild_id, user_id)
    started = time.monotonic()
    bal, cur = store.balance(gid), store.currency(gid)

    async def settle(embed_text: str | None = None):
        """직접 진행하는 화면으로 되돌려 놓는다."""
        run = await service.peek(store, gid, user_id)
        if run is None:
            await interaction.edit_original_response(embed=theme.notice(embed_text or "던전이 끝났어요.", False), view=None)
            return
        embed = run_embed(run, None, store.balance(gid), cur)
        if embed_text:
            embed.add_field(name="⚠️ 멈춘 이유", value=embed_text, inline=False)
        await interaction.edit_original_response(embed=embed, view=DungeonView(client, user_id, run, store.balance(gid)))

    try:
        while True:
            await asyncio.sleep(AUTO_DELAY)
            if time.monotonic() - started > AUTO_MAX_SECONDS:
                await service.set_auto(store, client.locks, gid, user_id, 0, False)
                await settle("디스코드는 15분이 지나면 이 메시지를 더 고칠 수 없어서 빠른진행을 멈췄어요. ⏩를 다시 누르면 이어서 해요(입장 물품은 더 안 내요).")
                return
            outcome = await service.auto_step(store, client.locks, client.rng, gid, user_id)
            if outcome.stale:                                # 멈췄거나(⏹) 던전이 끝났다
                await settle()
                return
            if not outcome.ok:                               # 컴퓨터가 할 수 없는 행동에 걸렸다: 멈추고 이유를 알린다.
                await service.set_auto(store, client.locks, gid, user_id, 0, False)
                await settle(outcome.text)
                return
            bal = store.balance(gid)
            if outcome.ended:
                await interaction.edit_original_response(embed=auto_embed(outcome.run, outcome, bal, cur), view=None)
                return
            if has_intro(outcome.run):             # 빠른진행 중에도 시크릿 보스 등장 연출은 그대로 보여 준다.
                await play_secret_intro(interaction, outcome.run.enemy, first_is_response=False, view=view)
            await interaction.edit_original_response(embed=auto_embed(outcome.run, outcome, bal, cur), view=view)
    except asyncio.CancelledError:
        raise
    except Exception:                                        # 메시지를 못 고치게 됐다(15분 지남·삭제 등): 안전하게 꺼 둔다. 던전은 /던전으로 이어서 한다.
        log.exception("빠른진행이 중단됐어요 (서버 %s, 사용자 %s)", gid, user_id)
        try:
            await service.set_auto(store, client.locks, gid, user_id, 0, False)
        except Exception:
            log.exception("빠른진행을 끄지 못했어요")
    finally:
        if client.autos.get(key) is asyncio.current_task():
            client.autos.pop(key, None)


SELL_PAGE = 25                 # 판매 선택 메뉴 한 쪽에 담을 수 있는 항목 수(디스코드 한도)


def sell_embed(run: Run, bal, cur, wallet: int, result: bool = False, page: int = 0) -> discord.Embed:
    """판매 화면. result=True면 방금 판 내용(run.events)을 맨 위에 보여 준다. 항목이 25개를 넘으면 쪽으로 나눠 그 쪽만 보여 준다."""
    all_entries = dungeon.sellable(run, bal)
    pages = chunks(all_entries, SELL_PAGE) or [[]]
    page = max(0, min(page, len(pages) - 1))
    entries = pages[page]
    intro = ("팔 것을 고른 뒤 아래 버튼을 눌러요. 여러 개를 한꺼번에 고를 수 있어요.\n"
             "던전을 나가거나 다운되면 장비·소지품은 사라지니, 팔 수 있을 때 팔아 두세요.")
    embed = theme.make_embed("💰 던전 판매", "\n".join(run.events) if result and run.events else intro, color=theme.ORANGE)
    lines = []
    for e in entries:
        if e["kind"] == "item":
            lines.append(f"{e['emoji']} **{e['name']}** ×{e['qty']:,} — 개당 {theme.money(e['price'], cur)} · 전부 {theme.money(e['price'] * e['qty'], cur)}")
        else:
            lines.append(f"{e['emoji']} **{e['name']}** ({e['tag']}) — {theme.money(e['price'], cur)}")
    for number, part in enumerate(split_pages(lines, 1000), start=1):        # 칸 하나는 1024자까지라서 나눠 담는다.
        embed.add_field(name="🧾 팔 수 있는 것" + (f" ({page + 1}/{len(pages)}쪽)" if len(pages) > 1 and number == 1 else ""), value=part, inline=False)
    if len(pages) > 1:
        embed.add_field(name="ℹ️", value=f"목록이 길어서 {len(pages)}쪽으로 나눴어요. 아래 ◀ ▶로 넘겨요. 다른 쪽에서 고른 것도 함께 팔려요.", inline=False)
    embed.add_field(name="내 재화", value=theme.money(wallet, cur), inline=False)
    return embed


class SellView(discord.ui.View):
    """판매 화면: 여러 개를 골라 한꺼번에 팔 수 있다. 던전 화면과 같은 메시지에서 바뀌어 나온다."""

    def __init__(self, client, user_id: int, run: Run, bal, cur, page: int = 0, picks=()):
        super().__init__(timeout=900)
        self.client, self.user_id, self.rev = client, user_id, run.rev
        all_entries = dungeon.sellable(run, bal)
        pages = chunks(all_entries, SELL_PAGE) or [[]]
        self.page, self.pages = max(0, min(page, len(pages) - 1)), len(pages)
        entries = pages[self.page]
        self.page_ids = [e["id"] for e in entries]
        known = {e["id"] for e in all_entries}
        self.picks = [p for p in picks if p in known]               # 다른 쪽에서 고른 것도 기억한다.
        options = []
        for e in entries:
            if e["kind"] == "item":
                label, hint = f"{e['name']} ×{e['qty']:,}", f"개당 {theme.plain_money(e['price'], cur)} · 전부 {theme.plain_money(e['price'] * e['qty'], cur)}"
            else:
                label, hint = f"{e['name']} ({e['tag']})", theme.plain_money(e["price"], cur)
            options.append(discord.SelectOption(label=label[:100], value=e["id"], emoji=e["emoji"], description=hint[:100], default=e["id"] in self.picks))
        holder = "💰 팔 것을 골라요 (여러 개 가능)" + (f" · {self.page + 1}/{self.pages}쪽" if self.pages > 1 else "")
        select = discord.ui.Select(placeholder=holder, min_values=0, max_values=max(1, len(options)), options=options, row=0)
        select.callback = self.on_select
        self.add_item(select)
        self._button("선택한 것 판매", "💰", self.sell_picked, discord.ButtonStyle.success)
        if any(e["kind"] == "item" and e["qty"] > 1 for e in entries):
            self._button("1개씩만", "☝️", self.sell_one, discord.ButtonStyle.secondary)
        if any(e["kind"] == "item" for e in entries):
            self._button("소모품 전부", "🧺", self.sell_items, discord.ButtonStyle.primary)
        self._button("돌아가기", "↩️", self.back, discord.ButtonStyle.secondary)
        if self.pages > 1:                                          # 25개를 넘으면 ◀ ▶로 쪽을 넘긴다.
            self._button(f"이전 쪽", "◀️", self.turn(-1), discord.ButtonStyle.secondary, row=2, disabled=self.page <= 0)
            self._button(f"다음 쪽 ({self.page + 1}/{self.pages})", "▶️", self.turn(+1), discord.ButtonStyle.secondary, row=2,
                         disabled=self.page >= self.pages - 1)

    def _button(self, label, emoji, callback, style, row=1, disabled=False):
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    def turn(self, step: int):
        async def callback(interaction: discord.Interaction):
            run = await service.peek(self.client.store, interaction.guild_id, interaction.user.id)
            if run is None or run.rev != self.rev:
                await interaction.response.send_message(embed=theme.notice("지난 화면이에요. `/던전`으로 다시 열어 주세요.", False), ephemeral=True)
                return
            bal, cur = self.client.store.balance(interaction.guild_id), self.client.store.currency(interaction.guild_id)
            await interaction.response.edit_message(
                embed=sell_embed(run, bal, cur, self.client.store.wallet(interaction.guild_id, interaction.user.id), page=self.page + step),
                view=SellView(self.client, self.user_id, run, bal, cur, self.page + step, self.picks))
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    async def on_select(self, interaction: discord.Interaction):
        self.picks = [p for p in self.picks if p not in self.page_ids] + list(interaction.data.get("values", []))   # 이 쪽에서 고른 것만 바꾼다.
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
    """장비 화면의 한 칸: '🔧 낡은 스패너 — 공격 피해량 +30'. 비어 있으면 '—'."""
    return f"{gear.emoji} **{gear.name}** — {gear.description}" if gear else "—"


def equip_embed(run: Run, bal, result: bool = False) -> discord.Embed:
    """장비 화면: 장착한 무기·방패와 예비 칸. result=True면 방금 한 교체(run.events)를 맨 위에 보여 준다."""
    intro = ("**무기·방패·갑옷을 1개씩** 예비 칸에 가지고 있다가 장착한 것과 서로 바꿀 수 있어요.\n"
             "예비 칸은 이번 던전 동안만 쓰고, 던전을 나가거나 다운되면 사라져요.")
    embed = theme.make_embed("🎒 장비", "\n".join(run.events) if result and run.events else intro, color=theme.ORANGE)
    for slot, name, icon, bare in (("weapon", "무기", "⚔️", "👊 맨손"), ("shield", "방패", "🛡️", "✋ 맨몸 가드"), ("armor", "갑옷", "🧥", "🧥 맨몸")):
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
        for slot, name, icon in (("weapon", "무기", "⚔️"), ("shield", "방패", "🛡️"), ("armor", "갑옷", "🧥")):
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
    """🏭 입장 물품을 내고 들어가는 10개 방의 던전."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="던전", description="🏭 입장 물품을 내고 던전에 들어가요 (진행 중이면 이어서 해요)")
    @app_commands.guild_only()
    async def enter(self, interaction: discord.Interaction):
        task = self.bot.autos.get((interaction.guild_id, interaction.user.id))
        running = task is not None and not task.done()
        outcome = await service.enter(self.bot.store, self.bot.locks, interaction.guild_id, interaction.user.id, auto_running=running)
        if not outcome.ok:                                   # 던전은 개인 화면이다: 입장 불가 안내도 던전 화면도 본인에게만 보인다.
            await interaction.response.send_message(embed=theme.notice(outcome.text, False), ephemeral=True)
            return
        bal = self.bot.store.balance(interaction.guild_id)
        if running and outcome.run.auto:                     # 다른 메시지에서 빠른진행이 돌고 있다: 여기서도 멈출 수 있게 한다.
            await interaction.response.send_message(embed=auto_embed(outcome.run, None, bal, self.bot.store.currency(interaction.guild_id)),
                                                    view=AutoView(self.bot, interaction.user.id), ephemeral=True)
            return
        await interaction.response.send_message(embed=run_embed(outcome.run, None, bal, self.bot.store.currency(interaction.guild_id)),
                                                view=DungeonView(self.bot, interaction.user.id, outcome.run, bal), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Dungeon(bot))
