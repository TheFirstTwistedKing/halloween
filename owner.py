"""공개 화면의 조작 제한. 명령어로 연 화면(가방·던전·확인 버튼 등)은 모두가 볼 수 있지만 연 사람만 누를 수 있다.
다른 사람이 누르면 아무 반응이 없는 '상호작용 실패' 대신, 누른 사람에게만 보이는 안내를 보낸다."""
from __future__ import annotations

import discord

import theme


async def owner_only(interaction: discord.Interaction, owner_id: int) -> bool:
    """누른 사람이 화면을 연 사람이면 True. 아니면 그 사람에게만 안내를 보내고 False."""
    if interaction.user.id == owner_id:
        return True
    try:
        await interaction.response.send_message(
            embed=theme.notice("이 화면은 연 사람만 조작할 수 있어요. 직접 명령어를 써 주세요.", False), ephemeral=True)
    except discord.HTTPException:
        pass
    return False
