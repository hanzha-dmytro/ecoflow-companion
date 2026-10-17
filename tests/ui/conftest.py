import os
import time

import pytest

# Must be set before the first QApplication; no window ever appears on the desktop
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app  # type: ignore[return-value]


@pytest.fixture
def pump(qapp):
    """Process Qt events until ``predicate`` holds (fails after ``seconds``)."""

    def wait(predicate, seconds: float = 5.0) -> None:
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("condition not reached in time")
            qapp.processEvents()
            time.sleep(0.005)

    return wait
