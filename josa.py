"""한국어 조사 고르기: 관리자가 재화·물품 이름을 마음대로 바꿀 수 있어서 '이/가' '은/는' 같은 조사를 받침에 맞춰 붙인다."""
from __future__ import annotations


# 대문자로만 쓴 영문 약어는 마지막 글자를 한국어로 읽었을 때(RAM → 램) 받침이 있으면 받침으로 본다: F(에프) L(엘) M(엠) N(엔) R(알).
# 소문자가 섞인 영단어(coin·Gem)는 읽는 법을 알 수 없어서 예전처럼 받침이 없는 것으로 본다.
LATIN_BATCHIM = set("FLMNR")


def has_batchim(word: str) -> bool:
    """마지막 글자가 받침이 있는 한글이면 True. 대문자 영문 약어(RAM)는 마지막 글자 소리로 본다. 그 밖의 영문·숫자·이모지는 받침이 없는 것으로 본다."""
    text = word.rstrip()
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if "가" <= char <= "힣":
            return (ord(char) - 0xAC00) % 28 != 0
        if char.isascii() and char.isalpha():
            start = index
            while start > 0 and text[start - 1].isascii() and text[start - 1].isalpha():
                start -= 1
            run = text[start:index + 1]
            return run.isupper() and char in LATIN_BATCHIM
        if char.isalnum():
            return False
    return False


def i_ga(word: str) -> str:
    return word + ("이" if has_batchim(word) else "가")


def eun_neun(word: str) -> str:
    return word + ("은" if has_batchim(word) else "는")


def eul_reul(word: str) -> str:
    return word + ("을" if has_batchim(word) else "를")
