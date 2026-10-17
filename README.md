# EcoFlow Companion for Windows

Local BLE desktop widget and notifications for EcoFlow power stations. Work in progress. The
current state is SPEC phase 5: the desktop widget, tray icon, settings and Windows notifications
run on top of the connection service and alert rules, and the app builds into a Windows package
that needs no Python (see [Building the Windows package](#building-the-windows-package)).

- Specification: [SPEC.md](SPEC.md)
- Upstream audit: [docs/AUDIT.md](docs/AUDIT.md)
- Design and alert-rule behaviour: [docs/DESIGN.md](docs/DESIGN.md)
- Hardware results: [docs/HARDWARE_LOG.md](docs/HARDWARE_LOG.md)

## Model compatibility

| Model | Status |
|---|---|
| DELTA 3 Classic | Discovery, authentication, telemetry and the grid flag verified on one unit (see hardware log). Full validation is pending. |
| DELTA 3 Lite | Not supported upstream; not validated. |
| Other DELTA 3 models | Supported upstream; **not validated** by this project. |

## Prerequisites

- Windows 10/11 x64 with Bluetooth LE, turned on.
- Python 3.13. The upstream protocol library requires it; 3.12 does not work.
- [uv](https://docs.astral.sh/uv/). Install it with `pip install uv`, then use `python -m uv`
  if `uv` is not on PATH.
- The station bound to your EcoFlow account in the official app, and your numeric EcoFlow
  **user ID**. To find it, see the upstream wiki section "Finding User ID Manually" and upstream
  issue #130. This tool never contacts the EcoFlow cloud.

## Setup

```powershell
uv python install 3.13
uv sync
```

## Desktop app

```powershell
# Normal start, no console window
.venv\Scripts\ecoflow-companion.exe

# Development: simulated station, verbose log also on the console
.venv\Scripts\python.exe -m ecoflow_companion --mock --debug
```

On first start, open **Settings** (right-click the widget, or the tray icon) and enter your
EcoFlow user ID. With one station in range, the app connects to it and pins it; with several,
press **Scan** in Settings and pick one.

- **Widget:** drag to move (the position is saved). Right-click for Settings, Reconnect, Open
  logs, Always on top, Hide and Exit. Closing it (Alt+F4) hides it to the tray unless that is
  turned off in Settings.
- **Tray icon:** click to show/hide the widget. The icon shows the battery level; a red slash
  means the data is not live.
- **Unknown and stale data:** unknown values show as `—`, never `0`. When the data is not live
  (disconnected, or nothing received for 30 s), the values turn grey and the widget shows the
  last update time and the reason, e.g. "Station not found: is it on, nearby, and not connected
  to the EcoFlow app?".
- **One copy only:** starting the app again shows the running copy's widget. A station accepts
  one BLE session, so close the EcoFlow phone app while the companion is connected.
- **Notifications:** Windows toasts for low battery (30/20/10 % by default), grid power lost /
  restored, and the station disconnected / reconnected. Each can be turned off in Settings,
  which also has **Send test notification**. If nothing appears, check Windows Settings ›
  System › Notifications (the app is listed as "EcoFlow Companion") and Focus assist / Do not
  disturb. The app registers its toast identity under
  `HKCU\Software\Classes\AppUserModelId\EcoFlowCompanion.Desktop`. If toasts cannot be set up,
  it falls back to tray-icon notifications.
- **Start with Windows:** a checkbox in Settings. It adds or removes an `EcoFlowCompanion` value
  under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; no admin rights needed.

Where data is kept (per Windows user):

| What | Where |
|---|---|
| Settings (JSON) | `%APPDATA%\EcoFlow Companion\settings.json` |
| User ID, encrypted with Windows DPAPI | `%APPDATA%\EcoFlow Companion\user_id.dpapi` |
| Logs, 4 × 1 MB max, address/serial/user ID masked | `%LOCALAPPDATA%\EcoFlow Companion\logs` |

`ECOFLOW_USER_ID`, if set, overrides the stored user ID. An unreadable `settings.json` is kept
as `settings.invalid-<time>.json` and defaults are used; single invalid values fall back to
their defaults. Advanced timings (`monitoring` section) can only be changed in the JSON file and
take effect on the next start.

### Troubleshooting

| Widget says | Try |
|---|---|
| Bluetooth is off or unavailable | Turn Bluetooth on in Windows Settings › Bluetooth & devices. The app reconnects by itself. |
| Station not found | Station on and within a few metres; EcoFlow phone app closed or its Bluetooth off. |
| Station busy | Another program holds the station's BLE session; close it. |
| Station rejected the user ID | Check the user ID in Settings; the station must be bound to that account. |
| Several stations in range | Settings › Scan, choose one. |

Open logs from the widget menu for details.

## Diagnostic tool

```powershell
# List nearby stations and whether upstream has a decoder for them
.venv\Scripts\ecoflow-diag.exe scan

# Connect, authenticate and print telemetry; prompts for the user ID (hidden input)
.venv\Scripts\ecoflow-diag.exe probe --duration 120 --all-fields

# Run the monitor service: reconnects, staleness and alert rules, printed as events
.venv\Scripts\ecoflow-diag.exe monitor --duration 600

# The same with a simulated station (no hardware, no user ID)
.venv\Scripts\ecoflow-diag.exe monitor --mock --duration 300 --interval 5
```

- Alternatively, set the user ID in the `ECOFLOW_USER_ID` environment variable. It is never
  accepted as an argument and never printed.
- The output masks the BLE address, serial number and user ID.
- Close the EcoFlow app, the desktop app and any other probe before connecting. A station
  accepts one BLE session; while connected it stops advertising, so a second session usually
  reports "not found" (once it failed with "unsupported protocol for notify" instead).
- `--debug` turns on verbose connection logging (also redacted).
- `--experimental-profile MODULE` forces an upstream decoder for a model upstream does not
  recognise. Its values are labelled UNVERIFIED.
- `monitor` accepts `--thresholds 30,20,10`, `--hysteresis`, `--grid-debounce`,
  `--link-confirm` and `--stale-after` to try alert settings. It prints a snapshot every
  `--interval` seconds and alerts (`ALERT ...`) as they happen. Rule semantics are in
  [docs/DESIGN.md](docs/DESIGN.md).

## Building the Windows package

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

This installs the `build` dependency group (PyInstaller), draws the icon, and builds
`dist\EcoFlowCompanion\` — a folder, not a single file, so Qt's LGPL libraries stay replaceable
and start-up is fast. It contains:

- `EcoFlowCompanion.exe` — the app, no console window;
- `ecoflow-diag.exe` — the diagnostic CLI (`scan`, `probe`, `monitor`), with a console;
- `LICENSES.md` and the bundled packages' license files under `_internal`.

The folder needs no Python. Copy it anywhere (e.g. `%LOCALAPPDATA%\Programs\EcoFlow Companion`)
and start `EcoFlowCompanion.exe`. `EcoFlowCompanion.exe --test-notification` sends one toast and
exits with 0 if Windows accepted it. `--debug` writes verbose logs; `--mock` uses the simulated
station.

The spec keeps `eflib` as `.py` files inside the package: eflib finds its device classes by
listing those files, and without them every station would be reported as unsupported.

**Updating:** exit the app (tray › Exit), replace the folder, start it again. Settings and the
stored user ID are kept. If the folder moved, open Settings and press OK once with "Start with
Windows" on, so the login entry points to the new place.

**Removing:** exit the app, turn off "Start with Windows" (or delete the `EcoFlowCompanion` value
under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`), delete the program folder, and
optionally delete `%APPDATA%\EcoFlow Companion`, `%LOCALAPPDATA%\EcoFlow Companion` and the
key `HKCU\Software\Classes\AppUserModelId\EcoFlowCompanion.Desktop`.

## Tests and lint

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check src tests packaging
.venv\Scripts\ruff.exe format --check src tests packaging
```

## Licenses

The BLE protocol code in `src/ecoflow_companion/_vendor/eflib` is an unmodified copy of
[rabits/ha-ef-ble](https://github.com/rabits/ha-ef-ble) v1.2.1 under the Apache License 2.0. See
`src/ecoflow_companion/_vendor/README.md` for details. The UI uses PySide6 (Qt for Python) under
the LGPL v3. All redistributed components and their licenses: [LICENSES.md](LICENSES.md).
