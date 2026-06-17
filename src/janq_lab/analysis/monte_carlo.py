"""Monte Carlo runner for offline JanQ strategies."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import random
from typing import Any

from janq_lab.analysis.stats import Interval, mean, wilson_interval
from janq_lab.assets.nyukyu import load_tables
from janq_lab.model.simulator import SimulationResult, random_initial_hand, simulate_hand
from janq_lab.strategy.greedy import choose_greedy_area, choose_greedy_discard
from janq_lab.strategy.public import choose_public_area, choose_public_discard
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard
from janq_lab.strategy.route_ev2 import choose_route_ev2_area, choose_route_ev2_discard


@dataclass(frozen=True)
class MonteCarloSummary:
    hands: int
    seed: int
    wins: int
    win_rate: float
    win_rate_ci95: Interval
    avg_shots: float
    avg_fourth_copy_refunds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "hands": self.hands,
            "seed": self.seed,
            "wins": self.wins,
            "win_rate": self.win_rate,
            "win_rate_ci95": {
                "low": self.win_rate_ci95.low,
                "high": self.win_rate_ci95.high,
            },
            "avg_shots": self.avg_shots,
            "avg_fourth_copy_refunds": self.avg_fourth_copy_refunds,
        }


def run_monte_carlo(
    *,
    hands: int,
    seed: int = 1,
    balls: int = 8,
    strategy: str = "public",
) -> MonteCarloSummary:
    if hands < 1:
        raise ValueError("hands must be positive")
    if balls < 1:
        raise ValueError("balls must be positive")

    rng = random.Random(seed)
    table = load_tables()["nyukyu_base_table.bytes"]
    if strategy == "public":
        choose_area = choose_public_area
        choose_discard = choose_public_discard
    elif strategy == "greedy":
        choose_area = choose_greedy_area
        choose_discard = choose_greedy_discard
    elif strategy == "route_ev":
        choose_area = choose_route_ev_area
        choose_discard = choose_route_ev_discard
    elif strategy == "route_ev2":
        choose_area = choose_route_ev2_area
        choose_discard = choose_route_ev2_discard
    else:
        raise ValueError(f"unknown strategy: {strategy}")

    results: list[SimulationResult] = []
    fourth_copy_counts: list[float] = []

    for _ in range(hands):
        initial = random_initial_hand(rng)
        result = simulate_hand(
            initial,
            table,
            balls=balls,
            rng=rng,
            choose_area=choose_area,
            choose_discard=choose_discard,
        )
        results.append(result)
        fourth_copy_counts.append(
            float(sum(1 for turn in result.turns if turn.shot.fourth_copy))
        )

    wins = sum(1 for result in results if result.win)
    return MonteCarloSummary(
        hands=hands,
        seed=seed,
        wins=wins,
        win_rate=wins / hands,
        win_rate_ci95=wilson_interval(wins, hands),
        avg_shots=mean([float(result.shots) for result in results]),
        avg_fourth_copy_refunds=mean(fourth_copy_counts),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the current greedy JanQ simulator.")
    parser.add_argument("--hands", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--balls", type=int, default=8)
    parser.add_argument("--strategy", choices=("public", "greedy", "route_ev", "route_ev2"), default="public")
    args = parser.parse_args(argv)

    summary = run_monte_carlo(
        hands=args.hands,
        seed=args.seed,
        balls=args.balls,
        strategy=args.strategy,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
