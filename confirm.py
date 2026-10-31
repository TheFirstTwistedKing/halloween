"""되돌릴 수 없는 관리 작업 앞의 확인 버튼. 누른 사람이 명령을 쓴 관리자일 때만 동작한다."""
from __future__ import annotations

import discord

import theme
from store import StoreError


class Confirm(discord.ui.View):
    def __init__(self, admin_id: int, action, *, label: str = "확인", emoji: str = "✅", after=None):
        """action(): 동기 함수. 문장을 돌려주면 성공 안내로 보여 준다. after: 성공한 뒤 부를 비동기 함수(interaction)."""
        super().__init__(timeout=60)
        self.admin_id, self.action, self.after = admin_id, action, after
        yes = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.danger)
        no = discord.ui.Button(label="취소", style=discord.ButtonStyle.secondary)
        yes.callback, no.callback = self.confirm, self.cancel
        self.add_item(yes)
        self.add_item(no)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin_id

    async def confirm(self, interaction: discord.Interaction):
        self.stop()
        try:
            text = self.action()
        except (StoreError, ValueError) as exc:
            await interaction.response.edit_message(embed=theme.notice(str(exc), False), view=None)
            return
        await interaction.response.edit_message(embed=theme.notice(text), view=None)
        if self.after:
            await self.after(interaction)

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(embed=theme.notice("취소했어요. 아무것도 바뀌지 않았어요."), view=None)
