"""연금술 규칙: 열리는 시간과 합성 가격. 디스코드에 접속하지 않는 순수 로직이라 그대로 시험할 수 있다.

- 연금술은 현실 시간으로 매일 오후 6시(18:00)부터 자정(오전 12시, 24:00)까지만 할 수 있다. 자정이 되는 순간 닫힌다.
  시간은 한국 표준시(UTC+9)로 센다. 한국은 일광 절약 시간이 없어서 고정 오프셋으로 충분하고, 시간대 데이터 설치도 필요 없다.
- 재료는 2~3개. 합쳐서 얻는 물품의 판매가는 재료들의 판매가를 전부 더한 값에 두 개면 0.8, 세 개면 0.6을 곱해서 정한다(반올림).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
OPEN_HOUR, CLOSE_HOUR = 18, 24                 # 18:00 이상, 24:00 미만
MIN_INGREDIENTS, MAX_INGREDIENTS = 2, 3
FACTOR_TENTHS = {2: 8, 3: 6}                   # 0.8, 0.6 (정수로 계산해서 소수 오차를 피한다)
WINDOW_TEXT = "매일 오후 6시부터 자정(오전 12시)까지"


def kst(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, KST)


def is_open(timestamp: float) -> bool:
    """지금(한국 시간)이 연금술이 열린 시간인가. 18:00:00 ~ 23:59:59.999."""
    return OPEN_HOUR <= kst(timestamp).hour < CLOSE_HOUR


def next_open_text(timestamp: float) -> str:
    """닫혀 있을 때 안내: 언제 다시 열리는지. 열려 있으면 빈 문자열."""
    if is_open(timestamp):
        return ""
    now = kst(timestamp)
    opening = now.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
    seconds = int((opening - now).total_seconds())      # 닫힌 시간은 00:00~17:59라서 항상 오늘 오후 6시가 다음이다.
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    left = (f"{hours}시간 " if hours else "") + f"{minutes}분" if (hours or minutes) else "1분 미만"
    return f"오늘 오후 6시에 열려요. ({left} 뒤)"


def combined_price(prices: list[int]) -> int:
    """재료들의 판매가로 합성물의 판매가를 정한다: 전부 더해서 2개면 ×0.8, 3개면 ×0.6. 소수는 반올림(0.5는 올림)."""
    count = len(prices)
    if count not in FACTOR_TENTHS:
        raise ValueError(f"재료는 {MIN_INGREDIENTS}~{MAX_INGREDIENTS}개여야 해요.")
    return (sum(prices) * FACTOR_TENTHS[count] + 5) // 10


def factor_text(count: int) -> str:
    return f"×0.{FACTOR_TENTHS[count]}"
