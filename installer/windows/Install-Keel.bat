@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Keel.ps1"
if errorlevel 1 (
  echo.
  echo Install failed. See the message above.
  pause
)
