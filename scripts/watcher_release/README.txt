mooInk Display Watcher - standalone Windows release

1. Install Windows Python 3.10 or newer with pip, venv and Tkinter.
   If wheelhouse is bundled, match the Python major/minor and architecture in
   wheel_python.txt (for example Python 3.12 AMD64 64-bit).
2. Install Microsoft Word or LibreOffice separately for DOCX conversion.
3. Copy the entire release folder to a writable local directory.
   Do not copy an installed .venv from another computer.
4. Run install.bat (creates a local .venv and installs dependencies).
   A bundled wheelhouse is installed offline; otherwise pip needs access
   to its configured package index. The installer copies missing working
   documents/images from template without overwriting existing ones.
5. The watcher can start without any USB device. Connect one mooInk whenever
   convenient. Pico 2W is optional: leave its COM field blank to disable wake,
   or use its actual COM port if Bluetooth Space wake is needed. Close Tera
   Term only if using Pico.
6. Run start.bat. Open settings (tray icon), select the working folder,
   display size and optional Pico COM port, then opt in to USB automatic
   updates. USB/Space manual tests are optional; enabling USB does not require
   either device to be connected.
7. On first upload select the matching custom sleep image in mooInk:
   mooink_display_10.png or mooink_display_13.png, not the book cover.
   Set auto sleep to 15 seconds if desired.

Working directory: keep template/ unchanged. Modify the working DOCX files
in the chosen watch directory, not template/. The current user's settings,
state, logs and backup files remain under %LOCALAPPDATA%\mooink.
No personal settings, logs, backups, source Word documents, or device pairing
credentials from the building PC are included. If another Watch Tool is
already installed on the destination PC, its per-user settings are shared;
check the actual configured working folder and COM port before enabling USB.

The release is for the existing USB MTP + Pico CDC Watch Tool, not the
experimental Pico_wifi_display project. Pico firmware is not flashed by
installation and the already working Pico does not need reflashing.
Windows WPD/MTP support is required; connect and verify the reader in
Windows Explorer before enabling USB updates. No Git or WSL is required on
the target PC. Python itself and Office/LibreOffice are not bundled.

USB hot-plug: while USB automatic update is enabled, WPD readiness is checked
approximately every 1 second, independently of the Word check interval.
On detection of a reader after an offline state, the current Word file is
rendered again and the reserved sleep PNG is uploaded or SHA-256 verified,
even when the Word file is unchanged. Pico is optional. The settings window
shows the last USB-verified update time, source Word modification time and
preview from the actual transferred/verified PNG, not a later local render.
Windows WPD device arrival/removal notifications also trigger an immediate check.
If notifications are unavailable, very brief disconnects between polls can be missed.
WPD operations can
take longer than the poll interval. Physical screen refresh is not verified.

Release packaging does not overwrite an existing ../mooink_display_watcher
folder. Stop the old watcher, back up or rename that folder before rebuilding,
and copy your edited working Word files into the new folder if needed.
Check the saved watch directory in settings after moving the folder.
release_commit.txt identifies the source Git revision when Git is available.

MULTI-DEVICE SIZE PROFILE
Each reader's WPD/PnP ID is SHA-256 fingerprinted and saved with its size in
%LOCALAPPDATA%\mooink\mooink_device_sizes.json. The profile includes the last
verified transfer time and the modification time of the Word used. Verified
PNG previews are saved under %LOCALAPPDATA%\mooink\verified_previews\.
The UI displays connected and remembered devices. If a reader's WPD ID changes
(e.g. Windows gives a new instance ID after a USB port change), pair it again.
No identification from physical screen size is attempted; an unpaired reader
requires an explicit size selection, which avoids accidentally sending a
13.3-inch PNG to a 10.3-inch reader. Multiple same-model readers are supported.
Automatic Pico Space is disabled in multi-device mode; USB MTP still works.

AUTOMATIC UPDATE TRIGGERS (both ON by default; select one or both):
- Hot-plug update: synchronize each paired reader when it first becomes ready
  or is unplugged and reconnected. A Word edit while still plugged in does
  not trigger a transfer if this is the only enabled mode.
- Polling file update: check the Word document every interval_sec seconds and
  synchronize only when its contents or modification time changed since
  that reader's last verified delivery. Connecting/reconnecting a previously
  synchronized reader alone never forces an update in polling-only mode.
  The first scheduled poll may deliver a file that has never been verified for
  that reader, and unsent edits made while disconnected are sent on the next
  scheduled check after reconnection.
- With both enabled, either event can update. At least one trigger must be
  enabled when USB auto update is enabled. Manual 'update now' overrides both.
- When polling is disabled, no automatic Word-to-PNG conversion is done just
  because the interval timer fires. USB is still discovered once per second.
- The settings are stored in %LOCALAPPDATA%\mooink\mooink_display_watcher.json
  as update_on_connect and update_on_file_change. Old installations default
  both to ON without overwriting existing settings.
- A success timestamp is recorded only after MTP readback verification. Pico
  is not required. Multiple readers retain distinct last-delivered versions.
