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
        self.assertIn("suuankou", decision.reason)

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
        self.assertIn("yakuman_tenpai", decision.reason)


if __name__ == "__main__":
    unittest.main()
