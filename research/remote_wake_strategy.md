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

## Confirmed on user's first-generation device: 2026-11-21

At the actual LAN IP `192.168.1.137`, the user's **awake** and **sleeping** probes reported:

| Condition | TCP 8080 | TCP 8443 | HTTP GET /register/information on 8080 | HTTP GET /api_version on 8080 |
|---|---|---|---|---|
| Awake | open | open | 200 JSON | 200 JSON |
| Sleeping | open | open | 200 JSON | 200 JSON |

This verifies a reachable registration/metadata service while the **screen is asleep**, but **does not prove waking the panel**, availability of authenticated endpoints, or a remote standby command. In particular it is normal for an E Ink screen to stay unchanged while parts of the controller are serving network requests.

The test used an earlier example IP before the actual LAN IP was found; any former timeouts at `192.168.1.150` are non-evidence about device functionality.

### Next targeted validation

- While sleeping, run a **GET-only** HTTPS request to `https://192.168.1.137:8443/system/status/battery` and record only the HTTP status (likely authentication required). This tests authenticated-API routing, **not wake**.
- If 401/403, perform standard pairing with the owner's consent using `dpt-rp1-py`, then test whether a documented *open document* operation `PUT /viewer/controls/open2` produces a visible change during sleep. This call changes the displayed document, so it should not be treated as read-only and should be carried out only after backing up data and preparing a non-sensitive test PDF. It is **not a documented wake/sleep toggle**.
- Sony documentation supports configuring auto-sleep, but current code `/system/configs/timeout_to_standby` only changes the delay; it does not remotely trigger immediate standby. No direct wake API was found in the researched public tool.

Sony sources: https://helpguide.sony.net/dpt/rp1/v1/en/contents/TP0001178278.html and https://helpguide.sony.net/dpt/rp1/v1/en/contents/TP0001178333.html

Open-source client: https://github.com/janten/dpt-rp1-py

## Sleeping-device HTTPS authentication finding (2026-11-21)

The user ran while the original mooInk Pro remained asleep:

```
curl.exe -k -s -o NUL -w "HTTP %{http_code}\\n" --max-time 5 https://192.168.1.137:8443/system/status/battery
# HTTP 401
```

This is a server-side **unauthorized** response: the service is alive and reached the request, but the user has not authenticated. It **does not prove** the target GET route will succeed after authentication, nor that a wake/sleep/display command exists.

Verified in the current upstream `janten/dpt-rp1-py` source (dptrp1/dptrp1.py and CLI):
- Sony-compatible authenticated API uses port 8443, with private key / client ID and per-session signed nonce; a cookie named Credentials is set after `PUT /auth`.
- The CLI command `dptrp1 --addr 192.168.1.137 list-documents` will look for existing credentials and only then authenticate. It is a useful read-only validation command **once the account has been paired**.
- `dptrp1 --addr 192.168.1.137 register` initiates a **state-changing interactive pairing sequence**: it first performs `PUT /register/cleanup`, then POSTs for PIN registration and saves credentials. Do not describe this command as read-only, and never share the generated private key / client ID in logs or chat.
- Only after successful pairing should a read-only `dptrp1 --addr 192.168.1.137 list-documents` or `get-configuration` be attempted. No immediate wake/sleep API has been validated.

Source: https://github.com/janten/dpt-rp1-py

## Awake vs asleep `display-document` behavior (2026-11-21)

Using the authenticated, Sony-compatible `dpt-rp1-py` tool, the first-generation mooInk Pro was instructed to open an existing PDF:

```cmd
python -m dptrp1.cli.dptrp1 --addr 192.168.1.137 display-document "Document/Samples/快速上手v1.pdf" 1
```

- **While awake:** user confirmed the reader *does* switch to the specified PDF page. Therefore this documented viewer command is practically compatible with the reader when awake.
- **While asleep:** command returned to shell without printing an error, but the e-paper panel **did not wake or switch**.
- Upstream's `DigitalPaper.display_document` invokes `self._put_endpoint("/viewer/controls/open2", data={"document_id": document_id, "page": page})` and **discards the HTTP response** instead of calling `raise_for_status()`. No CLI output is **not proof of success or of HTTP 2xx**.
- Conclusion: the viewer's open-document endpoint is **not a verified remote wake source**. Do not propose it as a solution for the desired sleep-image refresh cycle.
- Next diagnostic (if needed): record the actual HTTP response status of the authenticated `PUT` while asleep, but avoid repeated toggles or treating UI commands as power commands. A physical wake button may be required unless another verified wake source is found.

User goal remains *MTP image transfer → remotely wake → remotely sleep → new sleep image visible*. **Do not pivot to PDF mirroring as the chosen design.**

Source code: https://github.com/janten/dpt-rp1-py/blob/master/dptrp1/dptrp1.py
