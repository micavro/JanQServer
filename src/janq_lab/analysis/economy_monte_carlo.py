"""Monte Carlo for JanQ session economics."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import argparse
import json
import random
from typing import Any

from janq_lab.analysis.stats import Interval, mean, normal_mean_interval, wilson_interval
from janq_lab.assets.nyukyu import load_tables
from janq_lab.assets.special import SpecialHandRecord, load_special_tables
from janq_lab.model.economy import payout_for_score, yakuman_challenge_payout
from janq_lab.model.haipai import ObservedHaipaiSet, load_observed_normal_haipai, random_wall_hand
from janq_lab.model.hand import TileSet, tile_set
from janq_lab.model.scoring import JanqScore, score_hand
from janq_lab.model.simulator import SimulationResult, simulate_hand
from janq_lab.strategy.greedy import choose_greedy_area, choose_greedy_discard
from janq_lab.strategy.bonus import choose_bonus_area, choose_bonus_discard
from janq_lab.strategy.public import choose_public_area, choose_public_discard
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard


YAKUMAN_YAKU_NAMES = frozenset(
    (
        "tenhou",
        "kokushi",
        "tsuuiisou",
        "ryuuiisou",
        "chinroutou",
        "chuuren",
        "suuankou",
        "daisangen",
        "daisuushi",
        "shousuushi",
        "kazoe_yakuman",
    )
)


@dataclass(frozen=True)
class ScoredHandResult:
    simulation: SimulationResult
    score: JanqScore | None
    dora_id: int | None
    ura_dora_id: int | None
    payout: int


@dataclass(frozen=True)
class SessionResult:
    bet: int
    payout: int
    normal_win: bool
    paren_wins: int
    yakuman_challenge_wins: int
    yakuman_challenge_units: int
    normal_score: JanqScore | None
    paren_scores: tuple[JanqScore, ...]
    yakuman_scores: tuple[JanqScore, ...]
    normal_non_yakuman_payout: int = 0
    normal_yakuman_payout: int = 0
    paren_payout: int = 0
    yakuman_challenge_payout: int = 0
    normal_riichi: bool = False
    normal_double_riichi: bool = False
    normal_ippatsu_win: bool = False

    @property
    def net(self) -> int:
        return self.payout - self.bet

    @property
    def entered_paren(self) -> bool:
        return self.normal_win and self.normal_score is not None and not self.normal_score.is_yakuman

    @property
    def entered_yakuman_challenge(self) -> bool:
        return self.yakuman_challenge_wins > 0 or (
            self.normal_score is not None and self.normal_score.is_yakuman
        ) or any(score.is_yakuman for score in self.paren_scores) or self.paren_wins >= 7


@dataclass(frozen=True)
class EconomySummary:
    sessions: int
    seed: int
    bet: int
    strategy: str
    paren_table_mode: str
    normal_haipai_source: str
    observed_haipai_hands: int
    observed_haipai_ignored: int
    total_bet: int
    total_payout: int
    total_net: int
    roi: float
    return_to_player: float
    avg_payout: float
    avg_net: float
    avg_net_ci95: Interval
    normal_wins: int
    normal_win_rate: float
    normal_win_rate_ci95: Interval
    paren_entries: int
    paren_entry_rate: float
    yakuman_challenge_entries: int
    yakuman_challenge_entry_rate: float
    avg_paren_wins: float
    avg_yakuman_challenge_wins: float
    avg_yakuman_challenge_units: float
    normal_yaku_levels: dict[str, int]
    all_yaku_levels: dict[str, int]
    payout_histogram: dict[str, int]
    payout_breakdown: dict[str, int]
    payout_breakdown_ratio: dict[str, float]
    win_counts: dict[str, int]
    yakuman_yaku_counts: dict[str, int]
    yakuman_yaku_counts_by_phase: dict[str, dict[str, int]]
    normal_riichi_count: int = 0
    normal_riichi_rate: float = 0.0
    normal_ippatsu_wins: int = 0
    normal_double_riichi_count: int = 0
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "seed": self.seed,
            "bet": self.bet,
            "strategy": self.strategy,
            "paren_table_mode": self.paren_table_mode,
            "normal_haipai_source": self.normal_haipai_source,
            "observed_haipai_hands": self.observed_haipai_hands,
            "observed_haipai_ignored": self.observed_haipai_ignored,
            "total_bet": self.total_bet,
            "total_payout": self.total_payout,
            "total_net": self.total_net,
            "roi": self.roi,
            "return_to_player": self.return_to_player,
            "avg_payout": self.avg_payout,
            "avg_net": self.avg_net,
            "avg_net_ci95": self.avg_net_ci95.__dict__,
            "normal_wins": self.normal_wins,
            "normal_win_rate": self.normal_win_rate,
            "normal_win_rate_ci95": self.normal_win_rate_ci95.__dict__,
            "paren_entries": self.paren_entries,
            "paren_entry_rate": self.paren_entry_rate,
            "yakuman_challenge_entries": self.yakuman_challenge_entries,
            "yakuman_challenge_entry_rate": self.yakuman_challenge_entry_rate,
            "avg_paren_wins": self.avg_paren_wins,
            "avg_yakuman_challenge_wins": self.avg_yakuman_challenge_wins,
            "avg_yakuman_challenge_units": self.avg_yakuman_challenge_units,
            "normal_yaku_levels": self.normal_yaku_levels,
            "all_yaku_levels": self.all_yaku_levels,
            "payout_histogram": self.payout_histogram,
            "payout_breakdown": self.payout_breakdown,
            "payout_breakdown_ratio": self.payout_breakdown_ratio,
            "win_counts": self.win_counts,
            "yakuman_yaku_counts": self.yakuman_yaku_counts,
            "yakuman_yaku_counts_by_phase": self.yakuman_yaku_counts_by_phase,
            "normal_riichi_count": self.normal_riichi_count,
            "normal_riichi_rate": self.normal_riichi_rate,
            "normal_ippatsu_wins": self.normal_ippatsu_wins,
            "normal_double_riichi_count": self.normal_double_riichi_count,
            "assumptions": self.assumptions,
        }


def run_economy_monte_carlo(
    *,
    sessions: int,
    seed: int = 1,
    bet: int = 10,
    strategy: str = "public",
    paren_table_mode: str = "previous_han",
    normal_haipai_source: str = "wall",
    observed_events_path: str | None = None,
    max_bonus_hands: int = 1000,
) -> EconomySummary:
    if sessions < 1:
        raise ValueError("sessions must be positive")
    if bet < 1:
        raise ValueError("bet must be positive")

    rng = random.Random(seed)
    tables = load_tables()
    special = load_special_tables()
    choose_area, choose_discard = _strategy_functions(strategy)
    observed_haipai = _load_initial_hand_source(
        normal_haipai_source,
        observed_events_path=observed_events_path,
    )

    session_results: list[SessionResult] = []
    for _ in range(sessions):
        session_results.append(
            simulate_session(
                initial_hand=_sample_normal_haipai(
                    rng,
                    source=normal_haipai_source,
                    observed_haipai=observed_haipai,
                ),
                rng=rng,
                bet=bet,
                strategy=strategy,
                paren_table_mode=paren_table_mode,
                max_bonus_hands=max_bonus_hands,
                base_table=tables["nyukyu_base_table.bytes"],
                paren_table=tables["nyukyu_paren_table.bytes"],
                yakuman_table=tables["nyukyu_yakuman_table.bytes"],
                special_records=special,
                choose_area=choose_area,
                choose_discard=choose_discard,
            )
        )

    nets = [float(result.net) for result in session_results]
    payouts = [float(result.payout) for result in session_results]
    normal_wins = sum(1 for result in session_results if result.normal_win)
    normal_riichi_count = sum(1 for result in session_results if result.normal_riichi)
    normal_ippatsu_wins = sum(1 for result in session_results if result.normal_ippatsu_win)
    normal_double_riichi_count = sum(1 for result in session_results if result.normal_double_riichi)
    paren_entries = sum(1 for result in session_results if result.entered_paren)
    yakuman_entries = sum(1 for result in session_results if result.entered_yakuman_challenge)
    normal_levels = Counter(
        result.normal_score.yaku_level
        for result in session_results
        if result.normal_score is not None
    )
    all_levels: Counter[str] = Counter()
    payout_histogram: Counter[str] = Counter()
    for result in session_results:
        payout_histogram[_payout_bucket(result.payout)] += 1
        if result.normal_score is not None:
            all_levels[result.normal_score.yaku_level] += 1
        for score in result.paren_scores:
            all_levels[score.yaku_level] += 1
        for score in result.yakuman_scores:
            all_levels[score.yaku_level] += 1
    payout_breakdown = {
        "normal_non_yakuman": sum(result.normal_non_yakuman_payout for result in session_results),
        "normal_yakuman": sum(result.normal_yakuman_payout for result in session_results),
        "paren_challenge": sum(result.paren_payout for result in session_results),
        "yakuman_challenge": sum(result.yakuman_challenge_payout for result in session_results),
    }
    yakuman_by_phase = {
        "normal": Counter(),
        "paren_challenge": Counter(),
        "yakuman_challenge": Counter(),
    }
    for result in session_results:
        if result.normal_score is not None:
            yakuman_by_phase["normal"].update(_yakuman_yaku_names(result.normal_score))
        for score in result.paren_scores:
            yakuman_by_phase["paren_challenge"].update(_yakuman_yaku_names(score))
        for score in result.yakuman_scores:
            yakuman_by_phase["yakuman_challenge"].update(_yakuman_yaku_names(score))
    yakuman_total = Counter()
    for counter in yakuman_by_phase.values():
        yakuman_total.update(counter)

    total_bet = sessions * bet
    total_payout = sum(result.payout for result in session_results)
    total_net = total_payout - total_bet
    return EconomySummary(
        sessions=sessions,
        seed=seed,
        bet=bet,
        strategy=strategy,
        paren_table_mode=paren_table_mode,
        normal_haipai_source=normal_haipai_source,
        observed_haipai_hands=0 if observed_haipai is None else len(observed_haipai.hands),
        observed_haipai_ignored=0 if observed_haipai is None else observed_haipai.ignored_hands,
        total_bet=total_bet,
        total_payout=total_payout,
        total_net=total_net,
        roi=total_net / total_bet,
        return_to_player=total_payout / total_bet,
        avg_payout=mean(payouts),
        avg_net=mean(nets),
        avg_net_ci95=normal_mean_interval(nets),
        normal_wins=normal_wins,
        normal_win_rate=normal_wins / sessions,
        normal_win_rate_ci95=wilson_interval(normal_wins, sessions),
        paren_entries=paren_entries,
        paren_entry_rate=paren_entries / sessions,
        yakuman_challenge_entries=yakuman_entries,
        yakuman_challenge_entry_rate=yakuman_entries / sessions,
        avg_paren_wins=mean([float(result.paren_wins) for result in session_results]),
        avg_yakuman_challenge_wins=mean(
            [float(result.yakuman_challenge_wins) for result in session_results]
        ),
        avg_yakuman_challenge_units=mean(
            [float(result.yakuman_challenge_units) for result in session_results]
        ),
        normal_yaku_levels=dict(sorted(normal_levels.items())),
        all_yaku_levels=dict(sorted(all_levels.items())),
        payout_histogram=dict(sorted(payout_histogram.items(), key=lambda item: _bucket_sort_key(item[0]))),
        payout_breakdown=payout_breakdown,
        payout_breakdown_ratio=_ratios(payout_breakdown),
        win_counts={
            "normal_non_yakuman_wins": sum(
                1
                for result in session_results
                if result.normal_score is not None and not result.normal_score.is_yakuman
            ),
            "normal_yakuman_wins": sum(
                1
                for result in session_results
                if result.normal_score is not None and result.normal_score.is_yakuman
            ),
            "paren_wins": sum(result.paren_wins for result in session_results),
            "yakuman_challenge_wins": sum(result.yakuman_challenge_wins for result in session_results),
        },
        yakuman_yaku_counts=dict(sorted(yakuman_total.items())),
        yakuman_yaku_counts_by_phase={
            phase: dict(sorted(counter.items()))
            for phase, counter in yakuman_by_phase.items()
        },
        normal_riichi_count=normal_riichi_count,
        normal_riichi_rate=normal_riichi_count / sessions,
        normal_ippatsu_wins=normal_ippatsu_wins,
        normal_double_riichi_count=normal_double_riichi_count,
        assumptions=(
            _normal_haipai_assumption(normal_haipai_source, observed_haipai),
            "normal dora tile type is sampled uniformly from the 34 JanQ tile ids",
            "normal ura-dora tile type is sampled uniformly from the 34 JanQ tile ids and counts only after reach",
            "nyukyu draws use the copied client's official 7-area probability table",
            "fourth-copy draws refund one ball, matching the official help",
            "bonus modes use the copied client's paren/yakuman setup tables",
            _paren_table_assumption(paren_table_mode),
            "route_ev may declare reach in normal mode; reached hands are locked to tsumogiri and first draw after reach can score ippatsu",
        ),
    )


def simulate_session(
    *,
    initial_hand: TileSet,
    rng: random.Random,
    bet: int,
    strategy: str,
    paren_table_mode: str,
    max_bonus_hands: int,
    base_table: Any,
    paren_table: Any,
    yakuman_table: Any,
    special_records: Any,
    choose_area: Any,
    choose_discard: Any,
) -> SessionResult:
    del strategy
    normal = _simulate_scored_hand(
        initial_hand,
        base_table,
        balls=8,
        rng=rng,
        choose_area=choose_area,
        choose_discard=choose_discard,
        dora_id=_random_dora(rng),
        ura_dora_id=_random_dora(rng),
    )
    if normal.score is None:
        return SessionResult(
            bet=bet,
            payout=0,
            normal_win=False,
            paren_wins=0,
            yakuman_challenge_wins=0,
            yakuman_challenge_units=0,
            normal_score=None,
            paren_scores=(),
            yakuman_scores=(),
            normal_riichi=normal.simulation.riichi,
            normal_double_riichi=normal.simulation.double_riichi,
            normal_ippatsu_win=False,
        )

    normal_payout = payout_for_score(normal.score, bet=bet)
    normal_non_yakuman_payout = 0 if normal.score.is_yakuman else normal_payout
    normal_yakuman_payout = normal_payout if normal.score.is_yakuman else 0
    payout = normal_payout
    paren_payout = 0
    yakuman_bonus_payout = 0
    paren_scores: list[JanqScore] = []
    yakuman_scores: list[JanqScore] = []
    paren_wins = 0
    enter_yakuman = normal.score.is_yakuman
    previous_score = normal.score

    if not enter_yakuman:
        agari_count = 1
        for _ in range(max_bonus_hands):
            if agari_count >= 8:
                enter_yakuman = True
                break
            number = _choose_paren_number(previous_score, special_records, rng, mode=paren_table_mode)
            record = _choose_record(special_records.paren_tables[number].records, rng)
            paren = _simulate_scored_hand(
                tile_set(record.tiles),
                paren_table,
                balls=3,
                rng=rng,
                choose_area=choose_bonus_area,
                choose_discard=choose_bonus_discard,
                dora_id=record.dora_id,
            )
            if paren.score is None:
                break
            win_payout = payout_for_score(paren.score, bet=bet)
            paren_wins += 1
            agari_count += 1
            paren_scores.append(paren.score)
            paren_payout += win_payout
            payout += win_payout
            previous_score = paren.score
            if paren.score.is_yakuman or agari_count >= 8:
                enter_yakuman = True
                break

    yakuman_wins = 0
    yakuman_units = 0
    if enter_yakuman:
        cumulative_yakuman_units = 0
        for _ in range(max_bonus_hands):
            record = _choose_record(special_records.yakuman_records, rng)
            yakuman = _simulate_scored_hand(
                tile_set(record.tiles),
                yakuman_table,
                balls=3,
                rng=rng,
                choose_area=choose_bonus_area,
                choose_discard=choose_bonus_discard,
                dora_id=None,
            )
            if yakuman.score is None:
                break
            units = max(1, yakuman.score.yakuman_count)
            cumulative_yakuman_units += units
            yakuman_wins += 1
            yakuman_units += units
            yakuman_scores.append(yakuman.score)
            win_payout = yakuman_challenge_payout(
                bet=bet,
                cumulative_yakuman_count=cumulative_yakuman_units,
            )
            yakuman_bonus_payout += win_payout
            payout += win_payout

    return SessionResult(
        bet=bet,
        payout=payout,
        normal_win=True,
        paren_wins=paren_wins,
        yakuman_challenge_wins=yakuman_wins,
        yakuman_challenge_units=yakuman_units,
        normal_score=normal.score,
        paren_scores=tuple(paren_scores),
        yakuman_scores=tuple(yakuman_scores),
        normal_non_yakuman_payout=normal_non_yakuman_payout,
        normal_yakuman_payout=normal_yakuman_payout,
        paren_payout=paren_payout,
        yakuman_challenge_payout=yakuman_bonus_payout,
        normal_riichi=normal.simulation.riichi,
        normal_double_riichi=normal.simulation.double_riichi,
        normal_ippatsu_win=normal.simulation.ippatsu_win,
    )


def _simulate_scored_hand(
    initial_hand: TileSet,
    table: Any,
    *,
    balls: int,
    rng: random.Random,
    choose_area: Any,
    choose_discard: Any,
    dora_id: int | None,
    ura_dora_id: int | None = None,
) -> ScoredHandResult:
    simulation = simulate_hand(
        initial_hand,
        table,
        balls=balls,
        rng=rng,
        choose_area=choose_area,
        choose_discard=choose_discard,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
    )
    if not simulation.win:
        return ScoredHandResult(
            simulation=simulation,
            score=None,
            dora_id=dora_id,
            ura_dora_id=ura_dora_id,
            payout=0,
        )
    score = score_hand(
        simulation.final_hand,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        reach=simulation.riichi and not simulation.double_riichi,
        double_reach=simulation.double_riichi,
        ippatsu=simulation.ippatsu_win,
    )
    return ScoredHandResult(
        simulation=simulation,
        score=score,
        dora_id=dora_id,
        ura_dora_id=ura_dora_id,
        payout=payout_for_score(score),
    )


def _strategy_functions(strategy: str) -> tuple[Any, Any]:
    if strategy == "public":
        return choose_public_area, choose_public_discard
    if strategy == "greedy":
        return choose_greedy_area, choose_greedy_discard
    if strategy == "route_ev":
        return choose_route_ev_area, choose_route_ev_discard
    raise ValueError(f"unknown strategy: {strategy}")


def _random_wall_hand(rng: random.Random) -> TileSet:
    return random_wall_hand(rng)


def _load_initial_hand_source(
    source: str,
    *,
    observed_events_path: str | None,
) -> ObservedHaipaiSet | None:
    if source == "wall":
        return None
    if source == "observed":
        if observed_events_path is None:
            raise ValueError("--observed-events is required when --normal-haipai-source=observed")
        observed = load_observed_normal_haipai(observed_events_path)
        if not observed.hands:
            raise ValueError(f"no 13-tile NORMAL recv_game_haipai samples in {observed_events_path}")
        return observed
    raise ValueError(f"unknown normal_haipai_source: {source}")


def _sample_normal_haipai(
    rng: random.Random,
    *,
    source: str,
    observed_haipai: ObservedHaipaiSet | None,
) -> TileSet:
    if source == "wall":
        return _random_wall_hand(rng)
    if source == "observed":
        if observed_haipai is None:
            raise ValueError("observed haipai source was not loaded")
        return observed_haipai.sample(rng)
    raise ValueError(f"unknown normal_haipai_source: {source}")


def _random_dora(rng: random.Random) -> int:
    return rng.randrange(34)


def _choose_record(records: tuple[SpecialHandRecord, ...], rng: random.Random) -> SpecialHandRecord:
    enabled = [record for record in records if record.enabled]
    if not enabled:
        raise ValueError("no enabled special records")
    return rng.choice(enabled)


def _choose_paren_number(score: JanqScore, special_records: Any, rng: random.Random, *, mode: str) -> int:
    if mode == "previous_han":
        return min(12, max(2, score.han))
    if mode == "select_table":
        numbers = [number for number, weight in special_records.paren_select for _ in range(weight)]
        return rng.choice(numbers)
    raise ValueError(f"unknown paren_table_mode: {mode}")


def _paren_table_assumption(mode: str) -> str:
    if mode == "previous_han":
        return "paren_table_mode=previous_han chooses paren_N from the previous hand's han clamped to 2..12"
    if mode == "select_table":
        return "paren_table_mode=select_table samples paren_N from paren_select_table.bytes weights"
    return f"paren_table_mode={mode}"


def _normal_haipai_assumption(source: str, observed: ObservedHaipaiSet | None) -> str:
    if source == "wall":
        return "normal initial hands are sampled from a physical 136-tile wall"
    if source == "observed" and observed is not None:
        return (
            "normal initial hands are bootstrapped from observed recv_game_haipai "
            f"samples ({len(observed.hands)} usable, {observed.ignored_hands} ignored)"
        )
    return f"normal initial hands source={source}"


def _payout_bucket(payout: int) -> str:
    if payout == 0:
        return "0"
    if payout < 10:
        return "1-9"
    if payout < 50:
        return "10-49"
    if payout < 100:
        return "50-99"
    if payout < 500:
        return "100-499"
    if payout < 1000:
        return "500-999"
    if payout < 5000:
        return "1000-4999"
    return "5000+"


def _bucket_sort_key(bucket: str) -> int:
    if bucket == "0":
        return 0
    return int(bucket.split("-")[0].replace("+", ""))


def _ratios(values: dict[str, int]) -> dict[str, float]:
    total = sum(values.values())
    if total <= 0:
        return {key: 0.0 for key in values}
    return {key: value / total for key, value in values.items()}


def _yakuman_yaku_names(score: JanqScore) -> tuple[str, ...]:
    if not score.is_yakuman:
        return ()
    return tuple(name for name in score.yaku if name in YAKUMAN_YAKU_NAMES)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run full JanQ economy Monte Carlo.")
    parser.add_argument("--sessions", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--bet", type=int, default=10)
    parser.add_argument("--strategy", choices=("public", "greedy", "route_ev"), default="public")
    parser.add_argument(
        "--paren-table-mode",
        choices=("previous_han", "select_table"),
        default="previous_han",
    )
    parser.add_argument(
        "--normal-haipai-source",
        choices=("wall", "observed"),
        default="wall",
        help="Source for normal-game initial hands.",
    )
    parser.add_argument(
        "--observed-events",
        default=None,
        help="JanqProbe JSONL file used when --normal-haipai-source=observed.",
    )
    parser.add_argument("--max-bonus-hands", type=int, default=1000)
    args = parser.parse_args(argv)

    summary = run_economy_monte_carlo(
        sessions=args.sessions,
        seed=args.seed,
        bet=args.bet,
        strategy=args.strategy,
        paren_table_mode=args.paren_table_mode,
        normal_haipai_source=args.normal_haipai_source,
        observed_events_path=args.observed_events,
        max_bonus_hands=args.max_bonus_hands,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
