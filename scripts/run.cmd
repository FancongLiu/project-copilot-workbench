@echo off
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\project-copilot.exe" (
  echo Run scripts\bootstrap.cmd first.
  exit /b 1
)

set HAYSTACK_TELEMETRY_ENABLED=False
".venv\Scripts\project-copilot.exe" %*
