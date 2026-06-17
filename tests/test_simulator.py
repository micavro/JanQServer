import struct
import unittest

from janq_lab.assets.nyukyu import AREA_COUNT, NyukyuTable
from janq_lab.model.hand import is_complete_hand, shanten, tile_set, winning_tiles
from janq_lab.model.simulator import simulate_hand
from janq_lab.strategy.bonus import choose_bonus_discard
from janq_lab.strategy.greedy import AreaDecision, DiscardDecision


def deterministic_table(tile_id: int) -> NyukyuTable:
    values = []
    for current_tile in range(34):
        for _ in range(AREA_COUNT):
            values.append(10000 if current_tile == tile_id else 0)
    data = struct.pack("<" + "H" * len(values), *values)
    return NyukyuTable.from_bytes(data, name=f"always_{tile_id}")


class SequenceTable:
    def __init__(self, draws):
        self.draws = list(draws)

    def draw(self, area, rng):
        del area, rng
        if not self.draws:
            raise RuntimeError("no more draws")
        return self.draws.pop(0)


def fixed_area(hand, table, balls, **kwargs):
    del hand, table, balls, kwargs
    return AreaDecision(4, (), 0, "fixed_area")


def declare_then_agari(hand, balls, **kwargs):
    del balls
    state = hand if hasattr(hand, "counts") else tile_set(hand)
    if is_complete_hand(state):
        return DiscardDecision(True, None, None, (), "complete_hand")
    drawn_tile = kwargs.get("drawn_tile")
    if drawn_tile is None:
        drawn_tile = next(tile_id for tile_id, count in enumerate(state.counts) if count)
    after = state.with_removed_one(drawn_tile)
    return DiscardDecision(
        False,
        drawn_tile,
        shanten(after),
        winning_tiles(after),
        "declare_test_riichi",
        declare_riichi=True,
    )


fixed_area.uses_full_context = True
declare_then_agari.uses_full_context = True


def discard_drawn_tile(hand, balls, **kwargs):
    del balls
    state = hand if hasattr(hand, "counts") else tile_set(hand)
    drawn_tile = kwargs["drawn_tile"]
    after = state.with_removed_one(drawn_tile)
    return DiscardDecision(
        False,
        drawn_tile,
        shanten(after),
        winning_tiles(after),
        "discard_drawn_tile",
    )


discard_drawn_tile.uses_full_context = True


def forbidden_hold_discard(*args, **kwargs):
    del args, kwargs
    raise AssertionError("HOLD mode must not call the discard policy")


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

    def test_normal_mode_auto_surrenders_when_balls_do_not_exceed_shanten(self):
        table = deterministic_table(33)
        hand = [0, 1, 2, 3, 4, 5, 9, 10, 11, 18, 19, 27, 31]

        result = simulate_hand(
            hand,
            table,
            balls=2,
            choose_area=fixed_area,
            choose_discard=discard_drawn_tile,
        )

        self.assertFalse(result.win)
        self.assertTrue(result.auto_surrender)
        self.assertEqual(1, result.auto_surrender_shanten)
        self.assertEqual(1, result.shots)
        self.assertTrue(result.turns[0].auto_surrender)
        self.assertEqual(1, result.turns[0].shot.balls_after)
        self.assertEqual(tuple(sorted(hand)), result.final_hand.to_tiles())

    def test_hold_hand_auto_discards_draw_without_changing_locked_tiles(self):
        table = deterministic_table(5)
        hand = [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31]

        result = simulate_hand(
            hand,
            table,
            balls=1,
            max_turns=1,
            choose_area=fixed_area,
            choose_discard=forbidden_hold_discard,
            hold_hand=True,
        )

        self.assertFalse(result.win)
        self.assertEqual(tuple(sorted(hand)), result.final_hand.to_tiles())
        self.assertEqual(5, result.turns[0].discard.discard_tile)
        self.assertEqual("bonus_hold_auto_discard", result.turns[0].discard.reason)

    def test_hold_hand_fourth_copy_refunds_ball_then_auto_discards(self):
        table = deterministic_table(0)
        hand = [0, 0, 0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 31]

        result = simulate_hand(
            hand,
            table,
            balls=1,
            max_turns=1,
            choose_area=fixed_area,
            choose_discard=forbidden_hold_discard,
            hold_hand=True,
        )

        self.assertFalse(result.win)
        self.assertEqual(tuple(sorted(hand)), result.final_hand.to_tiles())
        self.assertTrue(result.turns[0].shot.fourth_copy)
        self.assertEqual(1, result.turns[0].shot.balls_after)
        self.assertEqual(0, result.turns[0].discard.discard_tile)

    def test_bonus_discard_policy_cannot_rebuild_locked_hand(self):
        hand = [0, 1, 2, 5, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31]

        decision = choose_bonus_discard(
            hand,
            balls=2,
            drawn_tile=5,
        )

        self.assertFalse(decision.is_agari)
        self.assertEqual(5, decision.discard_tile)
        self.assertEqual("bonus_hold_auto_discard", decision.reason)

    def test_riichi_and_ippatsu_are_recorded(self):
        table = SequenceTable([5, 31])
        hand = [0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 27, 27, 31]

        result = simulate_hand(
            hand,
            table,
            choose_area=fixed_area,
            choose_discard=declare_then_agari,
            balls=2,
        )

        self.assertTrue(result.win)
        self.assertTrue(result.riichi)
        self.assertTrue(result.ippatsu_win)
        self.assertEqual(1, result.riichi_turn)
        self.assertTrue(result.double_riichi)
        self.assertTrue(result.turns[0].riichi_declared)
        self.assertTrue(result.turns[1].ippatsu_chance)


if __name__ == "__main__":
    unittest.main()
