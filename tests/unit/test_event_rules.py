from datetime import timedelta

import pytest

from ecoflow_companion.application.event_rules import (
    BatteryRule,
    BatteryRuleConfig,
    EventRules,
    GridRule,
    GridRuleConfig,
    LinkRule,
    LinkRuleConfig,
)
from ecoflow_companion.domain.events import (
    BatteryLow,
    GridLost,
    GridRestored,
    LinkLost,
    LinkRestored,
)
from ecoflow_companion.domain.models import FailureKind


def feed(rule: BatteryRule, clock, *levels: int) -> list[BatteryLow]:
    events = []
    for level in levels:
        if (event := rule.on_battery(level, clock.now())) is not None:
            events.append(event)
        clock.advance(1)
    return events


# --- battery -------------------------------------------------------------------------------


def test_battery_alerts_once_per_downward_crossing(clock):
    rule = BatteryRule(BatteryRuleConfig())

    events = feed(rule, clock, 45, 31, 30, 29, 28, 25, 21, 20, 19, 12, 10, 9)

    assert [(e.battery_percent, e.threshold, e.initial) for e in events] == [
        (30, 30, False),
        (20, 20, False),
        (10, 10, False),
    ]


def test_battery_rearms_only_after_hysteresis(clock):
    rule = BatteryRule(BatteryRuleConfig(thresholds=(20,), hysteresis=3))

    assert feed(rule, clock, 25, 20) != []
    # Rising to 22 is within the hysteresis band: still disarmed
    assert feed(rule, clock, 21, 22, 20, 19) == []
    # 23 = threshold + hysteresis re-arms it
    events = feed(rule, clock, 23, 20)
    assert [(e.battery_percent, e.threshold) for e in events] == [(20, 20)]


def test_battery_startup_below_thresholds_sends_one_initial_alert(clock):
    rule = BatteryRule(BatteryRuleConfig())

    events = feed(rule, clock, 15, 14, 13)

    assert [(e.battery_percent, e.threshold, e.initial) for e in events] == [(15, 20, True)]
    # The lower threshold still fires when crossed later
    assert [e.threshold for e in feed(rule, clock, 10)] == [10]


def test_battery_startup_above_thresholds_is_silent(clock):
    rule = BatteryRule(BatteryRuleConfig())

    assert feed(rule, clock, 80, 79) == []


def test_battery_jump_across_several_thresholds_alerts_once_for_lowest(clock):
    rule = BatteryRule(BatteryRuleConfig())

    events = feed(rule, clock, 31, 9)

    assert [(e.battery_percent, e.threshold) for e in events] == [(9, 10)]
    assert feed(rule, clock, 8, 7) == []


@pytest.mark.parametrize(
    "kwargs",
    [{"thresholds": (0,)}, {"thresholds": (100,)}, {"hysteresis": 0}, {"hysteresis": 51}],
)
def test_battery_config_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        BatteryRuleConfig(**kwargs)


# --- grid ----------------------------------------------------------------------------------


def run_grid(rule: GridRule, clock, make_telemetry, steps):
    """steps: (seconds to advance, flag or None) pairs; returns emitted events."""
    events = []
    for advance, flag in steps:
        clock.advance(advance)
        if (event := rule.on_telemetry(make_telemetry(ac_plugged_in=flag))) is not None:
            events.append(event)
    return events


def test_grid_first_confirmed_value_is_a_silent_baseline(clock, make_telemetry):
    rule = GridRule(GridRuleConfig(debounce_seconds=5))

    events = run_grid(rule, clock, make_telemetry, [(0, True), (3, True), (3, True)])

    assert events == []
    assert rule.confirmed is True


def test_grid_loss_and_restore_after_debounce(clock, make_telemetry):
    rule = GridRule(GridRuleConfig(debounce_seconds=5))
    run_grid(rule, clock, make_telemetry, [(0, True), (6, True)])

    lost = run_grid(rule, clock, make_telemetry, [(1, False), (3, False), (2, False), (1, False)])
    restored = run_grid(rule, clock, make_telemetry, [(1, True), (5, True)])

    assert [type(e) for e in lost] == [GridLost]
    assert [type(e) for e in restored] == [GridRestored]


def test_grid_transient_flip_is_ignored(clock, make_telemetry):
    rule = GridRule(GridRuleConfig(debounce_seconds=5))
    run_grid(rule, clock, make_telemetry, [(0, True), (6, True)])

    events = run_grid(rule, clock, make_telemetry, [(1, False), (2, True), (10, True)])

    assert events == []
    assert rule.confirmed is True


def test_grid_confirms_on_any_later_update_but_never_on_silence(clock, make_telemetry):
    rule = GridRule(GridRuleConfig(debounce_seconds=5))
    run_grid(rule, clock, make_telemetry, [(0, True), (6, True), (1, False)])

    # No updates for a while: nothing may be confirmed by the passage of time alone
    clock.advance(10)
    assert rule.confirmed is True
    # An update without the flag shows telemetry is still flowing with no contrary report
    events = run_grid(rule, clock, make_telemetry, [(0, None)])

    assert [type(e) for e in events] == [GridLost]


def test_grid_link_loss_drops_pending_value(clock, make_telemetry):
    rule = GridRule(GridRuleConfig(debounce_seconds=5))
    run_grid(rule, clock, make_telemetry, [(0, True), (6, True), (1, False)])

    rule.on_link_lost()
    assert run_grid(rule, clock, make_telemetry, [(6, None)]) == []
    # A fresh report after reconnecting starts its own debounce; the baseline was kept
    events = run_grid(rule, clock, make_telemetry, [(1, False), (5, None)])
    assert [type(e) for e in events] == [GridLost]


def test_grid_stale_telemetry_forgets_baseline(clock, make_telemetry):
    rule = GridRule(GridRuleConfig(debounce_seconds=5))
    run_grid(rule, clock, make_telemetry, [(0, True), (6, True)])

    rule.on_stale()
    events = run_grid(rule, clock, make_telemetry, [(60, False), (6, False)])

    assert events == []
    assert rule.confirmed is False


def test_grid_ignores_input_power(clock, make_telemetry):
    rule = GridRule(GridRuleConfig(debounce_seconds=0))
    run_grid(rule, clock, make_telemetry, [(0, True), (1, True)])

    clock.advance(1)
    event = rule.on_telemetry(make_telemetry(ac_input_watts=0.0))

    assert event is None
    assert rule.confirmed is True


# --- BLE link ------------------------------------------------------------------------------


def test_link_short_blip_is_silent(clock):
    rule = LinkRule(LinkRuleConfig(confirm_after_seconds=15))

    rule.on_link_lost(FailureKind.LINK_LOST, clock.monotonic(), clock.now())
    clock.advance(10)
    assert rule.on_tick(clock.monotonic()) is None
    assert rule.on_telemetry(clock.monotonic(), clock.now()) is None
    clock.advance(30)
    assert rule.on_tick(clock.monotonic()) is None


def test_link_confirmed_outage_alerts_once_then_restores(clock):
    rule = LinkRule(LinkRuleConfig(confirm_after_seconds=15))
    lost_at = clock.now()

    rule.on_link_lost(FailureKind.LINK_LOST, clock.monotonic(), lost_at)
    clock.advance(14)
    assert rule.on_tick(clock.monotonic()) is None
    clock.advance(1)
    lost = rule.on_tick(clock.monotonic())
    # Retries during the same outage do not restart it or alert again
    rule.on_link_lost(FailureKind.NOT_FOUND, clock.monotonic(), clock.now())
    clock.advance(30)
    assert rule.on_tick(clock.monotonic()) is None
    restored = rule.on_telemetry(clock.monotonic(), clock.now())

    assert lost == LinkLost(lost_at, FailureKind.LINK_LOST)
    assert isinstance(restored, LinkRestored)
    assert restored.outage == timedelta(seconds=45)
    assert rule.on_telemetry(clock.monotonic(), clock.now()) is None


def test_link_startup_without_connection_is_not_an_outage(clock):
    rule = LinkRule(LinkRuleConfig())

    clock.advance(600)

    assert rule.on_tick(clock.monotonic()) is None
    assert rule.on_telemetry(clock.monotonic(), clock.now()) is None


# --- combined ------------------------------------------------------------------------------


def test_restore_is_reported_before_alerts_from_the_same_update(clock, make_telemetry):
    rules = EventRules(link=LinkRuleConfig(confirm_after_seconds=1))
    rules.on_telemetry(make_telemetry(battery_percent=50))
    rules.on_link_lost(FailureKind.LINK_LOST, clock.monotonic(), clock.now())
    clock.advance(2)
    assert [type(e) for e in rules.on_tick(clock.monotonic(), telemetry_fresh=True)] == [LinkLost]

    events = rules.on_telemetry(make_telemetry(battery_percent=29))

    assert [type(e) for e in events] == [LinkRestored, BatteryLow]


def test_stale_tick_resets_grid_baseline(clock, make_telemetry):
    rules = EventRules(grid=GridRuleConfig(debounce_seconds=0))
    rules.on_telemetry(make_telemetry(ac_plugged_in=True))
    clock.advance(1)
    rules.on_telemetry(make_telemetry(ac_plugged_in=True))

    rules.on_tick(clock.monotonic(), telemetry_fresh=False)

    assert rules.grid.confirmed is None
