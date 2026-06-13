"""JSONL event reader for the JanqProbe BepInEx plugin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Iterator


@dataclass(frozen=True)
class ProbeEvent:
    ts: datetime
    type: str
    payload: dict[str, Any]
    line_number: int


class ProbeEventError(ValueError):
    """Raised when a JSONL probe event is malformed."""


def read_events(path: str | Path, *, skip_bad_lines: bool = False) -> Iterator[ProbeEvent]:
    event_path = Path(path)
    with event_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield parse_event(line, line_number=line_number)
            except Exception:
                if not skip_bad_lines:
                    raise


def parse_event(line: str, *, line_number: int = 0) -> ProbeEvent:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProbeEventError(f"line {line_number}: invalid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProbeEventError(f"line {line_number}: event must be a JSON object")

    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ProbeEventError(f"line {line_number}: event type must be a non-empty string")

    payload = raw.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ProbeEventError(f"line {line_number}: payload must be an object")

    ts_text = raw.get("ts")
    if not isinstance(ts_text, str) or not ts_text:
        raise ProbeEventError(f"line {line_number}: ts must be a non-empty string")

    try:
        ts = datetime.fromisoformat(ts_text)
    except ValueError as exc:
        raise ProbeEventError(f"line {line_number}: invalid timestamp {ts_text!r}") from exc

    return ProbeEvent(ts=ts, type=event_type, payload=payload, line_number=line_number)


def count_by_type(events: Iterable[ProbeEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.type] = counts.get(event.type, 0) + 1
    return counts


def latest_event(events: Iterable[ProbeEvent], event_type: str) -> ProbeEvent | None:
    latest: ProbeEvent | None = None
    for event in events:
        if event.type == event_type:
            latest = event
    return latest

