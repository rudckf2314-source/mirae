@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Project virtual environment is missing: .venv\Scripts\python.exe
  echo Create or repair .venv, then install requirements.txt.
  pause
  exit /b 1
)

set "PENSION_AGENT_MODE=langgraph"
if "%PENSION_PORT%"=="" set "PENSION_PORT=8000"
set "PENSION_HOST=127.0.0.1"
if "%PENSION_DISABLE_ENV_PROXY%"=="" set "PENSION_DISABLE_ENV_PROXY=1"

".venv\Scripts\python.exe" -c "import fastapi, langgraph, requests, httpx, uvicorn" || (
  echo [ERROR] Required Python modules are unavailable.
  echo Run: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

echo Starting Pension Chatbot at http://%PENSION_HOST%:%PENSION_PORT%
echo Open http://%PENSION_HOST%:%PENSION_PORT%/docs after /health is ready.
".venv\Scripts\python.exe" -m uvicorn web_app:app --host %PENSION_HOST% --port %PENSION_PORT%
endlocal
