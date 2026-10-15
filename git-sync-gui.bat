@echo off
chcp 65001 >nul

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

rem 1) pythonw on PATH.
rem    IMPORTANT: skip the Microsoft Store placeholder
rem    (%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe). It is a
rem    0-byte alias that does nothing when Python was not installed
rem    from the Store. Picking it would fail silently.
for /f "delims=" %%I in ('where pythonw 2^>nul ^| findstr /v /i "WindowsApps"') do (
  if not defined PYW set "PYW=%%I"
)

rem 2) pythonw sitting next to a python that is on PATH
if not defined PYW (
  for /f "delims=" %%I in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do (
    if not defined PYW (
      if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
    )
  )
)

rem 3) the Windows "py" launcher (pyw.exe runs without a console)
if not defined PYW (
  for /f "delims=" %%I in ('where pyw 2^>nul') do (
    if not defined PYW set "PYW=%%I"
  )
)

rem 4) portable python (version directory is not fixed -> newest first)
if not defined PYW (
  for /f "delims=" %%V in ('dir /b /o-n "%USERPROFILE%\.workbuddy\binaries\python\versions" 2^>nul') do (
    if not defined PYW if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\%%V\pythonw.exe" (
      set "PYW=%USERPROFILE%\.workbuddy\binaries\python\versions\%%V\pythonw.exe"
    )
  )
)

if defined PYTHON set "PYW=%PYTHON%"

if not defined PYW (
  echo.
  echo [ERROR] pythonw.exe not found.
  echo         Either add Python 3 to PATH, or install the "py" launcher.
  echo         Or set PYTHON to a full path before running this file:
  echo             set PYTHON=C:\somewhere\pythonw.exe
  echo.
  pause
  exit /b 1
)

start "" "%PYW%" "%~dp0gui_server.py" %*
exit /b 0
