@echo off
setlocal
set "ROOT=%~dp0..\"
set "WHEELHOUSE=%ROOT%offline_wheelhouse"
if not exist "%WHEELHOUSE%" (
  echo Missing offline_wheelhouse. Extract offline_mooink_wheels.zip there first.
  exit /b 1
)
echo [mooink] Installing ONLY from local wheels. Network access disabled for pip.
python -m pip install --no-index --find-links "%WHEELHOUSE%" dpt-rp1-py Pillow PyMuPDF pywin32 comtypes
if errorlevel 1 (
  echo [ERROR] Missing or incompatible wheel. Rebuild wheel bundle while online using matching Windows/Python.
  exit /b 1
)
echo [mooink] Validating local installation...
python -m dptrp1.cli.dptrp1 -h
if errorlevel 1 exit /b 1
endlocal
