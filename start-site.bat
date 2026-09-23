chcp 65001 >nul
@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Сначала запусти setup.bat
  pause
  exit /b 1
)
set TELEGRAM_BOT_TOKEN=
set EKT_SITE_ONLY=1
echo EKT магазин: http://127.0.0.1:8001
echo CMS: http://127.0.0.1:8001/admin
".venv\Scripts\python.exe" scripts\run_site.py
pause
