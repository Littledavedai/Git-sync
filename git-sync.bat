@echo off
chcp 65001 >nul
title git-sync
cd /d "%~dp0"

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
  where py >nul 2>&1 && set "PY=py"
)
if not defined PY (
  echo [ERROR] Python not found. Please install Python 3 and add it to PATH.
  pause
  exit /b 1
)

"%PY%" "%~dp0git_sync.py" %*
if errorlevel 1 (
  echo.
  echo [INFO] script exited with non-zero status.
  pause
)
