# Phase 0 — upstream audit (rabits/ha-ef-ble)

Date: 2026-10-16. Audited revision: `v1.2.1`, commit `71f5b90` (2026-10-08).

## Summary

| Question (SPEC §2) | Finding |
|---|---|
| DELTA 3 Lite explicitly supported? | **No.** Not mentioned in the README, code, upstream model-name table, or GitHub issues (searched "delta 3 lite" and "lite"). |
| Actual unit (user-confirmed) | **DELTA 3 Classic**, serial prefix `P321`. Explicitly supported upstream (`delta3_classic`). See [Target model](#target-model). |
| Grid signal | `plugged_in_ac` is unreliable across DELTA 3 models/firmware (upstream #300, #469). **On this unit it works** (one unplug cycle measured). |
| License | Apache-2.0. No NOTICE file. Redistribution needs the license text and retained attribution; modified files must be marked. |
| Python | `requires-python >=3.13`. Hard requirement: `eflib/props/updatable_props.py` imports `typing.TypeIs` (3.13+). **Python 3.12 cannot be used.** |
| Reusable without Home Assistant? | **Yes.** `custom_components/ef_ble/eflib/` has no `homeassistant` imports, and upstream tests it without HA. |
| Works on Windows with the chosen dependencies? | **Yes, on the target unit.** The library installs and its own tests pass on Windows 11 / Python 3.13. Discovery, authentication and telemetry over the WinRT backend were verified on hardware (see HARDWARE_LOG.md). |

## How upstream is packaged and how we reuse it

- Not on PyPI. The installable package `custom_components.ef_ble` imports Home Assistant in its
  `__init__.py`, so `pip install` from git and then importing `eflib` does not work without HA.
- Decision: vendor `eflib/` unmodified into `src/ecoflow_companion/_vendor/eflib/`, with its
  license. See `src/ecoflow_companion/_vendor/README.md`. Nothing else from upstream is used.
- The application talks to `eflib` only through our adapter (Phase 2). That keeps one owner for
  the BLE connection and a single place to map upstream fields.

## Dependencies

Upstream runtime dependencies: `bleak`, `bleak-retry-connector`, `bluetooth-adapters`,
`ecdsa~=0.19`, `pycryptodome~=3.23`, `protobuf>=6.30`. `aiohttp` is used only by
`eflib/login.py` (cloud login), which `eflib/__init__.py` does not import.
`homeassistant`, `aiousbwatcher` and `pyserial` are HA-only.

Resolved and locked on Windows (`uv.lock`):

| Package | Version | Note |
|---|---|---|
| bleak | 3.0.2 | WinRT backend (`winrt-*` 3.2.1) |
| bleak-retry-connector | 4.7.1 | Linux/BlueZ paths are gated by `IS_LINUX`; `bluetooth-adapters` is not installed on Windows |
| ecdsa | 0.19.2 | |
| pycryptodome | 3.24.1 | |
| protobuf | 7.36.2 | |

Upstream `tests/eflib` run against this environment (plus `pytest-mock`, used only for that
run): **175 passed, 4 skipped**. All 4 skips are Linux/dbus-only tests.

## Architecture of `eflib`

- **Discovery.** `eflib` does not scan; Home Assistant hands it a `BLEDevice` and
  `AdvertisementData`. Stations are recognised by manufacturer data key `0xB5B5`. The serial
  number is bytes `1..17` (`sn_from_advertisement`). `NewDevice()` picks the device class whose
  `SN_PREFIX` matches the serial (`Device.check`), otherwise it returns `UnsupportedDevice`,
  which authenticates but decodes nothing. Our diagnostic CLI does the scan with
  `BleakScanner.discover(return_adv=True)`.
- **Pairing/authentication.** No Windows-level pairing is involved. The steps are:
  1. ECDH public-key exchange on SECP160r1 (`ecdsa`).
  2. Session key, then AES (`pycryptodome`).
  3. Auth status request.
  4. Authentication with `MD5(user_id + serial)` as upper-case hex (`Connection._auto_authentication`).

  The encryption type comes from the advertisement capability flags. **Authentication needs the
  numeric EcoFlow user ID of the account the station is bound to.**
- **Transport.** `bleak_retry_connector.establish_connection(BleakClient, …)`, GATT notifications,
  frame assembler, packet codecs v2/v3/v4.
- **Telemetry decoding.** DELTA 3 family: packet `src=0x02, cmd_set=0xFE, cmd_id=0x15` decoded as
  `pd335_sys.DisplayPropertyUpload` (protobuf) into descriptor fields on the device object.
  Change notification works through `register_callback` / `register_state_update_callback`.
- **Reconnects.** `Connection` has its own reconnect: at most 2 attempts with 10 s, then 20 s
  delay. Home Assistant turns it off with `with_disabled_reconnect()` and reloads the entry on
  disconnect. We do the same: upstream reconnect is off and our `ReconnectPolicy` owns retries
  (SPEC §5.3).
- **Shutdown.** `device.disconnect()` stops the data pump, cancels the device's tasks and calls
  `BleakClient.disconnect()` with a timeout.

## Telemetry fields (`eflib/devices/_delta3_base.py`, base of all DELTA 3 classes)

| Domain value | Upstream field | Protobuf source |
|---|---|---|
| battery % | `battery_level` | `cms_batt_soc` (float) |
| main battery % | `battery_level_main` | `bms_batt_soc` |
| AC input W | `ac_input_power` | `pow_get_ac_in` |
| AC output W | `ac_output_power` | `pow_get_ac_out` |
| grid / AC plugged | `plugged_in_ac` | `plug_in_info_ac_charger_flag` (bool) |
| total in / out W | `input_power` / `output_power` | `pow_in_sum_w` / `pow_out_sum_w` |
| battery charging | not decoded upstream; read from the message | `cms_chg_dsg_state` (0 idle, 1 discharging, 2 charging) |

`pd335_sys.proto` also has `plug_in_info_ac_in_flag` (uint32, field 61) and `flow_info_ac_in`
(uint32 bitmask, field 47). Upstream maps neither.

### Grid-state signal reliability (open upstream issues)

- [#300](https://github.com/rabits/ha-ef-ble/issues/300), open. On a DELTA 3 Plus,
  `plugged_in_ac` became "unknown" after a firmware update, and the maintainer says the firmware
  stopped sending the field. One reporter says the field **works on a DELTA 3 Classic with the
  latest firmware**. That is a single anecdote, not a test.
- [#469](https://github.com/rabits/ha-ef-ble/issues/469), open. On a DELTA 3 Max,
  `plug_in_info_ac_charger_flag` was absent from all 95 captured telemetry frames while AC was
  charging. The issue proposes `flow_info_ac_in` with upstream's `flow_is_on` transform as a
  grid signal; it matched measured input in 6 of 7 model/state pairs, and its bit meaning is not
  fully understood. The maintainer notes that `plug_in_info_ac_charger_flag` stays True while the
  cable is plugged in *even when not charging*. That is the semantics a grid-loss alert needs.

Consequence: the grid source for the target unit has to be measured on that unit and firmware.
`ecoflow-diag probe` reports, for every DELTA 3 telemetry frame, whether each candidate field is
present and which values it takes. It decodes with upstream's own `pd335_sys_pb2` and logs
transitions. Run it while unplugging and replugging AC input (SPEC §11, item 4).

**Measured on the target unit (DELTA 3 Classic, 2026-10-16).** `plugged_in_ac` was present
about once per second and followed one unplug/replug cycle. `flow_info_ac_in` stayed constant
and is not usable here. Details: [HARDWARE_LOG.md](HARDWARE_LOG.md).

## Target model

`ecoflow-diag scan` on this PC found one station:

- Advertised name `EF-P3…`; serial prefix **`P321`**.
- Upstream maps `P321` to device class `delta3_classic` ("Delta 3 Classic"). The upstream name
  table also says "EcoFlow DELTA 3 Classic".

On 2026-10-16 the user confirmed the unit is a **DELTA 3 Classic**, not a DELTA 3 Lite as
SPEC §1 states. DELTA 3 Classic **is explicitly supported upstream**:

- the README lists it in the DELTA 3 family;
- `delta3_classic.Device` has `SN_PREFIX = (b"P321", b"P322")`;
- support was added in #81, and P322 in #458.

Upstream has **no captured-telemetry test fixtures for the Classic**; its DELTA 3 tests cover
the Max Plus only. DELTA 3 Lite remains unsupported and unvalidated.

On this unit, discovery, authentication, 240 s of telemetry and clean shutdown on Windows all
pass; see [HARDWARE_LOG.md](HARDWARE_LOG.md) for what is still pending.

## Risks and follow-ups

- **Packaging (Phase 5).** `eflib/devices/__init__.py` finds device modules with
  `Path(__file__).parent.glob("*.py")`. In a PyInstaller bundle without `.py` files every station
  would become `UnsupportedDevice`. `eflib` must be collected as source
  (`module_collection_mode="py"`), and this must be verified in the built exe.
- **Files written by upstream.** `ConnectionLog(cache_to_file=True)` and diagnostics-on-exception
  write next to the `eflib` sources. Both are off by default and must stay off.
- **Secrets in logs.** Upstream logger names contain the BLE MAC, and its masking is opt-in
  (`LogOptions.MASKED`). Our `RedactingFormatter` masks the MAC, serial, user ID and auth digest
  in every formatted line. `LogOptions.PACKETS` and the payload options are never enabled,
  because they could log the auth packet.
- **User ID.** Upstream gets it through EcoFlow cloud login (`eflib/login.py`,
  `POST https://api*.ecoflow.com/auth/login`) or manual entry. The MVP must not use the cloud
  (SPEC §3.2), so the user ID is entered manually and stored locally. Storage protection is
  decided in Phase 3.
- **Concurrent EcoFlow app session.** Unknown; hardware test pending (SPEC §11, item 7).
