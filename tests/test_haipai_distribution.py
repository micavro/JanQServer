import unittest

from janq_lab.analysis.haipai_distribution import summarize_haipai
from janq_lab.model.haipai import observed_normal_haipai
from janq_lab.model.hand import tile_set
from janq_lab.probe.events import parse_event


def event(line_number, event_type, payload):
    return parse_event(
        '{"ts":"2026-06-12T13:52:10+00:00","type":"%s","payload":%s}'
        % (event_type, payload),
        line_number=line_number,
    )


class HaipaiDistributionTests(unittest.TestCase):
    def test_observed_normal_haipai_filters_to_normal_13_tile_hands(self):
        normal_tiles = list(range(1, 14))
        events = [
            event(1, "recv_game_haipai", '{"status":"NORMAL","haipai":%s}' % normal_tiles),
            event(2, "recv_game_haipai", '{"status":"PARENCHAN","haipai":%s}' % normal_tiles),
            event(3, "recv_game_haipai", '{"status":"NORMAL","haipai":[1,2,3]}'),
        ]

        observed = observed_normal_haipai(events)

        self.assertEqual(1, len(observed.hands))
        self.assertEqual(2, observed.ignored_hands)
        self.assertEqual(tuple(range(13)), observed.hands[0].to_tiles())

    def test_summary_reports_user_yakuman_opener_gates(self):
        hand = tile_set([0, 0, 0, 1, 1, 2, 2, 31, 32, 33, 3, 4, 5])

        summary = summarize_haipai([hand])

        self.assertEqual(1, summary["hands"])
        self.assertEqual(1.0, summary["opener_rates"]["suuankou_user_gate"])
        self.assertEqual(1.0, summary["opener_rates"]["daisangen_user_gate"])


if __name__ == "__main__":
    unittest.main()
