@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Build a standalone Watch Tool release. Never modify existing destination.
set "NO_WHEELS=0"
if "%~1"=="" goto args_ok
if /i "%~1"=="--no-wheels" if "%~2"=="" (
  set "NO_WHEELS=1"
  goto args_ok
)
echo Usage: %~nx0 [--no-wheels]
exit /b 2
:args_ok
pushd "%~dp0" || exit /b 1
for %%I in ("..\mooink_display_watcher") do set "DEST=%%~fI"
if exist "%DEST%" (
  echo [ERROR] Destination already exists:
  echo "%DEST%"
  echo Rename or back up that directory before building again.
  popd
  exit /b 1
)
set "STAGE=%DEST%.staging-%RANDOM%-%RANDOM%"
if exist "%STAGE%" (
  echo [ERROR] Staging directory already exists.
  popd
  exit /b 1
)
mkdir "%STAGE%" || goto failed
for %%F in (mooink_display_watcher.py mooink_device_photo_ui.py mooink_docx_pages.py mooink_photo.py mooink_multi_device.py mook_sleep_screen_manager.py mook_mtp_wpd.py pico2w_usb2bluetooth.py LICENSE) do (
  copy /b /y "%%F" "%STAGE%\%%F" >nul || goto failed
)
mkdir "%STAGE%\template" || goto failed
for %%F in (mooink_display_10.docx mooink_display_10.png mooink_display_13.docx mooink_display_13.png) do (
  copy /b /y "template\%%F" "%STAGE%\template\%%F" >nul || goto failed
)
mkdir "%STAGE%\third_party" || goto failed
for %%F in (heribert17_win_access.py heribert17_wpd_LICENSE) do (
  copy /b /y "third_party\%%F" "%STAGE%\third_party\%%F" >nul || goto failed
)
for %%F in (requirements.txt install.bat start.bat README.txt) do (
  copy /b /y "scripts\watcher_release\%%F" "%STAGE%\%%F" >nul || goto failed
)
rem Record the source revision if Git is available on the packaging PC.
where git >nul 2>&1
if not errorlevel 1 git rev-parse HEAD > "%STAGE%\release_commit.txt" 2>nul
if "%NO_WHEELS%"=="1" goto publish
echo [INFO] Preparing offline wheels for the current Windows Python.
python -c "import sys; assert sys.platform=='win32' and sys.version_info >= (3,10), 'Windows Python 3.10+ required'" || goto failed
mkdir "%STAGE%\wheelhouse" || goto failed
python -m pip download --only-binary=:all: --dest "%STAGE%\wheelhouse" -r "%STAGE%\requirements.txt" || goto failed
python -c "import sys,platform,struct; print('Python',str(sys.version_info.major)+'.'+str(sys.version_info.minor),platform.machine(),str(struct.calcsize('P')*8)+'-bit')" > "%STAGE%\wheel_python.txt" || goto failed
:publish
rem Rename in the same parent; unlike MOVE, REN will not nest in an existing destination.
ren "%STAGE%" "mooink_display_watcher" || goto failed
echo.
echo [OK] Release created:
echo "%DEST%"
echo Copy the entire directory to the other PC and run install.bat, then start.bat.
popd
exit /b 0
:failed
echo [ERROR] Release not published. Check the previous error.
echo Unfinished staging directory, if created:
echo "%STAGE%"
echo Existing installations and the original repository have not been overwritten.
popd
exit /b 1
