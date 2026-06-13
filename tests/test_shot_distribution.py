import json
import struct
import tempfile
import unittest
from pathlib import Path

from janq_lab.analysis.shot_distribution import compare_shot_distribution
from janq_lab.assets.nyukyu import AREA_COUNT, NyukyuTable


def deterministic_table(tile_id: int) -> NyukyuTable:
    values = []
    for current_tile in range(34):
        for _ in range(AREA_COUNT):
            values.append(10000 if current_tile == tile_id else 0)
    data = struct.pack("<" + "H" * len(values), *values)
    return NyukyuTable.from_bytes(data, name=f"always_{tile_id}")


class ShotDistributionTests(unittest.TestCase):
    def test_compares_normalized_observed_shots(self):
        rows = [
            {"ts": "2026-06-12T13:52:10+00:00", "type": "recv_game_haipai", "payload": {"haipai": [1]}},
            {"ts": "2026-06-12T13:52:11+00:00", "type": "send_action_shot", "payload": {"area": 4}},
            {"ts": "2026-06-12T13:52:12+00:00", "type": "recv_game_tsumo", "payload": {"pai": 31, "tehai": [1, 31]}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            report = compare_shot_distribution(str(path), table=deterministic_table(30))

        self.assertEqual(1, report.total_shots)
        area4 = report.areas[3]
        self.assertEqual(1, area4.shots)
        self.assertEqual(0, area4.impossible_observations)
        self.assertEqual(0.0, area4.chi_square)
        self.assertEqual("N", area4.top_observed[0][0])

    def test_marks_impossible_observation(self):
        rows = [
            {"ts": "2026-06-12T13:52:10+00:00", "type": "recv_game_haipai", "payload": {"haipai": [1]}},
            {"ts": "2026-06-12T13:52:11+00:00", "type": "send_action_shot", "payload": {"area": 1}},
            {"ts": "2026-06-12T13:52:12+00:00", "type": "recv_game_tsumo", "payload": {"pai": 2, "tehai": [1, 2]}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            report = compare_shot_distribution(str(path), table=deterministic_table(0))

        self.assertEqual(1, report.areas[0].impossible_observations)


if __name__ == "__main__":
    unittest.main()

