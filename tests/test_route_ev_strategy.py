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

    def test_suuankou_discard_preserves_honor_pair_for_next_area_probability(self):
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
            16,
            16,
            28,
            28,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        discard = choose_route_ev_discard(hand, balls=3, drawn_tile=9)
        next_hand = tuple(tile_id for index, tile_id in enumerate(hand) if index != 10)
        area = choose_route_ev_area(next_hand, table, balls=3)

        self.assertEqual(16, discard.discard_tile)
        self.assertIn("next_area=3", discard.reason)
        self.assertEqual(3, area.area)
        self.assertEqual((11, 12, 28), area.target_tiles)
        self.assertEqual(4400, area.target_weight)

    def test_normal_discard_uses_next_area_improvement_probability(self):
        hand = (
            0,
            1,
            5,
            5,
            7,
            11,
            16,
            17,
            21,
            28,
            29,
            30,
            32,
            32,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        discard = choose_route_ev_discard(hand, balls=5)
        next_hand = list(hand)
        next_hand.remove(discard.discard_tile)
        area = choose_route_ev_area(next_hand, table, balls=5)

        self.assertEqual(30, discard.discard_tile)
        self.assertIn("normal_next_area:area=3:p=0.970", discard.reason)
        self.assertEqual(3, area.area)
        self.assertEqual(9700, area.target_weight)

    def test_normal_discard_keeps_east_for_man_side_and_discards_white(self):
        initial_hand = (
            2,
            5,
            5,
            6,
            12,
            15,
            16,
            22,
            22,
            27,
            30,
            30,
            31,
        )
        hand = initial_hand + (0,)
        table = load_tables()["nyukyu_base_table.bytes"]

        discard = choose_route_ev_discard(hand, balls=7, drawn_tile=0)
        next_hand = list(hand)
        next_hand.remove(discard.discard_tile)
        area = choose_route_ev_area(next_hand, table, balls=7)

        self.assertEqual(31, discard.discard_tile)
        self.assertIn("area=1:p=0.850:alt=0.600", discard.reason)
        self.assertIn(27, next_hand)
        self.assertEqual(1, area.area)
        self.assertIn(27, area.target_tiles)
        self.assertNotIn(31, area.target_tiles)

    def test_honitsu_discard_preserves_honor_for_next_area_targets(self):
        hand = (
            0,
            0,
            10,
            10,
            11,
            12,
            14,
            16,
            16,
            20,
            22,
            27,
            28,
            32,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        discard = choose_route_ev_discard(hand, balls=5)
        next_hand = list(hand)
        next_hand.remove(discard.discard_tile)
        area = choose_route_ev_area(next_hand, table, balls=5)

        self.assertEqual(22, discard.discard_tile)
        self.assertIn("honitsu_sou:next_area=3:p=0.470", discard.reason)
        self.assertEqual(3, area.area)
        self.assertEqual((11, 12, 14, 27, 28, 32), area.target_tiles)
        self.assertEqual(4700, area.target_weight)

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
