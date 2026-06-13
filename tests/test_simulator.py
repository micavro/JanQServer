import struct
import unittest

from janq_lab.assets.nyukyu import AREA_COUNT, NyukyuTable
from janq_lab.model.simulator import simulate_hand


def deterministic_table(tile_id: int) -> NyukyuTable:
    values = []
    for current_tile in range(34):
        for _ in range(AREA_COUNT):
            values.append(10000 if current_tile == tile_id else 0)
    data = struct.pack("<" + "H" * len(values), *values)
    return NyukyuTable.from_bytes(data, name=f"always_{tile_id}")


class SimulatorTests(unittest.TestCase):
    def test_tenpai_hand_wins_when_wait_is_drawn(self):
        table = deterministic_table(31)
        hand = [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31]
        result = simulate_hand(hand, table)
        self.assertTrue(result.win)
        self.assertEqual(1, result.shots)
        self.assertEqual(31, result.turns[0].shot.tile_id)

    def test_fourth_copy_refunds_ball(self):
        table = deterministic_table(0)
        hand = [0, 0, 0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 31]
        result = simulate_hand(hand, table, balls=1, max_turns=1)
        self.assertFalse(result.win)
        self.assertGreaterEqual(result.shots, 1)
        self.assertTrue(result.turns[0].shot.fourth_copy)
        self.assertEqual(1, result.turns[0].shot.balls_before)
        self.assertEqual(1, result.turns[0].shot.balls_after)


if __name__ == "__main__":
    unittest.main()
