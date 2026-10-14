@echo off
chcp 65001 >nul
rem ============================================================
rem  git-sync service launcher
rem  For dev-server managers (e.g. Dev Server Launcher) that need
rem  to track and stop the process tree (taskkill /F /T).
rem  Runs the UI server in the FOREGROUND on purpose.
rem
rem  NOTE: keep this file ASCII-only.
rem ============================================================
cd /d "%~dp0"

set "PY="
rem 1) python already on PATH
for /f "delims=" %%I in ('where python 2^>nul') do (
  if not defined PY set "PY=%%I"
)
rem 2) Windows py launcher
if not defined PY (
  for /f "delims=" %%I in ('where py 2^>nul') do (
    if not defined PY set "PY=%%I"
  )
)
if not defined PY (
  echo [ERROR] Python not found. Please install Python 3 and add it to PATH.
  exit /b 1
)

"%PY%" "%~dp0gui_server.py" --keep-alive %*
