from ecoflow_companion.domain.telemetry_tracker import TelemetryTracker


def test_unreported_values_stay_unknown_not_zero(clock, make_telemetry):
    tracker = TelemetryTracker(stale_after_seconds=30)

    tracker.update(make_telemetry(battery_percent=54))
    snapshot = tracker.snapshot(clock.monotonic(), link_up=True)

    assert snapshot.battery_percent == 54
    assert snapshot.ac_input_watts is None
    assert snapshot.ac_output_watts is None
    assert snapshot.ac_plugged_in is None


def test_partial_updates_keep_previous_values(clock, make_telemetry):
    tracker = TelemetryTracker(stale_after_seconds=30)
    tracker.update(make_telemetry(battery_percent=54, ac_plugged_in=True, ac_input_watts=300.0))

    clock.advance(1)
    tracker.update(make_telemetry(ac_input_watts=0.0, ac_plugged_in=False))
    snapshot = tracker.snapshot(clock.monotonic(), link_up=True)

    assert snapshot.battery_percent == 54
    assert snapshot.ac_input_watts == 0.0
    assert snapshot.ac_plugged_in is False
    assert snapshot.last_update == clock.now()


def test_snapshot_is_stale_without_recent_telemetry_or_link(clock, make_telemetry):
    tracker = TelemetryTracker(stale_after_seconds=30)
    assert tracker.snapshot(clock.monotonic(), link_up=True).stale

    tracker.update(make_telemetry(battery_percent=54))
    assert not tracker.snapshot(clock.monotonic(), link_up=True).stale
    assert tracker.snapshot(clock.monotonic(), link_up=False).stale

    clock.advance(30)
    assert not tracker.snapshot(clock.monotonic(), link_up=True).stale
    clock.advance(0.1)
    stale = tracker.snapshot(clock.monotonic(), link_up=True)

    assert stale.stale
    # Values are kept for display as stale, never cleared to zero
    assert stale.battery_percent == 54
