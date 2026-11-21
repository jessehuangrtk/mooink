# First-generation mooInk Pro: USB MTP capability findings

Status: 2026-11-21 (Windows 10/11, user-provided v004 logs). This report is **not** proof of USB display refresh or remote sleep/wake support.

## Device under test

- Windows WPD device: `mooInk Pro`.
- USB VID/PID: `1F85:4E16` (specific unit tested; may differ on the user's other model).
- Driver: `WUDFWpdMtp`.
- Connection remains available while the device sleeps.
- User confirmed that they can write files into the `sleep` directory during sleep, and that the new sleep image can appear **after manually waking and sleeping again**. Live refresh without waking is **not verified**.

## v004 diagnostic results

- `--no-vendor-query` with read-only access returns 45 advertised Windows Portable Devices (WPD) commands.
- `--rw-vendor-query --force-vendor-query` requests a read/write **handle** but issues only the WPD *Get Supported Vendor Opcodes* metadata command. This returned five extended MTP operation codes.
- The 45 WPD commands are Windows **driver/API capabilities**, not 45 confirmed proprietary device operations. In particular `{4D545058-1A2E-4106-A357-771E0819FC56}` is Microsoft's WPD MTP extension command GUID.

## Decoded vendor-extension operation codes

Android Open Source Project's MTP definitions identify every returned code:

| Code | Android name | What it does |
| --- | --- | --- |
| `0x95C1` | `GetPartialObject64` | Reads part of a file with 64-bit offsets |
| `0x95C2` | `SendPartialObject` | Writes a portion of a file |
| `0x95C3` | `TruncateObject` | Changes file length |
| `0x95C4` | `BeginEditObject` | Begins an object edit transaction |
| `0x95C5` | `EndEditObject` | Ends an object edit transaction |

All five implement **file access/editing**, not E Ink display refresh, screen capture, sleep, wake, or ADB. Their presence indicates Android-style MTP extensions, but does **not** demonstrate availability of the Android Debug Bridge.

Primary reference: [AOSP frameworks/av media/mtp/mtp.h lines 403–414](https://android.googlesource.com/platform/frameworks/av/+/6e02bc5d58ce473952ffa4710aa6d7c4d327164a/media/mtp/mtp.h#403).

Secondary reference: [libmtp's Android partial-object extension patch](https://sourceforge.net/p/libmtp/mailman/message/30469430/).

## Key limitation

Windows WPD `GetSupportedCommands` is **not** the full list of standard MTP codes from the device's raw `GetDeviceInfo` dataset. The five extension codes do not establish whether standard power-related commands are supported. Do not issue power, reset, truncate, or unknown vendor commands to find out.

MTP file transfer updates storage; it does not cause the E Ink screen to redraw by definition. Current evidence points to the firmware refreshing its sleep image when it enters standby.

## Next tests (non-destructive first)

1. Back up all existing files in `sleep`.
2. Select a particular sleep image while awake; verify which image appears when sleeping.
3. Replace only that specific image file with another image of the same name **while the device remains asleep**. Observe whether the panel changes without touching the device. Do not delete system files.
4. If it does not auto-refresh, stop treating MTP storage operations as a display protocol. Focus on identifying a separately documented refresh/wake interface, if any.
5. Check whether Windows separately enumerates an authorized ADB, USB serial, or USB network interface (none seen in this unit's current WPD/PnP result). Do not switch USB drivers or flash firmware merely to force such an interface.
6. Compare first-generation 10.3-inch and 13.3-inch units independently; never generalize the USB ID or capabilities without checking.

For read-only follow-up, Microsoft's `WPD_COMMAND_MTP_EXT_GET_VENDOR_EXTENSION_DESCRIPTION` (category GUID above, command 18) can retrieve the extension-description string. It does **not** remotely wake or refresh the panel.

References:
- [Microsoft's WPD MTP command description](https://learn.microsoft.com/en-us/windows/win32/wpd_sdk/wpd-command-mtp-ext-get-vendor-extension-description)
- [Microsoft's WPD device properties](https://learn.microsoft.com/en-us/windows/win32/wpd_sdk/device-properties)
