"""긴 목록을 페이지로 나눠 ◀ ▶ 버튼으로 넘겨 보게 한다. 디스코드는 임베드 칸 하나를 1024자, 선택 메뉴를 25개까지만 받아서
목록이 길어지면 뒤가 잘리거나 오류가 나는데, 이 모듈이 그걸 막는다. 디스코드에 접속하지 않는 나누기 규칙(split_pages)은 그대로 시험할 수 있다."""
from __future__ import annotations

from typing import Callable

import discord

from owner import owner_only


def split_pages(lines: list[str], limit: int) -> list[str]:
    """줄 목록을 limit자 이하의 덩어리로 나눈다. 줄 하나는 쪼개지 않고(너무 긴 한 줄만 …로 줄인다), 비어 있으면 빈 목록."""
    pages, current, size = [], [], 0
    for line in lines:
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        extra = len(line) + (1 if current else 0)
        if current and size + extra > limit:
            pages.append("\n".join(current))
            current, size, extra = [], 0, len(line)
        current.append(line)
        size += extra
    if current:
        pages.append("\n".join(current))
    return pages


def chunks(items: list, size: int) -> list[list]:
    """items를 size개씩 묶는다(선택 메뉴 25개 한도 등). 비어 있으면 빈 목록."""
    return [items[i:i + size] for i in range(0, len(items), size)]


class PagedView(discord.ui.View):
    """◀ [3/5] ▶ 버튼으로 임베드 페이지를 넘기는 화면. 연 사람만 누를 수 있다. build(번호) → 그 페이지의 임베드."""

    def __init__(self, user_id: int, count: int, build: Callable[[int], discord.Embed], *, page: int = 0, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.user_id, self.count, self.build, self.page = user_id, max(1, count), build, max(0, min(page, count - 1))
        self.prev = discord.ui.Button(label="이전", emoji="◀️", style=discord.ButtonStyle.secondary)
        self.label = discord.ui.Button(label="", style=discord.ButtonStyle.secondary, disabled=True)
        self.next = discord.ui.Button(label="다음", emoji="▶️", style=discord.ButtonStyle.secondary)
        self.prev.callback, self.next.callback = self._go(-1), self._go(+1)
        for button in (self.prev, self.label, self.next):
            self.add_item(button)
        self._sync()

    def _sync(self) -> None:
        self.label.label = f"{self.page + 1} / {self.count}"
        self.prev.disabled, self.next.disabled = self.page <= 0, self.page >= self.count - 1

    def first(self) -> discord.Embed:
        return self.build(self.page)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_only(interaction, self.user_id)

    def _go(self, step: int):
        async def callback(interaction: discord.Interaction):
            self.page = max(0, min(self.count - 1, self.page + step))
            self._sync()
            await interaction.response.edit_message(embed=self.build(self.page), view=self)
        return callback


async def send_paged(interaction: discord.Interaction, user_id: int, count: int, build: Callable[[int], discord.Embed], *, ephemeral: bool = False) -> None:
    """페이지가 하나면 그냥 보내고, 둘 이상이면 넘김 버튼을 붙여 보낸다."""
    if count <= 1:
        await interaction.response.send_message(embed=build(0), ephemeral=ephemeral)
        return
    view = PagedView(user_id, count, build)
    await interaction.response.send_message(embed=view.first(), view=view, ephemeral=ephemeral)
