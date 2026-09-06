@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Project virtual environment is missing.
  exit /b 1
)

".venv\Scripts\python.exe" -m pytest -q TEST tests
endlocal
