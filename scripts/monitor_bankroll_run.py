"""Monitor a live JanQ bankroll run and build final stats at terminal state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--events", default="_runtime/logs/janq_events.jsonl")
    parser.add_argument("--target-mjchip", type=int, default=4000)
    parser.add_argument("--bankruptcy-mjchip", type=int, default=9)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--report-dir", default="_runtime/reports")
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    args = parser.parse_args()

    session_path = Path(args.session)
    events_path = Path(args.events)
    status_path = Path(args.status_path)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    final_report: dict[str, str] | None = None
    while True:
        summary = summarize_session(
            session_path,
            target_mjchip=args.target_mjchip,
            bankruptcy_mjchip=args.bankruptcy_mjchip,
        )
        payload: dict[str, Any] = {
            "state": "complete" if summary["terminal"] else "running",
            "updatedAt": utc_now(),
            "session": str(session_path),
            "events": str(events_path),
            "targetMjchip": args.target_mjchip,
            "bankruptcyMjchip": args.bankruptcy_mjchip,
            **summary,
        }
        if summary["terminal"] and final_report is None:
            final_report = build_report(session_path, events_path, report_dir)
            payload["report"] = final_report
        elif final_report is not None:
            payload["report"] = final_report
        write_status(status_path, payload)
        if summary["terminal"]:
            break
        time.sleep(max(1.0, args.poll_seconds))


def summarize_session(
    session_path: Path,
    *,
    target_mjchip: int,
    bankruptcy_mjchip: int,
) -> dict[str, Any]:
    rows = read_jsonl(session_path)
    latest_state = last_of_type(rows, "bot_state")
    latest_decision = last_of_type(rows, "bot_decision")
    latest_action = last_of_type(rows, "bot_action_done")
    latest_pause = last_of_type(rows, "bot_pause")
    latest_summary = last_of_type(rows, "bot_session_summary")

    state_payload = latest_state.get("payload") if latest_state else {}
    currency = state_payload.get("currency") if isinstance(state_payload, dict) else {}
    mjchip = currency.get("mjchip") if isinstance(currency, dict) else None
    start_mjchip = currency.get("start_mjchip") if isinstance(currency, dict) else None
    delta_mjchip = (
        mjchip - start_mjchip
        if isinstance(mjchip, int) and isinstance(start_mjchip, int)
        else None
    )

    pause_payload = latest_pause.get("payload") if latest_pause else {}
    terminal_reason = None
    if isinstance(pause_payload, dict) and pause_payload.get("reason"):
        terminal_reason = pause_payload["reason"]
    elif isinstance(mjchip, int) and mjchip >= target_mjchip:
        terminal_reason = "target_mjchip_observed"
    elif isinstance(mjchip, int) and mjchip <= bankruptcy_mjchip:
        terminal_reason = "bankruptcy_mjchip_observed"

    action_payload = latest_action.get("payload") if latest_action else {}
    decision_payload = latest_decision.get("payload") if latest_decision else {}
    terminal = terminal_reason in {
        "target_mjchip",
        "target_mjchip_observed",
        "bankruptcy_mjchip",
        "bankruptcy_mjchip_observed",
    }

    return {
        "terminal": terminal,
        "terminalReason": terminal_reason,
        "rows": len(rows),
        "sessionBytes": session_path.stat().st_size if session_path.exists() else 0,
        "lastWrite": session_path.stat().st_mtime if session_path.exists() else None,
        "seq": latest_state.get("seq") if latest_state else None,
        "phase": state_payload.get("phase") if isinstance(state_payload, dict) else None,
        "mode": state_payload.get("mode") if isinstance(state_payload, dict) else None,
        "status": state_payload.get("status") if isinstance(state_payload, dict) else None,
        "gameState": state_payload.get("game_state") if isinstance(state_payload, dict) else None,
        "balls": state_payload.get("balls") if isinstance(state_payload, dict) else None,
        "handIndex": state_payload.get("hand_index") if isinstance(state_payload, dict) else None,
        "completedHands": state_payload.get("completed_hands") if isinstance(state_payload, dict) else None,
        "mjchip": mjchip,
        "startMjchip": start_mjchip,
        "deltaMjchip": delta_mjchip,
        "isReach": state_payload.get("is_reach") if isinstance(state_payload, dict) else None,
        "lastResult": state_payload.get("last_result") if isinstance(state_payload, dict) else None,
        "latestDecision": compact_decision(latest_decision, decision_payload),
        "latestAction": compact_action(latest_action, action_payload),
        "hasSessionSummary": latest_summary is not None,
    }


def build_report(session_path: Path, events_path: Path, report_dir: Path) -> dict[str, str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{session_path.stem}_final_{stamp}"
    json_path = report_dir / f"{stem}.json"
    html_path = report_dir / f"{stem}.html"
    cmd = [
        sys.executable,
        str(Path("scripts") / "build_actual_session_review.py"),
        str(session_path),
        "--events-path",
        str(events_path),
        "--json-output",
        str(json_path),
        "--html-output",
        str(html_path),
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    report = {
        "json": str(json_path),
        "html": str(html_path),
        "returncode": str(result.returncode),
    }
    if result.stdout.strip():
        report["stdout"] = result.stdout.strip()[-1000:]
    if result.stderr.strip():
        report["stderr"] = result.stderr.strip()[-1000:]
    return report


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def last_of_type(rows: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    for row in reversed(rows):
        if row.get("type") == event_type:
            return row
    return None


def compact_decision(row: dict[str, Any] | None, payload: Any) -> dict[str, Any] | None:
    if not row or not isinstance(payload, dict):
        return None
    return {
        "seq": row.get("seq"),
        "action": payload.get("action"),
        "reason": payload.get("reason"),
    }


def compact_action(row: dict[str, Any] | None, payload: Any) -> dict[str, Any] | None:
    if not row or not isinstance(payload, dict):
        return None
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    bridge_result = details.get("bridge_result") if isinstance(details.get("bridge_result"), dict) else {}
    bridge_state = bridge_result.get("state") if isinstance(bridge_result.get("state"), dict) else {}
    return {
        "seq": row.get("seq"),
        "success": payload.get("success"),
        "action": payload.get("action"),
        "betRate": bridge_state.get("betRate"),
        "bridgeError": bridge_result.get("error"),
    }


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
