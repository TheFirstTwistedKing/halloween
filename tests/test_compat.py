"""호환성: 낮은 파이썬·SQLite에서도 돌아가야 한다. 새 기능이 최신 환경에서만 되는 문법을 몰래 들여오지 않게 소스를 점검한다."""
import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [p for p in list(ROOT.glob("*.py")) + list((ROOT / "cogs").glob("*.py")) + list((ROOT / "tests").glob("*.py"))
           if p.name != "test_compat.py"]
# SQLite 3.35(2021)에서야 생긴 문법: 이전 버전을 쓰는 호스트(Debian 11의 3.34, Ubuntu 20.04의 3.31 등)에서는 문법 오류가 난다.
NEW_SQLITE = {"RETURNING": r"\bRETURNING\b", "UPDATE ... FROM": r"\bUPDATE\b[^;\"']*\bFROM\b", "MATERIALIZED": r"\bMATERIALIZED\b",
              "->> / ->": r"\bjson_\w+\s*\(", "STRICT 표": r"\)\s*STRICT\b", "GENERATED 열": r"\bGENERATED\s+ALWAYS\b"}


class SqliteSyntaxTests(unittest.TestCase):
    def test_no_sql_that_needs_a_recent_sqlite(self):
        found = []
        for path in SOURCES:
            text = path.read_text(encoding="utf-8")
            for label, pattern in NEW_SQLITE.items():
                for match in re.finditer(pattern, text):
                    line = text.count("\n", 0, match.start()) + 1
                    found.append(f"{path.relative_to(ROOT)}:{line} {label}")
        self.assertEqual(found, [], "최신 SQLite에서만 되는 문법이 있어요 (호스트의 SQLite가 낮으면 오류가 나요)")

    def test_the_features_we_do_rely_on_are_old_enough(self):
        """쓰고 있는 문법(upsert=3.24, 표현식 인덱스=3.9)은 3.24 이상이면 된다. 지금 환경도 그 이상인지 확인한다."""
        self.assertGreaterEqual(tuple(int(n) for n in sqlite3.sqlite_version.split(".")), (3, 24))
        self.assertTrue(any("ON CONFLICT" in p.read_text(encoding="utf-8") for p in SOURCES))        # 이 점검이 실제 사용을 보고 있다.


if __name__ == "__main__":
    unittest.main()
