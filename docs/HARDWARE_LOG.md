# Hardware validation log

Sanitized results of runs on real hardware. A check not listed here has not been performed.

## Unit under test

- Model: **EcoFlow DELTA 3 Classic** (confirmed by the user from the label/app), serial prefix
  `P321`, upstream class `delta3_classic`.
- Firmware version: not recorded (not exposed by the decoded fields).
- Host: Windows 11 Pro 10.0.26200, built-in Bluetooth adapter, Python 3.13.16, bleak 3.0.2.

## Run 1 — 2026-10-16 22:23–22:27, `ecoflow-diag probe --duration 240 --all-fields`

| Check (SPEC §11) | Result |
|---|---|
| 1. Discovery | **Pass.** Found by manufacturer data, RSSI −56 dBm. |
| 1. Authentication | **Pass.** ECDH session + user-ID auth completed on Windows (WinRT backend). |
| 2. Stable telemetry | **Pass for 240 s.** 656 telemetry frames (`src 0x02, cmd_set 0xFE, cmd_id 0x15`, packet v19), ~2.7/s, 0 decode errors, no disconnects. |
| 3. Values vs station display | **Pending.** Values are plausible (battery 100 %, AC in = AC out ≈ 93–151 W in bypass) but were not compared with the display or app. |
| 4. Grid state on AC unplug/replug | **Pass, one cycle.** See below. |
| Clean shutdown | **Pass.** Disconnect completed; exit code 0. |
| 5. BLE loss / reconnect | Not tested. |
| 6. Station restart, Windows sleep/resume | Not tested. |
| 7. Concurrent official app | Not tested. |

### Grid signal (AC input unplugged at ~22:24:05 for ~10 s)

| Field | Present in frames | Behaviour |
|---|---|---|
| `plug_in_info_ac_charger_flag` (upstream `plugged_in_ac`) | 219/656 (~1 per 1.1 s) | True → **False at 22:24:09** → **True at 22:24:19** |
| `plug_in_info_ac_in_flag` (not mapped upstream) | 219/656 | 1 → 0 → 1 in the same frames as above |
| `flow_info_ac_in` (not mapped upstream, proposed in upstream #469) | 218/656 | **Constant 12** (`flow_is_on` = False) throughout, including while AC was charging/bypassing. **Not usable on this unit.** |
| `pow_get_ac_in` | 218/656 | > 0 → 0 → > 0, same frames |

The battery stayed at 100 % and the load moved to battery without interruption (AC out 113 W
while AC in was 0 W).

Conclusion for this unit and firmware: `plugged_in_ac` is a usable grid signal and reacts within
one telemetry period (≤ ~5 s at the probe's sample rate).

Not yet covered:

- The case "plugged in, AC input 0 W" was not reproduced, because a load was always bypassing
  through. That case would show the flag is independent of input power. To test it, keep AC
  plugged in, switch off every load on the AC outputs, and wait until AC in reads 0 W.
- Only one unplug cycle was recorded, so debounce parameters still need more data.

### Observed failure (before run 1)

A probe started while another `ecoflow-diag probe` from the same PC was connected:

- it "connected" in ~70 ms;
- it saw only the Generic Access and Generic Attribute services;
- upstream rejected it with `UnsupportedBluetoothProtocol`, and the station stopped
  advertising until the other session ended.

Most likely cause: Windows shares one BLE link between processes. The app must enforce a single
BLE owner (single instance) and show this error as "station busy / in use by another
connection", not as "unsupported device".

## Run 2 — 2026-10-17 11:20, `ecoflow-diag scan --timeout 8`

After discovery moved to `infrastructure/ecoflow/discovery.py` (phase 2). **Pass:** the
station was found (RSSI −64 dBm, prefix `P321`, upstream class `delta3_classic`). No
connection was made.

## Run 3 — 2026-10-17 11:33–11:34, `ecoflow-diag monitor --duration 60 --interval 10`

First run of the phase 2 stack (`MonitorService` → `EflibAdapter` → `TelemetryMapper`) on the
station. No AC or Bluetooth changes during the run.

| Check | Result |
|---|---|
| Discovery, auth | **Pass.** Connected 18 s after start: 15 s of that is the full scan used when no `--address` is given (the service then pins the station). |
| Telemetry through the adapter | **Pass.** Battery 100 %, AC in = AC out ≈ 96–99 W (bypass), grid connected, `live` for the whole run. Consistent with run 1; not compared with the display. |
| Clean shutdown | **Pass.** Disconnected at the deadline; exit code 0. Upstream logs our own disconnect as a WARNING ("Disconnected from device"); harmless. |

## Run 4 — 2026-10-17 11:42–11:52, `ecoflow-diag monitor --duration 600 --interval 15`

Alert rules with default settings (grid debounce 5 s, BLE confirmation 15 s) on the station.
Actions were done by the user; times are from the log.

| Check | Result |
|---|---|
| Grid unplug ~15 s | **Pass.** AC in 0 W / grid disconnected at 11:43:22, `ALERT grid power lost` 11:43:24; `ALERT grid power restored` 11:43:39. One alert each. |
| Grid unplug 2–3 s | **Pass for "no alert"**: none between 11:43:39 and the next action. Snapshots are printed every 15 s, so the log does not show whether the station reported the short unplug at all. |
| Bluetooth off in Windows ~30 s | **Pass.** Link dropped 11:44:49 → `reconnecting`; retries failed as `bluetooth_unavailable` at 11:44:51, 11:44:55, 11:45:03 (growing backoff); one `ALERT BLE connection lost at 11:44:49` at 11:45:05 (16 s). |
| Bluetooth back on | **Pass.** Reconnected 11:45:22, `ALERT BLE connection restored after 33s`. The snapshot printed during the outage showed the last values as `STALE, last update 11:44:48`. |
| Second concurrent session (separate `monitor`, 11:45:33–11:46:23) | **`not_found`, not `busy`.** A connected station stops advertising, so a second scan does not see it. The first session was unaffected. The `busy` path (run 1 note) needs Windows to still hold the device; it is not what a second app instance normally sees. |

Whole run: exactly 4 alerts (the ones above), no errors or tracebacks, telemetry `live` from
11:45:22 to the deadline, clean disconnect at 11:52:52, exit code 0.

Consequence for phase 3: `not_found` must tell the user the station may be connected to another
device (EcoFlow app, another instance), and single-instance enforcement remains necessary.

Still not covered: grid change while BLE is down; station restart; Windows sleep/resume; the
official app connected at the same time; "plugged in, AC input 0 W".

## Run 5 — 2026-10-17 12:09, desktop app (phase 3) against the station

`python -m ecoflow_companion` with the Qt offscreen platform (no window on screen), temporary
settings/log folders, user ID from the environment, stopped after 25 s.

| Check | Result |
|---|---|
| BLE from the service thread while Qt runs the main thread | **Pass.** Connected and authenticated 17 s after start (15 s full scan: no station pinned yet). |
| Widget and tray content | **Pass.** "Delta 3 Classic · Connected · 100% · AC in 99 W · AC out 99 W · Grid connected"; same in the tray tooltip. |
| Station pinned in settings | **Pass.** Address, serial and model saved after the first connection. |
| Log redaction | **Pass.** Address masked in every upstream line; no user ID or serial in the log. |
| Exit | **Pass.** Disconnect requested and completed at once (same log second); exit code 0. |

Not yet done: autostart after a real Windows login, sleep/resume (see run 6 for the visible
window).

## Run 6 — 2026-10-17 12:12, desktop app with a visible window

`ecoflow-companion.exe` started normally (user ID from the environment). Connected at 12:12:35
and pinned the station. The user confirmed that everything works, covering the suggested
checks: drag and position restore after restart, always on top, tray click to hide/show, Alt+F4
hides to the tray, and Bluetooth off (grey values with last update and hint) / on (reconnects).

## Run 7 — 2026-10-17 ~12:26, Windows toast notifications (phase 4)

On this PC (Windows 11 Pro 10.0.26200, development mode, not packaged):

- App ID registered under `HKCU\Software\Classes\AppUserModelId\EcoFlowCompanion.Desktop`.
- `ToastNotifier.Setting` raises "Element not found" (0x80070490) for this app ID, which has no
  Start-menu shortcut; the notifier reports the setting as unknown instead of failing.
- A test toast was accepted by `Show`, and it was then present in the notification centre
  history for the app ID (`ToastNotificationManager.History`), i.e. Windows displayed it.

## Run 8 — 2026-10-17 12:27–12:31, desktop app with notifications, AC unplug

`ecoflow-companion.exe` (development install). Connected in 1.5 s with the station pinned
(was 17 s with a full scan).

| Check | Result |
|---|---|
| Grid unplug ~10 s | **Pass.** `grid power lost` at 12:30:12 → toast "accepted by Windows"; `grid power restored` at 12:30:22 → toast accepted. The user confirmed both toasts appeared. |
| Notification setting | Readable this time (enabled); in run 7, before the first toast, Windows could not report it. |
| Settings › Send test notification | Not exercised (no log entry). |
| BLE lost / restored toasts | Not exercised in this run (the alert logic itself passed in run 4). |

## Run 9 — 2026-10-17 12:38–12:42, packaged build (phase 5)

`dist\EcoFlowCompanion` built with PyInstaller 6.22.3 (one folder, 109 MB), run on the same PC.

| Check | Result |
|---|---|
| `ecoflow-diag.exe scan` | **Pass.** Station found and matched to `delta3_classic`: eflib's device classes load from the bundled `.py` files. |
| `EcoFlowCompanion.exe --test-notification` | **Pass.** Exit code 0; the toast "Notifications are working." is in the notification centre history. |
| First start without a user ID | **Pass.** Widget asks for the user ID (`no_credentials`), no connection attempt. |
| User ID entered in Settings | **Pass.** Stored with DPAPI; connected 2 s later (12:40:33). |
| "Start with Windows" | **Pass.** Run entry = `"D:\Projects\untitled\dist\EcoFlowCompanion\EcoFlowCompanion.exe"`; not disabled in Windows' startup-apps list. |
| Start as Windows does at login | **Pass, simulated.** After a forced kill (stale single-instance name), the exact Run command was started from `C:\Windows\System32` without `ECOFLOW_USER_ID`: user ID read from storage; first attempt `not_found` (station still releasing the killed session), connected on the retry 22 s after start. |
| Real sign-in / reboot | **Pending.** Cannot be done from inside this session. |
| Sleep / resume | **Pending.** |

Rebuilt at 12:48 with the LGPL/GPL texts added: `ecoflow-diag.exe scan` again matched
`delta3_classic`, `--test-notification` exited 0, and the app started through the Run-entry
command connected in 2 s (12:49:00). Sleep/resume was not tested in this session; the real
sign-in start is still pending.

## Run 10 — 2026-10-17 13:15, Windows sleep / resume (packaged build)

Windows event log: sleep 13:15:21.8 → wake 13:15:28.9 (7 s, woken by USB input).

| Check | Result |
|---|---|
| Link on sleep | Dropped at sleep entry (13:15:22, `link_lost`), state `reconnecting`. |
| Reconnect after wake | **Pass.** Connected 13:15:33, 4.7 s after wake. bleak logged a harmless "unhandled services changed event". |
| Alerts | None, as designed: the outage was shorter than the 15 s confirmation. |

Finding: on this PC `time.monotonic()` (QueryPerformanceCounter) counts through sleep — it was
level with GetTickCount64 and ~94 500 s (≈26 h of past sleep) ahead of
QueryUnbiasedInterruptTime. With it, an overnight sleep would have produced "Station
disconnected" and "reconnected after 8 h" on every wake. Fixed: the app and `monitor` now time
outages, debouncing, staleness and the silent-link watchdog with QueryUnbiasedInterruptTime,
which excludes sleep (`infrastructure/windows/clock.py`). Rebuilt and restarted 13:19, connected
in 1.8 s. **A long (overnight) sleep with the fix is still to be observed.**
