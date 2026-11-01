@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title 할로윈 봇
cd /d "%~dp0"
echo ==============================================
echo   할로윈 봇을 시작해요
echo ==============================================
echo.

rem ---------- 1. 파이썬 찾기: py 런처, python, python3 순서 ----------
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY python3 -c "import sys" >nul 2>nul && set "PY=python3"
if not defined PY (
  echo [오류] 파이썬을 찾을 수 없어요.
  echo https://www.python.org/downloads/ 에서 3.10 이상을 설치해 주세요.
  echo 설치 화면 맨 아래의 Add python.exe to PATH 를 꼭 체크해 주세요.
  echo 설치한 뒤에 이 파일을 다시 실행해 주세요.
  echo.
  pause
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [오류] 파이썬 버전이 너무 낮아요. 3.10 이상이 필요해요.
  echo 지금 찾은 버전:
  %PY% --version
  echo https://www.python.org/downloads/ 에서 새 버전을 설치한 뒤 다시 실행해 주세요.
  echo.
  pause
  exit /b 1
)
%PY% --version

rem ---------- 2. .env 준비: 없으면 만들고, 토큰이 비어 있으면 메모장으로 받는다 ----------
if not exist ".env" (
  > ".env" echo DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN_HERE
  >> ".env" echo GUILD_ID=
  >> ".env" echo DATABASE_PATH=data/halloween.sqlite3
  >> ".env" echo MEMBERS_INTENT=
  echo .env 파일을 새로 만들었어요.
)
set "NEEDTOKEN="
findstr /b /r /c:"DISCORD_TOKEN=..*" ".env" >nul 2>nul
if errorlevel 1 set "NEEDTOKEN=1"
findstr /b /c:"DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN_HERE" ".env" >nul 2>nul
if not errorlevel 1 set "NEEDTOKEN=1"
if defined NEEDTOKEN (
  echo.
  echo 봇 토큰이 아직 없어요. 곧 메모장이 열리면 이렇게 해 주세요.
  echo   1. DISCORD_TOKEN= 바로 뒤에 봇 토큰을 붙여 넣어요. 공백이나 따옴표는 없이요.
  echo   2. GUILD_ID= 뒤에는 봇을 쓸 서버 ID를 적어요. 비워 두면 모든 서버에 등록돼요.
  echo   3. 저장하고 메모장을 닫아요.
  start /wait notepad ".env"
  set "NEEDTOKEN="
  findstr /b /r /c:"DISCORD_TOKEN=..*" ".env" >nul 2>nul
  if errorlevel 1 set "NEEDTOKEN=1"
  findstr /b /c:"DISCORD_TOKEN=PUT_YOUR_BOT_TOKEN_HERE" ".env" >nul 2>nul
  if not errorlevel 1 set "NEEDTOKEN=1"
  if defined NEEDTOKEN (
    echo.
    echo [오류] 아직 봇 토큰이 없어요. 토큰을 넣고 저장한 뒤 이 파일을 다시 실행해 주세요.
    echo.
    pause
    exit /b 1
  )
)

rem ---------- 3. 가상환경: 없거나 망가졌으면 새로 만든다 ----------
set "VENV=.venv\Scripts\python.exe"
if exist "%VENV%" (
  "%VENV%" -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo 가상환경이 망가져 있어서 새로 만들어요.
    rmdir /s /q ".venv"
  )
)
if not exist "%VENV%" (
  echo 가상환경을 만드는 중이에요. 처음 한 번만 걸려요.
  %PY% -m venv ".venv"
  if errorlevel 1 (
    echo.
    echo [오류] 가상환경을 만들지 못했어요. 파이썬을 다시 설치해 보고, 그래도 안 되면 알려 주세요.
    echo.
    pause
    exit /b 1
  )
)

rem ---------- 4. 패키지: requirements.txt가 바뀌었거나 빠진 것이 있을 때만 설치 ----------
set "NEEDPIP="
fc /b "requirements.txt" ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 set "NEEDPIP=1"
"%VENV%" -c "import discord, dotenv" >nul 2>nul
if errorlevel 1 set "NEEDPIP=1"
if defined NEEDPIP (
  echo 필요한 패키지를 설치하는 중이에요. 인터넷 연결이 필요해요.
  "%VENV%" -m pip install --disable-pip-version-check -r "requirements.txt"
  if errorlevel 1 (
    echo.
    echo [오류] 패키지를 설치하지 못했어요. 인터넷 연결을 확인하고 다시 실행해 주세요.
    echo.
    pause
    exit /b 1
  )
  copy /y "requirements.txt" ".venv\requirements.installed" >nul
)

rem ---------- 5. 실행 ----------
echo.
echo 봇을 시작해요. 끄려면 이 창에서 Ctrl+C 를 눌러요.
echo.
"%VENV%" main.py
set "CODE=%errorlevel%"
echo.
if "%CODE%"=="0" (
  echo 봇이 꺼졌어요.
) else (
  echo [알림] 봇이 오류로 꺼졌어요. 종료 코드 %CODE%
  echo 위쪽의 Traceback 이나 오류 문구가 원인이에요. 그 부분을 알려 주세요.
)
echo.
pause
exit /b %CODE%
