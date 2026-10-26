"""Exponential backoff with full jitter (design D10)."""

import random
from collections.abc import Callable

# 2**62 is far past any sensible cap; clamping the exponent keeps the
# arithmetic from overflowing on absurd attempt counts.
_MAX_EXPONENT = 62


def backoff_seconds(
    attempt_count: int,
    base_seconds: float,
    cap_seconds: float,
    rng: Callable[[], float] = random.random,
) -> float:
    """Delay before retrying after the ``attempt_count``-th failed attempt.

    The ceiling is ``base * 2**(attempt_count - 1)`` limited to the cap; the
    delay is uniform over (0, ceiling], so it is always positive.
    """
    exponent = min(max(attempt_count - 1, 0), _MAX_EXPONENT)
    ceiling = min(cap_seconds, base_seconds * 2**exponent)
    return ceiling * (1.0 - rng())  # rng() is in [0, 1), so this is in (0, ceiling]
