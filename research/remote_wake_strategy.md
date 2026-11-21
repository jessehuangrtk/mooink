# Remote wake / sleep strategy: original mooInk Pro 10.3 / 13.3

Research update: 2026-11-21.

## User's desired behavior

1. Windows captures image and sends it using the already verified USB MTP `sleep` folder (user tested write access while asleep).
2. **Trigger wake remotely**.
3. **Trigger sleep remotely** and make the updated sleep image visible.

Do **not** substitute the remote-document/PDF viewer concept as the default: the objective is specifically a wake→sleep cycle.

## Evidence and limitations

- Windows sees the original unit as `USB VID_1F85&PID_4E16`, `WUDFWpdMtp`; v004 returned 45 Windows WPD driver commands and five Android-like MTP file-edit extension codes `0x95C1–0x95C5`. None are verified wake/sleep commands.
- Sony's DPT-RP1 manual says pressing the power button exits sleep, and another brief press enters sleep; auto-sleep timing can be configured. Its *Keep Wi-Fi On During Sleep* mode maintains connectivity for file work, **not proof of remote wake**. [Sony power and sleep](https://helpguide.sony.net/dpt/rp1/v1/en/contents/TP0001178333.html), [Sony Wi-Fi during sleep](https://helpguide.sony.net/dpt/rp1/v1/en/contents/TP0001178315.html).
- [janten/dpt-rp1-py](https://github.com/janten/dpt-rp1-py) has a Sony-compatible remote system API on HTTP 8080 / HTTPS 8443, including status, set timeout, and open-document. It has **no proven remote wake command** for first-generation mooInk.
- Readmoo says original mooInk Pro supports Bluetooth peripherals, but a 2026 Bluetooth page-turner explicitly lists **Pro 2/2C, not the first-generation Pro**, for its keep-Bluetooth-on one-key wake feature. [Product listing](https://www.eslite.com/product/10052161182683219539004).
- Successful Windows MTP file writes while sleeping do **not** imply USB Remote Wakeup or an electronic paper redraw request is implemented.

## Research order

1. Probe Sony-compatible TCP ports 8080 / 8443 **while awake and while asleep**; the Python `sony_wifi_probe.py` tool is read-only, tests GET `/register/information` and `/api_version`, requires a private LAN IPv4 address, and does not pair, change settings, or issue power commands.
2. If accessible while asleep, investigate any **documented, model-compatible** wake/sleep API. Do not blindly try undocumented MTP power/reset operations, driver switching or firmware flashing.
3. If pure software wake is unsupported, an **external reversible button actuator** (one short physical press to wake, a second short press to sleep) controlled by Windows USB, plus MTP image replacement, can follow the required wake/sleep workflow **without installing anything on the reader**. This is hardware button automation, not a software-based wake API; determine press timing by test and never power cycle repeatedly at high frequency.
4. Validate both 10.3-inch and 13.3-inch readers separately.

## Remaining blockers

- No confirmed remote wake or immediate sleep command in either USB MTP or Sony-compatible API.
- It is unknown whether the first-generation mooInk's Wi-Fi service remains open while asleep.
- No demonstrated high-frame-rate continuous mirroring.
