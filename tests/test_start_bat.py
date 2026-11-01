"""start.bat 점검. Windows가 없는 환경에서도 돌릴 수 있게, 배치 파일이 깨지는 흔한 원인을 소스 수준에서 확인한다.
(실제 실행은 Windows에서만 확인할 수 있다. 이 시험은 줄바꿈·인코딩·문법 규칙과 다른 파일과의 일관성을 지킨다.)"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = (ROOT / "start.bat").read_bytes()
TEXT = RAW.decode("utf-8")
LINES = TEXT.split("\r\n")[:-1]
CODE = [line for line in LINES if line.strip() and not line.strip().lower().startswith("rem ")]


def strip_quotes(line: str) -> str:
    return re.sub(r'"[^"]*"', '""', line)


class FileFormatTests(unittest.TestCase):
    def test_windows_line_endings_everywhere(self):
        """cmd.exe는 CRLF를 기대한다. LF만 있으면 라벨·괄호 블록이 오동작할 수 있다."""
        self.assertGreater(RAW.count(b"\r\n"), 50)
        self.assertEqual(RAW.count(b"\n"), RAW.count(b"\r\n"), "CRLF가 아닌 줄바꿈(LF만)이 있어요")
        self.assertEqual(RAW.count(b"\r"), RAW.count(b"\r\n"), "CR만 있는 줄바꿈이 있어요")
        self.assertTrue(RAW.endswith(b"\r\n"))

    def test_utf8_without_bom(self):
        """BOM이 있으면 첫 줄 @echo off가 명령어로 인식되지 않는다."""
        self.assertFalse(RAW.startswith(b"\xef\xbb\xbf"))
        RAW.decode("utf-8")

    def test_the_console_is_switched_to_utf8_before_any_korean_text(self):
        self.assertEqual(LINES[0].strip().lower(), "@echo off")
        chcp = next(i for i, line in enumerate(LINES) if line.lower().startswith("chcp 65001"))
        first_korean = next(i for i, line in enumerate(LINES) if re.search(r"[가-힣]", line))
        self.assertLess(chcp, first_korean)
        self.assertIn('set "PYTHONUTF8=1"', TEXT)                                   # 파이썬 출력도 UTF-8

    def test_git_leaves_the_line_endings_alone(self):
        text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\*\.bat\s+-text\s*$")


class SyntaxSafetyTests(unittest.TestCase):
    def test_no_labels_goto_or_call(self):
        """라벨과 goto는 줄바꿈·인코딩 문제에 가장 약하다. 흐름은 if 블록과 exit /b로만 만든다."""
        for line in CODE:
            stripped = line.strip()
            self.assertFalse(stripped.startswith(":"), line)
            self.assertNotRegex(stripped.lower(), r"\b(goto|call)\b", line)

    def test_parentheses_blocks_are_balanced(self):
        depth = 0
        for number, line in enumerate(LINES, 1):
            stripped = line.strip()
            if not stripped or stripped.lower().startswith(("rem ", "echo")):
                continue                                                        # 안내 문구는 아래 시험이 따로 본다
            depth += strip_quotes(stripped).count("(") - strip_quotes(stripped).count(")")
            self.assertGreaterEqual(depth, 0, f"{number}번째 줄에서 닫는 괄호가 더 많아요: {line}")
        self.assertEqual(depth, 0, "열린 괄호 블록이 닫히지 않았어요")

    def test_messages_cannot_break_a_block(self):
        """괄호 블록 안의 echo에 ( ) & | < > ^ ! 가 있으면 블록이 깨진다. % 는 정해 둔 변수만 쓴다."""
        for number, line in enumerate(LINES, 1):
            stripped = line.strip()
            if not stripped.lower().startswith("echo") or not re.search(r"[가-힣]", stripped):
                continue
            self.assertIsNone(re.search(r"[()&|<>^!]", stripped), f"{number}번째 줄에 위험한 문자: {line}")
            for variable in re.findall(r"%[^%]*%", stripped):
                self.assertIn(variable, ("%CODE%", "%PY%"), f"{number}번째 줄: {line}")

    def test_every_variable_used_is_set_before(self):
        for variable in ("PY", "VENV", "CODE", "NEEDTOKEN", "NEEDPIP"):
            use = re.search(rf"%{variable}%|defined {variable}\b|errorlevel", TEXT)
            self.assertIsNotNone(use)
            first_set = TEXT.find(f'set "{variable}=')
            first_use = min([i for i in (TEXT.find(f"%{variable}%"), TEXT.find(f"defined {variable}")) if i >= 0] or [10**9])
            self.assertGreaterEqual(first_use, first_set, variable)
            self.assertGreaterEqual(first_set, 0, variable)

    def test_every_path_is_quoted_so_spaces_and_korean_folders_work(self):
        self.assertIn('cd /d "%~dp0"', TEXT)
        for needle in ('"%VENV%" main.py', '"requirements.txt"', '".env"', '".venv"'):
            self.assertIn(needle, TEXT)
        self.assertNotRegex(TEXT, r"(?m)^\s*\.venv\\Scripts\\python\.exe\s")      # 따옴표 없는 경로로 실행하지 않는다

    def test_every_failure_stops_with_a_pause_and_a_nonzero_exit(self):
        exits = [i for i, line in enumerate(LINES) if line.strip() == "exit /b 1"]
        self.assertGreaterEqual(len(exits), 5)
        for i in exits:
            self.assertEqual(LINES[i - 1].strip(), "pause", f"{i + 1}번째 줄: 오류로 끝나기 전에 pause가 없어요(창이 바로 닫혀 오류를 못 봐요)")
        self.assertEqual(LINES[-1].strip(), "exit /b %CODE%")                      # 봇의 종료 코드를 그대로 돌려준다


class BehaviourTests(unittest.TestCase):
    def test_it_tries_py_python_and_python3(self):
        for command in ('py -3 -c "import sys"', 'python -c "import sys"', 'python3 -c "import sys"'):
            self.assertIn(command, TEXT)
        self.assertLess(TEXT.index("py -3 -c"), TEXT.index("python -c"))

    def test_the_version_rule_matches_the_readme_and_the_messages_agree(self):
        """안내 문구(3.12)와 실제 검사(3.10)가 어긋나 있던 문제. 이제 모두 3.10이다."""
        self.assertIn("sys.version_info >= (3, 10)", TEXT)
        self.assertNotIn("3.12", TEXT)
        self.assertEqual(len(re.findall(r"3\.10 이상", TEXT)), 2)
        self.assertIn("Python 3.10 이상", (ROOT / "README.md").read_text(encoding="utf-8"))

    def test_the_env_template_is_ascii_only_and_has_exactly_the_keys_main_reads(self):
        """오래된 메모장이 한글 주석을 깨뜨려 .env를 못 읽는 일이 없도록, 만드는 .env는 영문만 쓴다."""
        template = [line for line in LINES if re.match(r'>{1,2} "\.env" echo ', line.strip())]
        self.assertEqual(len(template), 4)
        for line in template:
            line.encode("ascii")
        keys = {re.search(r"echo (\w+)=", line).group(1) for line in template}
        main_py = (ROOT / "main.py").read_text(encoding="utf-8")
        read_by_main = set(re.findall(r'os\.getenv\("(\w+)"', main_py))
        self.assertEqual(keys, read_by_main)

    def test_the_token_placeholder_matches_the_env_example_and_is_checked(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN_HERE", example)
        self.assertIn("DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN_HERE", TEXT)
        self.assertIn('findstr /b /c:"DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN_HERE"', TEXT)       # 안 바꾼 채 실행하는 것을 막는다
        self.assertIn('findstr /b /r /c:"DISCORD_TOKEN=..*"', TEXT)                         # 비어 있는 것도 막는다
        self.assertIn('start /wait notepad ".env"', TEXT)                                   # 메모장을 닫을 때까지 기다린 뒤 이어간다

    def test_it_installs_exactly_what_requirements_lists(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertIn("discord.py", requirements)
        self.assertIn("python-dotenv", requirements)
        self.assertIn('"%VENV%" -c "import discord, dotenv"', TEXT)                          # 두 패키지가 실제로 들어 있는지 본다
        self.assertIn('-m pip install --disable-pip-version-check -r "requirements.txt"', TEXT)
        self.assertIn('copy /y "requirements.txt" ".venv\\requirements.installed"', TEXT)

    def test_it_recreates_a_broken_virtual_environment_and_runs_main(self):
        self.assertIn('rmdir /s /q ".venv"', TEXT)
        self.assertTrue(TEXT.index("rmdir /s /q") < TEXT.index('%PY% -m venv ".venv"') < TEXT.index('"%VENV%" main.py'))
        self.assertTrue((ROOT / "main.py").exists())

    def test_the_order_of_the_steps(self):
        order = ["py -3 -c", "sys.version_info", 'if not exist ".env"', "start /wait notepad", '%PY% -m venv', "pip install", '"%VENV%" main.py']
        positions = [TEXT.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main()
