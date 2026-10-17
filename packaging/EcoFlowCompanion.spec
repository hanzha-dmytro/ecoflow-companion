# PyInstaller spec: one folder (dist\EcoFlowCompanion) with two executables sharing one runtime.
#   EcoFlowCompanion.exe  the desktop app, no console
#   ecoflow-diag.exe      the diagnostic CLI, console
# Build with packaging\build.ps1.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
ICON = ROOT / "build" / "EcoFlowCompanion.ico"

hiddenimports = [
    # eflib imports its device modules by file name at runtime (eflib/devices/__init__.py)
    *collect_submodules("ecoflow_companion._vendor.eflib"),
    # bleak picks its backend at runtime; the WinRT projections it and the toasts use
    *collect_submodules("bleak.backends.winrt"),
    *collect_submodules("winrt"),
]

# License texts and metadata of the redistributed packages
datas = [
    (str(ROOT / "LICENSES.md"), "."),
    (str(SRC / "ecoflow_companion" / "_vendor" / "LICENSE-ha-ef-ble.txt"), "licenses"),
    # The PySide6 wheels carry no LGPL/GPL text; these are the official gnu.org copies
    (str(ROOT / "packaging" / "licenses" / "LGPL-3.0.txt"), "licenses"),
    (str(ROOT / "packaging" / "licenses" / "GPL-3.0.txt"), "licenses"),
]
for dist in (
    "bleak",
    "bleak-retry-connector",
    "ecdsa",
    "six",
    "pycryptodome",
    "protobuf",
    "PySide6-Essentials",
    "shiboken6",
    "winrt-runtime",
):
    datas += copy_metadata(dist)


def analysis(script: str) -> Analysis:
    return Analysis(
        [str(ROOT / "packaging" / script)],
        pathex=[str(SRC)],
        hiddenimports=hiddenimports,
        datas=datas,
        excludes=["tkinter", "pytest", "ruff"],
        # Collected as .py files on disk: eflib finds its device classes with
        # Path(__file__).parent.glob("*.py"); in the PYZ archive every station would
        # become UnsupportedDevice (docs/AUDIT.md, "Packaging")
        module_collection_mode={"ecoflow_companion._vendor.eflib": "py"},
        noarchive=False,
    )


app = analysis("companion.py")
diag = analysis("diag.py")

app_exe = EXE(
    PYZ(app.pure),
    app.scripts,
    [],
    exclude_binaries=True,
    name="EcoFlowCompanion",
    console=False,
    icon=str(ICON),
    version=str(ROOT / "packaging" / "version_info.txt"),
    upx=False,
)
diag_exe = EXE(
    PYZ(diag.pure),
    diag.scripts,
    [],
    exclude_binaries=True,
    name="ecoflow-diag",
    console=True,
    icon=str(ICON),
    upx=False,
)
COLLECT(
    app_exe,
    app.binaries,
    app.datas,
    diag_exe,
    diag.binaries,
    diag.datas,
    name="EcoFlowCompanion",
    upx=False,
)
