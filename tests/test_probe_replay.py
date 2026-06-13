import unittest

from janq_lab.probe.events import parse_event
from janq_lab.probe.replay import replay_events


def event(line_number, event_type, payload):
    return parse_event(
        '{"ts":"2026-06-12T13:52:10+00:00","type":"%s","payload":%s}'
        % (event_type, payload),
        line_number=line_number,
    )


class ProbeReplayTests(unittest.TestCase):
    def test_replays_one_complete_hand(self):
        events = [
            event(1, "recv_game_haipai", '{"gold":10,"mjchip":1000,"cchip":50,"status":"NORMAL","zandan":8,"haipai":[1,2,3],"omoDora":5,"uraDora":6}'),
            event(2, "send_action_shot", '{"area":4}'),
            event(3, "recv_game_tsumo", '{"pai":31,"zandan":7,"replay":false,"tehai":[1,2,3,31]}'),
            event(4, "recv_act_dahai", '{"richi":false,"pos":3,"sutehai":31}'),
            event(5, "recv_janq_result", '{"gold":10,"mjchip":1012,"cchip":50,"win":1,"han":3,"yakuLevel":"YL_03HAN","odds":12,"tehai":[1,2,3,31]}'),
        ]

        summary = replay_events(events)
        self.assertEqual(1, len(summary.hands))
        hand = summary.hands[0]
        self.assertTrue(hand.complete)
        self.assertEqual(1, hand.win)
        self.assertEqual((0, 1, 2), hand.haipai_model)
        self.assertEqual(4, hand.dora_model)
        self.assertEqual(5, hand.ura_dora_model)
        self.assertEqual(4, hand.shots[0].area)
        self.assertEqual(31, hand.shots[0].pai)
        self.assertEqual(30, hand.shots[0].pai_model)
        self.assertEqual((0, 1, 2, 30), hand.shots[0].tehai_model)
        self.assertEqual((0, 1, 2, 30), hand.result_payload["tehai_model"])
        self.assertEqual({"gold": 0, "mjchip": 12, "cchip": 0}, hand.currency_delta)

    def test_keeps_incomplete_hand(self):
        events = [
            event(1, "recv_game_haipai", '{"gold":10,"mjchip":1000,"cchip":50,"status":"NORMAL","zandan":8,"haipai":[0]}'),
            event(2, "send_action_shot", '{"area":7}'),
        ]

        summary = replay_events(events)
        self.assertEqual(1, len(summary.hands))
        self.assertFalse(summary.hands[0].complete)


if __name__ == "__main__":
    unittest.main()
