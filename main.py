"""할로윈 봇 시작 파일."""
from __future__ import annotations

import logging
import os
import random
import re
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from packages import PackageView
from panel import ShopPanelView
from services import Locks
from store import Store

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("halloween")
EXTENSIONS = ("cogs.economy", "cogs.admin", "cogs.shop", "cogs.dungeon", "cogs.dungeon_admin",
              "cogs.rules_admin", "cogs.content_admin")


def env_flag(raw: str) -> bool:
    """.env의 켜짐/꺼짐 값. 1·true·yes·on(대소문자 무관)이면 켜짐."""
    return raw.strip().lower() in ("1", "true", "yes", "on")


def parse_guild_ids(raw: str) -> list[int]:
    """GUILD_ID 값을 서버 ID 목록으로 바꾼다. 쉼표·공백·세미콜론으로 구분하고, 잘못된 값은 이름을 알려 준다."""
    ids, bad = [], []
    for token in re.split(r"[,\s;]+", raw.strip()):
        if not token:
            continue
        if token.isascii() and token.isdigit() and int(token) > 0:
            if int(token) not in ids:
                ids.append(int(token))
        else:
            bad.append(token)
    if bad:
        raise ValueError("GUILD_ID에 숫자가 아닌 값이 있어요: " + ", ".join(bad))
    return ids


class HalloweenBot(commands.Bot):
    def __init__(self, store: Store, guild_ids: list[int] | None = None, rng: random.Random | None = None,
                 members_intent: bool = False):
        intents = discord.Intents.default()          # 기본은 특권 인텐트(멤버·메시지 내용)가 필요 없어요.
        intents.members = members_intent             # 켜면 '역할 재화 지급'처럼 역할을 가진 사람 전체가 필요한 기능을 쓸 수 있어요.
        # 시작할 때 모든 멤버를 미리 불러오지 않는다(필요할 때만 가져온다).
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None,
                         chunk_guilds_at_startup=False)
        self.store = store
        self.locks = Locks()
        self.rng = rng or random.Random()
        self.guild_ids = frozenset(guild_ids or [])

    def allowed_guild(self, guild_id: int) -> bool:
        """GUILD_ID를 정했다면 그 서버에서만 동작한다. 비어 있으면 봇이 들어간 모든 서버."""
        return not self.guild_ids or guild_id in self.guild_ids

    async def setup_hook(self):
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        # 재시작해도 이미 올라간 상점·패키지의 버튼이 계속 동작하게 한다.
        self.add_view(ShopPanelView())
        self.add_view(PackageView())
        if self.guild_ids:
            for guild_id in sorted(self.guild_ids):
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                try:
                    synced = await self.tree.sync(guild=guild)
                    LOG.info("서버 ID %s: 슬래시 명령어 %d개 등록 완료", guild_id, len(synced))
                except discord.HTTPException as exc:
                    LOG.error("서버 ID %s: 명령어를 등록하지 못했어요 (HTTP %s). 봇이 그 서버에 초대됐는지 확인하세요.", guild_id, exc.status)
        else:
            synced = await self.tree.sync()
            LOG.info("슬래시 명령어 %d개를 전체 서버에 등록했어요. (반영까지 최대 1시간)", len(synced))

    async def on_ready(self):
        LOG.info("연결 완료: %s / 서버 %d개", self.user, len(self.guilds))


def main():
    load_dotenv(BASE / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        sys.exit(".env 파일에 DISCORD_TOKEN을 적어 주세요. (.env.example을 복사해서 .env로 만드세요)")
    try:
        guild_ids = parse_guild_ids(os.getenv("GUILD_ID", ""))
    except ValueError as exc:
        sys.exit(str(exc))
    store = Store(BASE / os.getenv("DATABASE_PATH", "data/halloween.sqlite3"))
    bot = HalloweenBot(store, guild_ids, members_intent=env_flag(os.getenv("MEMBERS_INTENT", "")))
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        sys.exit("토큰이 올바르지 않아요. 개발자 포털에서 토큰을 다시 확인해 주세요.")
    except discord.PrivilegedIntentsRequired:
        sys.exit(".env에서 MEMBERS_INTENT를 켰는데 개발자 포털에서 'Server Members Intent'가 꺼져 있어요. "
                 "포털의 Bot 탭에서 켜거나, .env의 MEMBERS_INTENT를 지워 주세요.")
    finally:
        store.close()


if __name__ == "__main__":
    main()
