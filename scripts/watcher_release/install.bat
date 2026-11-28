@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONIOENCODING=utf-8"
pushd "%~dp0" || exit /b 1
python -c "import sys; assert sys.platform=='win32' and sys.version_info >= (3,10), 'Windows Python 3.10+ required'" || goto failed
if exist "wheelhouse\" (
  if not exist "wheel_python.txt" goto failed
  python -c "import sys,platform,struct,pathlib; s='Python '+str(sys.version_info.major)+'.'+str(sys.version_info.minor)+' '+platform.machine()+' '+str(struct.calcsize('P')*8)+'-bit'; assert pathlib.Path('wheel_python.txt').read_text().strip()==s, 'Use the same Python major/minor and Windows architecture as the packaging PC'" || goto failed
)
if not exist ".venv\Scripts\python.exe" (
  python -m venv ".venv" || goto failed
)
set "VPY=%CD%\.venv\Scripts\python.exe"
"%VPY%" -c "import sys; assert sys.platform=='win32' and sys.version_info >= (3,10)" || goto failed
if exist "wheelhouse\" (
  "%VPY%" -c "import sys,platform,struct,pathlib; s='Python '+str(sys.version_info.major)+'.'+str(sys.version_info.minor)+' '+platform.machine()+' '+str(struct.calcsize('P')*8)+'-bit'; assert pathlib.Path('wheel_python.txt').read_text().strip()==s, 'Existing venv does not match wheelhouse'" || goto failed
  echo [INFO] Installing from bundled offline wheels only.
  "%VPY%" -m pip install --no-index --find-links "%CD%\wheelhouse" -r requirements.txt || goto failed
) else (
  echo [INFO] Installing from configured pip package index. Internet may be required.
  "%VPY%" -m pip install -r requirements.txt || goto failed
)
"%VPY%" -m pip check || goto failed
"%VPY%" -c "import tkinter, PIL, pymupdf, pythoncom, win32com.client, win32gui, win32api, win32con, comtypes, serial, pystray; import mooink_display_watcher, mooink_docx_pages, mooink_photo, mooink_multi_device, mook_mtp_wpd, pico2w_usb2bluetooth; from third_party.heribert17_win_access import get_portable_devices" || goto failed
"%VPY%" mooink_display_watcher.py --init-templates || goto failed
echo.
echo [OK] Installation complete. Run start.bat.
echo Microsoft Word or LibreOffice must be installed separately.
popd
exit /b 0
:failed
echo [ERROR] Installation failed. Check the previous error.
echo Existing working documents were not intentionally overwritten.
popd
exit /b 1
