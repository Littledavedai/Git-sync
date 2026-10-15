@echo off
chcp 65001 >nul
rem ============================================================
rem  git-sync service launcher
rem  For dev-server managers (e.g. Dev Server Launcher) that need
rem  to track and stop the process tree (taskkill /F /T).
rem  Runs the UI server in the FOREGROUND on purpose.
rem
rem  NOTE: keep this file ASCII-only -- cmd reads .bat in the OEM
rem  codepage, so non-ASCII literals would be garbled.
rem ============================================================
cd /d "%~dp0"

set "PY="

rem 1) python on PATH.
rem    IMPORTANT: skip the Microsoft Store placeholder
rem    (%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe). It is a
rem    0-byte alias that exits with 9009 and prints NOTHING when
rem    Python was not installed from the Store -- which silently
rem    breaks any process manager waiting for this script.
for /f "delims=" %%I in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do (
  if not defined PY set "PY=%%I"
)

rem 2) the Windows "py" launcher
if not defined PY (
  for /f "delims=" %%I in ('where py 2^>nul') do (
    if not defined PY set "PY=%%I"
  )
)

rem 3) portable python (version directory is not fixed -> newest first)
if not defined PY (
  for /f "delims=" %%V in ('dir /b /o-n "%USERPROFILE%\.workbuddy\binaries\python\versions" 2^>nul') do (
    if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\%%V\python.exe" (
      set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\%%V\python.exe"
    )
  )
)

if not defined PY (
  echo [ERROR] Python 3 not found.
  echo         Either add python.exe to PATH, or install the "py" launcher.
  echo         Or set PYTHON to a full path before running this script:
  echo             set PYTHON=C:\somewhere\python.exe
  exit /b 1
)

if defined PYTHON set "PY=%PYTHON%"

"%PY%" "%~dp0gui_server.py" --keep-alive %*
