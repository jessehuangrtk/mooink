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

ARBITRARY DOCX SOURCE AND MINIMAL MARGINS
- In the Watcher settings choose "Custom DOCX file" (Select file) and browse to
  any existing .docx anywhere on the PC. The path is stored in
  %LOCALAPPDATA%\mooink\mooink_display_watcher.json (custom_docx).
- When selected, this one DOCX is the input for all paired readers; the output
  PNG is still size-specific: mooink_display_10.png or mooink_display_13.png.
  The selected DOCX is NEVER modified. The generated PNG stays in watch_dir.
- When rendering this custom DOCX, white page margins are trimmed by finding
  the bounding box of grayscale content below luminance 245/255 on the
  high-resolution PDF page. The result is fitted without stretching into the
  full e-paper resolution, keeping a thin ~0.8% white safety border.
  Aspect ratio differences may leave larger whitespace on two sides.
- Empty white pages remain empty. Multi-page documents are rejected to avoid
  silently discarding pages. Word/LibreOffice must be installed.
- Clear the custom DOCX field to resume size-specific default Word templates
  with their original layout and margins.
- Both the hot-plug and file-polling trigger options apply to the chosen DOCX.
  File-polling uses the selected document's content/hash and modification time.
  Changing the file selection creates a new render cache key.

MANUAL SELECT FILE (SEPARATE FROM AUTOMATIC WATCHING)
- The "Automatic DOCX" field selects the source for hot-plug/file-polling
  updates; leave blank to monitor the normal size-specific DOCX files.
- The new "Manual DOCX" row has its own file browser and "Update now" button.
  Manual selection does NOT change the automatic DOCX or its polling interval,
  watcher config, cached render hashes, or locally generated automatic PNGs.
- Press "Update now" to crop whitespace from that independent DOCX into
  temporary size-specific PNGs and send them to every connected, paired Mooink.
  Results and previews are recorded per reader after MTP SHA-256 verification.
  No device, unpaired device, or transfer failure produces a false success.
- Manual transfer works regardless of whether USB auto-update or either
  automatic trigger is enabled. The manual file path is remembered separately
  in %LOCALAPPDATA%\mooink\mooink_manual_docx.json.
- A later *actual* automatic trigger can replace the manually uploaded image
  with the automatic Word source. No manual operation edits the source DOCX.
- If the manual document has more than one page, conversion still rejects it;
  only one-page documents are currently supported.

TROUBLESHOOTING MANUAL DOCX: NO CONNECTED/PAIRED MOOINK
- "Update now" requires the device to be recognized in Windows WPD,
  its sleep folder accessible, and its ID paired to the physical panel size.
- The manual update UI now distinguishes three cases:
  * WPD finds no compatible Mooink: verify Windows Explorer, data USB cable,
    and device connection. Open "Manage devices / size pairing" to inspect.
  * Device found but sleep not accessible: reconnect/unlock the reader,
    then inspect the WPD/sleep error in the status.
  * Device connected but never size-paired: the GUI automatically opens
    size pairing. Choose the real 10.3 or 13.3-inch size, save, and the same
    manual DOCX will be retried without selecting it again.
- The first pairing is intentional: the WPD ID cannot reliably encode
  physical screen size. An unpaired device must not receive any uploads.
- No changes to the automatic monitored Word source or PNG files are made.

MAIN WINDOW / PAGE SELECTOR / ADVANCED MODE
- On EVERY startup, the window begins in Light/Simple mode. The main pane
  shows exactly three action buttons: Select File, Update Now, Mode:Simple;
  below them are XX/YY page number, a page slider, and a full-page preview.
- Word DOCX can contain multiple pages. Selecting a DOCX exports it once to a
  temporary PDF and loads its full page count (YY), not just the first page.
  The slider selects page XX and previews that entire page, including margins.
  "Update Now" sends only the selected page XX to every paired/ready Mooink;
  only the uploaded page is whitespace-cropped and fitted to each panel size.
  A changed DOCX invalidates the old preview, preventing stale-page updates.
  Manual selection and the automatic watched DOCX remain fully separate.
- Clicking Mode:Simple switches to Mode:Advanced and reveals all previous
  settings, pairing controls and transfer history in a panel ON THE RIGHT.
  The entire app changes to dark colors in advanced mode. Click again to
  collapse the right pane and return to Simple/Light mode.
  The mode is NEVER persisted: every program start begins Simple/Light.
- Advanced panel adds a "Stay resident" checkbox. Default OFF: closing the
  window quits the watcher rather than leaving a background tray process.
  When ON and SAVED, closing the window hides it in the system tray and
  monitoring continues. Use the tray menu to reopen the window or exit.
  This setting is saved in mooink_display_watcher.json as stay_resident.
- Page conversion needs Microsoft Word (Windows COM) or LibreOffice; the PDF
  preview renderer is bundled via PyMuPDF. The preview PDF is temporary and
  is removed when the app exits. A multi-page selection never changes or
  overwrites the source DOCX.
