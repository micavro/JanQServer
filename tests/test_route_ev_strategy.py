import unittest

from janq_lab.assets.nyukyu import load_tables
from janq_lab.strategy.review_regressions import REVIEW_REGRESSION_CASES
from janq_lab.strategy.route_ev import choose_route_ev_area, choose_route_ev_discard


class RouteEvStrategyTests(unittest.TestCase):
    def test_all_user_review_regression_cases(self):
        table = load_tables()["nyukyu_base_table.bytes"]

        for case in REVIEW_REGRESSION_CASES:
            with self.subTest(case=case.case_id):
                if case.kind == "area":
                    decision = choose_route_ev_area(
                        case.hand,
                        table,
                        balls=case.balls,
                        is_reach=case.is_reach,
                        dora_id=case.dora_id,
                        ura_dora_id=case.ura_dora_id,
                    )
                    choice = decision.area
                    reason = decision.reason
                else:
                    decision = choose_route_ev_discard(
                        case.hand,
                        balls=case.balls,
                        is_reach=case.is_reach,
                        drawn_tile=case.drawn_tile,
                        dora_id=case.dora_id,
                        ura_dora_id=case.ura_dora_id,
                    )
                    choice = decision.discard_tile
                    reason = decision.reason

                self.assertNotIn(
                    choice,
                    case.forbidden_choices,
                    (
                        f"{case.case_id} chose forbidden {choice}; "
                        f"reason={reason}; objection={case.objection}"
                    ),
                )
                if case.accepted_choices:
                    self.assertIn(
                        choice,
                        case.accepted_choices,
                        (
                            f"{case.case_id} chose {choice}, expected one of "
                            f"{case.accepted_choices}; reason={reason}"
                        ),
                    )

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
        self.assertIn("next_area=1", discard.reason)
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

        # line 3073 question: no bespoke rule is added; current general scoring
        # cuts 1索 after the normal efficiency and next-area comparison.
        discard_3073 = choose_route_ev_discard(
            (0, 3, 5, 7, 9, 9, 10, 11, 11, 11, 14, 16, 27, 1),
            balls=3,
            drawn_tile=1,
        )
        self.assertEqual(9, discard_3073.discard_tile)

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

    def test_random_review_keeps_meld_before_area_probability_chase(self):
        # Random review sample #1 turn 5: once no special yaku remains, normal
        # efficiency must not break the completed 789p block for a side-sou area.
        decision = choose_route_ev_discard(
            (3, 5, 6, 6, 9, 11, 11, 12, 16, 16, 24, 25, 26, 11),
            balls=4,
            drawn_tile=11,
        )

        self.assertEqual(12, decision.discard_tile)
        self.assertEqual(1, decision.shanten_after)
        self.assertNotIn(decision.discard_tile, (24, 25, 26))

    def test_random_review_normal_efficiency_does_not_break_sou_shape(self):
        # Random review sample #2 turns 3/4: the old next-area scorer cut 4s/5s.
        # General efficiency should preserve the sou block and cut a weaker tile.
        turn3 = choose_route_ev_discard(
            (3, 5, 10, 12, 12, 14, 21, 22, 26, 28, 28, 28, 29, 12),
            balls=7,
            drawn_tile=12,
        )
        turn4 = choose_route_ev_discard(
            (3, 5, 10, 12, 12, 14, 21, 22, 26, 28, 28, 28, 29, 13),
            balls=6,
            drawn_tile=13,
        )

        self.assertEqual(26, turn3.discard_tile)
        self.assertEqual(2, turn3.shanten_after)
        self.assertNotIn(turn3.discard_tile, (12, 13))
        self.assertEqual(26, turn4.discard_tile)
        self.assertEqual(2, turn4.shanten_after)
        self.assertNotIn(turn4.discard_tile, (12, 13))

    def test_random_review_fourth_honor_is_tsumogiri(self):
        decision = choose_route_ev_discard(
            (3, 5, 10, 10, 11, 12, 12, 14, 14, 21, 28, 28, 28, 28),
            balls=1,
            drawn_tile=28,
            dora_id=28,
            ura_dora_id=28,
        )

        self.assertEqual(28, decision.discard_tile)
        self.assertIn("honor_fourth_tsumogiri", decision.reason)

    def test_known_dora_and_ura_retain_a_direct_value_tile_in_a_close_choice(self):
        hand = (4, 6, 6, 8, 10, 11, 13, 14, 15, 17, 21, 22, 22, 24)

        baseline = choose_route_ev_discard(hand, balls=5)
        with_dora = choose_route_ev_discard(hand, balls=5, dora_id=17)
        with_ura = choose_route_ev_discard(hand, balls=5, ura_dora_id=17)

        self.assertEqual(17, baseline.discard_tile)
        self.assertEqual(21, with_dora.discard_tile)
        self.assertEqual(with_dora.discard_tile, with_ura.discard_tile)
        self.assertIn("dora=", with_dora.reason)

    def test_dora_adjacent_singleton_breaks_a_close_efficiency_tie(self):
        hand = (0, 0, 3, 4, 7, 10, 11, 13, 14, 18, 21, 22, 22, 27)

        baseline = choose_route_ev_discard(hand, balls=5)
        with_adjacent_dora = choose_route_ev_discard(hand, balls=5, dora_id=19)

        self.assertEqual(18, baseline.discard_tile)
        self.assertEqual(7, with_adjacent_dora.discard_tile)
        self.assertNotEqual(18, with_adjacent_dora.discard_tile)

    def test_area_uses_dora_only_as_a_near_tie_value_bonus(self):
        table = load_tables()["nyukyu_base_table.bytes"]
        hand = (0, 3, 12, 13, 14, 16, 18, 19, 21, 26, 28, 29, 29)

        baseline = choose_route_ev_area(hand, table, balls=5)
        with_dora = choose_route_ev_area(hand, table, balls=5, dora_id=18)

        self.assertEqual(5, baseline.area)
        self.assertEqual(6, with_dora.area)
        self.assertIn("dora=0.020", with_dora.reason)

    def test_yakuman_route_does_not_preserve_dora_over_route_shape(self):
        decision = choose_route_ev_discard(
            (3, 5, 7, 9, 9, 11, 11, 12, 12, 14, 15, 16, 16, 27),
            balls=5,
            drawn_tile=11,
            dora_id=3,
            ura_dora_id=3,
        )

        self.assertEqual(3, decision.discard_tile)
        self.assertIn("suuankou", decision.reason)

    def test_random_review_question_uses_normal_improvement_efficiency(self):
        decision = choose_route_ev_discard(
            (1, 3, 5, 6, 9, 11, 11, 12, 16, 16, 24, 25, 26, 6),
            balls=5,
            drawn_tile=6,
        )

        self.assertEqual(9, decision.discard_tile)
        self.assertEqual(2, decision.shanten_after)
        self.assertNotIn(decision.discard_tile, (24, 25, 26))

    def test_random_review_does_not_count_entire_suit_as_direct_progress(self):
        table = load_tables()["nyukyu_base_table.bytes"]

        turn2 = choose_route_ev_area(
            (2, 2, 6, 8, 9, 12, 12, 13, 14, 16, 17, 23, 25),
            table,
            balls=7,
        )
        turn3 = choose_route_ev_area(
            (2, 2, 6, 8, 9, 9, 12, 12, 13, 14, 16, 17, 23),
            table,
            balls=6,
        )

        self.assertEqual(2, turn2.area)
        self.assertEqual((7, 15, 24), turn2.target_tiles)
        self.assertEqual(1300, turn2.target_weight)
        self.assertEqual(2, turn3.area)
        self.assertEqual((2, 7, 9, 15), turn3.target_tiles)
        self.assertEqual(2100, turn3.target_weight)

    def test_random_review_daisangen_and_yakuman_fallback_rules(self):
        table = load_tables()["nyukyu_base_table.bytes"]

        daisangen = choose_route_ev_area(
            (4, 5, 14, 18, 20, 22, 28, 29, 30, 31, 32, 33, 33),
            table,
            balls=8,
        )
        fallback = choose_route_ev_discard(
            (3, 5, 7, 9, 9, 11, 11, 12, 12, 14, 15, 16, 16, 27),
            balls=5,
            drawn_tile=11,
        )

        self.assertEqual(4, daisangen.area)
        self.assertEqual((31, 32, 33), daisangen.target_tiles)
        self.assertIn("daisangen", daisangen.reason)
        self.assertEqual(3, fallback.discard_tile)
        self.assertNotEqual(15, fallback.discard_tile)
        self.assertIn("suuankou", fallback.reason)


if __name__ == "__main__":
    unittest.main()
