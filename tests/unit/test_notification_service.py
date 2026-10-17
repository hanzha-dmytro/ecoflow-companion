from datetime import timedelta

import pytest

from ecoflow_companion.application.notification_service import (
    AlertContext,
    DeliveryResult,
    Notification,
    NotificationPreferences,
    NotificationService,
    compose,
)
from ecoflow_companion.domain.events import (
    BatteryLow,
    GridLost,
    GridRestored,
    LinkLost,
    LinkRestored,
)
from ecoflow_companion.domain.models import FailureKind
from ecoflow_companion.infrastructure.windows.toast import toast_xml

CONTEXT = AlertContext(station_name="Delta 3 Classic", battery_percent=54)
ACCEPTED = DeliveryResult(True, "accepted by Windows")


class RecordingNotifier:
    def __init__(self, result=ACCEPTED):
        self.sent: list[Notification] = []
        self.result = result

    def notify(self, notification):
        self.sent.append(notification)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def service(clock, notifier=None, **prefs):
    notifier = notifier or RecordingNotifier()
    return notifier, NotificationService(
        notifier, NotificationPreferences(**prefs), repeat_window_seconds=900, clock=clock
    )


def test_texts(clock):
    at = clock.now()

    battery = compose(BatteryLow(18, 20, at), CONTEXT)
    initial = compose(BatteryLow(15, 20, at, initial=True), CONTEXT)
    lost = compose(GridLost(at), CONTEXT)
    restored = compose(GridRestored(at), AlertContext())
    link = compose(LinkLost(at, FailureKind.LINK_LOST), CONTEXT)
    back = compose(LinkRestored(at, timedelta(seconds=83)), CONTEXT)

    assert (battery.category, battery.title) == ("battery", "Battery low: 18%")
    assert "20% alert level" in battery.body
    assert "already this low" in initial.body
    assert lost.title == "Grid power lost"
    assert "Delta 3 Classic is running on battery since" in lost.body
    assert lost.body.endswith("Battery 54%.")
    assert restored.body.startswith("The station has grid power again")
    assert "Battery" not in restored.body  # unknown battery is left out, not shown as 0
    assert (link.category, link.title) == ("link", "Station disconnected")
    assert back.body == "Connection to Delta 3 Classic is back after 1 min 23 s. Battery 54%."


def test_sends_and_reports_result(clock):
    notifier, s = service(clock)

    result = s.handle(GridLost(clock.now()), CONTEXT)

    assert result == DeliveryResult(True, "accepted by Windows")
    assert [n.title for n in notifier.sent] == ["Grid power lost"]


@pytest.mark.parametrize(
    ("prefs", "event_factory"),
    [
        ({"enabled": False}, lambda at: BatteryLow(10, 10, at)),
        ({"grid": False}, GridLost),
        ({"grid": False}, GridRestored),
        ({"link_lost": False}, lambda at: LinkLost(at, FailureKind.LINK_LOST)),
        ({"link_restored": False}, lambda at: LinkRestored(at, timedelta(seconds=30))),
    ],
)
def test_toggles(clock, prefs, event_factory):
    notifier, s = service(clock, **prefs)

    assert s.handle(event_factory(clock.now()), CONTEXT) is None
    assert notifier.sent == []


def test_other_toggles_do_not_block_battery(clock):
    _, s = service(clock, grid=False, link_lost=False, link_restored=False)

    assert s.handle(BatteryLow(19, 20, clock.now()), CONTEXT) is not None


def test_repeats_are_dropped_within_window_but_alternation_is_not(clock):
    notifier, s = service(clock)
    at = clock.now()

    s.handle(GridLost(at), CONTEXT)
    s.handle(GridRestored(at), CONTEXT)
    s.handle(GridLost(at), CONTEXT)
    assert len(notifier.sent) == 3

    # Same threshold again (rules rebuilt after a settings change): dropped
    s.handle(BatteryLow(18, 20, at, initial=True), CONTEXT)
    s.handle(BatteryLow(18, 20, at, initial=True), CONTEXT)
    # A different threshold is news
    s.handle(BatteryLow(9, 10, at), CONTEXT)
    assert [n.title for n in notifier.sent[3:]] == ["Battery low: 18%", "Battery low: 9%"]

    clock.advance(901)
    s.handle(BatteryLow(9, 10, at), CONTEXT)
    assert len(notifier.sent) == 6


def test_preferences_can_change(clock):
    _, s = service(clock)
    s.set_preferences(NotificationPreferences(enabled=False))

    assert s.handle(GridLost(clock.now()), CONTEXT) is None


def test_failures_are_reported_not_hidden(clock, caplog):
    blocked = RecordingNotifier(DeliveryResult(False, "blocked by Windows (disabled_for_user)"))
    _, s = service(clock, blocked)

    result = s.handle(GridLost(clock.now()), CONTEXT)

    assert result is not None and not result.accepted
    assert "not shown: blocked by Windows" in caplog.text


def test_notifier_exception_becomes_failed_result(clock):
    _, s = service(clock, RecordingNotifier(OSError("boom")))

    result = s.send_test()

    assert result == DeliveryResult(False, "error: OSError")


def test_toast_xml_escapes_text():
    xml = toast_xml(Notification("grid", "A & B", '<b>x</b> "q"'))

    assert "<text>A &amp; B</text>" in xml
    assert "&lt;b&gt;x&lt;/b&gt;" in xml
    assert xml.startswith("<toast>")
