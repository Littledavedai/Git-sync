@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ============================================================
rem  git-sync GUI launcher
rem  Starts the local UI server with pythonw (no console window).
rem  The server opens the browser by itself.
rem
rem  NOTE: keep this file ASCII-only -- cmd reads .bat in the OEM
rem  codepage, so non-ASCII literals would be garbled.
rem ============================================================

cd /d "%~dp0"

set "PYW="
rem 1) pythonw.exe already on PATH
for /f "delims=" %%I in ('where pythonw 2^>nul') do (
  if not defined PYW set "PYW=%%I"
)
rem 2) pythonw sitting next to the python that is on PATH
if not defined PYW (
  for /f "delims=" %%I in ('where python 2^>nul') do (
    if not defined PYW (
      if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
    )
  )
)
if not defined PYW (
  echo.
  echo [ERROR] pythonw.exe not found.
  echo         Please install Python 3 and make sure it is on PATH.
  echo.
  pause
  exit /b 1
)

start "" "%PYW%" "%~dp0gui_server.py" %*
exit /b 0
