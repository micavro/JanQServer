from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
import time
from typing import Any

from janq_lab.analysis.economy_monte_carlo import (
    _payout_bucket,
    _sample_normal_haipai,
    _strategy_functions,
    _yakuman_yaku_names,
    simulate_session,
)
from janq_lab.assets.nyukyu import load_tables
from janq_lab.assets.special import load_special_tables
from janq_lab.model.haipai import load_observed_normal_haipai


YAKU_LEVEL_KEYS = (
    "YL_01HAN",
    "YL_02HAN",
    "YL_03HAN",
    "YL_04HAN",
    "YL_06HAN",
    "YL_08HAN",
    "YL_11HAN",
    "YL_YAKUMAN",
)

_WORKER: dict[str, Any] = {}


@dataclass
class StrategyAccumulator:
    strategy: str
    bet: int
    seed_start: int
    elapsed_samples: list[float] = field(default_factory=list)
    sessions: int = 0
    total_bet: int = 0
    total_payout: int = 0
    payout_breakdown: Counter[str] = field(default_factory=Counter)
    payout_histogram: Counter[str] = field(default_factory=Counter)
    win_counts: Counter[str] = field(default_factory=Counter)
    counts: Counter[str] = field(default_factory=Counter)
    normal_yaku_levels: Counter[str] = field(default_factory=Counter)
    all_yaku_levels: Counter[str] = field(default_factory=Counter)
    yakuman_yaku_counts: Counter[str] = field(default_factory=Counter)
    yakuman_by_phase: dict[str, Counter[str]] = field(
        default_factory=lambda: {
            "normal": Counter(),
            "paren_challenge": Counter(),
            "yakuman_challenge": Counter(),
        }
    )

    def add(self, result: dict[str, Any]) -> None:
        self.sessions += 1
        self.total_bet += self.bet
        self.total_payout += int(result["payout"])
        self.elapsed_samples.append(float(result["elapsed_seconds"]))

        self.payout_breakdown.update(result["payout_breakdown"])
        self.payout_histogram.update({_payout_bucket(int(result["payout"])): 1})
        self.win_counts.update(result["win_counts"])
        self.counts.update(result["counts"])
        self.normal_yaku_levels.update(result["normal_yaku_levels"])
        self.all_yaku_levels.update(result["all_yaku_levels"])
        for phase, values in result["yakuman_yaku_counts_by_phase"].items():
            self.yakuman_by_phase[phase].update(values)
            self.yakuman_yaku_counts.update(values)

    def to_summary(self) -> dict[str, Any]:
        total_net = self.total_payout - self.total_bet
        sessions = self.sessions
        normal_wins = self.counts["normal_wins"]
        normal_yakuman_wins = self.win_counts["normal_yakuman_wins"]
        normal_counted_yakuman_wins = self.yakuman_by_phase["normal"]["kazoe_yakuman"]
        normal_natural_yakuman_wins = normal_yakuman_wins - normal_counted_yakuman_wins
        normal_non_yakuman_wins = self.win_counts["normal_non_yakuman_wins"]
        normal_han_eligible_wins = normal_wins - normal_natural_yakuman_wins
        paren_attempts = self.counts["paren_attempts"]
        yakuman_attempts = self.counts["yakuman_challenge_attempts"]
        bonus_attempts = paren_attempts + yakuman_attempts
        bonus_wins = self.win_counts["paren_wins"] + self.win_counts["yakuman_challenge_wins"]
        all_hand_attempts = sessions + bonus_attempts
        all_hand_wins = normal_wins + bonus_wins
        normal_yakuman_units = self.counts["normal_yakuman_units"]
        all_yakuman_units = self.counts["all_yakuman_units"]
        all_scored_wins = self.counts["all_scored_wins"]
        all_non_yakuman_wins = self.counts["all_non_yakuman_wins"]
        elapsed = self.elapsed_samples

        return {
            "strategy": self.strategy,
            "sessions": sessions,
            "seed_start": self.seed_start,
            "bet": self.bet,
            "total_bet": self.total_bet,
            "total_payout": self.total_payout,
            "total_net": total_net,
            "roi": total_net / self.total_bet if self.total_bet else 0.0,
            "rtp": self.total_payout / self.total_bet if self.total_bet else 0.0,
            "average_net": total_net / sessions if sessions else 0.0,
            "normal_wins": normal_wins,
            "normal_win_rate": normal_wins / sessions if sessions else 0.0,
            "normal_auto_surrenders": self.counts["normal_auto_surrenders"],
            "normal_auto_surrender_rate": (
                self.counts["normal_auto_surrenders"] / sessions if sessions else 0.0
            ),
            "paren_entries": self.counts["paren_entries"],
            "paren_entry_rate": self.counts["paren_entries"] / sessions if sessions else 0.0,
            "yakuman_challenge_entries": self.counts["yakuman_challenge_entries"],
            "yakuman_challenge_entry_rate": (
                self.counts["yakuman_challenge_entries"] / sessions if sessions else 0.0
            ),
            "paren_attempts": paren_attempts,
            "paren_win_rate": (
                self.win_counts["paren_wins"] / paren_attempts if paren_attempts else 0.0
            ),
            "yakuman_challenge_attempts": yakuman_attempts,
            "yakuman_challenge_win_rate": (
                self.win_counts["yakuman_challenge_wins"] / yakuman_attempts
                if yakuman_attempts
                else 0.0
            ),
            "bonus_attempts": bonus_attempts,
            "bonus_wins": bonus_wins,
            "bonus_win_rate": bonus_wins / bonus_attempts if bonus_attempts else 0.0,
            "all_hand_attempts": all_hand_attempts,
            "all_hand_win_rate": (
                all_hand_wins / all_hand_attempts if all_hand_attempts else 0.0
            ),
            "normal_yakuman_wins": normal_yakuman_wins,
            "normal_yakuman_rate": normal_yakuman_wins / sessions if sessions else 0.0,
            "normal_yakuman_units": normal_yakuman_units,
            "normal_yakuman_unit_rate": normal_yakuman_units / sessions if sessions else 0.0,
            "all_yakuman_units": all_yakuman_units,
            "all_yakuman_unit_rate": all_yakuman_units / sessions if sessions else 0.0,
            "normal_counted_yakuman_wins": normal_counted_yakuman_wins,
            "normal_natural_yakuman_wins": normal_natural_yakuman_wins,
            "avg_normal_han": (
                self.counts["normal_han_sum"] / normal_han_eligible_wins
                if normal_han_eligible_wins
                else 0.0
            ),
            "avg_normal_han_zeroed_natural_yakuman": (
                self.counts["normal_han_sum"] / normal_wins if normal_wins else 0.0
            ),
            "avg_normal_non_yakuman_han": (
                self.counts["normal_non_yakuman_han_sum"] / normal_non_yakuman_wins
                if normal_non_yakuman_wins
                else 0.0
            ),
            "avg_all_han": (
                self.counts["all_han_sum"] / all_scored_wins if all_scored_wins else 0.0
            ),
            "avg_all_non_yakuman_han": (
                self.counts["all_non_yakuman_han_sum"] / all_non_yakuman_wins
                if all_non_yakuman_wins
                else 0.0
            ),
            "normal_riichi_count": self.counts["normal_riichi_count"],
            "normal_riichi_rate": (
                self.counts["normal_riichi_count"] / sessions if sessions else 0.0
            ),
            "normal_ippatsu_wins": self.counts["normal_ippatsu_wins"],
            "normal_double_riichi_count": self.counts["normal_double_riichi_count"],
            "payout_breakdown": dict(sorted(self.payout_breakdown.items())),
            "payout_breakdown_ratio": _ratios(dict(self.payout_breakdown)),
            "payout_histogram": dict(sorted(self.payout_histogram.items(), key=_bucket_key)),
            "win_counts": dict(sorted(self.win_counts.items())),
            "normal_yaku_levels": dict(sorted(self.normal_yaku_levels.items())),
            "all_yaku_levels": dict(sorted(self.all_yaku_levels.items())),
            "yakuman_yaku_counts": dict(sorted(self.yakuman_yaku_counts.items())),
            "yakuman_yaku_counts_by_phase": {
                phase: dict(sorted(counter.items()))
                for phase, counter in self.yakuman_by_phase.items()
            },
            "timing": timing_summary(elapsed),
        }


def init_worker(
    strategy: str,
    bet: int,
    paren_table_mode: str,
    normal_haipai_source: str,
    observed_events_path: str | None,
    max_bonus_hands: int,
) -> None:
    tables = load_tables()
    observed = None
    if normal_haipai_source == "observed":
        if not observed_events_path:
            raise ValueError("observed-events is required for observed haipai")
        observed = load_observed_normal_haipai(observed_events_path)
    choose_area, choose_discard = _strategy_functions(strategy)
    _WORKER.clear()
    _WORKER.update(
        {
            "strategy": strategy,
            "bet": bet,
            "paren_table_mode": paren_table_mode,
            "normal_haipai_source": normal_haipai_source,
            "observed_haipai": observed,
            "max_bonus_hands": max_bonus_hands,
            "base_table": tables["nyukyu_base_table.bytes"],
            "paren_table": tables["nyukyu_paren_table.bytes"],
            "yakuman_table": tables["nyukyu_yakuman_table.bytes"],
            "special_records": load_special_tables(),
            "choose_area": choose_area,
            "choose_discard": choose_discard,
        }
    )


def simulate_one(seed: int) -> dict[str, Any]:
    started = time.perf_counter()
    rng = random.Random(seed)
    result = simulate_session(
        initial_hand=_sample_normal_haipai(
            rng,
            source=_WORKER["normal_haipai_source"],
            observed_haipai=_WORKER["observed_haipai"],
        ),
        rng=rng,
        bet=_WORKER["bet"],
        strategy=_WORKER["strategy"],
        paren_table_mode=_WORKER["paren_table_mode"],
        max_bonus_hands=_WORKER["max_bonus_hands"],
        base_table=_WORKER["base_table"],
        paren_table=_WORKER["paren_table"],
        yakuman_table=_WORKER["yakuman_table"],
        special_records=_WORKER["special_records"],
        choose_area=_WORKER["choose_area"],
        choose_discard=_WORKER["choose_discard"],
    )
    elapsed = time.perf_counter() - started
    return session_to_record(seed, result, elapsed)


def session_to_record(seed: int, result: Any, elapsed: float) -> dict[str, Any]:
    payout_breakdown = {
        "normal_non_yakuman": result.normal_non_yakuman_payout,
        "normal_yakuman": result.normal_yakuman_payout,
        "paren_challenge": result.paren_payout,
        "yakuman_challenge": result.yakuman_challenge_payout,
    }
    win_counts = Counter(
        {
            "normal_non_yakuman_wins": 0,
            "normal_yakuman_wins": 0,
            "paren_wins": result.paren_wins,
            "yakuman_challenge_wins": result.yakuman_challenge_wins,
        }
    )
    counts = Counter(
        {
            "normal_wins": int(result.normal_win),
            "normal_auto_surrenders": int(result.normal_auto_surrender),
            "paren_entries": int(result.entered_paren),
            "yakuman_challenge_entries": int(result.entered_yakuman_challenge),
            "paren_attempts": result.paren_attempts,
            "yakuman_challenge_attempts": result.yakuman_challenge_attempts,
            "normal_riichi_count": int(result.normal_riichi),
            "normal_ippatsu_wins": int(result.normal_ippatsu_win),
            "normal_double_riichi_count": int(result.normal_double_riichi),
            "normal_han_sum": 0,
            "normal_non_yakuman_han_sum": 0,
            "all_han_sum": 0,
            "all_non_yakuman_han_sum": 0,
            "all_scored_wins": 0,
            "all_non_yakuman_wins": 0,
            "normal_yakuman_units": 0,
            "all_yakuman_units": 0,
        }
    )
    normal_yaku_levels: Counter[str] = Counter()
    all_yaku_levels: Counter[str] = Counter()
    yakuman_by_phase = {
        "normal": Counter(),
        "paren_challenge": Counter(),
        "yakuman_challenge": Counter(),
    }

    if result.normal_score is not None:
        score = result.normal_score
        normal_yaku_levels[score.yaku_level] += 1
        all_yaku_levels[score.yaku_level] += 1
        counts["normal_han_sum"] += score.han
        counts["all_han_sum"] += score.han
        counts["all_scored_wins"] += 1
        if score.is_yakuman:
            win_counts["normal_yakuman_wins"] += 1
            units = max(1, score.yakuman_count)
            counts["normal_yakuman_units"] += units
            counts["all_yakuman_units"] += units
            yakuman_by_phase["normal"].update(_yakuman_yaku_names(score))
        else:
            win_counts["normal_non_yakuman_wins"] += 1
            counts["normal_non_yakuman_han_sum"] += score.han
            counts["all_non_yakuman_han_sum"] += score.han
            counts["all_non_yakuman_wins"] += 1

    for score in result.paren_scores:
        all_yaku_levels[score.yaku_level] += 1
        counts["all_han_sum"] += score.han
        counts["all_scored_wins"] += 1
        if score.is_yakuman:
            counts["all_yakuman_units"] += max(1, score.yakuman_count)
            yakuman_by_phase["paren_challenge"].update(_yakuman_yaku_names(score))
        else:
            counts["all_non_yakuman_han_sum"] += score.han
            counts["all_non_yakuman_wins"] += 1
    for score in result.yakuman_scores:
        all_yaku_levels[score.yaku_level] += 1
        counts["all_han_sum"] += score.han
        counts["all_scored_wins"] += 1
        counts["all_yakuman_units"] += max(1, score.yakuman_count)
        yakuman_by_phase["yakuman_challenge"].update(_yakuman_yaku_names(score))

    return {
        "seed": seed,
        "payout": result.payout,
        "elapsed_seconds": elapsed,
        "payout_breakdown": payout_breakdown,
        "win_counts": dict(win_counts),
        "counts": dict(counts),
        "normal_yaku_levels": dict(normal_yaku_levels),
        "all_yaku_levels": dict(all_yaku_levels),
        "yakuman_yaku_counts_by_phase": {
            phase: dict(counter) for phase, counter in yakuman_by_phase.items()
        },
    }


def run_fixed_count(
    *,
    phase: str,
    strategies: tuple[str, ...],
    count: int,
    seed_start: int,
    workers: dict[str, int],
    args: argparse.Namespace,
    output_dir: Path,
) -> tuple[dict[str, StrategyAccumulator], dict[str, float]]:
    accs = {name: StrategyAccumulator(name, args.bet, seed_start) for name in strategies}
    started_at = {name: time.perf_counter() for name in strategies}
    finished_at: dict[str, float] = {}
    with _executors(strategies, workers, args) as executors:
        futures = {}
        for name in strategies:
            for index in range(count):
                seed = seed_start + index
                futures[executors[name].submit(simulate_one, seed)] = name
        completed = 0
        total = count * len(strategies)
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                name = futures.pop(future)
                accs[name].add(future.result())
                completed += 1
                if accs[name].sessions == count:
                    finished_at[name] = time.perf_counter()
            if completed % args.progress_every == 0 or completed == total:
                write_progress(output_dir, phase, accs, total=total, completed=completed)
    wall_seconds = {
        name: finished_at.get(name, time.perf_counter()) - started_at[name]
        for name in strategies
    }
    return accs, wall_seconds


def run_budget(
    *,
    strategies: tuple[str, ...],
    seed_start: int,
    budget_seconds: float,
    reserve_seconds: float,
    workers: dict[str, int],
    args: argparse.Namespace,
    output_dir: Path,
) -> tuple[dict[str, StrategyAccumulator], dict[str, Any]]:
    accs = {name: StrategyAccumulator(name, args.bet, seed_start) for name in strategies}
    deadline = time.perf_counter() + budget_seconds
    stop_submitting_at = deadline - reserve_seconds
    started = time.perf_counter()
    next_index = {name: 0 for name in strategies}
    submitted = {name: 0 for name in strategies}
    max_pending = {name: max(1, workers[name] * args.queue_multiplier) for name in strategies}

    with _executors(strategies, workers, args) as executors:
        futures: dict[Any, str] = {}

        def submit_until_full(name: str) -> None:
            while (
                time.perf_counter() < stop_submitting_at
                and sum(1 for strategy in futures.values() if strategy == name)
                < max_pending[name]
            ):
                seed = seed_start + next_index[name]
                next_index[name] += 1
                submitted[name] += 1
                futures[executors[name].submit(simulate_one, seed)] = name

        for strategy in strategies:
            submit_until_full(strategy)

        last_progress = 0
        while futures:
            done, _ = wait(futures, timeout=5.0, return_when=FIRST_COMPLETED)
            if not done:
                now = time.perf_counter()
                if now - last_progress >= args.progress_interval_seconds:
                    write_progress(output_dir, "budget", accs, submitted=submitted)
                    last_progress = now
                continue
            for future in done:
                name = futures.pop(future)
                accs[name].add(future.result())
                submit_until_full(name)

            completed = sum(acc.sessions for acc in accs.values())
            now = time.perf_counter()
            if (
                completed % args.progress_every == 0
                or now - last_progress >= args.progress_interval_seconds
                or not futures
            ):
                write_progress(output_dir, "budget", accs, submitted=submitted)
                last_progress = now

    metadata = {
        "budget_seconds": budget_seconds,
        "reserve_seconds": reserve_seconds,
        "started_at_monotonic": started,
        "elapsed_wall_seconds": time.perf_counter() - started,
        "submitted": submitted,
        "stop_reason": "deadline_reserve_reached",
    }
    return accs, metadata


class _executors:
    def __init__(
        self,
        strategies: tuple[str, ...],
        workers: dict[str, int],
        args: argparse.Namespace,
    ) -> None:
        self.strategies = strategies
        self.workers = workers
        self.args = args
        self.executors: dict[str, ProcessPoolExecutor] = {}

    def __enter__(self) -> dict[str, ProcessPoolExecutor]:
        for strategy in self.strategies:
            self.executors[strategy] = ProcessPoolExecutor(
                max_workers=self.workers[strategy],
                initializer=init_worker,
                initargs=(
                    strategy,
                    self.args.bet,
                    self.args.paren_table_mode,
                    self.args.normal_haipai_source,
                    self.args.observed_events,
                    self.args.max_bonus_hands,
                ),
            )
        return self.executors

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        for executor in self.executors.values():
            executor.shutdown(wait=True, cancel_futures=True)


def write_progress(
    output_dir: Path,
    phase: str,
    accs: dict[str, StrategyAccumulator],
    **extra: Any,
) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "summaries": {name: acc.to_summary() for name, acc in accs.items()},
        **extra,
    }
    (output_dir / "latest_progress.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def timing_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "samples": 0,
            "mean_seconds": None,
            "median_seconds": None,
            "p95_seconds": None,
            "max_seconds": None,
        }
    ordered = sorted(values)
    return {
        "samples": len(values),
        "mean_seconds": statistics.fmean(values),
        "median_seconds": quantile_sorted(ordered, 0.50),
        "p95_seconds": quantile_sorted(ordered, 0.95),
        "max_seconds": ordered[-1],
    }


def quantile_sorted(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _ratios(values: dict[str, int]) -> dict[str, float]:
    total = sum(values.values())
    if total <= 0:
        return {key: 0.0 for key in values}
    return {key: value / total for key, value in values.items()}


def _bucket_key(item: tuple[str, int]) -> int:
    bucket = item[0]
    if bucket == "0":
        return 0
    return int(bucket.split("-")[0].replace("+", ""))


def parse_workers(value: str, strategies: tuple[str, ...]) -> dict[str, int]:
    if "," not in value and "=" not in value:
        workers = int(value)
        return {strategy: workers for strategy in strategies}
    parsed: dict[str, int] = {}
    for item in value.split(","):
        if not item.strip():
            continue
        name, raw_count = item.split("=", 1)
        parsed[name.strip()] = int(raw_count)
    missing = set(strategies) - set(parsed)
    if missing:
        raise ValueError(f"missing worker counts for: {', '.join(sorted(missing))}")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Budgeted route_ev vs route_ev2 comparison.")
    parser.add_argument("--strategies", default="route_ev,route_ev2")
    parser.add_argument("--seed", type=int, default=2026061701)
    parser.add_argument("--bet", type=int, default=10)
    parser.add_argument("--benchmark-sessions", type=int, default=100)
    parser.add_argument("--budget-seconds", type=float, default=5.5 * 3600)
    parser.add_argument("--reserve-seconds", type=float, default=180.0)
    parser.add_argument("--workers", default="route_ev=4,route_ev2=4")
    parser.add_argument("--queue-multiplier", type=int, default=2)
    parser.add_argument("--paren-table-mode", default="previous_han")
    parser.add_argument("--normal-haipai-source", choices=("wall", "observed"), default="wall")
    parser.add_argument("--observed-events", default=None)
    parser.add_argument("--max-bonus-hands", type=int, default=1000)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--progress-interval-seconds", type=float, default=60.0)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    strategies = tuple(item.strip() for item in args.strategies.split(",") if item.strip())
    if not strategies:
        raise ValueError("at least one strategy is required")
    workers = parse_workers(args.workers, strategies)
    output_dir = args.output_dir or Path(
        "_runtime/analysis"
    ) / f"strategy_budget_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "seed": args.seed,
        "bet": args.bet,
        "benchmark_sessions": args.benchmark_sessions,
        "budget_seconds": args.budget_seconds,
        "reserve_seconds": args.reserve_seconds,
        "workers": workers,
        "strategies": strategies,
        "paren_table_mode": args.paren_table_mode,
        "normal_haipai_source": args.normal_haipai_source,
        "max_bonus_hands": args.max_bonus_hands,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"phase": "benchmark_start", "output_dir": str(output_dir), **config}, ensure_ascii=True), flush=True)

    benchmark_accs, benchmark_wall = run_fixed_count(
        phase="benchmark",
        strategies=strategies,
        count=args.benchmark_sessions,
        seed_start=args.seed,
        workers=workers,
        args=args,
        output_dir=output_dir,
    )
    benchmark = {
        name: {
            **acc.to_summary(),
            "wall_seconds": benchmark_wall[name],
            "wall_throughput_sessions_per_second": (
                acc.sessions / benchmark_wall[name] if benchmark_wall[name] else 0.0
            ),
            "conservative_estimated_sessions_in_budget": math.floor(
                max(0.0, args.budget_seconds - args.reserve_seconds)
                * (acc.sessions / benchmark_wall[name] if benchmark_wall[name] else 0.0)
            ),
        }
        for name, acc in benchmark_accs.items()
    }
    (output_dir / "benchmark.json").write_text(
        json.dumps(benchmark, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"phase": "benchmark_done", "benchmark": benchmark}, ensure_ascii=True), flush=True)

    budget_accs, budget_metadata = run_budget(
        strategies=strategies,
        seed_start=args.seed,
        budget_seconds=args.budget_seconds,
        reserve_seconds=args.reserve_seconds,
        workers=workers,
        args=args,
        output_dir=output_dir,
    )
    summary = {
        "configuration": config,
        "benchmark": benchmark,
        "budget_run": {
            "metadata": budget_metadata,
            "summaries": {name: acc.to_summary() for name, acc in budget_accs.items()},
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"phase": "done", "summary": summary}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
