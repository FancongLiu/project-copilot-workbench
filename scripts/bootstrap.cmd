@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv || exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip || exit /b 1
".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements.lock || exit /b 1
".venv\Scripts\python.exe" -m pip install --no-deps -e . || exit /b 1
echo Environment ready: %CD%\.venv
