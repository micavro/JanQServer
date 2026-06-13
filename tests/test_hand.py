import unittest

from janq_lab.model.hand import (
    best_discards_by_shanten,
    chiitoitsu_shanten,
    improving_tiles,
    is_complete_hand,
    kokushi_shanten,
    shanten,
    tile_set,
    winning_tiles,
)


class HandModelTests(unittest.TestCase):
    def test_standard_complete_hand(self):
        hand = [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31, 31]
        self.assertTrue(is_complete_hand(hand))
        self.assertEqual(-1, shanten(hand))

    def test_standard_tenpai_wait(self):
        hand = [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31]
        self.assertEqual(0, shanten(hand))
        self.assertEqual((31,), winning_tiles(hand))

    def test_chiitoitsu_complete_hand(self):
        hand = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 27, 27]
        counts = tile_set(hand).counts
        self.assertEqual(-1, chiitoitsu_shanten(counts))
        self.assertTrue(is_complete_hand(hand))

    def test_kokushi_complete_hand(self):
        hand = [0, 0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33]
        counts = tile_set(hand).counts
        self.assertEqual(-1, kokushi_shanten(counts))
        self.assertTrue(is_complete_hand(hand))

    def test_improving_tiles_for_simple_ryanmen(self):
        hand = [0, 1, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31, 31]
        self.assertIn(2, improving_tiles(hand))

    def test_best_discards_keeps_complete_hand_shape(self):
        hand = [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31, 32]
        discards = best_discards_by_shanten(hand)
        self.assertIn(31, discards)
        self.assertIn(32, discards)

    def test_rejects_five_copies(self):
        with self.assertRaises(ValueError):
            tile_set([0, 0, 0, 0, 0])


if __name__ == "__main__":
    unittest.main()

