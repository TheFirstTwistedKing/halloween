"""관리자 확인. 상점·이벤트 설정 명령은 서버 관리자(Administrator 권한)나 서버 주인만 쓸 수 있다."""
from __future__ import annotations

import discord

import theme


def is_admin(interaction) -> bool:
    if getattr(interaction, "guild_id", None) is None:
        return False
    permissions = getattr(interaction, "permissions", None)
    if permissions is not None and permissions.administrator:
        return True
    owner_id = getattr(getattr(interaction, "guild", None), "owner_id", None)
    return owner_id is not None and getattr(getattr(interaction, "user", None), "id", None) == owner_id


async def require_admin(interaction: discord.Interaction) -> bool:
    """관리자가 아니면 본인에게만 보이는 거절 메시지를 보내고 False를 돌려준다."""
    if is_admin(interaction):
        return True
    await interaction.response.send_message(embed=theme.notice("이 명령어는 서버 관리자만 쓸 수 있어요.", False), ephemeral=True)
    return False
