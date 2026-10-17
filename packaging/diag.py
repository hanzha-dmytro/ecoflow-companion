"""PyInstaller entry point of ecoflow-diag.exe (console)."""

import sys

from ecoflow_companion.diagnostics.ble_probe import main

sys.exit(main())
