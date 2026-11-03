"""대량 삭제: 목록에서 여러 개를 골라 한 번에 지운다. 되돌릴 수 없는 작업이라 ① 선택 메뉴로 고르고(25개씩 쪽으로 넘김, 전체 선택 가능)
② 지울 목록을 한 번 더 보여 주는 확인 화면을 거친다. 못 지우는 것(다른 설정이 쓰는 것, 마지막 하나 등)은 이유와 함께 건너뛴다.
디스코드에 접속하지 않는 삭제 실행 규칙(run_deletions)은 그대로 시험할 수 있다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import discord

import theme
from owner import owner_only
from paging import chunks, split_pages

PAGE = 25                       # 선택 메뉴 한 쪽에 담을 수 있는 항목 수(디스코드 한도)


@dataclass(frozen=True)
class BulkEntry:
    value: str                  # 삭제 함수에 넘길 값(이름·번호 등)
    label: str                  # 목록에 보일 이름
    emoji: str | None = None
    hint: str = ""              # 선택 메뉴 아래 작은 설명


def run_deletions(picks: list[str], delete: Callable[[str], str | None], errors: tuple) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """고른 것들을 하나씩 지운다. ([(값, 메모)], [(값, 못 지운 이유)])를 돌려준다.
    하나가 다른 것에 막혀 있다가 같이 고른 다른 것을 지우고 나면 풀리는 경우(예: 레시피에 쓰이는 장비 + 그 레시피)가 있어서,
    진전이 없을 때까지 못 지운 것을 다시 시도한다."""
    deleted: list[tuple[str, str]] = []
    pending, reasons = list(picks), {}
    while pending:
        still, progress = [], False
        for value in pending:
            try:
                note = delete(value)
            except errors as exc:
                still.append(value)
                reasons[value] = str(exc)
            else:
                deleted.append((value, note or ""))
                progress = True
        pending = still
        if not progress:
            break
    return deleted, [(value, reasons[value]) for value in pending]


class BulkDelete(discord.ui.View):
    """대량 삭제 화면. 선택 → [삭제] → 확인 → 결과 순서로 같은 메시지가 바뀐다. 연 관리자만 누를 수 있다."""

    def __init__(self, admin_id: int, noun: str, entries: list[BulkEntry], delete: Callable[[str], str | None], errors: tuple, *,
                 after=None, warn: Callable[[list[BulkEntry]], str] | None = None, page: int = 0, picks=()):
        super().__init__(timeout=300)
        self.admin_id, self.noun, self.entries, self.delete, self.errors = admin_id, noun, entries, delete, errors
        self.after, self.warn = after, warn
        self.by_value = {e.value: e for e in entries}
        self.pages = chunks(entries, PAGE) or [[]]
        self.page = max(0, min(page, len(self.pages) - 1))
        self.picks = [p for p in picks if p in self.by_value]
        self.confirming = False
        self._build()

    # ---- 화면 ----
    def embed(self) -> discord.Embed:
        if self.confirming:
            chosen = [self.by_value[p] for p in self.picks]
            lines = [f"{e.emoji + ' ' if e.emoji else ''}**{e.label}**" for e in chosen]
            shown = split_pages(lines, 3000)[0].split("\n")
            more = len(lines) - len(shown)
            text = f"아래 **{len(chosen)}개**를 지워요. 되돌릴 수 없어요.\n\n" + "\n".join(shown) + (f"\n… 외 {more}개" if more else "")
            if self.warn and (extra := self.warn(chosen)):
                text += "\n\n" + extra
            return theme.make_embed(f"🗑️ 정말 {len(chosen)}개를 지울까요?", text, color=theme.RED)
        paging = f" ({self.page + 1}/{len(self.pages)}쪽)" if len(self.pages) > 1 else ""
        text = (f"지울 {self.noun}을(를) 골라요. 여러 개를 고를 수 있고, 목록이 길면 ◀ ▶로 넘겨요{paging}.\n"
                f"고른 뒤 **[삭제]**를 누르면 지울 목록을 한 번 더 보여 줘요. 지금 **{len(self.picks)}개** 골랐어요 (전체 {len(self.entries)}개).")
        return theme.make_embed(f"🗑️ {self.noun} 대량 삭제", text, color=theme.ORANGE)

    def _build(self) -> None:
        self.clear_items()
        if self.confirming:
            self._button("삭제", "🗑️", self.run, discord.ButtonStyle.danger)
            self._button("돌아가기", "↩️", self.back, discord.ButtonStyle.secondary)
            self._button("취소", None, self.cancel, discord.ButtonStyle.secondary)
            return
        options = [discord.SelectOption(label=e.label[:100], value=e.value, emoji=e.emoji, description=e.hint[:100] or None, default=e.value in self.picks)
                   for e in self.pages[self.page]]
        if options:
            holder = "🗑️ 지울 것을 골라요 (여러 개 가능)" + (f" · {self.page + 1}/{len(self.pages)}쪽" if len(self.pages) > 1 else "")
            select = discord.ui.Select(placeholder=holder, min_values=0, max_values=len(options), options=options, row=0)
            select.callback = self.on_select
            self.add_item(select)
        self._button("삭제", "🗑️", self.ask, discord.ButtonStyle.danger, row=1, disabled=not self.picks)
        self._button("전체 선택", "☑️", self.select_all, discord.ButtonStyle.secondary, row=1)
        self._button("선택 해제", "⬜", self.clear_picks, discord.ButtonStyle.secondary, row=1, disabled=not self.picks)
        self._button("취소", None, self.cancel, discord.ButtonStyle.secondary, row=1)
        if len(self.pages) > 1:
            self._button("이전 쪽", "◀️", self.turn(-1), discord.ButtonStyle.secondary, row=2, disabled=self.page <= 0)
            self._button("다음 쪽", "▶️", self.turn(+1), discord.ButtonStyle.secondary, row=2, disabled=self.page >= len(self.pages) - 1)

    def _button(self, label, emoji, callback, style, row=0, disabled=False) -> None:
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.admin_id)

    # ---- 고르기 ----
    async def on_select(self, interaction: discord.Interaction):
        here = {e.value for e in self.pages[self.page]}
        self.picks = [p for p in self.picks if p not in here] + [v for v in interaction.data.get("values", []) if v in here]
        await self._refresh(interaction)

    async def select_all(self, interaction: discord.Interaction):
        self.picks = [e.value for e in self.entries]
        await self._refresh(interaction)

    async def clear_picks(self, interaction: discord.Interaction):
        self.picks = []
        await self._refresh(interaction)

    def turn(self, step: int):
        async def callback(interaction: discord.Interaction):
            self.page = max(0, min(len(self.pages) - 1, self.page + step))
            await self._refresh(interaction)
        return callback

    # ---- 확인 → 실행 ----
    async def ask(self, interaction: discord.Interaction):
        if not self.picks:
            await interaction.response.send_message(embed=theme.notice("먼저 위 목록에서 지울 것을 골라 주세요.", False), ephemeral=True)
            return
        self.picks = [e.value for e in self.entries if e.value in self.picks]          # 목록 순서대로
        self.confirming = True
        await self._refresh(interaction)

    async def back(self, interaction: discord.Interaction):
        self.confirming = False
        await self._refresh(interaction)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("취소했어요. 아무것도 지우지 않았어요."), view=None)

    async def run(self, interaction: discord.Interaction):
        self.stop()
        deleted, failed = run_deletions(list(self.picks), self.delete, self.errors)
        await interaction.response.edit_message(embed=self.result_embed(deleted, failed), view=None)
        if deleted and self.after:
            await self.after(interaction)

    def result_embed(self, deleted, failed) -> discord.Embed:
        label = lambda v: (f"{self.by_value[v].emoji} " if self.by_value[v].emoji else "") + f"**{self.by_value[v].label}**"
        head = f"🗑️ **{len(deleted)}개**를 지웠어요." + (f" 못 지운 것 **{len(failed)}개**." if failed else "")
        embed = theme.make_embed("🗑️ 대량 삭제 결과" if deleted or not failed else "👻 하나도 못 지웠어요", head, color=theme.GREEN if deleted else theme.RED)
        if deleted:
            lines = [label(v) + (f" — {note}" if note else "") for v, note in deleted]
            for number, part in enumerate(split_pages(lines, 1000)[:3], start=1):
                embed.add_field(name="지운 것" if number == 1 else "지운 것 (이어서)", value=part, inline=False)
            if len(split_pages(lines, 1000)) > 3:
                embed.add_field(name="…", value="더 많아서 일부만 보여 줘요.", inline=False)
        if failed:
            lines = [f"{label(v)} — {reason}" for v, reason in failed]
            for number, part in enumerate(split_pages(lines, 1000)[:2], start=1):
                embed.add_field(name="못 지운 것과 이유" if number == 1 else "못 지운 것 (이어서)", value=part, inline=False)
        return embed


async def send_bulk(interaction: discord.Interaction, noun: str, entries: list[BulkEntry], delete, errors: tuple, **kw) -> None:
    """대량 삭제 화면을 보낸다. 지울 수 있는 게 없으면 안내만 한다."""
    if not entries:
        await interaction.response.send_message(embed=theme.notice(f"지울 수 있는 {noun}이(가) 없어요.", False), ephemeral=False)
        return
    view = BulkDelete(interaction.user.id, noun, entries, delete, errors, **kw)
    await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=False)
