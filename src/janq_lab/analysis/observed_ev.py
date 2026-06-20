"""Observed EV summaries from JanqProbe replay logs."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from typing import Any

from janq_lab.analysis.stats import Interval, mean, normal_mean_interval
from janq_lab.probe.replay import replay_file


@dataclass(frozen=True)
class ObservedEvSummary:
    currency: str
    hands: int
    mean_delta: float
    mean_delta_ci95: Interval
    positive_hands: int
    negative_hands: int
    zero_hands: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "hands": self.hands,
            "mean_delta": self.mean_delta,
            "mean_delta_ci95": {
                "low": self.mean_delta_ci95.low,
                "high": self.mean_delta_ci95.high,
            },
            "positive_hands": self.positive_hands,
            "negative_hands": self.negative_hands,
            "zero_hands": self.zero_hands,
        }


def summarize_observed_ev(path: str, *, currency: str = "mjchip") -> ObservedEvSummary:
    if currency not in {"gold", "mjchip", "cchip"}:
        raise ValueError("currency must be one of: gold, mjchip, cchip")

    replay = replay_file(path)
    deltas: list[float] = []
    for hand in replay.complete_hands:
        delta = None if hand.currency_delta is None else hand.currency_delta.get(currency)
        if delta is not None:
            deltas.append(float(delta))

    return ObservedEvSummary(
        currency=currency,
        hands=len(deltas),
        mean_delta=mean(deltas),
        mean_delta_ci95=normal_mean_interval(deltas),
        positive_hands=sum(1 for delta in deltas if delta > 0),
        negative_hands=sum(1 for delta in deltas if delta < 0),
        zero_hands=sum(1 for delta in deltas if delta == 0),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Summarize observed JanQ currency deltas.")
    parser.add_argument("path", help="Path to janq_events.jsonl")
    parser.add_argument("--currency", choices=("gold", "mjchip", "cchip"), default="mjchip")
    args = parser.parse_args(argv)
    summary = summarize_observed_ev(args.path, currency=args.currency)
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

