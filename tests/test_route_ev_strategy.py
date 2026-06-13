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

    def test_suuankou_route_overrides_normal_tenpai_with_three_ball_build(self):
        hand = (
            13,
            13,
            19,
            19,
            19,
            20,
            20,
            23,
            24,
            25,
            29,
            29,
            29,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        area = choose_route_ev_area(hand, table, balls=3)
        discard = choose_route_ev_discard(hand + (14,), balls=3, drawn_tile=14)

        self.assertEqual(7, area.area)
        self.assertEqual((13, 20, 23, 24, 25), area.target_tiles)
        self.assertEqual((1.0, 1.0, 1.0, 1.0, 1.0), area.target_factors)
        self.assertEqual(4100, area.target_weight)
        self.assertIn("tenpai_override", area.reason)
        self.assertEqual(14, discard.discard_tile)
        self.assertFalse(discard.declare_riichi)
        self.assertIn("suuankou", discard.reason)

    def test_riichi_lock_prevents_suuankou_shape_rebuild(self):
        hand = (
            13,
            13,
            19,
            19,
            19,
            20,
            20,
            23,
            24,
            25,
            29,
            29,
            29,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        decision = choose_route_ev_area(hand, table, balls=3, is_reach=True)

        self.assertEqual((13, 20), decision.target_tiles)
        self.assertIn("riichi_locked_wait", decision.reason)

    def test_suuankou_replacement_singletons_are_discounted_targets(self):
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
            9,
            9,
            18,
            21,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        decision = choose_route_ev_area(hand, table, balls=3)
        factors = dict(zip(decision.target_tiles, decision.target_factors))

        self.assertEqual((1, 3, 6, 9, 18, 21), decision.target_tiles)
        self.assertEqual(1.0, factors[1])
        self.assertEqual(1.0, factors[9])
        self.assertEqual(0.25, factors[18])
        self.assertEqual(0.25, factors[21])
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
        self.assertEqual((11, 12, 16, 28), area.target_tiles)
        self.assertEqual((1.0, 1.0, 0.25, 1.0), area.target_factors)
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

        self.assertEqual(32, discard.discard_tile)
        self.assertIn("normal_side_sou_next_area:area=3:p=0.820", discard.reason)
        self.assertEqual(3, area.area)
        self.assertEqual((9, 10, 11, 12, 13, 14, 15, 16, 17, 28), area.target_tiles)
        self.assertEqual(8200, area.target_weight)

    def test_honitsu_keeps_off_suit_pair_and_builds_pin_meld_when_balls_remain(self):
        hand = (
            13,
            13,
            18,
            19,
            19,
            19,
            19,
            20,
            23,
            24,
            25,
            27,
            29,
            30,
        )
        table = load_tables()["nyukyu_base_table.bytes"]

        discard = choose_route_ev_discard(hand, balls=6, drawn_tile=13)
        next_hand = list(hand)
        next_hand.remove(discard.discard_tile)
        area = choose_route_ev_area(next_hand, table, balls=6)

        self.assertEqual(29, discard.discard_tile)
        self.assertIn("honitsu_pin:next_area=7", discard.reason)
        self.assertIn(13, next_hand)
        self.assertEqual(2, next_hand.count(13))
        self.assertEqual(7, area.area)
        self.assertEqual((18, 20, 21, 22, 23, 24, 25, 26, 30), area.target_tiles)
        self.assertEqual(9600, area.target_weight)

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

    def test_actual_review_lines_2631_to_2708_choose_man_side_and_cut_dragons(self):
        table = load_tables()["nyukyu_base_table.bytes"]

        # janq_bot_20260614_013302, lines 2631/2655:
        # 1万 1万 2万 3万 4万 5万 8万 7索 2饼 6饼 9饼 白 发
        area_2631 = choose_route_ev_area(
            (0, 0, 1, 2, 3, 4, 7, 15, 19, 23, 26, 31, 32),
            table,
            balls=8,
        )
        self.assertEqual(1, area_2631.area)
        self.assertIn(27, area_2631.target_tiles)
        self.assertIn("normal_side_man", area_2631.reason)

        # line 2649, after drawing 4饼(id=21), real bot cut 9饼.
        discard_2649 = choose_route_ev_discard(
            (0, 0, 1, 2, 3, 4, 7, 15, 19, 23, 26, 31, 32, 21),
            balls=7,
            drawn_tile=21,
        )
        self.assertIn(discard_2649.discard_tile, (31, 32))

        # line 2697, after drawing 9万(id=8), real bot cut 7索.
        discard_2697 = choose_route_ev_discard(
            (0, 0, 1, 2, 3, 4, 7, 15, 19, 21, 23, 23, 31, 8),
            balls=5,
            drawn_tile=8,
        )
        self.assertEqual(31, discard_2697.discard_tile)

        # line 2721, after drawing 东(id=27), real bot cut 东.
        discard_2721 = choose_route_ev_discard(
            (0, 0, 1, 2, 3, 4, 7, 8, 19, 21, 23, 23, 31, 27),
            balls=4,
            drawn_tile=27,
        )
        self.assertEqual(31, discard_2721.discard_tile)

    def test_actual_review_lines_3153_3651_3845_cut_unrelated_dragons(self):
        # line 3153: 白(id=31) is not part of the area-2 man/sou plan.
        discard_3153 = choose_route_ev_discard(
            (1, 1, 6, 8, 9, 10, 10, 13, 20, 27, 28, 29, 31, 5),
            balls=7,
            drawn_tile=5,
        )
        self.assertEqual(31, discard_3153.discard_tile)

        # line 3651: pin/North side route should cut 白/发 before 9索.
        discard_3651 = choose_route_ev_discard(
            (12, 14, 17, 18, 22, 22, 23, 24, 26, 27, 30, 31, 32, 30),
            balls=7,
            drawn_tile=30,
        )
        self.assertIn(discard_3651.discard_tile, (31, 32))
        self.assertNotEqual(17, discard_3651.discard_tile)

        # line 3845: 白(id=31) is weaker than 南(id=28) under pin/North plan.
        discard_3845 = choose_route_ev_discard(
            (1, 3, 14, 16, 18, 19, 21, 21, 22, 26, 28, 30, 31, 30),
            balls=7,
            drawn_tile=30,
        )
        self.assertEqual(31, discard_3845.discard_tile)

    def test_actual_review_line_3869_does_not_cut_sou_bridge_on_pin_side(self):
        # line 3869: real state still contains 白(id=31). The generalized rule
        # cuts the unrelated dragon first; after that, weak man tiles are below 8索.
        discard_3869 = choose_route_ev_discard(
            (1, 3, 14, 16, 18, 19, 21, 21, 22, 26, 30, 30, 31, 24),
            balls=6,
            drawn_tile=24,
        )
        self.assertEqual(31, discard_3869.discard_tile)
        self.assertNotEqual(16, discard_3869.discard_tile)

        corrected_flow_discard = choose_route_ev_discard(
            (1, 3, 14, 16, 18, 19, 21, 21, 22, 26, 30, 30, 24, 24),
            balls=6,
            drawn_tile=24,
        )
        self.assertIn(corrected_flow_discard.discard_tile, (1, 3))

    def test_actual_review_line_4695_dragon_pair_pushes_daisangen_area4(self):
        table = load_tables()["nyukyu_base_table.bytes"]

        # line 4695: 4万 7万 1索 2索 4索 5索 6索 9索 5饼 6饼 8饼 白 白
        decision = choose_route_ev_area(
            (3, 6, 9, 10, 12, 13, 14, 17, 22, 23, 25, 31, 31),
            table,
            balls=8,
        )

        self.assertEqual(4, decision.area)
        self.assertEqual((31, 32, 33), decision.target_tiles)
        self.assertIn("daisangen", decision.reason)

    def test_actual_review_lines_4785_4809_break_weak_12s_for_pin_side(self):
        # line 4785, real bot cut 北. With four balls, 12索 is the weak block.
        discard_4785 = choose_route_ev_discard(
            (9, 10, 12, 13, 14, 22, 23, 24, 25, 26, 30, 31, 31, 26),
            balls=4,
            drawn_tile=26,
        )
        self.assertIn(discard_4785.discard_tile, (9, 10))

        # line 4809, real bot cut 9饼. With three balls, still do not break pin side.
        discard_4809 = choose_route_ev_discard(
            (9, 10, 12, 13, 14, 22, 23, 24, 25, 26, 26, 31, 31, 23),
            balls=3,
            drawn_tile=23,
        )
        self.assertIn(discard_4809.discard_tile, (9, 10))

    def test_actual_review_questions_are_deterministic_and_protection_aware(self):
        table = load_tables()["nyukyu_base_table.bytes"]

        # line 3073 question: no bespoke rule is added; current general scoring cuts 2万.
        discard_3073 = choose_route_ev_discard(
            (0, 3, 5, 7, 9, 9, 10, 11, 11, 11, 14, 16, 27, 1),
            balls=3,
            drawn_tile=1,
        )
        self.assertEqual(1, discard_3073.discard_tile)

        # line 3989 question: 北(id=30) is protected, so effective denominators
        # are area6=1400/9000 and area7=1700/8000; area7 remains best.
        area_3989 = choose_route_ev_area(
            (18, 18, 19, 20, 21, 21, 22, 24, 25, 26, 30, 30, 30),
            table,
            balls=1,
            is_reach=True,
        )
        self.assertEqual(7, area_3989.area)
        self.assertEqual((20, 23), area_3989.target_tiles)
        self.assertIn("progress=0.212", area_3989.reason)


if __name__ == "__main__":
    unittest.main()
