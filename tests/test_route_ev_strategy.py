import unittest

from janq_lab.assets.nyukyu import load_tables
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard


class RouteEvStrategyTests(unittest.TestCase):
    def test_suuankou_discards_off_suit_terminal_before_sou_side_route_singleton(self):
        hand = (
            6,
            9,
            9,
            10,
            10,
            10,
            11,
            12,
            12,
            16,
            16,
            26,
            28,
            28,
        )

        decision = choose_route_ev_discard(hand, balls=6)

        self.assertEqual(26, decision.discard_tile)
        self.assertIn("suuankou", decision.reason)
        self.assertIn("side_sou", decision.reason)

    def test_suuankou_tenpai_area_targets_all_yakuman_waits(self):
        hand = (
            9,
            9,
            9,
            10,
            10,
            10,
            11,
            11,
            12,
            12,
            12,
            28,
            28,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        decision = choose_route_ev_area(hand, table, balls=2)

        self.assertEqual((11, 28), decision.target_tiles)
        self.assertEqual(3200, decision.target_weight)
        self.assertIn("yakuman_tenpai", decision.reason)

    def test_suuankou_tenpai_discards_fourth_copy_instead_of_pair(self):
        hand = (
            9,
            9,
            9,
            9,
            10,
            10,
            10,
            11,
            11,
            12,
            12,
            12,
            28,
            28,
        )

        decision = choose_route_ev_discard(hand, balls=2)

        self.assertEqual(9, decision.discard_tile)
        self.assertEqual(0, decision.shanten_after)
        self.assertEqual((11, 28), decision.accepts)
        self.assertTrue(decision.declare_riichi)
        self.assertIn("yakuman_tenpai", decision.reason)

    def test_normal_tenpai_declares_riichi_with_one_ball_left(self):
        hand = (
            0,
            1,
            2,
            9,
            10,
            11,
            18,
            19,
            20,
            27,
            27,
            27,
            31,
            5,
        )

        decision = choose_route_ev_discard(hand, balls=1, dora_id=31, ura_dora_id=5)

        self.assertEqual(5, decision.discard_tile)
        self.assertEqual((31,), decision.accepts)
        self.assertTrue(decision.declare_riichi)
        self.assertIn("normal_tenpai", decision.reason)
        self.assertIn("riichi", decision.reason)

    def test_suuankou_route_beats_chiitoi_tenpai_when_three_balls_can_complete(self):
        hand = (
            0,
            0,
            0,
            1,
            1,
            3,
            3,
            6,
            6,
            18,
            18,
            21,
            21,
            17,
        )

        decision = choose_route_ev_discard(hand, balls=3)

        self.assertEqual(17, decision.discard_tile)
        self.assertFalse(decision.declare_riichi)
        self.assertEqual(1, decision.shanten_after)
        self.assertIn("suuankou", decision.reason)

    def test_suuankou_route_area_targets_all_pair_progress_tiles(self):
        hand = (
            0,
            0,
            0,
            1,
            1,
            3,
            3,
            6,
            6,
            18,
            18,
            21,
            21,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        decision = choose_route_ev_area(hand, table, balls=3)

        self.assertEqual((1, 3, 6, 18, 21), decision.target_tiles)
        self.assertIn("suuankou", decision.reason)

    def test_suuankou_route_converts_to_chiitoi_when_remaining_balls_cannot_complete(self):
        hand = (
            0,
            0,
            0,
            1,
            1,
            3,
            3,
            6,
            6,
            18,
            18,
            21,
            21,
            17,
        )

        decision = choose_route_ev_discard(hand, balls=2)

        self.assertEqual(0, decision.discard_tile)
        self.assertEqual((17,), decision.accepts)
        self.assertEqual(0, decision.shanten_after)
        self.assertTrue(decision.declare_riichi)
        self.assertIn("normal_tenpai", decision.reason)


if __name__ == "__main__":
    unittest.main()
