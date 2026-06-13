"""Small statistical helpers for JanQ experiments."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Interval:
    low: float
    high: float


def wilson_interval(successes: int, trials: int, *, z: float = 1.959963984540054) -> Interval:
    """Wilson score interval for a binomial proportion."""

    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("successes and trials must satisfy 0 <= successes <= trials")
    if trials == 0:
        return Interval(0.0, 0.0)

    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    half = z * math.sqrt((p * (1 - p) + z2 / (4 * trials)) / trials) / denom
    return Interval(max(0.0, center - half), min(1.0, center + half))


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def sample_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def normal_mean_interval(values: list[float], *, z: float = 1.959963984540054) -> Interval:
    if not values:
        return Interval(0.0, 0.0)
    avg = mean(values)
    if len(values) < 2:
        return Interval(avg, avg)
    half = z * sample_stddev(values) / math.sqrt(len(values))
    return Interval(avg - half, avg + half)
