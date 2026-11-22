# Offline source and wheel backup (Windows)

## What is preserved in Git

`third_party/dpt-rp1-py/` is a **source snapshot** of [janten/dpt-rp1-py](https://github.com/janten/dpt-rp1-py), whose `pyproject.toml` currently identifies version **0.1.21**. It includes the original MIT LICENSE, README, packaging metadata, primary Python client and CLI modules. This copy belongs to the upstream authors; it is not a Readmoo official SDK and is not maintained upstream in this repository.

The snapshot allows local rebuilding **even if the original GitHub repository disappears**. It does **not** by itself guarantee a machine with no Internet can install the full Python dependency tree: `pycryptodome`, `requests`, `pbkdf2`, `urllib3`, `pyyaml`, `anytree`, `fusepy`, `zeroconf`, `tqdm`, and packaging/build dependencies can still be required.

To preserve those packages before they disappear, create a wheel bundle while the Internet is available.

## Prepare once, while online

Use **Windows CMD** in the repository root:

```cmd
scripts\build_offline_wheels.cmd
```

This produces `offline_wheelhouse\*.whl` for the installed version of Python **and** all transitive dependencies, and `offline_mooink_wheels.zip` as a portable backup. The script uses the bundled `third_party/dpt-rp1-py` source, **not** the original upstream GitHub repository.

**Back up the zip separately** (USB drive / private cloud / GitHub Releases), not as text or a regular Git commit. Wheels can be large and OS/Python-version-specific. The repository's `.gitignore` excludes the generated bundle by default.

## Install with no Internet

Unpack the wheel zip on the target Windows machine under `offline_wheelhouse\`, or keep the existing directory, then run:

```cmd
scripts\install_offline_wheels.cmd
```

This uses pip's `--no-index --find-links` options and **never downloads from PyPI or GitHub**. If a wheel is missing, the command fails, rather than silently going online.

Verify:

```cmd
python -m dptrp1.cli.dptrp1 -h
python mooink_full_cmd_demo.py --help
python mook_sleep_screen_manager.py --help
```

If you update Python (for example 3.14 to a later ABI), or switch from x64 Windows to ARM64, rebuild a matching wheelhouse before going offline.

## Two readers / pairing keys

The code never hardcodes the USB instance IDs or IP addresses. The diagnostic tool stores each reader's credentials locally under the Windows user's profile, **not in Git**. Do **not** commit private keys, registration tokens, logs containing document names, USB serial IDs or personal files.

## Scope and limitations

- The authenticated Sony-compatible Wi-Fi API can list documents even when the tested original mooInk Pro is asleep; it is **not** a proven method for wake/sleep or writing to the `sleep` directory.
- The sleep-screen manager uses Windows USB/MTP for sleep storage access. The native Wi-Fi API supports pairing and read-only discovery; sleep PNG upload is not yet verified.
- The `display-document` endpoint changes PDF pages when awake, but user's tests showed it **did not** wake the sleeping panel.
- Excel/Word conversion additionally requires locally installed Microsoft Office or LibreOffice. Those applications are **not** bundled in the offline wheel archive.
- Source is archived without cryptographic upstream attestations; review new versions before updating.
