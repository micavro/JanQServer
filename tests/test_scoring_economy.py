from fractions import Fraction
import random
import unittest

from janq_lab.analysis.economy_monte_carlo import _simulate_scored_hand
from janq_lab.model.economy import multiplier_for_score, payout_for_score, yakuman_challenge_payout
from janq_lab.model.hand import tile_set
from janq_lab.model.scoring import score_hand
from janq_lab.strategy.greedy import AreaDecision


class FixedDrawTable:
    def __init__(self, tile_id: int) -> None:
        self.tile_id = tile_id

    def draw(self, area: int, rng: random.Random) -> int:
        del area, rng
        return self.tile_id


def fixed_area(*args, **kwargs) -> AreaDecision:
    del args, kwargs
    return AreaDecision(4, (), 0, "fixed_area")


def forbidden_discard(*args, **kwargs):
    del args, kwargs
    raise AssertionError("HOLD mode must not call the discard policy")


class ScoringEconomyTest(unittest.TestCase):
    def test_tanyao_pinfu_tsumo(self) -> None:
        score = score_hand([1, 2, 3, 4, 5, 6, 11, 12, 13, 23, 23, 23, 24, 25])
        self.assertEqual(score.han, 3)
        self.assertIn("tanyao", score.yaku)
        self.assertIn("pinfu", score.yaku)
        self.assertIn("tsumo", score.yaku)
        self.assertEqual(multiplier_for_score(score), Fraction(3, 5))
        self.assertEqual(payout_for_score(score, bet=10), 6)

    def test_honitsu_dragon_triplet(self) -> None:
        score = score_hand([0, 1, 2, 3, 4, 5, 6, 6, 6, 31, 31, 31, 32, 32])
        self.assertEqual(score.han, 5)
        self.assertIn("honitsu", score.yaku)
        self.assertIn("haku", score.yaku)
        self.assertEqual(payout_for_score(score, bet=10), 10)

    def test_reach_ippatsu_dora_and_ura_dora_add_han(self) -> None:
        score = score_hand(
            [1, 2, 3, 4, 5, 6, 11, 12, 13, 23, 23, 23, 24, 25],
            dora_id=1,
            ura_dora_id=23,
            reach=True,
            ippatsu=True,
        )

        self.assertEqual(score.han, 9)
        self.assertIn("reach", score.yaku)
        self.assertIn("ippatsu", score.yaku)
        self.assertIn("dora", score.yaku)
        self.assertIn("ura_dora", score.yaku)

    def test_ura_dora_requires_reach(self) -> None:
        score = score_hand(
            [1, 2, 3, 4, 5, 6, 11, 12, 13, 23, 23, 23, 24, 25],
            dora_id=23,
            ura_dora_id=1,
        )

        self.assertEqual(score.han, 6)
        self.assertEqual(score.yaku.count("dora"), 3)
        self.assertNotIn("ura_dora", score.yaku)

    def test_natural_yakuman_does_not_add_dora_or_ura_han(self) -> None:
        score = score_hand(
            [0, 0, 0, 1, 1, 1, 9, 9, 9, 18, 18, 18, 31, 31],
            dora_id=0,
            ura_dora_id=31,
            reach=True,
        )

        self.assertEqual(score.han, 0)
        self.assertEqual(score.yakuman_count, 1)
        self.assertNotIn("dora", score.yaku)
        self.assertNotIn("ura_dora", score.yaku)

    def test_paren_scored_hand_starts_with_reach_han(self) -> None:
        result = _simulate_scored_hand(
            tile_set([0, 14, 14, 16, 16, 24, 24, 27, 27, 28, 28, 33, 33]),
            FixedDrawTable(0),
            balls=3,
            rng=random.Random(1),
            choose_area=fixed_area,
            choose_discard=forbidden_discard,
            dora_id=None,
            hold_hand=True,
            force_reach=True,
        )

        self.assertIsNotNone(result.score)
        assert result.score is not None
        self.assertEqual(result.score.han, 4)
        self.assertIn("reach", result.score.yaku)

    def test_suuankou_is_yakuman(self) -> None:
        score = score_hand([0, 0, 0, 1, 1, 1, 9, 9, 9, 18, 18, 18, 31, 31])
        self.assertEqual(score.yakuman_count, 1)
        self.assertEqual(multiplier_for_score(score), Fraction(10, 1))
        self.assertEqual(payout_for_score(score, bet=10), 100)

    def test_overlapping_yakuman_count_separately(self) -> None:
        score = score_hand([27, 27, 27, 28, 28, 28, 29, 29, 29, 30, 30, 30, 31, 31])
        self.assertEqual(score.yakuman_count, 3)
        self.assertIn("tsuuiisou", score.yaku)
        self.assertIn("daisuushi", score.yaku)
        self.assertIn("suuankou", score.yaku)
        self.assertEqual(payout_for_score(score, bet=10), 300)

    def test_yakuman_challenge_progressive_payout(self) -> None:
        self.assertEqual(yakuman_challenge_payout(bet=10, cumulative_yakuman_count=1), 100)
        self.assertEqual(yakuman_challenge_payout(bet=10, cumulative_yakuman_count=3), 300)


if __name__ == "__main__":
    unittest.main()
