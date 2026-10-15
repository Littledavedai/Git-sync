@echo off
chcp 65001 >nul
title git-sync
cd /d "%~dp0"

set "PY="
rem python on PATH -- skip the Microsoft Store placeholder (0-byte alias
rem that exits with 9009 and prints nothing when Python was not installed
rem from the Store)
for /f "delims=" %%I in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do (
  if not defined PY set "PY=%%I"
)
if not defined PY (
  for /f "delims=" %%I in ('where py 2^>nul') do (
    if not defined PY set "PY=%%I"
  )
)
rem portable python (version directory is not fixed -> newest first)
if not defined PY (
  for /f "delims=" %%V in ('dir /b /o-n "%USERPROFILE%\.workbuddy\binaries\python\versions" 2^>nul') do (
    if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\%%V\python.exe" (
      set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\%%V\python.exe"
    )
  )
)
if defined PYTHON set "PY=%PYTHON%"
if not defined PY (
  echo [ERROR] Python 3 not found.
  echo         Either add python.exe to PATH, or install the "py" launcher.
  echo         Or set PYTHON to a full path before running this file.
  pause
  exit /b 1
)

"%PY%" "%~dp0git_sync.py" %*
if errorlevel 1 (
  echo.
  echo [INFO] script exited with non-zero status.
  pause
)
