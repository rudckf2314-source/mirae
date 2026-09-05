@echo off
setlocal
cd /d %~dp0\..
python scripts\sync_legal_db.py --fail-on-partial
exit /b %ERRORLEVEL%
