@echo off
chcp 65001 >nul
title Limit-Up Sniper QT - Local Start
cd /d "%~dp0"

echo Limit-Up Sniper QT - Local Start
echo Project directory: %CD%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local.ps1" %*
set EXIT_CODE=%ERRORLEVEL%

echo.
if not "%EXIT_CODE%"=="0" (
  echo Startup script exit code: %EXIT_CODE%
)
echo Keep this window open to view logs.
pause
exit /b %EXIT_CODE%
