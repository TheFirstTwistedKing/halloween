"""재화 자체·던전 입장 물품·패키지 결과표를 관리자가 바꾸는 명령어."""
from __future__ import annotations

import secrets

import discord
from discord import app_commands
from discord.ext import commands

import theme
from access import require_admin
from cogs.admin import item_choices
from confirm import Confirm
from panel import refresh_panels
from store import MAX_CLEAR_REWARDS, MAX_PACKAGE_ENTRIES, StoreError, valid_emoji

Choice = app_commands.Choice
KINDS = [Choice(name="꽝 (아무것도 없음)", value="none"), Choice(name="재화", value="candy"), Choice(name="물품", value="item")]
KIND_LABEL = {"none": "꽝", "candy": "재화", "item": "물품"}
RESET_TEXT = "-"          # 이모지·이름에 이 글자를 적으면 기본값(물품·재화에서 가져오기)으로 되돌린다.


def package_lines(store, guild_id: int) -> list[str]:
    table = store.package_table(guild_id)
    total = sum(e["weight"] for e in table) or 1
    lines = []
    for i, e in enumerate(table, start=1):
        emoji, title = theme.entry_icon_title(e, store, guild_id)
        lines.append(f"`{i}` {emoji} **{title}** — {e['weight'] / total * 100:.4g}% ({KIND_LABEL[e['kind']]}, 비율 {e['weight']:g})")
    return lines


class RulesAdmin(commands.Cog):
    """재화·입장 물품·패키지 결과표 설정 (관리자 전용)."""

    def __init__(self, bot):
        self.bot = bot

    async def _deny(self, interaction, text):
        await interaction.response.send_message(embed=theme.notice(text, False), ephemeral=True)

    # ---------------------------------------------------------------- 재화
    @app_commands.command(name="재화설정", description="재화의 이모지·이름·최대 보유량을 바꿔요. 비워 두면 현재 값을 보여줘요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(emoji="이모지", name="이름", maximum="최대")
    @app_commands.describe(emoji="재화 아이콘 (이모지 하나 또는 서버 이모지)", name="재화 이름 (20자까지)",
                           maximum="한 사람이 가질 수 있는 최대량. 낮추면 그보다 많이 가진 사람은 최대량으로 줄어요")
    async def currency(self, interaction: discord.Interaction, emoji: str | None = None, name: str | None = None,
                       maximum: app_commands.Range[int, 1, 1_000_000_000] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        changes = {k: v for k, v in dict(emoji=emoji and emoji.strip(), name=name and name.strip(), max=maximum).items() if v is not None}
        if not changes:
            cur = store.currency(gid)
            await interaction.response.send_message(embed=theme.notice(f"지금 재화: {cur.emoji} **{cur.name}** · 최대 {cur.max:,}개"), ephemeral=True)
            return
        if "emoji" in changes and not valid_emoji(changes["emoji"]):
            await self._deny(interaction, "이모지는 😀 같은 이모지 하나이거나 서버 이모지여야 해요.")
            return

        def apply() -> str:
            cur, trimmed = store.set_currency(gid, **changes)
            text = f"재화가 {cur.emoji} **{cur.name}** · 최대 {cur.max:,}개로 바뀌었어요."
            return text + (f"\n최대량보다 많이 가졌던 {trimmed:,}명은 {cur.max:,}개로 맞춰졌어요." if trimmed else "")

        async def after(inter):
            await refresh_panels(self.bot, inter.guild)

        if "max" in changes and (n := store.wallets_above(gid, changes["max"])):
            view = Confirm(interaction.user.id, apply, label="줄이기", emoji="⚠️", after=after)
            await interaction.response.send_message(embed=theme.make_embed(
                "⚠️ 최대 보유량을 줄일까요?", f"재화를 {changes['max']:,}개보다 많이 가진 **{n:,}명**이 {changes['max']:,}개로 줄어요. 되돌릴 수 없어요.",
                color=theme.RED), view=view, ephemeral=True)
            return
        try:
            text = apply()
        except ValueError as exc:
            await self._deny(interaction, str(exc))
            return
        await interaction.response.send_message(embed=theme.notice(text), ephemeral=True)
        await refresh_panels(self.bot, interaction.guild)

    # ---------------------------------------------------------------- 던전 입장 물품
    @app_commands.command(name="던전입장물품", description="던전에 들어갈 때 내는 물품과 개수를 정해요. 물품을 비우면 무료 입장이에요 (관리자)")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.rename(item="물품", count="개수")
    async def entry_item(self, interaction: discord.Interaction, item: str | None = None, count: app_commands.Range[int, 1, 99] = 1):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        store.ensure_defaults(gid)
        if item is None:
            store.update_settings(gid, entry_item=None, entry_cost=1)
            await interaction.response.send_message(embed=theme.notice("이제 던전은 **무료 입장**이에요."), ephemeral=True)
            return
        row = store.item_by_name(gid, item)
        if row is None or row["kind"] != "item":
            await self._deny(interaction, "그런 물품이 없어요. (역할은 입장 물품으로 쓸 수 없어요)")
            return
        store.update_settings(gid, entry_item=row["id"], entry_cost=count)
        await interaction.response.send_message(
            embed=theme.notice(f"던전에 들어가려면 {row['emoji']} **{row['name']}** {count}개가 필요해요. (이미 들어간 사람의 진행은 그대로예요)"), ephemeral=True)

    @entry_item.autocomplete("item")
    async def entry_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        self.bot.store.ensure_defaults(interaction.guild_id)
        return await item_choices(self.bot.store, interaction.guild_id, current, items_only=True)

    # ---------------------------------------------------------------- 패키지 결과표
    package = app_commands.Group(name="패키지결과", description="패키지를 뜯었을 때 나오는 결과를 바꿔요 (관리자)", guild_only=True,
                                 default_permissions=discord.Permissions(administrator=True))

    async def _entry_args(self, interaction, kind, item, amount):
        """종류에 맞는 물품·개수 값을 확인한다. (물품 id, 문제)."""
        store, gid = self.bot.store, interaction.guild_id
        if kind == "item":
            row = store.item_by_name(gid, item or "")
            if row is None or row["kind"] != "item":
                return None, "물품 결과에는 등록된 물품(역할 제외)을 골라 주세요."
            return row["id"], None
        return None, None

    @package.command(name="목록", description="패키지 결과표를 봐요 (관리자)")
    async def package_list(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        lines = package_lines(self.bot.store, interaction.guild_id)
        table = self.bot.store.package_table(interaction.guild_id)
        total = sum(e["weight"] for e in table)
        note = f"\n\n비율 합계 {total:g} — 합계가 100이 아니어도 비율로 계산해서 위 확률로 나와요."
        await interaction.response.send_message(embed=theme.make_embed("📦 패키지 결과표", "\n".join(lines) + note, color=theme.PURPLE), ephemeral=True)

    @package.command(name="추가", description="패키지 결과를 하나 더해요 (관리자)")
    @app_commands.rename(kind="종류", weight="확률", item="물품", amount="개수", emoji="이모지", title="이름")
    @app_commands.describe(kind="꽝 / 재화 / 물품", weight="나올 비율 (다른 결과와의 비율로 계산해요. 예: 60, 20, 10)",
                           item="종류가 물품일 때 줄 물품", amount="재화·물품을 몇 개 줄지 (기본 1)",
                           emoji="결과에 보일 아이콘 (비우면 물품·재화의 것)", title="결과에 보일 이름 (비우면 자동)")
    @app_commands.choices(kind=KINDS)
    async def package_add(self, interaction: discord.Interaction, kind: Choice[str], weight: app_commands.Range[float, 0, 1000],
                          item: str | None = None, amount: app_commands.Range[int, 1, 99999] = 1,
                          emoji: str | None = None, title: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        table = store.package_table(gid)
        if len(table) >= MAX_PACKAGE_ENTRIES:
            await self._deny(interaction, f"결과는 최대 {MAX_PACKAGE_ENTRIES}개까지예요.")
            return
        item_id, problem = await self._entry_args(interaction, kind.value, item, amount)
        if problem or (emoji and not valid_emoji(emoji.strip())) or (title and len(title.strip()) > 30):
            await self._deny(interaction, problem or "이모지가 올바르지 않거나 이름이 30자를 넘어요.")
            return
        ids = {e["id"] for e in table}
        new_id = "e" + secrets.token_hex(3)
        while new_id in ids:
            new_id = "e" + secrets.token_hex(3)
        entry = {"id": new_id, "kind": kind.value, "weight": weight, "amount": amount}
        if item_id is not None:
            entry["item"] = item_id
        if emoji and emoji.strip():
            entry["emoji"] = emoji.strip()
        if title and title.strip():
            entry["title"] = title.strip()
        try:
            store.update_settings(gid, package_table=[*table, entry])
        except ValueError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._show(interaction, "결과를 더했어요.")

    async def _show(self, interaction, head: str):
        lines = package_lines(self.bot.store, interaction.guild_id)
        await interaction.response.send_message(embed=theme.notice(head + "\n" + "\n".join(lines)), ephemeral=True)

    def _find_entry(self, gid: int, ident: str):
        return next((e for e in self.bot.store.package_table(gid) if e["id"] == ident), None)

    @package.command(name="수정", description="패키지 결과 하나를 고쳐요. 바꿀 것만 적으세요 (관리자)")
    @app_commands.rename(ident="결과", weight="확률", item="물품", amount="개수", emoji="이모지", title="이름")
    @app_commands.describe(ident="고칠 결과 (자동완성에서 골라요)", weight="나올 비율", item="물품 결과의 물품", amount="개수",
                           emoji=f"아이콘 ({RESET_TEXT}를 적으면 자동으로)", title=f"이름 ({RESET_TEXT}를 적으면 자동으로)")
    async def package_edit(self, interaction: discord.Interaction, ident: str, weight: app_commands.Range[float, 0, 1000] | None = None,
                           item: str | None = None, amount: app_commands.Range[int, 1, 99999] | None = None,
                           emoji: str | None = None, title: str | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        old = self._find_entry(gid, ident)
        if old is None:
            await self._deny(interaction, "그런 결과가 없어요. 자동완성에서 골라 주세요.")
            return
        new = dict(old)
        if weight is not None:
            new["weight"] = weight
        if amount is not None:
            new["amount"] = amount
        if item is not None:
            if old["kind"] != "item":
                await self._deny(interaction, "물품은 '물품' 종류의 결과에서만 바꿀 수 있어요.")
                return
            item_id, problem = await self._entry_args(interaction, "item", item, 1)
            if problem:
                await self._deny(interaction, problem)
                return
            new["item"] = item_id
        for field, value in (("emoji", emoji), ("title", title)):
            if value is None:
                continue
            value = value.strip()
            if value == RESET_TEXT or not value:
                new.pop(field, None)
            elif field == "emoji" and not valid_emoji(value) or field == "title" and len(value) > 30:
                await self._deny(interaction, "이모지가 올바르지 않거나 이름이 30자를 넘어요.")
                return
            else:
                new[field] = value
        if new == old:
            await self._deny(interaction, "바꿀 내용을 하나 이상 적어 주세요.")
            return
        table = [new if e["id"] == ident else e for e in store.package_table(gid)]
        try:
            store.update_settings(gid, package_table=table)
        except ValueError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._show(interaction, "결과를 고쳤어요.")

    @package.command(name="삭제", description="패키지 결과 하나를 지워요 (관리자)")
    @app_commands.rename(ident="결과")
    async def package_delete(self, interaction: discord.Interaction, ident: str):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        table = store.package_table(gid)
        if self._find_entry(gid, ident) is None:
            await self._deny(interaction, "그런 결과가 없어요. 자동완성에서 골라 주세요.")
            return
        rest = [e for e in table if e["id"] != ident]
        if not rest or sum(e["weight"] for e in rest) <= 0:
            await self._deny(interaction, "결과가 하나도 없거나 확률이 전부 0이 되면 안 돼요. 다른 결과를 먼저 더해 주세요.")
            return
        store.update_settings(gid, package_table=rest)
        await self._show(interaction, "결과를 지웠어요.")

    @package_edit.autocomplete("ident")
    @package_delete.autocomplete("ident")
    async def ident_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        store, gid = self.bot.store, interaction.guild_id
        needle, out = current.strip().lower(), []
        for e in store.package_table(gid):
            emoji, title = theme.entry_icon_title(e, store, gid)
            label = f"{emoji} {title} ({KIND_LABEL[e['kind']]}, 비율 {e['weight']:g})"
            if needle in label.lower():
                out.append(Choice(name=label[:100], value=e["id"]))
        return out[:25]

    @package_add.autocomplete("item")
    @package_edit.autocomplete("item")
    async def pkg_item_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        self.bot.store.ensure_defaults(interaction.guild_id)
        return await item_choices(self.bot.store, interaction.guild_id, current, items_only=True)

    # ---------------------------------------------------------------- 던전 클리어 보상
    clear = app_commands.Group(name="던전보상", description="던전 클리어 때 받는 보상을 바꿔요 (관리자)", guild_only=True,
                               default_permissions=discord.Permissions(administrator=True))

    def _clear_lines(self, gid: int) -> list[str]:
        store = self.bot.store
        bal, cur = store.balance(gid), store.currency(gid)
        lo, hi = bal.clear_candy
        lines = [f"{theme.money(lo, cur)} ~ {theme.money(hi, cur)} (항상)", f"🎟️ 히든 칭호 획득권 {bal.ticket_chance:g}%"]
        for i, r in enumerate(store.settings(gid)["clear_rewards"], start=1):
            row = store.item(gid, r["item"])
            name = f"{row['emoji']} {row['name']}" if row else "(지워진 물품 — 지급 안 됨)"
            lines.append(f"`{i}` {name} ×{r['amount']:,} — {r['chance']:g}%")
        return lines

    async def _clear_show(self, interaction, head: str):
        text = head + "\n" + "\n".join(self._clear_lines(interaction.guild_id))
        await interaction.response.send_message(embed=theme.notice(text), ephemeral=True)

    def _find_clear(self, gid: int, ident: str):
        return next((r for r in self.bot.store.settings(gid)["clear_rewards"] if r["id"] == ident), None)

    @clear.command(name="목록", description="던전 클리어 보상을 봐요 (관리자)")
    async def clear_list(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        note = ("\n\n재화 범위와 획득권 확률은 `/던전설정`의 `보상.최소`·`보상.최대`·`보상.획득권확률`로 바꿔요. "
                "물품 보상은 각각 따로 확률을 굴려요.")
        await interaction.response.send_message(
            embed=theme.make_embed("🏆 던전 클리어 보상", "\n".join(self._clear_lines(interaction.guild_id)) + note, color=theme.PURPLE),
            ephemeral=True)

    @clear.command(name="추가", description="클리어 보상 물품을 하나 더해요 (관리자)")
    @app_commands.rename(item="물품", amount="개수", chance="확률")
    @app_commands.describe(item="줄 물품 (역할 제외)", amount="클리어 때 몇 개 줄지", chance="받을 확률 % (0~100, 100이면 항상)")
    async def clear_add(self, interaction: discord.Interaction, item: str, amount: app_commands.Range[int, 1, 99999] = 1,
                        chance: app_commands.Range[float, 0, 100] = 100):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        rewards = store.settings(gid)["clear_rewards"]
        if len(rewards) >= MAX_CLEAR_REWARDS:
            await self._deny(interaction, f"클리어 보상 물품은 최대 {MAX_CLEAR_REWARDS}개까지예요.")
            return
        row = store.item_by_name(gid, item)
        if row is None or row["kind"] != "item":
            await self._deny(interaction, "등록된 물품(역할 제외)을 골라 주세요.")
            return
        ids = {r["id"] for r in rewards}
        new_id = "r" + secrets.token_hex(3)
        while new_id in ids:
            new_id = "r" + secrets.token_hex(3)
        try:
            store.update_settings(gid, clear_rewards=[*rewards, {"id": new_id, "item": row["id"], "amount": amount, "chance": chance}])
        except ValueError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._clear_show(interaction, "클리어 보상을 더했어요.")

    @clear.command(name="수정", description="클리어 보상 물품 하나를 고쳐요. 바꿀 것만 적으세요 (관리자)")
    @app_commands.rename(ident="보상", item="물품", amount="개수", chance="확률")
    @app_commands.describe(ident="고칠 보상 (자동완성에서 골라요)", item="바꿀 물품", amount="개수", chance="확률 % (0~100)")
    async def clear_edit(self, interaction: discord.Interaction, ident: str, item: str | None = None,
                         amount: app_commands.Range[int, 1, 99999] | None = None,
                         chance: app_commands.Range[float, 0, 100] | None = None):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        old = self._find_clear(gid, ident)
        if old is None:
            await self._deny(interaction, "그런 보상이 없어요. 자동완성에서 골라 주세요.")
            return
        new = dict(old)
        if item is not None:
            row = store.item_by_name(gid, item)
            if row is None or row["kind"] != "item":
                await self._deny(interaction, "등록된 물품(역할 제외)을 골라 주세요.")
                return
            new["item"] = row["id"]
        if amount is not None:
            new["amount"] = amount
        if chance is not None:
            new["chance"] = chance
        if new == old:
            await self._deny(interaction, "바꿀 내용을 하나 이상 적어 주세요.")
            return
        try:
            store.update_settings(gid, clear_rewards=[new if r["id"] == ident else r for r in store.settings(gid)["clear_rewards"]])
        except ValueError as exc:
            await self._deny(interaction, str(exc))
            return
        await self._clear_show(interaction, "클리어 보상을 고쳤어요.")

    @clear.command(name="삭제", description="클리어 보상 물품 하나를 지워요 (관리자)")
    @app_commands.rename(ident="보상")
    async def clear_delete(self, interaction: discord.Interaction, ident: str):
        if not await require_admin(interaction):
            return
        store, gid = self.bot.store, interaction.guild_id
        if self._find_clear(gid, ident) is None:
            await self._deny(interaction, "그런 보상이 없어요. 자동완성에서 골라 주세요.")
            return
        store.update_settings(gid, clear_rewards=[r for r in store.settings(gid)["clear_rewards"] if r["id"] != ident])
        await self._clear_show(interaction, "클리어 보상을 지웠어요.")

    @clear_edit.autocomplete("ident")
    @clear_delete.autocomplete("ident")
    async def clear_ident_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        store, gid = self.bot.store, interaction.guild_id
        needle, out = current.strip().lower(), []
        for r in store.settings(gid)["clear_rewards"]:
            row = store.item(gid, r["item"])
            label = f"{row['emoji']} {row['name']}" if row else "(지워진 물품)"
            label = f"{label} ×{r['amount']:,} ({r['chance']:g}%)"
            if needle in label.lower():
                out.append(Choice(name=label[:100], value=r["id"]))
        return out[:25]

    @clear_add.autocomplete("item")
    @clear_edit.autocomplete("item")
    async def clear_item_autocomplete(self, interaction: discord.Interaction, current: str):
        if interaction.guild_id is None:
            return []
        self.bot.store.ensure_defaults(interaction.guild_id)
        return await item_choices(self.bot.store, interaction.guild_id, current, items_only=True)


async def setup(bot):
    await bot.add_cog(RulesAdmin(bot))
