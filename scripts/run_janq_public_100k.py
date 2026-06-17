from __future__ import annotations

import argparse
import json
import math
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from janq_lab.analysis.economy_monte_carlo import run_economy_monte_carlo


def run_chunk(args: tuple[int, int, int]) -> dict:
    seed, sessions, bet = args
    return run_economy_monte_carlo(
        sessions=sessions,
        seed=seed,
        bet=bet,
        strategy="public",
        paren_table_mode="previous_han",
    ).to_dict()


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def aggregate(results: list[dict]) -> dict:
    sessions = sum(result["sessions"] for result in results)
    total_bet = sum(result["total_bet"] for result in results)
    total_payout = sum(result["total_payout"] for result in results)
    chunk_rtps = [result["return_to_player"] for result in results]
    chunk_average_nets = [result["avg_net"] for result in results]
    net_mean = statistics.mean(chunk_average_nets)
    net_standard_error = statistics.stdev(chunk_average_nets) / math.sqrt(len(results))

    average_keys = (
        "avg_paren_wins",
        "avg_yakuman_challenge_wins",
        "avg_yakuman_challenge_units",
    )
    payout_keys = (
        "normal_non_yakuman",
        "normal_yakuman",
        "paren_challenge",
        "yakuman_challenge",
    )

    counts = {
        "normal_wins": sum(result["normal_wins"] for result in results),
        "normal_riichi_count": sum(
            result["normal_riichi_count"] for result in results
        ),
        "paren_entries": sum(result["paren_entries"] for result in results),
        "yakuman_challenge_entries": sum(
            result["yakuman_challenge_entries"] for result in results
        ),
        "paren_attempts": sum(result["paren_attempts"] for result in results),
        "yakuman_challenge_attempts": sum(
            result["yakuman_challenge_attempts"] for result in results
        ),
    }
    weighted_averages = {
        key: sum(result[key] * result["sessions"] for result in results) / sessions
        for key in average_keys
    }
    payout_breakdown = {
        key: sum(result["payout_breakdown"][key] for result in results)
        for key in payout_keys
    }
    payout_histogram: dict[str, int] = {}
    for result in results:
        for bucket, count in result["payout_histogram"].items():
            payout_histogram[bucket] = payout_histogram.get(bucket, 0) + count

    win_counts: dict[str, int] = {}
    normal_yaku_levels: dict[str, int] = {}
    all_yaku_levels: dict[str, int] = {}
    for result in results:
        for key, count in result["win_counts"].items():
            win_counts[key] = win_counts.get(key, 0) + count
        for key, count in result["normal_yaku_levels"].items():
            normal_yaku_levels[key] = normal_yaku_levels.get(key, 0) + count
        for key, count in result["all_yaku_levels"].items():
            all_yaku_levels[key] = all_yaku_levels.get(key, 0) + count

    bonus_attempts = counts["paren_attempts"] + counts["yakuman_challenge_attempts"]
    bonus_wins = (
        win_counts.get("paren_wins", 0)
        + win_counts.get("yakuman_challenge_wins", 0)
    )
    all_hand_attempts = sessions + bonus_attempts
    all_hand_wins = counts["normal_wins"] + bonus_wins

    return {
        "sessions": sessions,
        "chunks": len(results),
        "sessions_per_chunk": results[0]["sessions"],
        "strategy": "public",
        "bet": results[0]["bet"],
        "total_bet": total_bet,
        "total_payout": total_payout,
        "total_net": total_payout - total_bet,
        "rtp": total_payout / total_bet,
        "average_net": (total_payout - total_bet) / sessions,
        "chunk_mean_net_95pct_interval": [
            net_mean - 1.96 * net_standard_error,
            net_mean + 1.96 * net_standard_error,
        ],
        "profitable_1000_session_chunks": sum(rtp > 1 for rtp in chunk_rtps),
        "chunk_rtp_percentiles": {
            "min": min(chunk_rtps),
            "p05": percentile(chunk_rtps, 0.05),
            "p25": percentile(chunk_rtps, 0.25),
            "median": percentile(chunk_rtps, 0.50),
            "p75": percentile(chunk_rtps, 0.75),
            "p95": percentile(chunk_rtps, 0.95),
            "max": max(chunk_rtps),
        },
        **counts,
        "normal_win_rate": counts["normal_wins"] / sessions,
        "normal_riichi_rate": counts["normal_riichi_count"] / sessions,
        "paren_entry_rate": counts["paren_entries"] / sessions,
        "yakuman_challenge_entry_rate": counts["yakuman_challenge_entries"] / sessions,
        "paren_win_rate": (
            win_counts.get("paren_wins", 0) / counts["paren_attempts"]
            if counts["paren_attempts"]
            else 0.0
        ),
        "yakuman_challenge_win_rate": (
            win_counts.get("yakuman_challenge_wins", 0)
            / counts["yakuman_challenge_attempts"]
            if counts["yakuman_challenge_attempts"]
            else 0.0
        ),
        "bonus_attempts": bonus_attempts,
        "bonus_wins": bonus_wins,
        "bonus_win_rate": bonus_wins / bonus_attempts if bonus_attempts else 0.0,
        "all_hand_attempts": all_hand_attempts,
        "all_hand_win_rate": (
            all_hand_wins / all_hand_attempts if all_hand_attempts else 0.0
        ),
        **weighted_averages,
        "win_counts": win_counts,
        "normal_yaku_levels": normal_yaku_levels,
        "all_yaku_levels": all_yaku_levels,
        "payout_breakdown": payout_breakdown,
        "payout_histogram": payout_histogram,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=100)
    parser.add_argument("--sessions-per-chunk", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=2026061600)
    parser.add_argument("--bet", type=int, default=10)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("_runtime/analysis/janq_public_100k_summary.json"),
    )
    args = parser.parse_args()

    jobs = [
        (args.seed + index, args.sessions_per_chunk, args.bet)
        for index in range(args.chunks)
    ]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        results = list(executor.map(run_chunk, jobs))

    output = {
        "configuration": {
            "chunks": args.chunks,
            "sessions_per_chunk": args.sessions_per_chunk,
            "seed_start": args.seed,
            "bet": args.bet,
            "workers": args.workers,
        },
        "aggregate": aggregate(results),
        "chunks": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(output["aggregate"], ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
