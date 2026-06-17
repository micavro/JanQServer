from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any

from janq_lab.analysis.economy_monte_carlo import (
    _payout_bucket,
    _ratios,
    _sample_normal_haipai,
    _strategy_functions,
    _yakuman_yaku_names,
    choose_enabled_record,
    choose_paren_number,
    simulate_session,
)
from janq_lab.analysis.stats import mean, normal_mean_interval, wilson_interval
from janq_lab.assets.nyukyu import load_tables
from janq_lab.assets.special import load_special_tables
from janq_lab.model.haipai import load_observed_normal_haipai


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def run_chunk(job: dict[str, Any]) -> dict[str, Any]:
    seed = job["seed"]
    sessions = job["sessions"]
    bet = job["bet"]
    paren_table_mode = job["paren_table_mode"]
    normal_haipai_source = job["normal_haipai_source"]
    observed_events_path = job["observed_events_path"]
    max_bonus_hands = job["max_bonus_hands"]

    rng = random.Random(seed)
    tables = load_tables()
    special = load_special_tables()
    choose_area, choose_discard = _strategy_functions("route_ev")
    observed_haipai = None
    if normal_haipai_source == "observed":
        observed_haipai = load_observed_normal_haipai(observed_events_path)

    nets: list[float] = []
    payouts: list[float] = []
    payout_histogram: Counter[str] = Counter()
    normal_yaku_levels: Counter[str] = Counter()
    all_yaku_levels: Counter[str] = Counter()
    yakuman_yaku_counts: Counter[str] = Counter()
    yakuman_by_phase = {
        "normal": Counter(),
        "paren_challenge": Counter(),
        "yakuman_challenge": Counter(),
    }
    payout_breakdown = Counter(
        {
            "normal_non_yakuman": 0,
            "normal_yakuman": 0,
            "paren_challenge": 0,
            "yakuman_challenge": 0,
        }
    )
    win_counts = Counter(
        {
            "normal_non_yakuman_wins": 0,
            "normal_yakuman_wins": 0,
            "paren_wins": 0,
            "yakuman_challenge_wins": 0,
        }
    )
    counts = Counter()
    normal_han_sum = 0
    normal_non_yakuman_han_sum = 0
    all_han_sum = 0
    all_non_yakuman_han_sum = 0
    all_scored_wins = 0
    all_non_yakuman_wins = 0
    normal_yakuman_units = 0
    all_yakuman_units = 0

    for _ in range(sessions):
        result = simulate_session(
            initial_hand=_sample_normal_haipai(
                rng,
                source=normal_haipai_source,
                observed_haipai=observed_haipai,
            ),
            rng=rng,
            bet=bet,
            strategy="route_ev",
            paren_table_mode=paren_table_mode,
            max_bonus_hands=max_bonus_hands,
            base_table=tables["nyukyu_base_table.bytes"],
            paren_table=tables["nyukyu_paren_table.bytes"],
            yakuman_table=tables["nyukyu_yakuman_table.bytes"],
            special_records=special,
            choose_area=choose_area,
            choose_discard=choose_discard,
        )

        nets.append(float(result.net))
        payouts.append(float(result.payout))
        payout_histogram[_payout_bucket(result.payout)] += 1
        payout_breakdown["normal_non_yakuman"] += result.normal_non_yakuman_payout
        payout_breakdown["normal_yakuman"] += result.normal_yakuman_payout
        payout_breakdown["paren_challenge"] += result.paren_payout
        payout_breakdown["yakuman_challenge"] += result.yakuman_challenge_payout

        if result.normal_win:
            counts["normal_wins"] += 1
        if result.normal_auto_surrender:
            counts["auto_surrenders"] += 1
        if result.entered_paren:
            counts["paren_entries"] += 1
        if result.entered_yakuman_challenge:
            counts["yakuman_challenge_entries"] += 1
        counts["paren_attempts"] += result.paren_attempts
        counts["yakuman_challenge_attempts"] += result.yakuman_challenge_attempts
        if result.normal_riichi:
            counts["normal_riichi_count"] += 1
        if result.normal_double_riichi:
            counts["normal_double_riichi_count"] += 1
        if result.normal_ippatsu_win:
            counts["normal_ippatsu_wins"] += 1

        if result.normal_score is not None:
            normal_yaku_levels[result.normal_score.yaku_level] += 1
            all_yaku_levels[result.normal_score.yaku_level] += 1
            all_scored_wins += 1
            normal_han_sum += result.normal_score.han
            all_han_sum += result.normal_score.han
            if result.normal_score.is_yakuman:
                win_counts["normal_yakuman_wins"] += 1
                normal_yakuman_units += max(1, result.normal_score.yakuman_count)
                all_yakuman_units += max(1, result.normal_score.yakuman_count)
                yakuman_by_phase["normal"].update(_yakuman_yaku_names(result.normal_score))
            else:
                win_counts["normal_non_yakuman_wins"] += 1
                normal_non_yakuman_han_sum += result.normal_score.han
                all_non_yakuman_han_sum += result.normal_score.han
                all_non_yakuman_wins += 1

        win_counts["paren_wins"] += result.paren_wins
        win_counts["yakuman_challenge_wins"] += result.yakuman_challenge_wins

        for score in result.paren_scores:
            all_yaku_levels[score.yaku_level] += 1
            all_scored_wins += 1
            all_han_sum += score.han
            if score.is_yakuman:
                all_yakuman_units += max(1, score.yakuman_count)
                yakuman_by_phase["paren_challenge"].update(_yakuman_yaku_names(score))
            else:
                all_non_yakuman_han_sum += score.han
                all_non_yakuman_wins += 1
        for score in result.yakuman_scores:
            all_yaku_levels[score.yaku_level] += 1
            all_scored_wins += 1
            all_han_sum += score.han
            all_yakuman_units += max(1, score.yakuman_count)
            yakuman_by_phase["yakuman_challenge"].update(_yakuman_yaku_names(score))

    for counter in yakuman_by_phase.values():
        yakuman_yaku_counts.update(counter)

    total_bet = sessions * bet
    total_payout = int(sum(payouts))
    total_net = total_payout - total_bet
    normal_wins = counts["normal_wins"]
    normal_non_yakuman_wins = win_counts["normal_non_yakuman_wins"]
    normal_counted_yakuman_wins = yakuman_by_phase["normal"]["kazoe_yakuman"]
    normal_natural_yakuman_wins = (
        win_counts["normal_yakuman_wins"] - normal_counted_yakuman_wins
    )
    normal_han_eligible_wins = normal_wins - normal_natural_yakuman_wins
    paren_attempts = counts["paren_attempts"]
    yakuman_challenge_attempts = counts["yakuman_challenge_attempts"]
    bonus_attempts = paren_attempts + yakuman_challenge_attempts
    bonus_wins = win_counts["paren_wins"] + win_counts["yakuman_challenge_wins"]
    all_hand_attempts = sessions + bonus_attempts
    all_hand_wins = normal_wins + bonus_wins

    return {
        "sessions": sessions,
        "seed": seed,
        "bet": bet,
        "strategy": "route_ev",
        "paren_table_mode": paren_table_mode,
        "normal_haipai_source": normal_haipai_source,
        "observed_haipai_hands": 0 if observed_haipai is None else len(observed_haipai.hands),
        "observed_haipai_ignored": 0 if observed_haipai is None else observed_haipai.ignored_hands,
        "total_bet": total_bet,
        "total_payout": total_payout,
        "total_net": total_net,
        "roi": total_net / total_bet,
        "return_to_player": total_payout / total_bet,
        "avg_payout": mean(payouts),
        "avg_net": mean(nets),
        "avg_net_ci95": normal_mean_interval(nets).__dict__,
        "normal_wins": normal_wins,
        "normal_win_rate": normal_wins / sessions,
        "normal_win_rate_ci95": wilson_interval(normal_wins, sessions).__dict__,
        "auto_surrenders": counts["auto_surrenders"],
        "auto_surrender_rate": counts["auto_surrenders"] / sessions,
        "paren_entries": counts["paren_entries"],
        "paren_entry_rate": counts["paren_entries"] / sessions,
        "yakuman_challenge_entries": counts["yakuman_challenge_entries"],
        "yakuman_challenge_entry_rate": counts["yakuman_challenge_entries"] / sessions,
        "paren_attempts": paren_attempts,
        "paren_win_rate": (
            win_counts["paren_wins"] / paren_attempts if paren_attempts else 0.0
        ),
        "yakuman_challenge_attempts": yakuman_challenge_attempts,
        "yakuman_challenge_win_rate": (
            win_counts["yakuman_challenge_wins"] / yakuman_challenge_attempts
            if yakuman_challenge_attempts
            else 0.0
        ),
        "bonus_attempts": bonus_attempts,
        "bonus_wins": bonus_wins,
        "bonus_win_rate": bonus_wins / bonus_attempts if bonus_attempts else 0.0,
        "all_hand_attempts": all_hand_attempts,
        "all_hand_win_rate": (
            all_hand_wins / all_hand_attempts if all_hand_attempts else 0.0
        ),
        "normal_yakuman_wins": win_counts["normal_yakuman_wins"],
        "normal_yakuman_rate": win_counts["normal_yakuman_wins"] / sessions,
        "normal_yakuman_units": normal_yakuman_units,
        "normal_yakuman_unit_rate": normal_yakuman_units / sessions,
        "all_yakuman_units": all_yakuman_units,
        "all_yakuman_unit_rate": all_yakuman_units / sessions,
        "normal_counted_yakuman_wins": normal_counted_yakuman_wins,
        "normal_natural_yakuman_wins": normal_natural_yakuman_wins,
        "normal_han_sum": normal_han_sum,
        "normal_non_yakuman_han_sum": normal_non_yakuman_han_sum,
        "all_han_sum": all_han_sum,
        "all_non_yakuman_han_sum": all_non_yakuman_han_sum,
        "all_scored_wins": all_scored_wins,
        "all_non_yakuman_wins": all_non_yakuman_wins,
        "avg_normal_han": (
            normal_han_sum / normal_han_eligible_wins
            if normal_han_eligible_wins
            else 0.0
        ),
        "avg_normal_han_zeroed_natural_yakuman": (
            normal_han_sum / normal_wins if normal_wins else 0.0
        ),
        "avg_normal_non_yakuman_han": (
            normal_non_yakuman_han_sum / normal_non_yakuman_wins
            if normal_non_yakuman_wins
            else 0.0
        ),
        "avg_all_han": all_han_sum / all_scored_wins if all_scored_wins else 0.0,
        "avg_all_non_yakuman_han": (
            all_non_yakuman_han_sum / all_non_yakuman_wins
            if all_non_yakuman_wins
            else 0.0
        ),
        "avg_paren_wins": win_counts["paren_wins"] / sessions,
        "avg_yakuman_challenge_wins": win_counts["yakuman_challenge_wins"] / sessions,
        "avg_yakuman_challenge_units": (
            (all_yakuman_units - normal_yakuman_units) / sessions
        ),
        "normal_yaku_levels": dict(sorted(normal_yaku_levels.items())),
        "all_yaku_levels": dict(sorted(all_yaku_levels.items())),
        "payout_histogram": dict(sorted(payout_histogram.items())),
        "payout_breakdown": dict(payout_breakdown),
        "payout_breakdown_ratio": _ratios(dict(payout_breakdown)),
        "win_counts": dict(win_counts),
        "yakuman_yaku_counts": dict(sorted(yakuman_yaku_counts.items())),
        "yakuman_yaku_counts_by_phase": {
            phase: dict(sorted(counter.items()))
            for phase, counter in yakuman_by_phase.items()
        },
        "normal_riichi_count": counts["normal_riichi_count"],
        "normal_riichi_rate": counts["normal_riichi_count"] / sessions,
        "normal_ippatsu_wins": counts["normal_ippatsu_wins"],
        "normal_double_riichi_count": counts["normal_double_riichi_count"],
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = sum(result["sessions"] for result in results)
    total_bet = sum(result["total_bet"] for result in results)
    total_payout = sum(result["total_payout"] for result in results)
    total_net = total_payout - total_bet
    chunk_rtps = [result["return_to_player"] for result in results]
    chunk_average_nets = [result["avg_net"] for result in results]
    net_mean = statistics.mean(chunk_average_nets)
    net_standard_error = (
        statistics.stdev(chunk_average_nets) / math.sqrt(len(results))
        if len(results) > 1
        else 0.0
    )

    sum_keys = [
        "normal_wins",
        "auto_surrenders",
        "paren_entries",
        "yakuman_challenge_entries",
        "paren_attempts",
        "yakuman_challenge_attempts",
        "normal_yakuman_wins",
        "normal_yakuman_units",
        "all_yakuman_units",
        "normal_riichi_count",
        "normal_ippatsu_wins",
        "normal_double_riichi_count",
        "normal_han_sum",
        "normal_non_yakuman_han_sum",
        "all_han_sum",
        "all_non_yakuman_han_sum",
        "all_scored_wins",
        "all_non_yakuman_wins",
    ]
    counts = {key: sum(result[key] for result in results) for key in sum_keys}

    payout_breakdown = Counter()
    payout_histogram = Counter()
    win_counts = Counter()
    normal_yaku_levels = Counter()
    all_yaku_levels = Counter()
    yakuman_yaku_counts = Counter()
    yakuman_by_phase = {
        "normal": Counter(),
        "paren_challenge": Counter(),
        "yakuman_challenge": Counter(),
    }
    for result in results:
        payout_breakdown.update(result["payout_breakdown"])
        payout_histogram.update(result["payout_histogram"])
        win_counts.update(result["win_counts"])
        normal_yaku_levels.update(result["normal_yaku_levels"])
        all_yaku_levels.update(result["all_yaku_levels"])
        yakuman_yaku_counts.update(result["yakuman_yaku_counts"])
        for phase, values in result["yakuman_yaku_counts_by_phase"].items():
            yakuman_by_phase[phase].update(values)

    normal_wins = counts["normal_wins"]
    normal_non_yakuman_wins = win_counts["normal_non_yakuman_wins"]
    normal_counted_yakuman_wins = yakuman_by_phase["normal"]["kazoe_yakuman"]
    normal_natural_yakuman_wins = (
        counts["normal_yakuman_wins"] - normal_counted_yakuman_wins
    )
    normal_han_eligible_wins = normal_wins - normal_natural_yakuman_wins
    paren_attempts = counts["paren_attempts"]
    yakuman_challenge_attempts = counts["yakuman_challenge_attempts"]
    bonus_attempts = paren_attempts + yakuman_challenge_attempts
    bonus_wins = win_counts["paren_wins"] + win_counts["yakuman_challenge_wins"]
    all_hand_attempts = sessions + bonus_attempts
    all_hand_wins = normal_wins + bonus_wins
    return {
        "sessions": sessions,
        "chunks": len(results),
        "sessions_per_chunk": results[0]["sessions"],
        "strategy": "route_ev",
        "bet": results[0]["bet"],
        "total_bet": total_bet,
        "total_payout": total_payout,
        "total_net": total_net,
        "roi": total_net / total_bet,
        "rtp": total_payout / total_bet,
        "return_to_player": total_payout / total_bet,
        "average_net": total_net / sessions,
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
        "normal_win_rate": normal_wins / sessions,
        "auto_surrender_rate": counts["auto_surrenders"] / sessions,
        "paren_entry_rate": counts["paren_entries"] / sessions,
        "yakuman_challenge_entry_rate": counts["yakuman_challenge_entries"] / sessions,
        "paren_win_rate": (
            win_counts["paren_wins"] / paren_attempts if paren_attempts else 0.0
        ),
        "yakuman_challenge_win_rate": (
            win_counts["yakuman_challenge_wins"] / yakuman_challenge_attempts
            if yakuman_challenge_attempts
            else 0.0
        ),
        "bonus_attempts": bonus_attempts,
        "bonus_wins": bonus_wins,
        "bonus_win_rate": bonus_wins / bonus_attempts if bonus_attempts else 0.0,
        "all_hand_attempts": all_hand_attempts,
        "all_hand_win_rate": (
            all_hand_wins / all_hand_attempts if all_hand_attempts else 0.0
        ),
        "normal_yakuman_rate": counts["normal_yakuman_wins"] / sessions,
        "normal_yakuman_unit_rate": counts["normal_yakuman_units"] / sessions,
        "all_yakuman_unit_rate": counts["all_yakuman_units"] / sessions,
        "normal_riichi_rate": counts["normal_riichi_count"] / sessions,
        "normal_counted_yakuman_wins": normal_counted_yakuman_wins,
        "normal_natural_yakuman_wins": normal_natural_yakuman_wins,
        "avg_normal_han": (
            counts["normal_han_sum"] / normal_han_eligible_wins
            if normal_han_eligible_wins
            else 0.0
        ),
        "avg_normal_han_zeroed_natural_yakuman": (
            counts["normal_han_sum"] / normal_wins if normal_wins else 0.0
        ),
        "avg_normal_non_yakuman_han": (
            counts["normal_non_yakuman_han_sum"] / normal_non_yakuman_wins
            if normal_non_yakuman_wins
            else 0.0
        ),
        "avg_all_han": (
            counts["all_han_sum"] / counts["all_scored_wins"]
            if counts["all_scored_wins"]
            else 0.0
        ),
        "avg_all_non_yakuman_han": (
            counts["all_non_yakuman_han_sum"] / counts["all_non_yakuman_wins"]
            if counts["all_non_yakuman_wins"]
            else 0.0
        ),
        "avg_paren_wins": win_counts["paren_wins"] / sessions,
        "avg_yakuman_challenge_wins": win_counts["yakuman_challenge_wins"] / sessions,
        "avg_yakuman_challenge_units": (
            (counts["all_yakuman_units"] - counts["normal_yakuman_units"]) / sessions
        ),
        "payout_breakdown": dict(sorted(payout_breakdown.items())),
        "payout_breakdown_ratio": _ratios(dict(payout_breakdown)),
        "payout_histogram": dict(sorted(payout_histogram.items())),
        "win_counts": dict(sorted(win_counts.items())),
        "normal_yaku_levels": dict(sorted(normal_yaku_levels.items())),
        "all_yaku_levels": dict(sorted(all_yaku_levels.items())),
        "yakuman_yaku_counts": dict(sorted(yakuman_yaku_counts.items())),
        "yakuman_yaku_counts_by_phase": {
            phase: dict(sorted(counter.items()))
            for phase, counter in yakuman_by_phase.items()
        },
    }


def normalize_han_metrics(result: dict[str, Any]) -> dict[str, Any]:
    normal_yakuman = result["yakuman_yaku_counts_by_phase"]["normal"]
    counted_yakuman_wins = int(normal_yakuman.get("kazoe_yakuman", 0))
    natural_yakuman_wins = result["normal_yakuman_wins"] - counted_yakuman_wins
    eligible_wins = result["normal_wins"] - natural_yakuman_wins

    result["normal_counted_yakuman_wins"] = counted_yakuman_wins
    result["normal_natural_yakuman_wins"] = natural_yakuman_wins
    result["avg_normal_han_zeroed_natural_yakuman"] = (
        result["normal_han_sum"] / result["normal_wins"]
        if result["normal_wins"]
        else 0.0
    )
    result["avg_normal_han"] = (
        result["normal_han_sum"] / eligible_wins if eligible_wins else 0.0
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=100)
    parser.add_argument("--sessions-per-chunk", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=2026061500)
    parser.add_argument("--bet", type=int, default=10)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--paren-table-mode", default="previous_han")
    parser.add_argument("--normal-haipai-source", default="wall")
    parser.add_argument("--observed-events", default=None)
    parser.add_argument("--max-bonus-hands", type=int, default=1000)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("_runtime/analysis/janq_route_ev_100k_summary.json"),
    )
    parser.add_argument(
        "--parts-dir",
        type=Path,
        default=Path("_runtime/analysis/janq_route_ev_100k_parts"),
    )
    args = parser.parse_args()

    args.parts_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    results: list[dict[str, Any]] = []
    for index in range(args.chunks):
        part_path = args.parts_dir / f"part_{index:03d}.json"
        if part_path.exists():
            results.append(
                normalize_han_metrics(
                    json.loads(part_path.read_text(encoding="utf-8"))
                )
            )
            continue
        jobs.append(
            {
                "index": index,
                "part_path": str(part_path),
                "seed": args.seed + index,
                "sessions": args.sessions_per_chunk,
                "bet": args.bet,
                "paren_table_mode": args.paren_table_mode,
                "normal_haipai_source": args.normal_haipai_source,
                "observed_events_path": args.observed_events,
                "max_bonus_hands": args.max_bonus_hands,
            }
        )

    if jobs:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(run_chunk, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                result = normalize_han_metrics(future.result())
                result["chunk_index"] = job["index"]
                Path(job["part_path"]).write_text(
                    json.dumps(result, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
                results.append(result)
                print(
                    json.dumps(
                        {
                            "completed": len(results),
                            "chunks": args.chunks,
                            "chunk_index": job["index"],
                            "rtp": result["return_to_player"],
                            "normal_win_rate": result["normal_win_rate"],
                        },
                        ensure_ascii=True,
                    ),
                    flush=True,
                )

    results = sorted(results, key=lambda result: result.get("chunk_index", result["seed"]))
    output = {
        "configuration": {
            "chunks": args.chunks,
            "sessions_per_chunk": args.sessions_per_chunk,
            "seed_start": args.seed,
            "bet": args.bet,
            "workers": args.workers,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "paren_table_mode": args.paren_table_mode,
            "normal_haipai_source": args.normal_haipai_source,
            "max_bonus_hands": args.max_bonus_hands,
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
