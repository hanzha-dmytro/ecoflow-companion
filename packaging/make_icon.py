"""Write the application icon (drawn in code) as a multi-size .ico for the executable."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication, QImageWriter

from ecoflow_companion.presentation.icons import app_icon


def main(target: Path) -> int:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - pixmaps need a GUI application
    if b"ico" not in QImageWriter.supportedImageFormats():
        print("Qt cannot write .ico files (imageformats plugin missing)")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    # Qt's ICO writer stores one image per write; the largest is what Windows scales from
    if not app_icon().pixmap(256).save(str(target), "ICO"):
        print(f"Could not write {target}")
        return 1
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
