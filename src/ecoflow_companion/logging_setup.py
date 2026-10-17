"""Application logging: size-limited file, redacted, plus the console in debug mode."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ecoflow_companion.redaction import RedactingFormatter, Redactor

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MAX_BYTES = 1_000_000
BACKUPS = 3


def configure_logging(log_file: Path, redactor: Redactor, debug: bool) -> None:
    formatter = RedactingFormatter(redactor, LOG_FORMAT)
    handlers: list[logging.Handler] = []
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
        handlers.append(file_handler)
    except OSError:
        pass  # The application works without a log file
    # pythonw (no console) has no stderr
    if debug and sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # bleak and the WinRT layer are chatty at DEBUG and add nothing at INFO
    logging.getLogger("bleak").setLevel(logging.DEBUG if debug else logging.WARNING)

    def log_uncaught(exc_type, exc, tb) -> None:
        logging.getLogger("ecoflow_companion").critical(
            "Uncaught exception", exc_info=(exc_type, exc, tb)
        )

    sys.excepthook = log_uncaught
