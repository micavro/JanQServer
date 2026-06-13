from fractions import Fraction
import unittest

from janq_lab.model.economy import multiplier_for_score, payout_for_score, yakuman_challenge_payout
from janq_lab.model.scoring import score_hand


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
