"""PyInstaller entry point of EcoFlowCompanion.exe (no console)."""

import sys

from ecoflow_companion.main import main

sys.exit(main())
