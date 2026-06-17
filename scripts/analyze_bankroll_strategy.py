from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from numba import njit, prange
from scipy.optimize import brentq, lsq_linear


BUCKETS = (
    "0",
    "1-9",
    "10-49",
    "50-99",
    "100-499",
    "500-999",
    "1000-4999",
    "5000+",
)

LOWER_PAYOUT_MULTIPLIERS = np.array((0.0, 0.1, 1.0, 5.0, 10.0, 50.0, 100.0, 500.0))
UPPER_PAYOUT_MULTIPLIERS = np.array(
    (1e-10, 0.9, 4.9, 9.9, 49.9, 99.9, 499.9, 5000.0)
)
DEFAULT_BET_LADDER = "10,20,30,50,100,200"


def percentile(values: np.ndarray, probability: float) -> float | None:
    if values.size == 0:
        return None
    return float(np.quantile(values, probability))


def fit_bucket_distribution(source: dict[str, Any]) -> dict[str, Any]:
    chunks = source["chunks"]
    counts = np.array(
        [
            [chunk["payout_histogram"].get(bucket, 0) for bucket in BUCKETS]
            for chunk in chunks
        ],
        dtype=float,
    )
    payout_units = np.array(
        [chunk["total_payout"] / chunk["bet"] for chunk in chunks],
        dtype=float,
    )
    result = lsq_linear(
        counts,
        payout_units,
        bounds=(LOWER_PAYOUT_MULTIPLIERS, UPPER_PAYOUT_MULTIPLIERS),
        lsq_solver="exact",
        max_iter=1000,
    )
    probabilities = counts.sum(axis=0) / counts.sum()
    fitted_payouts = counts @ result.x
    actual_rtp = payout_units / source["aggregate"]["sessions_per_chunk"]
    fitted_rtp = fitted_payouts / source["aggregate"]["sessions_per_chunk"]
    net_increments = result.x - 1.0
    mean_net = float(probabilities @ net_increments)
    variance = float(probabilities @ ((net_increments - mean_net) ** 2))

    return {
        "buckets": list(BUCKETS),
        "probabilities": probabilities,
        "payout_multipliers": result.x,
        "net_increments_bets": net_increments,
        "actual_chunk_rtp": actual_rtp,
        "fitted_chunk_rtp": fitted_rtp,
        "fit_success": bool(result.success),
        "fit_cost": float(result.cost),
        "rtp": float(probabilities @ result.x),
        "mean_net_bets_per_hand": mean_net,
        "sd_net_bets_per_hand": math.sqrt(variance),
        "chunk_rtp_rmse": float(np.sqrt(np.mean((fitted_rtp - actual_rtp) ** 2))),
    }


def parse_float_list(value: str) -> list[float]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise argparse.ArgumentTypeError("expected a comma-separated list of numbers")
    return [float(item) for item in items]


def load_source(source_path: Path) -> dict[str, Any]:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    if "chunks" in source:
        return source

    configuration = source.get("configuration", {})
    completed_chunks = int(
        configuration.get("completed_chunks")
        or source.get("aggregate", {}).get("chunks")
        or 0
    )
    seed_start = configuration.get("seed_start")
    candidate_dirs: list[Path] = []
    if seed_start is not None:
        candidate_dirs.extend(sorted(source_path.parent.glob(f"*seed{seed_start}*parts")))
    candidate_dirs.extend(sorted(source_path.parent.glob("*parts")))

    for parts_dir in dict.fromkeys(candidate_dirs):
        part_files = sorted(parts_dir.glob("part_*.json"))
        if completed_chunks and len(part_files) < completed_chunks:
            continue
        if not part_files:
            continue
        source["chunks"] = [
            json.loads(part_path.read_text(encoding="utf-8"))
            for part_path in part_files[: completed_chunks or len(part_files)]
        ]
        source["configuration"]["parts_dir"] = str(parts_dir)
        return source

    raise FileNotFoundError(
        f"{source_path} does not contain chunks and no matching part_*.json directory was found"
    )


def validate_reconstruction(
    fitted: dict[str, Any],
    *,
    windows: int,
    seed: int,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(
        1000,
        fitted["probabilities"],
        size=windows,
    )
    payout_units = counts @ fitted["payout_multipliers"]
    rtps = payout_units / 1000.0
    return {
        "windows": windows,
        "profitable_share": float(np.mean(rtps > 1.0)),
        "rtp_min": float(np.min(rtps)),
        "rtp_p05": percentile(rtps, 0.05),
        "rtp_p25": percentile(rtps, 0.25),
        "rtp_median": percentile(rtps, 0.50),
        "rtp_p75": percentile(rtps, 0.75),
        "rtp_p95": percentile(rtps, 0.95),
        "rtp_max": float(np.max(rtps)),
        "rtp_mean": float(np.mean(rtps)),
        "rtp_sd": float(np.std(rtps, ddof=1)),
    }


@njit(cache=True)
def draw_increment(
    cumulative_probabilities: np.ndarray,
    increments: np.ndarray,
) -> float:
    value = np.random.random()
    for index in range(cumulative_probabilities.size):
        if value <= cumulative_probabilities[index]:
            return increments[index]
    return increments[-1]


@njit(cache=True, parallel=True)
def simulate_policy_paths(
    paths: int,
    start_bankroll: float,
    target_bankroll: float,
    max_hands: int,
    cumulative_probabilities: np.ndarray,
    increments: np.ndarray,
    bets: np.ndarray,
    up_multiple: float,
    down_multiple: float,
    fixed_bet: bool,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    statuses = np.zeros(paths, dtype=np.int8)
    hands = np.full(paths, max_hands, dtype=np.int64)
    ending_bankrolls = np.empty(paths, dtype=np.float64)
    max_bets = np.empty(paths, dtype=np.float64)

    for path_index in prange(paths):
        np.random.seed(seed + path_index * 104729)
        bankroll = start_bankroll
        tier_index = 0
        highest_bet = bets[0]

        for hand_index in range(1, max_hands + 1):
            if not fixed_bet:
                while (
                    tier_index > 0
                    and bankroll < down_multiple * bets[tier_index]
                ):
                    tier_index -= 1
                while (
                    tier_index + 1 < bets.size
                    and bankroll >= up_multiple * bets[tier_index + 1]
                ):
                    tier_index += 1

            bet = bets[0] if fixed_bet else bets[tier_index]
            if bankroll < bet:
                statuses[path_index] = -1
                hands[path_index] = hand_index - 1
                break

            if bet > highest_bet:
                highest_bet = bet
            bankroll += bet * draw_increment(cumulative_probabilities, increments)

            if bankroll >= target_bankroll:
                statuses[path_index] = 1
                hands[path_index] = hand_index
                break
        ending_bankrolls[path_index] = bankroll
        max_bets[path_index] = highest_bet

    return statuses, hands, ending_bankrolls, max_bets


def summarize_policy(
    *,
    name: str,
    statuses: np.ndarray,
    hands: np.ndarray,
    ending_bankrolls: np.ndarray,
    max_bets: np.ndarray,
    hands_per_day: int,
) -> dict[str, Any]:
    reached = statuses == 1
    ruined = statuses == -1
    unresolved = statuses == 0
    reached_hands = hands[reached]
    return {
        "name": name,
        "paths": int(statuses.size),
        "target_reached_rate": float(np.mean(reached)),
        "ruin_rate": float(np.mean(ruined)),
        "unresolved_rate": float(np.mean(unresolved)),
        "hands_to_target_p05": percentile(reached_hands, 0.05),
        "hands_to_target_p25": percentile(reached_hands, 0.25),
        "hands_to_target_median": percentile(reached_hands, 0.50),
        "hands_to_target_p75": percentile(reached_hands, 0.75),
        "hands_to_target_p95": percentile(reached_hands, 0.95),
        "days_to_target_median": (
            percentile(reached_hands, 0.50) / hands_per_day
            if reached_hands.size
            else None
        ),
        "days_to_target_p95": (
            percentile(reached_hands, 0.95) / hands_per_day
            if reached_hands.size
            else None
        ),
        "ending_bankroll_median": percentile(ending_bankrolls, 0.50),
        "max_bet_median": percentile(max_bets, 0.50),
    }


def adjustment_coefficient(
    probabilities: np.ndarray,
    increments: np.ndarray,
) -> float:
    def equation(value: float) -> float:
        return float(probabilities @ np.exp(-value * increments) - 1.0)

    return float(brentq(equation, 1e-9, 0.05))


def capital_multiple_risk_table(coefficient: float) -> list[dict[str, Any]]:
    rows = []
    for multiple in (100, 200, 300, 400, 500, 800, 1000, 1200, 1500, 2000):
        rows.append(
            {
                "capital_multiple": multiple,
                "approx_eventual_ruin_probability": math.exp(-coefficient * multiple),
            }
        )
    return rows


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("_runtime/analysis/janq_route_ev_100k_summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("_runtime/analysis/janq_bankroll_strategy.json"),
    )
    parser.add_argument("--bet-ladder", default=DEFAULT_BET_LADDER)
    parser.add_argument("--start-bankroll", type=float, default=1000.0)
    parser.add_argument("--target-bankroll", type=float, default=1_000_000.0)
    parser.add_argument("--hands-per-day", type=int, default=1000)
    parser.add_argument(
        "--start-bankrolls",
        default="1000,2000,4000,8000,12000,20000,30000,50000",
    )
    parser.add_argument("--paths", type=int, default=30000)
    parser.add_argument("--validation-windows", type=int, default=200000)
    parser.add_argument("--max-days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=20260616)
    args = parser.parse_args()

    bet_ladder = np.array(parse_float_list(args.bet_ladder), dtype=np.float64)
    start_bankrolls = parse_float_list(args.start_bankrolls)
    source = load_source(args.source)
    fitted = fit_bucket_distribution(source)
    validation = validate_reconstruction(
        fitted,
        windows=args.validation_windows,
        seed=args.seed,
    )
    probabilities = fitted["probabilities"]
    cumulative_probabilities = np.cumsum(probabilities)
    increments = fitted["net_increments_bets"]
    max_hands = args.max_days * args.hands_per_day

    policies = (
        ("fixed_bet_10", 0.0, 0.0, True),
        ("original_200x", 200.0, 200.0, False),
        ("hysteresis_300x_up_200x_down", 300.0, 200.0, False),
        ("hysteresis_500x_up_300x_down", 500.0, 300.0, False),
        ("hysteresis_600x_up_400x_down", 600.0, 400.0, False),
        ("hysteresis_800x_up_500x_down", 800.0, 500.0, False),
        ("hysteresis_1000x_up_800x_down", 1000.0, 800.0, False),
    )
    policy_results = []
    for policy_index, (name, up_multiple, down_multiple, fixed_bet) in enumerate(policies):
        statuses, hands, ending_bankrolls, max_bets = simulate_policy_paths(
            args.paths,
            args.start_bankroll,
            args.target_bankroll,
            max_hands,
            cumulative_probabilities,
            increments,
            bet_ladder,
            up_multiple,
            down_multiple,
            fixed_bet,
            args.seed + policy_index * 1_000_003,
        )
        policy_results.append(
            summarize_policy(
                name=name,
                statuses=statuses,
                hands=hands,
                ending_bankrolls=ending_bankrolls,
                max_bets=max_bets,
                hands_per_day=args.hands_per_day,
            )
        )

    sensitivity_results = []
    sensitivity_policies = (
        ("hysteresis_300x_up_200x_down", 300.0, 200.0),
        ("hysteresis_600x_up_400x_down", 600.0, 400.0),
        ("hysteresis_1000x_up_800x_down", 1000.0, 800.0),
    )
    for start_index, start_bankroll in enumerate(start_bankrolls):
        for policy_index, (name, up_multiple, down_multiple) in enumerate(
            sensitivity_policies
        ):
            statuses, hands, ending_bankrolls, max_bets = simulate_policy_paths(
                args.paths,
                float(start_bankroll),
                args.target_bankroll,
                max_hands,
                cumulative_probabilities,
                increments,
                bet_ladder,
                up_multiple,
                down_multiple,
                False,
                args.seed + 10_000_019 + start_index * 1_000_003 + policy_index * 97,
            )
            summary = summarize_policy(
                name=name,
                statuses=statuses,
                hands=hands,
                ending_bankrolls=ending_bankrolls,
                max_bets=max_bets,
                hands_per_day=args.hands_per_day,
            )
            summary["start_bankroll"] = start_bankroll
            sensitivity_results.append(summary)

    coefficient = adjustment_coefficient(probabilities, increments)
    configuration = source.get("configuration", {})
    generated_at = configuration.get("generated_at")
    if generated_at is None:
        generated_at = configuration.get("note", "unknown")
    output = {
        "source": str(args.source),
        "source_generated_at": generated_at,
        "source_warning": "Results are conditional on the supplied simulation summary and reconstructed payout buckets.",
        "method": {
            "description": (
                "Bounded least-squares estimates one representative payout multiplier "
                "per payout bucket from independent 1,000-session chunks. "
                "Monte Carlo paths then sample those reconstructed per-session outcomes "
                "independently and update the bet tier before every hand."
            ),
            "limitations": [
                "Within-bucket payout variation is replaced by a representative mean.",
                "Serial dependence between sessions is not modeled.",
                "The 100k run used physical-wall normal starting hands rather than verified server quotas.",
            ],
        },
        "fit": fitted,
        "validation": {
            "observed": {
                "profitable_share": source["aggregate"]["profitable_1000_session_chunks"]
                / source["aggregate"]["chunks"],
                "rtp_p05": source["aggregate"]["chunk_rtp_percentiles"]["p05"],
                "rtp_median": source["aggregate"]["chunk_rtp_percentiles"]["median"],
                "rtp_p95": source["aggregate"]["chunk_rtp_percentiles"]["p95"],
                "rtp_mean": source["aggregate"]["rtp"],
                "rtp_sd": float(np.std(fitted["actual_chunk_rtp"], ddof=1)),
            },
            "reconstructed": validation,
        },
        "simulation": {
            "start_bankroll": args.start_bankroll,
            "target_bankroll": args.target_bankroll,
            "hands_per_day": args.hands_per_day,
            "max_days": args.max_days,
            "paths": args.paths,
            "bet_ladder": bet_ladder,
            "policies": policy_results,
            "start_bankroll_sensitivity": sensitivity_results,
        },
        "risk": {
            "adjustment_coefficient": coefficient,
            "interpretation": (
                "Approximate eventual ruin probability at a fixed bet is exp(-R * capital_in_bets)."
            ),
            "capital_multiple_table": capital_multiple_risk_table(coefficient),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(json_ready(output), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(json_ready(output["simulation"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
