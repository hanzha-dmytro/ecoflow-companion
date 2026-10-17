# Licenses

EcoFlow Companion is an unofficial tool and is not affiliated with or endorsed by EcoFlow.
"EcoFlow" and "DELTA" are trademarks of their owner and are used only to name compatible
devices.

The project's own code is licensed under the Apache License 2.0 (`pyproject.toml`).

## Redistributed third-party software

The packaged application (`dist\EcoFlowCompanion`) contains the following. Versions are those
locked in `uv.lock` at the time of writing. License texts and metadata are included in the
package folder: `_internal\licenses` (ha-ef-ble's Apache-2.0 text, LGPL-3.0 and GPL-3.0) and
`_internal\<package>.dist-info` (each package's own metadata and license files).

| Component | Version | License | Notes |
|---|---|---|---|
| [ha-ef-ble](https://github.com/rabits/ha-ef-ble) `eflib` | v1.2.1 (`71f5b90`) | Apache-2.0 | Vendored unmodified in `src/ecoflow_companion/_vendor/eflib`; license in `_vendor/LICENSE-ha-ef-ble.txt`; no NOTICE file upstream |
| [bleak](https://github.com/hbldh/bleak) | 3.0.2 | MIT | |
| [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector) | 4.7.1 | MIT | |
| [ecdsa](https://github.com/tlsfuzzer/python-ecdsa) | 0.19.2 | MIT | |
| [six](https://github.com/benjaminp/six) | 1.17.0 | MIT | dependency of ecdsa |
| [pycryptodome](https://www.pycryptodome.org) | 3.24.1 | BSD-2-Clause, parts Public Domain | |
| [protobuf](https://github.com/protocolbuffers/protobuf) | 7.36.2 | BSD-3-Clause | |
| [pywinrt](https://github.com/pywinrt/pywinrt) (`winrt-runtime` and the `winrt-Windows.*` projections) | 3.2.1 | MIT | |
| [PySide6-Essentials](https://pypi.org/project/PySide6-Essentials/) / Qt 6, [shiboken6](https://pypi.org/project/shiboken6/) | 6.12.0 | LGPL-3.0-only (used under LGPL; also offered as GPL-2.0/GPL-3.0) | see below |
| CPython runtime | 3.13 | PSF-2.0 | `python313.dll` and the standard library |
| Microsoft Visual C++ runtime (`vcruntime140*.dll`) | — | Microsoft redistributable terms | shipped by Python/Qt |

### Qt and PySide6 (LGPL v3)

- Qt and PySide6 are used unmodified and linked dynamically: their DLLs and `.pyd` modules are
  separate files in `_internal\PySide6` and `_internal\shiboken6`. You may replace them with
  compatible builds of your own; the application is built as a folder (not a single packed
  `.exe`) so that this stays possible.
- The PySide6 wheels do not include the LGPL text (their `dist-info` holds only Qt's
  commercial-license notice), so the official LGPL v3 and GPL v3 texts from gnu.org are shipped
  in `_internal\licenses\LGPL-3.0.txt` and `GPL-3.0.txt` (source: `packaging/licenses`).
- Source code: https://code.qt.io/cgit/pyside/pyside-setup.git (PySide6) and
  https://download.qt.io/official_releases/qt/ (Qt).

## Build- and development-only tools (not redistributed)

PyInstaller (GPL-2.0 with a bootloader exception that permits distributing the built
executable under any license), pyinstaller-hooks-contrib, pytest, pytest-asyncio and Ruff.
