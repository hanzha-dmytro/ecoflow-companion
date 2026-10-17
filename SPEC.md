# EcoFlow Companion for Windows — Technical Specification

**Document:** `SPEC.md`  
**Version:** 1.0  
**Status:** MVP specification  
**Target platform:** Windows 10/11 x64  
**Primary goal:** Display live EcoFlow power-station status in a lightweight desktop widget and deliver Windows notifications.

---

## 1. Product goal

Build a native-feeling Windows desktop utility called **EcoFlow Companion**. It connects directly to a compatible EcoFlow power station over Bluetooth Low Energy (BLE), displays telemetry in a desktop widget, and notifies the user about:

- Low battery.
- Confirmed grid power loss and restoration, when the device exposes a reliable signal.
- Lost BLE connection and successful reconnection.

The application must work locally without requiring an EcoFlow cloud account or internet access during normal monitoring.

The initial target device is **EcoFlow DELTA 3 Lite**. Do not assume that protocol support for DELTA 3, DELTA 3 Classic, or DELTA 3 Plus automatically implies support for DELTA 3 Lite. Verify the exact model and protocol before claiming compatibility.

## 2. Required engineering approach

Before implementing the UI, inspect the upstream project:

- Repository: https://github.com/rabits/ha-ef-ble

Determine:
1. Its current supported-device list and whether DELTA 3 Lite is explicitly supported.
2. License and redistribution requirements.
3. Python version and dependency constraints.
4. Whether its protocol/client code can be reused independently of Home Assistant.
5. How scanning, pairing/authentication, BLE transport, telemetry decoding, reconnects, and shutdown are implemented.
6. Which telemetry fields are available from BLE, particularly battery percentage, AC input power, AC output power, and grid/AC-plugged state.
7. Whether it works on Windows with the selected dependency versions.

Do not fabricate support, sensor names, protocol details, API methods, or test results. If exact-model compatibility cannot be verified without hardware, document that explicitly and implement a clear adapter boundary so compatibility can be tested later.

Prefer reusing the upstream implementation in accordance with its license. Avoid copying large amounts of code or reimplementing cryptography/protocol details unnecessarily. If the integration is tightly coupled to Home Assistant, extract only the minimum required logic into a small adapter while preserving license notices. Do not create a second BLE connection/client to the same station.

## 3. MVP scope

### 3.1 Must have

- Direct BLE connection.
- Connection status visible in the widget and system tray.
- Battery percentage.
- AC input power, when available.
- AC output power, when available.
- Reliable grid/AC-plugged state, when exposed by the device.
- Desktop widget that can be moved and whose position is saved.
- Windows system tray icon and context menu.
- Configurable low-battery thresholds.
- Notifications for low battery, confirmed grid state transitions, BLE disconnection, and reconnection.
- Automatic reconnect with bounded exponential backoff.
- Clear stale-data / unknown-state handling.
- Launch on Windows login, configurable by the user.
- Local configuration storage.
- Logs useful for troubleshooting, without exposing secrets.
- Unit tests for domain logic and event rules.
- A Windows executable build and clear run/build instructions.

### 3.2 Explicitly out of scope for MVP

- Controlling AC/DC/USB outputs or changing station settings.
- Cloud API integration.
- Remote access or telemetry upload.
- User accounts.
- Advanced charts, long-term analytics, or forecasting.
- Battery charging/discharging automation.
- Automatic PC shutdown.
- Running a Home Assistant server as a requirement.

## 4. Technology choices

Use these as defaults, but validate them against the upstream integration before locking dependencies:

- **Python:** a version compatible with the current upstream integration and its BLE dependencies; prefer Python 3.12 if verified compatible.
- **UI:** PySide6.
- **BLE:** reuse the upstream BLE implementation. Use Bleak/Windows WinRT only if compatible with the upstream design and required by the chosen adapter.
- **Configuration:** JSON or Pydantic Settings; keep the configuration local.
- **Windows notifications:** use a reliable Windows Toast implementation compatible with packaged/unpackaged execution. Verify notification behavior in the actual build.
- **Tests:** pytest; pytest-asyncio if asynchronous code requires it.
- **Lint/format:** Ruff.
- **Packaging:** PyInstaller, unless upstream constraints suggest a better simple Windows packaging approach.

Do not add dependencies without a clear need. Pin verified versions in the project dependency lock/configuration. Do not assume a library works in a packaged Windows executable without testing it.

## 5. Architecture

Keep presentation, application logic, domain state, and device communication separated.

### 5.1 Domain layer

Define UI-independent models and events, for example:

- `Telemetry`
  - `battery_percent: int | None`
  - `ac_input_watts: float | None`
  - `ac_output_watts: float | None`
  - `ac_plugged_in: bool | None`
  - `received_at: datetime`
- `ConnectionState`
  - `connected: bool`
  - `last_successful_update: datetime | None`
  - `error: str | None`
- Domain events for telemetry updates, BLE disconnect/reconnect, low-battery threshold crossing, and grid-state transitions.

`None` means unknown/unavailable and must never be treated as zero or `False`.

### 5.2 Infrastructure layer

Implement an `EcoFlowAdapter` around the verified upstream implementation. Its public interface should be small and documented, such as:

- `scan()` / device discovery as supported by the upstream library.
- `connect()`
- `disconnect()`
- `telemetry_stream()` or an equivalent callback/event mechanism.
- `connection_state`

These are desired adapter-level contracts, not claims about existing upstream method names. Map them to the actual upstream API after inspection.

The adapter must:
- Handle authentication/pairing according to the verified protocol.
- Normalize upstream sensor values into domain models.
- Handle partial/missing values.
- Avoid blocking the UI thread.
- Shut down cleanly.
- Never log credentials, keys, or other secrets.

### 5.3 Application layer

Implement:
- `MonitorService`: owns connection lifecycle and telemetry freshness.
- `ReconnectPolicy`: bounded exponential backoff, cancellation-aware.
- `EventRules`: evaluates battery thresholds and state transitions.
- `NotificationService`: sends Windows notifications and suppresses duplicate alerts.
- `SettingsRepository`: loads, validates, and persists local settings.

### 5.4 Presentation layer

Implement:
- `DesktopWidget`: compact, movable, readable widget.
- `TrayController`: tray icon and menu.
- `SettingsDialog`: device selection, thresholds, launch-at-login, widget behavior, and notification toggles.
- View models or signals to deliver state changes from application services to the UI.

Never call BLE operations directly from widget event handlers. Do not block the Qt UI thread with scanning, connecting, or telemetry reads.

## 6. Suggested project layout

Adapt this layout if upstream constraints require it, but keep equivalent boundaries.

```text
ecoflow-companion/
├── pyproject.toml
├── README.md
├── SPEC.md
├── LICENSES.md
├── src/
│   └── ecoflow_companion/
│       ├── __init__.py
│       ├── main.py
│       ├── app.py
│       ├── config.py
│       ├── domain/
│       │   ├── models.py
│       │   ├── events.py
│       │   └── state_machine.py
│       ├── application/
│       │   ├── monitor_service.py
│       │   ├── notification_service.py
│       │   ├── reconnect_policy.py
│       │   └── event_rules.py
│       ├── infrastructure/
│       │   ├── ecoflow/
│       │   │   ├── adapter.py
│       │   │   ├── connection.py
│       │   │   └── telemetry_mapper.py
│       │   ├── windows/
│       │   │   ├── toast.py
│       │   │   └── autostart.py
│       │   └── storage/
│       │       └── settings_repository.py
│       ├── presentation/
│       │   ├── widget.py
│       │   ├── tray.py
│       │   ├── settings_dialog.py
│       │   └── view_models.py
│       └── resources/
│           └── icons/
└── tests/
    ├── unit/
    │   ├── test_event_rules.py
    │   ├── test_state_machine.py
    │   ├── test_reconnect_policy.py
    │   └── test_settings.py
    └── integration/
        └── test_telemetry_mapper.py
```

## 7. Widget requirements

The widget should display, when available:

- Device name/model.
- Battery percentage and a compact battery indicator.
- AC input power in watts.
- AC output power in watts.
- Grid state: `Grid connected`, `Grid disconnected`, or `Unknown`.
- BLE state: `Connected`, `Reconnecting`, or `Disconnected`.
- Last successful telemetry update when data is stale.

Behavior:
- Movable with the mouse.
- Position saved between launches.
- Optional always-on-top mode.
- Compact design with readable contrast.
- Context menu for Settings, Reconnect, Open logs (if practical), and Exit.
- Closing the widget should minimize to tray by default; provide an explicit Exit action.
- Do not present stale readings as current. Show a stale/disconnected indicator and last-update time.
- Unknown values should be rendered as `—` or `Unknown`, not `0`.

Avoid overdesigning. Prioritize clarity, low resource usage, and reliable behavior.

## 8. Notification rules

All notification rules must be configurable and testable without physical hardware.

### 8.1 Low battery

- Provide configurable thresholds; default to 30%, 20%, and 10%.
- Trigger when a fresh valid battery reading crosses a threshold downward.
- Do not trigger repeatedly for every telemetry update below the same threshold.
- Define and test a reset/re-arm policy. A reasonable default is to re-arm a threshold only after the battery rises above that threshold plus a configurable hysteresis (default 3 percentage points).
- Ignore missing/stale battery values.
- Avoid emitting all lower-threshold notifications at once when the application starts while the battery is already low. Define startup behavior and document it; prefer one initial low-battery notification at the most relevant threshold or wait for a new crossing.

### 8.2 Grid power loss/restoration

- Prefer an explicit, verified `AC Plugged In` / grid-connected signal if the exact device exposes it reliably.
- Emit a notification only for a confirmed state transition.
- Debounce transitions using consecutive fresh readings or a suitable stability window to prevent transient alerts.
- Do not infer grid loss solely from `AC input = 0 W` unless the user explicitly enables an approximate mode. Zero input power can have other causes.
- When the state is `None`, stale, or unknown, do not report grid loss/restoration.
- BLE loss must not be reported as grid loss.

### 8.3 BLE loss/reconnection

- Distinguish an actual BLE disconnect from telemetry staleness.
- If the connection drops, notify once per outage after the loss is confirmed.
- Reconnect automatically using bounded exponential backoff and a maximum delay (for example, 60 seconds), with cancellation and clean shutdown support.
- Notify once when connection is restored and valid telemetry resumes.
- Do not spam notifications for each retry.
- If the station is powered off or out of range, keep the UI in a clear disconnected/reconnecting state.

### 8.4 Windows notification behavior

- Use native Windows notifications where practical.
- Verify notifications from both development mode and the packaged executable.
- If notification delivery fails, log a non-sensitive diagnostic and keep the widget functional.
- Do not claim a notification was delivered if the host API reports failure.

## 9. Configuration

Persist settings in a per-user application data directory, not in the installation directory. Validate configuration on startup and recover safely from malformed files.

Example configuration (adapt as implementation evolves):

```json
{
  "device": {
    "preferred_address": null,
    "preferred_name": null
  },
  "widget": {
    "always_on_top": false,
    "close_to_tray": true
  },
  "notifications": {
    "enabled": true,
    "battery_thresholds": [30, 20, 10],
    "battery_hysteresis_percent": 3,
    "grid_state": true,
    "ble_disconnect": true,
    "ble_reconnect": true
  },
  "monitoring": {
    "stale_after_seconds": 30,
    "reconnect_max_delay_seconds": 60
  },
  "startup": {
    "launch_at_login": false
  }
}
```

Do not store EcoFlow cloud credentials; the MVP must not use the cloud. If BLE authentication requires local secrets, follow the upstream library's documented storage approach and protect sensitive data appropriately. Do not place secrets in source control or logs.

## 10. Reliability and lifecycle

- Use one owner for the BLE connection.
- Use asynchronous I/O or a dedicated worker so the GUI remains responsive.
- Ensure orderly shutdown: stop reconnect loop, disconnect BLE, stop workers, close the Qt application.
- Handle Bluetooth being disabled, device out of range, station power-off, failed authentication, and temporary Windows BLE errors.
- Avoid uncontrolled reconnect loops.
- Track last successful telemetry time using a monotonic clock for timeout calculations; use wall-clock timestamps for display/logging.
- The application must continue running when the widget is closed to tray, unless the user selects Exit.
- Prevent duplicate application instances if feasible.
- Logs should rotate or be size-limited and must not include secrets.

## 11. Testing strategy

### Unit tests
- `None` values remain unknown, not zero/false.
- Battery threshold crossings, hysteresis, and duplicate suppression.
- Startup behavior when the battery is already below a threshold.
- Grid-state debounce and transitions.
- BLE disconnect/reconnect event suppression.
- Telemetry staleness.
- Reconnect backoff and cancellation.
- Configuration validation and recovery from invalid configuration.

### Integration tests
- Map real upstream telemetry fields into domain models using captured, sanitized fixtures where permitted.
- Verify adapter behavior with a mock BLE client.
- Test notification service behind an injectable interface.

### Hardware validation
On the exact target model, verify:
1. Discovery and authentication.
2. Stable telemetry updates.
3. Battery/input/output values against the official app or device display.
4. Grid state before and after unplugging AC input, if safe and practical.
5. BLE loss and reconnect.
6. Behavior after station restart and Windows sleep/resume.
7. Whether the official EcoFlow app can connect concurrently. Do not assume concurrent BLE sessions are supported.

Do not claim hardware validation unless it has actually been performed. If hardware is unavailable, clearly label hardware tests as pending.

## 12. Packaging and developer experience

Provide:
- `README.md` with prerequisites, setup, run, test, and build commands.
- Reproducible dependency versions.
- A PyInstaller build configuration.
- App icon and Windows application metadata if feasible.
- Instructions for enabling/disabling launch-at-login.
- Troubleshooting steps for Windows Bluetooth permissions, adapter availability, and device pairing.
- `LICENSES.md` listing upstream code and dependency licenses and any required notices.

The program should run without a console window in the packaged release, while retaining an easy development/debug mode.

## 13. Acceptance criteria

The MVP is accepted when:

1. The app launches on a supported Windows 10/11 x64 system.
2. It can discover and connect to the exact target station over BLE, if the device/protocol is supported.
3. It displays battery, input/output power, and connection status where those values are exposed.
4. Missing values are shown as unknown, never silently converted to zero.
5. The widget can be moved and its position persists.
6. The app can minimize to the system tray and exit cleanly.
7. Low-battery notifications obey configured thresholds and do not spam.
8. Grid notifications only fire from a reliable, fresh grid-state signal, or the UI explicitly labels the state as approximate if an opt-in fallback is implemented.
9. BLE loss and reconnection are reported separately from grid state.
10. Reconnection is bounded, cancellable, and does not freeze the UI.
11. Unit tests pass.
12. A packaged build has been tested for widget behavior, tray behavior, and notifications.
13. Documentation accurately states which exact EcoFlow models have and have not been validated.

If DELTA 3 Lite support cannot be verified, the app must still launch in a diagnostic/device-discovery mode and clearly report that target-model compatibility is unverified. Do not substitute another model's protocol silently.

## 14. Implementation plan

Implement in this order:

### Phase 0 — upstream audit
- Inspect `rabits/ha-ef-ble`, license, device support, dependencies, and architecture.
- Report whether DELTA 3 Lite is explicitly supported.
- Identify the smallest viable way to use its protocol code on Windows.
- Produce a short audit note before major implementation.

### Phase 1 — BLE proof of concept
- Build a small diagnostic command/script.
- Scan for the device, connect/authenticate, and print sanitized battery/input/output/grid telemetry.
- Add timeouts and clean shutdown.
- Test on actual hardware if available.
- Do not start the widget until this phase works or the hardware limitation is documented.

### Phase 2 — domain and services
- Add models, adapter interface, telemetry mapper, state machine, reconnect policy, and event rules.
- Add unit tests and a mock telemetry source for development without hardware.

### Phase 3 — widget and tray
- Implement the widget, tray menu, settings dialog, persistence, and application lifecycle.
- Use mock telemetry to develop UI independently of BLE.

### Phase 4 — notifications
- Implement battery, grid, and BLE notifications.
- Test debounce, stale-data handling, duplicate suppression, and re-arm behavior.

### Phase 5 — packaging and validation
- Build with PyInstaller.
- Test packaged notifications, autostart, reconnect, and Windows sleep/resume.
- Update README and license notices.

At the end of each phase, run relevant tests and summarize:
- Files changed.
- Commands/tests executed and their actual results.
- Known limitations.
- Next step.

## 15. Instructions to the coding agent

Work in the current repository. First inspect existing files and upstream implementation before creating or modifying files.

Do not:
- Assume DELTA 3 Lite is supported because another DELTA 3 model is supported.
- Invent upstream API names or sensor keys.
- Replace real BLE integration with a fake implementation and call it complete.
- silently fall back from grid state to input power without labeling it approximate.
- perform destructive repository changes without permission.
- hardcode credentials, addresses, or secrets.
- claim tests or hardware checks passed unless they were executed.

If a necessary decision is blocked, choose a safe, minimal default, document the assumption, and continue with work that can be validated independently. Ask the user only for information that cannot reasonably be discovered from the repository or environment.

**First task:** complete Phase 0 and report findings, then implement Phase 1. Do not build the full application before confirming the BLE integration path.
