import random

from app.delivery.backoff import backoff_seconds


def ceiling(attempt, base=2.0, cap=300.0):
    # rng() == 0.0 selects the top of the jitter range.
    return backoff_seconds(attempt, base, cap, rng=lambda: 0.0)


def test_ceiling_grows_exponentially():
    assert [ceiling(n) for n in (1, 2, 3, 4, 5)] == [2.0, 4.0, 8.0, 16.0, 32.0]


def test_cap_is_enforced():
    assert ceiling(8) == 256.0
    assert ceiling(9) == 300.0
    assert ceiling(50) == 300.0


def test_huge_attempt_counts_do_not_overflow():
    assert ceiling(10**6) == 300.0


def test_full_jitter_spreads_across_the_range():
    low = backoff_seconds(4, 2.0, 300.0, rng=lambda: 0.999)
    high = backoff_seconds(4, 2.0, 300.0, rng=lambda: 0.0)
    assert 0 < low < 0.1 and high == 16.0


def test_delays_are_always_positive_and_within_the_cap():
    rng = random.Random(1234)
    for attempt in range(1, 40):
        for _ in range(200):
            delay = backoff_seconds(attempt, 2.0, 300.0, rng=rng.random)
            assert 0 < delay <= min(300.0, 2.0 * 2 ** (attempt - 1))


def test_jitter_is_actually_random_by_default():
    assert len({backoff_seconds(5, 2.0, 300.0) for _ in range(20)}) > 1
