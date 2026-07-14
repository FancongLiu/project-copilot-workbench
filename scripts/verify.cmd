@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\bootstrap.cmd first.
  exit /b 1
)

".venv\Scripts\python.exe" -m ruff check . || exit /b 1
".venv\Scripts\python.exe" -m ruff format --check . || exit /b 1
".venv\Scripts\python.exe" -m pytest || exit /b 1
".venv\Scripts\python.exe" -m project_copilot.release_guard . || exit /b 1
echo Verification passed.
