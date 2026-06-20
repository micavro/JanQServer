"""JSONL session logger for JanQ automation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any


class SessionLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._sequence = 0

    def write(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        envelope = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "seq": self._next_sequence(),
            "type": event_type,
            "payload": payload or {},
        }
        line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence


def default_session_log_path(session_dir: str | Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(session_dir) / f"janq_bot_{stamp}.jsonl"
