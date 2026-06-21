"""Monitor a live JanQ run and capture the first observed yakuman bonus."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import time


YAKUMAN_TEXT_MARKERS = (
    "YakumanBonus",
    '"status":"YAKUMAN"',
    '"status": "YAKUMAN"',
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="_runtime/logs/janq_events.jsonl")
    parser.add_argument("--session", required=True)
    parser.add_argument("--output-dir", default="_runtime/captures")
    parser.add_argument("--start-line", type=int, default=0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--pre-lines", type=int, default=500)
    parser.add_argument("--post-seconds", type=float, default=150.0)
    parser.add_argument("--post-lines", type=int, default=500)
    parser.add_argument("--status-path", default=None)
    args = parser.parse_args()

    events_path = Path(args.events)
    session_path = Path(args.session)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = Path(args.status_path) if args.status_path else output_dir / "live_yakuman_monitor_status.json"

    offset = 0
    line_no = 0
    pre_buffer: deque[tuple[int, str]] = deque(maxlen=max(1, args.pre_lines))
    detected: tuple[int, str] | None = None

    if events_path.exists():
        with events_path.open("r", encoding="utf-8-sig") as handle:
            while True:
                raw = handle.readline()
                if not raw:
                    break
                line_no += 1
                if line_no <= args.start_line:
                    continue
                line = raw.rstrip("\n")
                pre_buffer.append((line_no, line))
                if detected is None and is_yakuman_line(line):
                    detected = (line_no, line)
                    break
            offset = handle.tell()

    write_status(
        status_path,
        {
            "state": "watching" if detected is None else "detected",
            "events": str(events_path),
            "session": str(session_path),
            "startLine": args.start_line,
            "currentLine": line_no,
            "updatedAt": utc_now(),
        },
    )

    while detected is None:
        if events_path.exists():
            size = events_path.stat().st_size
            if size < offset:
                offset = 0
                line_no = 0
                pre_buffer.clear()
            with events_path.open("r", encoding="utf-8-sig") as handle:
                handle.seek(offset)
                while True:
                    raw = handle.readline()
                    if not raw:
                        break
                    line_no += 1
                    if line_no <= args.start_line:
                        continue
                    line = raw.rstrip("\n")
                    pre_buffer.append((line_no, line))
                    if is_yakuman_line(line):
                        detected = (line_no, line)
                        break
                offset = handle.tell()
        if detected is not None:
            break
        write_status(
            status_path,
            {
                "state": "watching",
                "events": str(events_path),
                "session": str(session_path),
                "startLine": args.start_line,
                "currentLine": line_no,
                "updatedAt": utc_now(),
            },
        )
        time.sleep(args.poll_seconds)

    assert detected is not None
    detected_line, detected_text = detected
    post_lines: list[tuple[int, str]] = []
    deadline = time.monotonic() + max(0.0, args.post_seconds)
    while time.monotonic() < deadline and len(post_lines) < args.post_lines:
        if events_path.exists():
            with events_path.open("r", encoding="utf-8-sig") as handle:
                handle.seek(offset)
                while True:
                    raw = handle.readline()
                    if not raw:
                        break
                    line_no += 1
                    line = raw.rstrip("\n")
                    post_lines.append((line_no, line))
                    if len(post_lines) >= args.post_lines:
                        break
                offset = handle.tell()
        time.sleep(args.poll_seconds)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = output_dir / f"yakuman_capture_{stamp}"
    event_capture = prefix.with_suffix(".events.jsonl")
    session_capture = prefix.with_suffix(".session.jsonl")
    summary_path = prefix.with_suffix(".summary.json")

    event_rows = list(pre_buffer)
    if not event_rows or event_rows[-1][0] != detected_line:
        event_rows.append((detected_line, detected_text))
    event_rows.extend(post_lines)
    write_numbered_lines(event_capture, event_rows)

    session_rows = read_text_lines(session_path)
    session_capture.write_text("\n".join(session_rows[-1500:]) + ("\n" if session_rows else ""), encoding="utf-8")

    summary = {
        "detectedAt": utc_now(),
        "detectedEventLine": detected_line,
        "detectedEvent": parse_json_or_text(detected_text),
        "eventsPath": str(events_path),
        "sessionPath": str(session_path),
        "eventCapture": str(event_capture),
        "sessionCapture": str(session_capture),
        "preLines": len(pre_buffer),
        "postLines": len(post_lines),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_status(
        status_path,
        {
            "state": "captured",
            "summary": str(summary_path),
            "detectedEventLine": detected_line,
            "updatedAt": utc_now(),
        },
    )


def is_yakuman_line(line: str) -> bool:
    lower = line.lower()
    if any(marker in line for marker in YAKUMAN_TEXT_MARKERS) or "yakumanbonus" in lower:
        return True

    payload = parse_json_or_text(line)
    if not isinstance(payload, dict):
        return False
    return has_positive_yakuman(payload)


def has_positive_yakuman(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower().replace("_", "")
            if normalized in {"yakuman", "yakumancount", "yakumanrenchan"}:
                if numeric_positive(item):
                    return True
            if key.lower() in {"status", "gamemode", "gamestate", "mode"} and str(item).upper() == "YAKUMAN":
                return True
            if isinstance(item, (dict, list)) and has_positive_yakuman(item):
                return True
    elif isinstance(value, list):
        return any(has_positive_yakuman(item) for item in value)
    return False


def numeric_positive(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        try:
            return float(value) > 0
        except ValueError:
            return False
    return False


def write_numbered_lines(path: Path, rows: list[tuple[int, str]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for line_no, line in rows:
            try:
                payload = json.loads(line)
                payload["_line"] = line_no
                handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            except Exception:
                handle.write(json.dumps({"_line": line_no, "raw": line}, ensure_ascii=False) + "\n")


def read_text_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8-sig").splitlines()


def parse_json_or_text(text: str) -> object:
    try:
        return json.loads(text)
    except Exception:
        return text


def write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
