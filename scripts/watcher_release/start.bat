@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo Run install.bat first.
  popd
  exit /b 1
)
".venv\Scripts\python.exe" mooink_display_watcher.py
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [ERROR] Watch Tool exited with code %RC%.
popd
exit /b %RC%
