"""Run all stored JanQ accounts one-by-one to a terminal bankroll state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
import uuid

def _default_root() -> Path:
    env_root = os.environ.get("JANQ_WORKSPACE")
    if env_root and env_root.strip():
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


ROOT = _default_root()
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from janq_lab.automation.accounts import AutomationAccount, load_accounts, update_account_result  # noqa: E402


def configure_root(root: str | Path | None = None, *, update_environment: bool = True) -> Path:
    global ROOT, SRC

    ROOT = (Path(root).expanduser() if root is not None else _default_root()).resolve()
    SRC = ROOT / "src"
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    if update_environment:
        os.environ["JANQ_WORKSPACE"] = str(ROOT)
    return ROOT


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None, help="JanQ workspace root; defaults to this script's repository.")
    parser.add_argument("--accounts-path", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--events-path", default=None)
    parser.add_argument("--bridge-dir", default=None)
    parser.add_argument("--sessions-dir", default=None)
    parser.add_argument("--status-path", default=None)
    parser.add_argument("--target-mjchip", type=int, default=4000)
    parser.add_argument("--bankruptcy-mjchip", type=int, default=9)
    parser.add_argument("--forced-bet", type=int, default=10)
    parser.add_argument("--max-hands-per-account", type=int, default=100000)
    parser.add_argument("--max-normal-hands-per-account", type=int, default=None)
    parser.add_argument("--max-runtime-seconds-per-account", type=float, default=86400.0)
    parser.add_argument("--login-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--exit-timeout-seconds", type=float, default=240.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--rerun-terminal", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--limit-accounts", type=int, default=None)
    args = parser.parse_args()
    configure_root(args.root)
    args.accounts_path = args.accounts_path or str(ROOT / "_runtime" / "accounts" / "accounts.json")
    args.config = args.config or str(ROOT / "automation.example.yaml")
    args.events_path = args.events_path or str(ROOT / "_runtime" / "logs" / "janq_events.jsonl")
    args.bridge_dir = args.bridge_dir or str(ROOT / "_runtime" / "bridge")
    args.sessions_dir = args.sessions_dir or str(ROOT / "_runtime" / "sessions")
    args.status_path = args.status_path or str(ROOT / "_runtime" / "batch" / "account_batch_status.json")
    os.environ["JANQ_PROBE_LOG"] = str(Path(args.events_path).resolve())

    accounts_path = Path(args.accounts_path).resolve()
    bridge_dir = Path(args.bridge_dir).resolve()
    sessions_dir = Path(args.sessions_dir).resolve()
    status_path = Path(args.status_path).resolve()
    sessions_dir.mkdir(parents=True, exist_ok=True)
    status_path.parent.mkdir(parents=True, exist_ok=True)

    accounts = load_accounts(accounts_path)
    selected_accounts = accounts[: args.limit_accounts] if args.limit_accounts is not None else accounts
    write_status(
        status_path,
        {
            "state": "starting",
            "updatedAt": utc_now(),
            "accountsPath": str(accounts_path),
            "accountCount": len(selected_accounts),
            "targetMjchip": args.target_mjchip,
            "bankruptcyMjchip": args.bankruptcy_mjchip,
        },
    )

    completed = 0
    skipped = 0
    failed = 0
    for index, account in enumerate(selected_accounts, start=1):
        selector = account.request_id or account.login_id
        account_payload = public_account_payload(account)
        if (
            not args.rerun_terminal
            and account.final_mjchip is not None
            and is_terminal_chip(account.final_mjchip, args.target_mjchip, args.bankruptcy_mjchip)
        ):
            skipped += 1
            write_status(
                status_path,
                {
                    "state": "skipped_terminal_account",
                    "updatedAt": utc_now(),
                    "accountIndex": index,
                    "accountCount": len(selected_accounts),
                    "account": account_payload,
                    "completed": completed,
                    "skipped": skipped,
                    "failed": failed,
                },
            )
            continue

        session_path = sessions_dir / session_name(index, account)
        write_status(
            status_path,
            {
                "state": "preparing_account",
                "updatedAt": utc_now(),
                "accountIndex": index,
                "accountCount": len(selected_accounts),
                "account": account_payload,
                "session": str(session_path),
                "completed": completed,
                "skipped": skipped,
                "failed": failed,
            },
        )

        try:
            clean_bridge_queue(bridge_dir)
            send_bridge_command(
                bridge_dir,
                "exit_to_login",
                timeout_seconds=args.exit_timeout_seconds,
                poll_seconds=args.poll_seconds,
            )
            result = run_one_account(
                args,
                selector=selector,
                accounts_path=accounts_path,
                bridge_dir=bridge_dir,
                session_path=session_path,
            )
            summary = summarize_session(session_path)
            status = classify_status(
                summary,
                returncode=result.returncode,
                target_mjchip=args.target_mjchip,
                bankruptcy_mjchip=args.bankruptcy_mjchip,
            )
            update_payload = update_account_result(
                accounts_path,
                selector,
                current_mjchip=summary.get("mjchip"),
                status=status["accountStatus"],
                terminal_reason=status["terminalReason"],
                session_path=str(session_path),
                completed_hands=summary.get("completedHands"),
            )
            write_status(
                status_path,
                {
                    "state": "account_finished",
                    "updatedAt": utc_now(),
                    "accountIndex": index,
                    "accountCount": len(selected_accounts),
                    "account": update_payload,
                    "session": str(session_path),
                    "botReturncode": result.returncode,
                    "summary": summary,
                    "terminal": status["terminal"],
                    "completed": completed,
                    "skipped": skipped,
                    "failed": failed,
                },
            )

            send_bridge_command(
                bridge_dir,
                "exit_to_login",
                timeout_seconds=args.exit_timeout_seconds,
                poll_seconds=args.poll_seconds,
            )

            if not status["terminal"] and not args.continue_on_error:
                failed += 1
                write_status(
                    status_path,
                    {
                        "state": "stopped_nonterminal_account",
                        "updatedAt": utc_now(),
                        "accountIndex": index,
                        "accountCount": len(selected_accounts),
                        "account": update_payload,
                        "session": str(session_path),
                        "summary": summary,
                        "completed": completed,
                        "skipped": skipped,
                        "failed": failed,
                    },
                )
                return 2

            completed += 1
        except Exception as exc:
            failed += 1
            write_status(
                status_path,
                {
                    "state": "failed",
                    "updatedAt": utc_now(),
                    "accountIndex": index,
                    "accountCount": len(selected_accounts),
                    "account": account_payload,
                    "session": str(session_path),
                    "error": str(exc),
                    "completed": completed,
                    "skipped": skipped,
                    "failed": failed,
                },
            )
            if not args.continue_on_error:
                return 1

    write_status(
        status_path,
        {
            "state": "complete",
            "updatedAt": utc_now(),
            "accountCount": len(selected_accounts),
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
        },
    )
    return 0 if failed == 0 else 1


def run_one_account(
    args: argparse.Namespace,
    *,
    selector: str,
    accounts_path: Path,
    bridge_dir: Path,
    session_path: Path,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "janq_lab.automation.bot",
        "--config",
        str(Path(args.config).resolve()),
        "--mode",
        "plugin_live",
        "--events-path",
        str(Path(args.events_path).resolve()),
        "--bridge-dir",
        str(bridge_dir),
        "--session-log-path",
        str(session_path),
        "--login-account",
        selector,
        "--account-store-path",
        str(accounts_path),
        "--login-timeout-seconds",
        str(args.login_timeout_seconds),
        "--target-mjchip",
        str(args.target_mjchip),
        "--bankruptcy-mjchip",
        str(args.bankruptcy_mjchip),
        "--max-hands",
        str(args.max_hands_per_account),
        "--max-runtime-seconds",
        str(args.max_runtime_seconds_per_account),
    ]
    if args.max_normal_hands_per_account is not None:
        command += ["--max-normal-hands", str(args.max_normal_hands_per_account)]
    if args.forced_bet > 0:
        command += ["--forced-bet", str(args.forced_bet)]
    if args.seed is not None:
        command += ["--seed", str(args.seed)]

    env = dict(**os_environ_with_pythonpath())
    return subprocess.run(command, text=True, env=env, check=False)


def send_bridge_command(
    bridge_dir: Path,
    kind: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    commands_dir = bridge_dir / "commands"
    results_dir = bridge_dir / "results"
    commands_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    command_id = uuid.uuid4().hex
    command = {
        "id": command_id,
        "kind": kind,
        "createdAt": utc_now(),
    }
    command_path = commands_dir / f"{command_id}.json"
    result_path = results_dir / f"{command_id}.json"
    temp_path = commands_dir / f".{command_id}.tmp"
    temp_path.write_text(
        json.dumps(command, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(command_path)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if result_path.exists():
            result = read_bridge_result_when_ready(result_path, deadline=deadline)
            if result is None:
                time.sleep(max(0.05, poll_seconds))
                continue
            unlink_when_ready(result_path)
            if result.get("success") is not True:
                raise RuntimeError(f"{kind} failed: {result.get('error') or 'bridge_rejected'}")
            return result
        time.sleep(max(0.05, poll_seconds))
    raise TimeoutError(f"{kind} timed out after {timeout_seconds}s")


def read_bridge_result_when_ready(path: Path, *, deadline: float) -> dict[str, Any] | None:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None
        except (PermissionError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.05)
            continue
        if not isinstance(value, dict):
            raise ValueError(f"bridge result must be an object: {path}")
        return value
    if last_error is not None:
        raise last_error
    return None


def unlink_when_ready(path: Path, *, attempts: int = 10) -> bool:
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except (PermissionError, OSError):
            if attempt + 1 >= attempts:
                return False
            time.sleep(0.05)
    return False


def summarize_session(path: Path) -> dict[str, Any]:
    rows = read_jsonl(path)
    latest_state = last_of_type(rows, "bot_state")
    latest_summary = last_of_type(rows, "bot_session_summary")
    latest_pause = last_of_type(rows, "bot_pause")
    state_payload: dict[str, Any] = {}
    if latest_summary and isinstance(latest_summary.get("payload"), dict):
        summary_payload = latest_summary["payload"]
        if isinstance(summary_payload.get("state"), dict):
            state_payload = summary_payload["state"]
    if not state_payload and latest_state and isinstance(latest_state.get("payload"), dict):
        state_payload = latest_state["payload"]

    currency = state_payload.get("currency") if isinstance(state_payload, dict) else {}
    pause_payload = latest_pause.get("payload") if latest_pause else {}
    return {
        "rows": len(rows),
        "mjchip": currency.get("mjchip") if isinstance(currency, dict) else None,
        "startMjchip": currency.get("start_mjchip") if isinstance(currency, dict) else None,
        "completedHands": state_payload.get("completed_hands") if isinstance(state_payload, dict) else None,
        "normalCompletedHands": (
            state_payload.get("normal_completed_hands") if isinstance(state_payload, dict) else None
        ),
        "phase": state_payload.get("phase") if isinstance(state_payload, dict) else None,
        "pauseReason": pause_payload.get("reason") if isinstance(pause_payload, dict) else None,
        "hasSummary": latest_summary is not None,
    }


def classify_status(
    summary: dict[str, Any],
    *,
    returncode: int,
    target_mjchip: int,
    bankruptcy_mjchip: int,
) -> dict[str, Any]:
    mjchip = summary.get("mjchip")
    pause_reason = summary.get("pauseReason")
    if isinstance(mjchip, int) and mjchip >= target_mjchip:
        return {"terminal": True, "terminalReason": "target_mjchip", "accountStatus": "target_reached"}
    if isinstance(mjchip, int) and mjchip <= bankruptcy_mjchip:
        return {"terminal": True, "terminalReason": "bankruptcy_mjchip", "accountStatus": "bankrupt"}
    if pause_reason == "max_normal_hands":
        return {
            "terminal": True,
            "terminalReason": "max_normal_hands",
            "accountStatus": "rotation_quota_reached",
        }
    reason = pause_reason or ("bot_returncode_" + str(returncode) if returncode else "nonterminal_stop")
    return {
        "terminal": False,
        "terminalReason": reason,
        "accountStatus": "stopped_" + sanitize_token(reason),
    }


def is_terminal_chip(mjchip: int, target_mjchip: int, bankruptcy_mjchip: int) -> bool:
    return mjchip >= target_mjchip or mjchip <= bankruptcy_mjchip


def clean_bridge_queue(bridge_dir: Path) -> None:
    commands_dir = bridge_dir / "commands"
    results_dir = bridge_dir / "results"
    for directory in (commands_dir, results_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.json", "*.working", ".*.tmp"):
        for path in commands_dir.glob(pattern):
            path.unlink(missing_ok=True)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def public_account_payload(account: AutomationAccount) -> dict[str, Any]:
    return {
        "requestId": account.request_id,
        "loginId": account.masked_login_id,
        "nickname": account.nickname,
        "finalMjchip": account.final_mjchip,
        "status": account.status,
    }


def session_name(index: int, account: AutomationAccount) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    token = sanitize_token(account.request_id or account.login_id)[:16]
    return f"batch_{stamp}_{index:03d}_{token}.jsonl"


def sanitize_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:64] or "account"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def os_environ_with_pythonpath() -> dict[str, str]:
    env = dict(os.environ)
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing else src + os.pathsep + existing
    env["JANQ_WORKSPACE"] = str(ROOT)
    return env


if __name__ == "__main__":
    raise SystemExit(main())
