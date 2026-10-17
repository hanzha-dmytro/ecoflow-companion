import pytest

from ecoflow_companion.application.reconnect_policy import ReconnectPolicy


def test_delays_grow_exponentially_up_to_the_cap():
    policy = ReconnectPolicy(initial_delay=2, max_delay=60, factor=2, jitter=0)

    delays = [policy.next_delay() for _ in range(8)]

    assert delays == [2, 4, 8, 16, 32, 60, 60, 60]
    assert policy.attempt == 8


def test_reset_starts_over():
    policy = ReconnectPolicy(initial_delay=2, max_delay=60, jitter=0)
    for _ in range(5):
        policy.next_delay()

    policy.reset()

    assert policy.attempt == 0
    assert policy.next_delay() == 2


@pytest.mark.parametrize(("rng", "expected"), [(0.0, 0.9), (0.5, 1.0), (1.0, 1.1)])
def test_jitter_spreads_delay(rng, expected):
    policy = ReconnectPolicy(initial_delay=1, max_delay=60, jitter=0.1, rng=lambda: rng)

    assert policy.next_delay() == pytest.approx(expected)


def test_jitter_never_exceeds_cap():
    policy = ReconnectPolicy(initial_delay=60, max_delay=60, jitter=0.5, rng=lambda: 1.0)

    assert policy.next_delay() == 60


def test_days_of_retries_do_not_overflow():
    policy = ReconnectPolicy(initial_delay=2, max_delay=60, jitter=0)

    for _ in range(5000):
        delay = policy.next_delay()

    assert delay == 60


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_delay": 0},
        {"initial_delay": 10, "max_delay": 5},
        {"factor": 0.5},
        {"jitter": 1.0},
    ],
)
def test_rejects_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        ReconnectPolicy(**kwargs)
