@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  set REPLAY_DATE=2026-09-16
) else (
  set REPLAY_DATE=%~1
)

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install -q -r requirements.txt

if "%GOOGLE_CLOUD_PROJECT%"=="" (
  echo [ERROR] GOOGLE_CLOUD_PROJECT is not set.
  echo Example: set GOOGLE_CLOUD_PROJECT=your-project-id
  exit /b 2
)

echo [RUN] Walk-forward replay day: %REPLAY_DATE%
python bigquery_scan.py --date %REPLAY_DATE%
