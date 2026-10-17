"""Runs the monitor service on its own asyncio thread and bridges it to Qt.

Qt owns the main thread. BLE, reconnects and timers run on a daemon thread with its own event
loop, so the UI never waits on Bluetooth. Events cross back as a queued Qt signal; commands go
the other way with ``call_soon_threadsafe``.
"""

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from ecoflow_companion.application.event_rules import EventRules
from ecoflow_companion.application.monitor_service import MonitorConfig, MonitorService
from ecoflow_companion.application.ports import (
    Clock,
    ConnectFailed,
    StationAdapter,
    StationSelector,
)
from ecoflow_companion.application.reconnect_policy import ReconnectPolicy

log = logging.getLogger(__name__)


class ServiceHost(QObject):
    # Connect receivers that are QObjects living on the GUI thread: the signal is emitted from
    # the service thread, so Qt queues it to the receiver's thread.
    event_received = Signal(object)
    # (list[StationInfo] | None, error text | None)
    scan_finished = Signal(object, object)

    def __init__(self, adapter: StationAdapter, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._adapter = adapter
        self._loop: asyncio.AbstractEventLoop | None = None
        self._service: MonitorService | None = None
        self._task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and not self._stopped.is_set()

    def start(
        self,
        selector: StationSelector,
        user_id_provider: Callable[[], str | None],
        config: MonitorConfig,
        rules: EventRules,
        policy: ReconnectPolicy,
        clock: Clock | None = None,
    ) -> None:
        def make_service() -> MonitorService:
            return MonitorService(
                self._adapter,
                selector,
                user_id_provider,
                self.event_received.emit,
                config=config,
                rules=rules,
                policy=policy,
                clock=clock,
            )

        self._thread = threading.Thread(
            target=self._run, args=(make_service,), name="monitor-service", daemon=True
        )
        self._thread.start()
        self._ready.wait(5)

    def _run(self, make_service: Callable[[], MonitorService]) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._service = make_service()
            self._task = loop.create_task(self._service.run())
            self._ready.set()
            loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Monitor service stopped with an error")
        finally:
            self._ready.set()
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
                self._stopped.set()

    def _call(self, func: Callable[..., object], *args: object) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        # RuntimeError: the loop closed between the check and the call
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(func, *args)

    def request_reconnect(self) -> None:
        if self._service is not None:
            self._call(self._service.request_reconnect)

    def set_rules(self, rules: EventRules) -> None:
        if self._service is not None:
            self._call(self._service.set_rules, rules)

    def set_selector(self, selector: StationSelector) -> None:
        if self._service is not None:
            self._call(self._service.set_selector, selector)

    def scan(self, seconds: float) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            self.scan_finished.emit(None, "monitor is not running")
            return

        async def run_scan() -> tuple[object, object]:
            try:
                return await self._adapter.scan(seconds), None
            except ConnectFailed as e:
                return None, e.detail or str(e.kind)
            except Exception as e:
                log.exception("Scan failed")
                return None, type(e).__name__

        def done(future) -> None:
            if not future.cancelled():
                self.scan_finished.emit(*future.result())

        asyncio.run_coroutine_threadsafe(run_scan(), loop).add_done_callback(done)

    def stop(self, timeout: float = 15.0) -> bool:
        """Cancel the service (it disconnects) and wait; False if it did not finish in time."""
        if self._thread is None:
            return True
        if self._task is not None:
            self._call(self._task.cancel)
        finished = self._stopped.wait(timeout)
        if not finished:
            log.warning("Monitor service did not stop within %.0f s", timeout)
        return finished
