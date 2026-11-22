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
5. Connect one mooInk via USB, and the already configured Pico 2W via USB
   if Bluetooth Space wake is needed. Close Tera Term to free its COM port.
6. Run start.bat. Open settings (tray icon), select the working folder,
   display size and actual Pico COM port, test USB and Space, then opt in
   to USB automatic updates.
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
approximately every 2 seconds, independently of the Word check interval.
On detection of a reader after an offline state, the current Word file is
rendered again and the reserved sleep PNG is uploaded or SHA-256 verified,
even when the Word file is unchanged. Pico is optional. The settings window
shows the last USB-verified update time, source Word modification time and
preview from the actual transferred/verified PNG, not a later local render.
Very brief disconnects between polls may not be detected. WPD operations can
take longer than the poll interval. Physical screen refresh is not verified.
