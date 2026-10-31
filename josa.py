"""한국어 조사 고르기: 관리자가 재화·물품 이름을 마음대로 바꿀 수 있어서 '이/가' '은/는' 같은 조사를 받침에 맞춰 붙인다."""
from __future__ import annotations


def has_batchim(word: str) -> bool:
    """마지막 글자가 받침이 있는 한글이면 True. 한글이 아니면(숫자·영문·이모지 등) 받침이 없는 것으로 본다."""
    for char in reversed(word.rstrip()):
        if "가" <= char <= "힣":
            return (ord(char) - 0xAC00) % 28 != 0
        if char.isalnum():
            return False
    return False


def i_ga(word: str) -> str:
    return word + ("이" if has_batchim(word) else "가")


def eun_neun(word: str) -> str:
    return word + ("은" if has_batchim(word) else "는")


def eul_reul(word: str) -> str:
    return word + ("을" if has_batchim(word) else "를")
