import json
import tempfile
import unittest
from pathlib import Path

from janq_lab.probe.events import ProbeEventError, count_by_type, read_events


class ProbeEventsTests(unittest.TestCase):
    def test_reads_jsonl_events(self):
        rows = [
            {"ts": "2026-06-12T13:52:10.2874845+00:00", "type": "probe_loaded", "payload": {"version": "0.1.0"}},
            {"ts": "2026-06-12T13:52:11+00:00", "type": "send_action_shot", "payload": {"area": 4}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            events = list(read_events(path))

        self.assertEqual(2, len(events))
        self.assertEqual("probe_loaded", events[0].type)
        self.assertEqual({"probe_loaded": 1, "send_action_shot": 1}, count_by_type(events))

    def test_rejects_bad_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                json.dumps({"ts": "2026-06-12T13:52:10+00:00", "type": "x", "payload": []}),
                encoding="utf-8",
            )
            with self.assertRaises(ProbeEventError):
                list(read_events(path))


if __name__ == "__main__":
    unittest.main()

