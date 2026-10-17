"""One running copy per Windows user: a second launch asks the first to show its widget.

Two copies would fight over the single BLE session the station accepts (HARDWARE_LOG.md).
The request is the connection itself, not a message: a client that writes and disconnects at
once can be gone before the server reads anything.
"""

import getpass
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def server_name() -> str:
    return f"EcoFlowCompanion-{getpass.getuser()}"


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def acquire(self) -> bool:
        """True if this is the first copy; otherwise the first copy is told to show itself."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if socket.waitForConnected(500):
            socket.disconnectFromServer()
            return False
        # Nobody answered: a server name left by a crashed copy is stale
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            log.warning("Single-instance server failed: %s", self._server.errorString())
        return True

    def _on_connection(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            socket.disconnectFromServer()
            socket.deleteLater()
            self.activated.emit()
