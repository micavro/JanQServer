"""Official JanQ payout math from the SEGA help page."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from janq_lab.model.scoring import JanqScore


@dataclass(frozen=True)
class RewardBand:
    label: str
    multiplier: Fraction

    @property
    def multiplier_float(self) -> float:
        return float(self.multiplier)


REWARD_BANDS: tuple[RewardBand, ...] = (
    RewardBand("1 han", Fraction(1, 5)),
    RewardBand("2 han", Fraction(2, 5)),
    RewardBand("3 han", Fraction(3, 5)),
    RewardBand("4-5 han mangan", Fraction(1, 1)),
    RewardBand("6-7 han haneman", Fraction(3, 2)),
    RewardBand("8-10 han baiman", Fraction(2, 1)),
    RewardBand("11-12 han sanbaiman", Fraction(3, 1)),
    RewardBand("13+ han counted yakuman", Fraction(10, 1)),
)


def reward_band_for_han(han: int) -> RewardBand:
    if han <= 0:
        return RewardBand("none", Fraction(0, 1))
    if han == 1:
        return REWARD_BANDS[0]
    if han == 2:
        return REWARD_BANDS[1]
    if han == 3:
        return REWARD_BANDS[2]
    if han <= 5:
        return REWARD_BANDS[3]
    if han <= 7:
        return REWARD_BANDS[4]
    if han <= 10:
        return REWARD_BANDS[5]
    if han <= 12:
        return REWARD_BANDS[6]
    return REWARD_BANDS[7]


def multiplier_for_score(score: JanqScore) -> Fraction:
    if score.yakuman_count:
        return Fraction(10 * min(4, score.yakuman_count), 1)
    return reward_band_for_han(score.han).multiplier


def payout_for_score(score: JanqScore, *, bet: int = 10) -> int:
    return _fraction_to_coin(bet * multiplier_for_score(score))


def yakuman_challenge_payout(*, bet: int, cumulative_yakuman_count: int) -> int:
    if cumulative_yakuman_count < 1:
        return 0
    return bet * 10 * cumulative_yakuman_count


def _fraction_to_coin(value: Fraction) -> int:
    if value.denominator == 1:
        return value.numerator
    return value.numerator // value.denominator
