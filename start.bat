chcp 65001 >nul
@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Нет окружения. Сначала запусти setup.bat
  pause
  exit /b 1
)
echo EKT: http://127.0.0.1:8000
echo Telegram подключится, если заполнен TELEGRAM_BOT_TOKEN в .env
".venv\Scripts\python.exe" -m app
pause
