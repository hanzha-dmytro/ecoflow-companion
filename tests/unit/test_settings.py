import json

import pytest

from ecoflow_companion.application.ports import StationSelector
from ecoflow_companion.config import (
    Settings,
    monitor_config_from,
    policy_from,
    rules_from,
    selector_from,
)
from ecoflow_companion.infrastructure.storage.settings_repository import SettingsRepository


def test_defaults_match_spec():
    s = Settings()

    assert s.notifications.battery_thresholds == (30, 20, 10)
    assert s.notifications.battery_hysteresis_percent == 3
    assert s.monitoring.stale_after_seconds == 30
    assert s.monitoring.reconnect_max_delay_seconds == 60
    assert s.widget.close_to_tray is True
    assert s.startup.launch_at_login is False
    assert selector_from(s) == StationSelector()


def test_round_trip_through_json():
    s = Settings.from_dict({"widget": {"x": 10, "y": -20, "always_on_top": True}})[0]

    restored, problems = Settings.from_dict(json.loads(json.dumps(s.to_dict())))

    assert restored == s
    assert problems == []


@pytest.mark.parametrize(
    ("data", "where"),
    [
        ({"widget": {"always_on_top": "yes"}}, "widget.always_on_top"),
        ({"widget": {"x": 1.5}}, "widget.x"),
        ({"notifications": {"battery_thresholds": [0, 20]}}, "battery_thresholds"),
        ({"notifications": {"battery_thresholds": "30"}}, "battery_thresholds"),
        ({"notifications": {"battery_hysteresis_percent": True}}, "battery_hysteresis"),
        ({"monitoring": {"stale_after_seconds": 1}}, "stale_after_seconds"),
        ({"monitoring": {"stale_after_seconds": float("nan")}}, "stale_after_seconds"),
        ({"device": {"preferred_address": "not-an-address"}}, "preferred_address"),
        ({"startup": []}, "startup"),
    ],
)
def test_invalid_values_fall_back_to_defaults(data, where):
    settings, problems = Settings.from_dict(data)

    assert settings == Settings()
    assert len(problems) == 1 and where in problems[0]


def test_unknown_keys_are_ignored_and_thresholds_normalized():
    settings, problems = Settings.from_dict(
        {"future": 1, "notifications": {"battery_thresholds": [10, 30, 30, 20], "x": 1}}
    )

    assert problems == []
    assert settings.notifications.battery_thresholds == (30, 20, 10)


def test_conversion_to_service_configuration():
    s, _ = Settings.from_dict(
        {
            "device": {"preferred_address": "AA:BB:CC:DD:57:2E", "preferred_serial": "P321X"},
            "monitoring": {"stale_after_seconds": 45, "reconnect_max_delay_seconds": 20},
        }
    )

    assert selector_from(s) == StationSelector("AA:BB:CC:DD:57:2E", "P321X")
    assert monitor_config_from(s).stale_after_seconds == 45
    rules_from(s)  # valid settings always build valid rules
    policy = policy_from(s)
    assert max(policy.next_delay() for _ in range(10)) <= 20


# --- repository ----------------------------------------------------------------------------


def test_missing_file_gives_defaults(tmp_path):
    assert SettingsRepository(tmp_path / "settings.json").load() == Settings()


def test_save_then_load(tmp_path):
    repo = SettingsRepository(tmp_path / "sub" / "settings.json")
    settings = Settings.from_dict({"widget": {"x": 100, "y": 200}})[0]

    repo.save(settings)

    assert repo.load() == settings
    assert not list(tmp_path.glob("sub/*.tmp"))


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", b"\xff\xfe\x00garbage"])
def test_unreadable_file_is_set_aside_and_defaults_used(tmp_path, content):
    path = tmp_path / "settings.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    repo = SettingsRepository(path)

    assert repo.load() == Settings()
    kept = list(tmp_path.glob("settings.invalid-*.json"))
    assert len(kept) == 1 and not path.exists()

    repo.save(Settings())
    assert repo.load() == Settings()


def test_partially_invalid_file_keeps_valid_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"widget": {"x": 5, "y": "bad"}, "startup": {"launch_at_login": True}}),
        encoding="utf-8",
    )

    settings = SettingsRepository(path).load()

    assert settings.widget.x == 5
    assert settings.widget.y is None
    assert settings.startup.launch_at_login is True
    assert path.exists()
