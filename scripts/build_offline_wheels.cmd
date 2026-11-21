@echo off
setlocal
set "ROOT=%~dp0..\"
set "WHEELHOUSE=%ROOT%offline_wheelhouse"
if not exist "%ROOT%third_party\dpt-rp1-py\pyproject.toml" (
  echo Missing offline source snapshot.
  exit /b 1
)
if not exist "%WHEELHOUSE%" mkdir "%WHEELHOUSE%"
echo [mooink] Building offline Python wheels using local vendored dpt-rp1-py...
python -m pip wheel --wheel-dir "%WHEELHOUSE%" "%ROOT%third_party\dpt-rp1-py" Pillow PyMuPDF pywin32 comtypes
if errorlevel 1 (
  echo [ERROR] Could not prepare complete wheelhouse. Internet may be needed now.
  exit /b 1
)
echo [mooink] Checking local wheel contents...
dir /b "%WHEELHOUSE%\*.whl"
if errorlevel 1 exit /b 1
if exist "%ROOT%offline_mooink_wheels.zip" del /q "%ROOT%offline_mooink_wheels.zip"
powershell -NoProfile -Command "Compress-Archive -Path '%WHEELHOUSE%\*' -DestinationPath '%ROOT%offline_mooink_wheels.zip' -Force"
if errorlevel 1 exit /b 1
echo.
echo Saved: %ROOT%offline_mooink_wheels.zip
echo Copy the ZIP to secure offline storage. Wheels are Python/Windows architecture specific.
endlocal
