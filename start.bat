@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.12 이상을 먼저 설치해주세요. https://www.python.org/downloads/
  pause
  exit /b 1
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo 설치된 Python이 너무 오래되었습니다. Python 3.12 이상을 설치해주세요.
  pause
  exit /b 1
)
if not exist .env (
  if exist .env.example (
    copy .env.example .env >nul
  ) else (
    echo DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN_HERE> .env
    echo GUILD_ID=>> .env
    echo DATABASE_PATH=data/halloween.sqlite3>> .env
  )
  echo .env 파일에 봇 토큰과 사용할 서버 ID를 넣고 저장한 뒤 다시 실행해주세요.
  notepad .env
  pause
  exit /b 0
)
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
rem requirements.txt가 바뀐 경우에만 패키지를 설치합니다.
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto failed
  copy /y requirements.txt .venv\requirements.installed >nul
)
.venv\Scripts\python.exe main.py
pause
exit /b 0
:failed
echo 설치에 실패했습니다. 인터넷 연결과 Python 설치를 확인해주세요.
pause
exit /b 1
