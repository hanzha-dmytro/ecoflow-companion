# Design: domain, services and alert rules (SPEC phase 2)

## Layers

```text
presentation (phase 3)          widget, tray, settings — consumes DomainEvents
        │
application/                    MonitorService, EventRules, ReconnectPolicy
        │  ports.StationAdapter (interface)
infrastructure/ecoflow/         EflibAdapter, TelemetryMapper, discovery, MockStationAdapter
        │
_vendor/eflib                   upstream BLE protocol library (unmodified)
```

`domain/` (models, events, `TelemetryTracker`) imports nothing from the other layers.
`application/` depends only on `domain/` and the `StationAdapter` port. Only `infrastructure/`
imports `eflib` or `bleak`.

## Data flow

1. `EflibAdapter` registers upstream's `on_message_processed` hook. For each decoded protobuf
   message, `TelemetryMapper` emits a `Telemetry` containing **only the fields present in that
   message** (`proto_has_attr`). Upstream keeps the last value of every field on the device
   object, so reading the object directly cannot tell a fresh value from a carried-over one.
2. `MonitorService` merges updates in `TelemetryTracker` (for display) and feeds them to
   `EventRules` (for alerts).
3. Every event goes to one sink callback on the event-loop thread. The UI (phase 3) will
   marshal it to the Qt thread.

Field mapping and normalization (`telemetry_mapper.py`):

| Domain | Upstream field | Normalization |
|---|---|---|
| `battery_percent` | `battery_level` (`cms_batt_soc`) | rounded half up to an integer; outside 0–100 → unknown |
| `ac_input_watts` | `ac_input_power` (`pow_get_ac_in`) | 1 decimal; non-finite → unknown |
| `ac_output_watts` | `ac_output_power` (`pow_get_ac_out`, sign flipped by upstream) | as above |
| `ac_plugged_in` | `plugged_in_ac` (`plug_in_info_ac_charger_flag`) | booleans only |

`None` always means unknown. `0 W` and `False` are values.

## Connection lifecycle (`MonitorService`)

- One session at a time; upstream's own reconnect is disabled.
- Statuses: `connecting` (first attempt or after a manual reconnect), `connected`,
  `reconnecting` (automatic retry or waiting for one; carries the last error), `disconnected`
  (stopped, or a failure retrying cannot fix).
- Retries use `ReconnectPolicy`: 2 s, 4 s, 8 s … capped at 60 s, ±10 % jitter. The backoff
  restarts only after a session **delivers telemetry**, so a station that accepts connections
  and drops them at once is still retried with growing delays.
- Not retried until the user acts (`request_reconnect()`): no user ID, several stations and
  none selected, unsupported model, authentication rejected. Retried: not found, busy,
  timeout, Bluetooth unavailable, link lost, other errors.
- `busy` is reported when upstream raises `UnsupportedBluetoothProtocol`, which is what
  happened once when a second session from the same PC tried to connect. Usually a second
  session gets `not_found` instead, because a connected station stops advertising
  (HARDWARE_LOG.md, run 4); the UI has to say the station may be in use elsewhere.
- After the first connection with no station selected, the target is pinned to that station's
  address and serial, so a reconnect never picks up a different station in range.
- A connected session that delivers no telemetry for 90 s is treated as dead and reconnected
  (reason `no_telemetry`).
- Stop by cancelling the `run()` task; it disconnects before returning.

## Time

Durations (staleness, grid debounce, outage confirmation, silent-link watchdog) use a monotonic
clock that **stops while Windows sleeps** (`QueryUnbiasedInterruptTime`,
`infrastructure/windows/clock.py`). `time.monotonic()` keeps counting through sleep on Windows,
which would turn every overnight sleep into an 8-hour "outage" with alerts on wake. Displayed
times are local wall-clock time. Retry delays are plain `asyncio` sleeps; after a wake they
simply fire at once.

## Staleness

The snapshot is **stale** when no telemetry arrived in the last 30 s, or the link is down.
Stale snapshots keep the last values (never zeroed) together with `last_update`, so the widget
can show them as stale with a time, never as current. Staleness is not a BLE event: it never
produces a link alert by itself (only the 90 s watchdog above ends a silent session).

## Alert rules (`event_rules.py`)

All rules are pure functions of the updates and times they are given, and are unit-tested
without hardware.

### Low battery (defaults: thresholds 30/20/10 %, hysteresis 3)

- A threshold fires when a reported level is **at or below** it, then disarms.
- It re-arms when a reported level is **at or above threshold + hysteresis** (23 % for the
  20 % threshold).
- One reading that crosses several thresholds gives one alert, for the lowest of them.
- **Startup:** the first reading disarms every threshold it is already at or below and gives
  one alert marked `initial` for the lowest of them (15 % at start → one alert at the 20 %
  threshold; the 10 % threshold still fires later).
- Missing values are ignored. Armed state survives BLE outages, so a level that dropped while
  disconnected is alerted when telemetry resumes.

### Grid lost / restored (default debounce 5 s)

- Source: the station's AC-plugged flag only. AC input power is never used; an approximate
  mode based on input power is **not implemented**.
- A new flag value is confirmed when it has been the latest reported value for 5 s **and a
  later update arrives** with no contrary report. Confirmation never happens on a timer, so
  silence cannot confirm anything.
- The first confirmed value is a silent baseline.
- A pending value is dropped when the link is lost; the baseline is forgotten when telemetry
  goes stale (30 s). A BLE outage therefore never produces a grid alert, and a grid change
  during an outage longer than 30 s is not alerted (the widget still shows the current state).

Measured on the DELTA 3 Classic: the flag is reported about once per second even when it does
not change, and follows AC unplug/replug (HARDWARE_LOG.md).

### BLE connection lost / restored (default confirmation 15 s)

- An outage starts when an established session drops (or the 90 s watchdog ends it).
- `LinkLost` is sent once, when the outage has lasted 15 s without telemetry resuming. Shorter
  blips produce no alert at all. Retries inside the outage never alert again.
- `LinkRestored` is sent with the first telemetry after a confirmed outage, with its duration.
- Failing to connect at startup is not an outage, and neither is a reconnect the user asked
  for.

### Notifications (`notification_service.py`, phase 4)

- `NotificationService` applies the toggles (master switch; grid; BLE lost; BLE restored —
  battery alerts follow the master switch and the thresholds), writes the text, and drops a
  repeat: the same alert as the previous one in its category (battery: same threshold) within
  15 min. The rules never repeat by themselves; repeats come from rules rebuilt after a
  settings change. Alternating alerts (lost → restored → lost) are never dropped.
- Texts name the station and add the battery level when it is live; an unknown level is left
  out, not shown as 0.
- Delivery: Windows toasts through WinRT (`Windows.UI.Notifications`) under the app ID
  `EcoFlowCompanion.Desktop`, registered per user in
  `HKCU\Software\Classes\AppUserModelId` with a display name and icon. Toasts are tagged by
  category, so a newer grid/link/battery toast replaces the older one in the notification
  centre.
- Honest results: a toast blocked by the user's or a policy's notification setting is
  reported as not shown. For an app without a Start-menu shortcut Windows cannot report that
  setting ("element not found"); the result then says the setting is unknown. A refused
  `Show` is reported as a failure; `Failed` events are logged. "Accepted" never claims the user
  saw it (Focus assist can hide toasts without telling the app).
- Fallback when WinRT or the registration is unavailable: the tray icon's balloon, reported as
  "handed to the tray (no delivery status)".

## Desktop application (phase 3)

```text
GUI thread (Qt)                               service thread (asyncio, daemon)
AppController ── settings, storage            MonitorService ── EflibAdapter ── eflib/bleak
  ├─ DesktopWidget  ◄── render(StationView)        │
  ├─ TrayController ◄──┘                           │ sink = ServiceHost.event_received.emit
  └─ ServiceHost ─── call_soon_threadsafe ──►      │
        ▲                                          │
        └──────── queued Qt signal (DomainEvent) ◄─┘
```

- Qt owns the main thread. The monitor service runs in its own asyncio loop on a daemon
  thread, so scanning, connecting and timeouts never block the UI. bleak's WinRT backend was
  verified to scan from such a thread while the Qt main thread stays responsive.
- Events cross to the GUI thread as a queued Qt signal; commands (reconnect, new rules, new
  station, scan) go the other way with `call_soon_threadsafe`. Widget handlers never touch BLE.
- `presentation/view_models.py` turns snapshot + connection state + station into display text
  and tones without Qt, so display rules are unit-tested: unknown is `—`; stale values are
  grey with "Last update HH:MM:SS"; the failure reason is shown as a hint; the badge
  `Unverified model` / `SIMULATED` marks stations that are not hardware-tested.
- Exit (menu, tray, or Windows logoff via `aboutToQuit`) saves settings, cancels the service
  (which disconnects BLE) and waits up to 10 s.
- Settings changes apply live: new thresholds replace the rules (their state restarts), a new
  station or user ID reconnects, always-on-top and autostart apply at once. The `monitoring`
  timings apply on the next start.
- On the first connection with no station selected, the station's address and serial are saved,
  so later connections find exactly that station and stop scanning as soon as it is seen.
- One copy per Windows user (`QLocalServer`): a second start only asks the first to show its
  widget. The request is the connection itself, so it cannot be lost to timing.
- The user ID is stored with DPAPI (current Windows user, application-specific entropy), never
  in `settings.json` or the logs; the logs pass through the same redactor as the CLI.

## Development without hardware

`MockStationAdapter` plays a synthetic timeline: the grid alternates 40 s on / 25 s off, the
battery charges or drains 0.2 % per second, and the link drops for 25 s every 150 s. Its
station is labelled `simulated`. Try it with `ecoflow-diag monitor --mock`.
