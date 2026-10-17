# Vendored third-party code

## `eflib/` — from rabits/ha-ef-ble

- Upstream: https://github.com/rabits/ha-ef-ble
- Version: tag `v1.2.1`, commit `71f5b90a23dce7acdf7b8d701460de59f6d3b0f6` (2026-10-08)
- Path in upstream: `custom_components/ef_ble/eflib/`
- License: Apache License 2.0, copied to `LICENSE-ha-ef-ble.txt`. Upstream ships no NOTICE file.
- Modifications: **none**. The directory is a byte-for-byte copy (checked with `diff -r`).

`eflib` is the upstream BLE protocol library: device identification, ECDH session setup,
authentication, packet framing/encryption and protobuf telemetry decoding. It has no Home
Assistant imports. The rest of the upstream repository (the Home Assistant integration) is not
used.

The copy is vendored instead of installed because upstream is not published on PyPI and its
installable package (`custom_components.ef_ble`) imports Home Assistant in `__init__.py`.

### Updating

1. Clone upstream at the new tag.
2. Replace `eflib/` with upstream `custom_components/ef_ble/eflib/` and remove `__pycache__`.
3. Update the version/commit above and re-check upstream `pyproject.toml` dependency constraints
   against this project's `pyproject.toml`.
4. Run the upstream `tests/eflib` suite against this project's environment and this project's
   tests.

If a local modification ever becomes necessary, record it here and mark the changed files, as
required by Apache-2.0 section 4(b).
