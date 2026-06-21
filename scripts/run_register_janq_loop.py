"""Register fresh accounts, collect Yakuhime rewards, then run JanQ on each account."""

from __future__ import annotations

import argparse
import ctypes
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


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from janq_lab.automation.accounts import update_account_result  # noqa: E402


GAME_PATH = ROOT / "sega_net_MJ" / "MJ" / "MJ.exe"
GAME_DIR = GAME_PATH.parent
BEPINEX_LOG = GAME_DIR / "BepInEx" / "LogOutput.log"
EVENTS_PATH = ROOT / "_runtime" / "logs" / "janq_events.jsonl"
BRIDGE_DIR = ROOT / "_runtime" / "bridge"
PREP_DIR = ROOT / "_runtime" / "account_prep"
PREP_REQUEST = PREP_DIR / "request.json"
PREP_STATUS = PREP_DIR / "status.json"
ACCOUNTS_PATH = ROOT / "_runtime" / "accounts" / "accounts.json"
LOOP_DIR = ROOT / "_runtime" / "register_janq_loop"
LOOP_STATUS = LOOP_DIR / "status.json"
SESSIONS_DIR = ROOT / "_runtime" / "sessions"

COMPLETE_PREP_STAGES = {"complete", "complete_accessible_stories"}
LOADING_STAGES = (
    "loading_yakuhime_story",
    "loading_yakuhime_submenu_resources",
    "loading_yakuhime_chapters",
    "loading_yakuhime_story_list",
)
LOGIN_WAIT_STAGES = (
    "waiting_captured_account_login",
)
SAFE_EXIT_PHASES = {"bet_wait", "free_wait"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--count", type=int, default=1)
    parser.add_argument("--bet", type=int, default=50)
    parser.add_argument("--target-mjchip", type=int, default=10000)
    parser.add_argument("--bankruptcy-mjchip", type=int, default=49)
    parser.add_argument("--strategy", choices=("public", "greedy", "route_ev", "route_ev2"), default="route_ev")
    parser.add_argument("--game-width", type=int, default=320)
    parser.add_argument("--game-height", type=int, default=180)
    parser.add_argument("--show-game", action="store_true")
    parser.add_argument("--hidden-game", action="store_true")
    parser.add_argument("--prep-timeout-seconds", type=float, default=7200.0)
    parser.add_argument("--prep-loading-stall-seconds", type=float, default=150.0)
    parser.add_argument("--prep-generic-stall-seconds", type=float, default=420.0)
    parser.add_argument("--prep-max-stories", type=int, default=None)
    parser.add_argument("--bot-max-hands", type=int, default=100000)
    parser.add_argument("--bot-max-runtime-seconds", type=float, default=86400.0)
    parser.add_argument("--exit-timeout-seconds", type=float, default=25.0)
    parser.add_argument("--fresh-game", action="store_true")
    parser.add_argument("--fresh-prep", action="store_true")
    parser.add_argument("--no-resume-stopped", action="store_true")
    parser.add_argument("--keep-game-open", action="store_true")
    parser.add_argument("--nickname-prefix", default="JanQ")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    if args.count < 1:
        raise ValueError("--count must be positive")
    ensure_runtime()
    if args.fresh_game:
        stop_copied_mj()
    start_game(args)
    wait_probe_loaded()
    if args.hidden_game:
        hide_or_minimize_game_windows(
            copied_mj_pids(),
            hide=True,
            width=args.game_width,
            height=args.game_height,
        )

    try:
        completed = 0
        failed = 0
        attempts = 0
        while completed < args.count:
            attempts += 1
            iteration = completed + 1
            resumable = find_resumable_account(args) if not args.no_resume_stopped else None
            active_prep = find_active_prep_request()
            request_id = (
                str(resumable.get("requestId"))
                if resumable
                else str(active_prep.get("requestId") or active_prep.get("id"))
                if active_prep
                else f"loop_{datetime.now():%Y%m%d_%H%M%S}_{iteration:03d}_{uuid.uuid4().hex[:8]}"
            )
            nickname = (
                str(resumable.get("nickname") or "")
                if resumable
                else str(active_prep.get("nickname") or "")
                if active_prep
                else run_nickname(args.nickname_prefix, iteration, args.count)
            )
            write_loop_status(
                {
                    "state": "resuming_account" if resumable else "preparing_account",
                    "iteration": iteration,
                    "count": args.count,
                    "attempt": attempts,
                    "requestId": request_id,
                    "nickname": nickname,
                    "resume": public_account_row(resumable) if resumable else None,
                    "updatedAt": utc_now(),
                }
            )
            session_path: Path | None = None
            try:
                if resumable:
                    prep = {"resumedAccount": public_account_row(resumable)}
                else:
                    prep = prepare_account(args, request_id=request_id, nickname=nickname)
                    request_id = str(prep.get("requestId") or request_id)
                    write_loop_status(
                        {
                            "state": "account_prepared",
                            "iteration": iteration,
                            "count": args.count,
                            "attempt": attempts,
                            "requestId": request_id,
                            "prep": prep,
                            "updatedAt": utc_now(),
                        }
                    )
                session_path = session_path_for(iteration, request_id)
                session_path = run_janq(
                    args,
                    iteration=iteration,
                    request_id=request_id,
                    session_path=session_path,
                    login_account=request_id if resumable else None,
                )
                summary = summarize_session(session_path)
                terminal = classify_terminal(
                    summary,
                    target_mjchip=args.target_mjchip,
                    bankruptcy_mjchip=args.bankruptcy_mjchip,
                )
                account_update = update_account_result(
                    ACCOUNTS_PATH,
                    request_id,
                    current_mjchip=summary.get("mjchip"),
                    status=terminal["accountStatus"],
                    terminal_reason=terminal["terminalReason"],
                    session_path=str(session_path),
                    completed_hands=summary.get("completedHands"),
                )
                write_loop_status(
                    {
                        "state": "account_finished",
                        "iteration": iteration,
                        "count": args.count,
                        "attempt": attempts,
                        "requestId": request_id,
                        "session": str(session_path),
                        "summary": summary,
                        "terminal": terminal,
                        "account": account_update,
                        "updatedAt": utc_now(),
                    }
                )
                if not terminal["terminal"]:
                    failed += 1
                    print(
                        f"[loop] nonterminal stop for {request_id}: {terminal['terminalReason']}; restarting MJ and resuming",
                        flush=True,
                    )
                    cleanup_bridge_working_files()
                    restart_game(args)
                    continue
                completed += 1
                if completed < args.count:
                    recovery = return_to_login_or_restart(
                        args,
                        reason=f"account_finished:{request_id}",
                        summary=summary,
                    )
                    write_loop_status(
                        {
                            "state": "ready_for_next_account",
                            "iteration": iteration,
                            "count": args.count,
                            "attempt": attempts,
                            "requestId": request_id,
                            "recovery": recovery,
                            "completed": completed,
                            "failed": failed,
                            "updatedAt": utc_now(),
                        }
                    )
            except Exception as exc:
                failed += 1
                recovery = recover_after_loop_error(
                    args,
                    request_id=request_id,
                    session_path=session_path,
                    exc=exc,
                )
                write_loop_status(
                    {
                        "state": "failed_recovered" if args.continue_on_error else "failed",
                        "iteration": iteration,
                        "count": args.count,
                        "attempt": attempts,
                        "requestId": request_id,
                        "error": exception_text(exc),
                        "recovery": recovery,
                        "completed": completed,
                        "failed": failed,
                        "updatedAt": utc_now(),
                    }
                )
                if not args.continue_on_error:
                    return 1

        write_loop_status(
            {
                "state": "complete",
                "count": args.count,
                "completed": completed,
                "failed": failed,
                "updatedAt": utc_now(),
            }
        )
        return 0 if failed == 0 else 1
    finally:
        if not args.keep_game_open:
            stop_copied_mj()


def ensure_runtime() -> None:
    required = [
        GAME_PATH,
        GAME_DIR / "BepInEx" / "core" / "BepInEx.dll",
        GAME_DIR / "BepInEx" / "plugins" / "JanqProbe.dll",
        GAME_DIR / "winhttp.dll",
        GAME_DIR / "doorstop_config.ini",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing runtime files: " + ", ".join(missing))
    for directory in (LOOP_DIR, PREP_DIR, ACCOUNTS_PATH.parent, SESSIONS_DIR, BRIDGE_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def prepare_account(args: argparse.Namespace, *, request_id: str, nickname: str) -> dict[str, Any]:
    if args.fresh_prep:
        archive_prep_state("fresh")

    existing_request = read_json(PREP_REQUEST)
    existing_status = read_json(PREP_STATUS)
    if existing_request and existing_status.get("active"):
        request_id = str(existing_request["id"])
        print(f"[loop] resuming active account prep request {request_id}", flush=True)
    elif (
        existing_status.get("accountCaptured") is True
        and existing_status.get("stage") == "failed"
        and existing_status.get("requestId")
    ):
        request_id = str(existing_status["requestId"])
        nickname = str(existing_status.get("nickname") or nickname)
        atomic_write(PREP_REQUEST, prep_request_payload(args, request_id, nickname))
        existing_status["active"] = True
        existing_status["stage"] = "resume_after_account_capture"
        existing_status["error"] = None
        existing_status["updatedAt"] = utc_now()
        atomic_write(PREP_STATUS, existing_status)
        print(f"[loop] reactivated failed account prep checkpoint {request_id}", flush=True)
    else:
        atomic_write(PREP_REQUEST, prep_request_payload(args, request_id, nickname))
        print(f"[loop] submitted account prep request {request_id}", flush=True)

    deadline = time.monotonic() + args.prep_timeout_seconds
    last_signature: tuple[Any, ...] | None = None
    last_progress_at = time.monotonic()
    restarts = 0
    while time.monotonic() < deadline:
        status = read_json(PREP_STATUS)
        if status.get("requestId") != request_id:
            time.sleep(1)
            continue
        signature = prep_signature(status)
        if signature != last_signature:
            print(json.dumps({"prep": status_summary(status)}, ensure_ascii=False), flush=True)
            last_signature = signature
            last_progress_at = time.monotonic()
        stage = str(status.get("stage") or "")
        if stage in COMPLETE_PREP_STAGES and not status.get("active"):
            return status
        if stage == "failed":
            raise RuntimeError("account prep failed: " + json.dumps(status, ensure_ascii=False))
        if stage.startswith(LOADING_STAGES):
            stall_limit = args.prep_loading_stall_seconds
        elif stage.startswith(LOGIN_WAIT_STAGES) and status.get("error"):
            stall_limit = min(args.prep_generic_stall_seconds, 120.0)
        else:
            stall_limit = args.prep_generic_stall_seconds
        if time.monotonic() - last_progress_at > stall_limit:
            restarts += 1
            print(
                f"[loop] account prep stalled at {stage or 'unknown'} for {stall_limit:.0f}s; restarting MJ and resuming request {request_id}",
                flush=True,
            )
            restart_game(args)
            last_progress_at = time.monotonic()
            last_signature = None
            write_loop_status(
                {
                    "state": "account_prep_restarted",
                    "requestId": request_id,
                    "stage": stage,
                    "restarts": restarts,
                    "updatedAt": utc_now(),
                }
            )
        time.sleep(1)
    raise TimeoutError(f"account prep timed out after {args.prep_timeout_seconds}s")


def run_janq(
    args: argparse.Namespace,
    *,
    iteration: int,
    request_id: str,
    session_path: Path,
    login_account: str | None = None,
) -> Path:
    command = [
        sys.executable,
        "-m",
        "janq_lab.automation.bot",
        "--config",
        str(ROOT / "automation.example.yaml"),
        "--mode",
        "plugin_live",
        "--events-path",
        str(EVENTS_PATH),
        "--bridge-dir",
        str(BRIDGE_DIR),
        "--session-log-path",
        str(session_path),
        "--strategy",
        args.strategy,
        "--target-mjchip",
        str(args.target_mjchip),
        "--bankruptcy-mjchip",
        str(args.bankruptcy_mjchip),
        "--forced-bet",
        str(args.bet),
        "--max-hands",
        str(args.bot_max_hands),
        "--max-runtime-seconds",
        str(args.bot_max_runtime_seconds),
    ]
    if login_account:
        command.extend(
            [
                "--login-account",
                login_account,
                "--account-store-path",
                str(ACCOUNTS_PATH),
            ]
        )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    print(f"[loop] starting JanQ for {request_id}; BET={args.bet}; session={session_path}", flush=True)
    process = subprocess.Popen(command, cwd=str(ROOT), env=env, text=True)
    last_report: tuple[Any, ...] | None = None
    while process.poll() is None:
        report = latest_session_report(session_path)
        if report and report != last_report:
            print(json.dumps({"bot": report}, ensure_ascii=False), flush=True)
            last_report = report
        time.sleep(5)
    if process.returncode != 0:
        raise RuntimeError(f"JanQ bot exited with code {process.returncode}")
    return session_path


def prep_request_payload(args: argparse.Namespace, request_id: str, nickname: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": request_id, "nickname": nickname}
    if args.prep_max_stories is not None:
        payload["maxStories"] = args.prep_max_stories
    return payload


def session_path_for(iteration: int, request_id: str) -> Path:
    return SESSIONS_DIR / f"loop_{datetime.now():%Y%m%d_%H%M%S}_{iteration:03d}_{sanitize(request_id)}.jsonl"


def return_to_login_or_restart(
    args: argparse.Namespace,
    *,
    reason: str,
    summary: dict[str, Any] | None,
) -> dict[str, Any]:
    cleanup_bridge_working_files()
    if not should_attempt_exit_to_login(summary):
        restart_game(args)
        return {
            "method": "restart_game",
            "reason": reason,
            "exitSkipped": "unsafe_or_unknown_phase",
            "phase": summary.get("phase") if isinstance(summary, dict) else None,
        }

    try:
        result = send_bridge_command(
            "exit_to_login",
            timeout_seconds=args.exit_timeout_seconds,
            poll_seconds=0.5,
        )
        return {
            "method": "exit_to_login",
            "reason": reason,
            "bridgeResult": public_bridge_result(result),
        }
    except Exception as exc:
        print(
            f"[loop] exit_to_login failed quickly; restarting MJ for next account: {exception_text(exc)}",
            flush=True,
        )
        cleanup_bridge_working_files()
        restart_game(args)
        return {
            "method": "restart_game",
            "reason": reason,
            "exitError": exception_text(exc),
            "phase": summary.get("phase") if isinstance(summary, dict) else None,
        }


def recover_after_loop_error(
    args: argparse.Namespace,
    *,
    request_id: str,
    session_path: Path | None,
    exc: BaseException,
) -> dict[str, Any]:
    account_recovery = mark_account_after_loop_error(
        ACCOUNTS_PATH,
        request_id=request_id,
        session_path=session_path,
        exc=exc,
        target_mjchip=args.target_mjchip,
        bankruptcy_mjchip=args.bankruptcy_mjchip,
    )
    cleanup_bridge_working_files()
    restart_game(args)
    return {
        "method": "restart_game",
        "reason": "loop_exception",
        "error": exception_text(exc),
        "account": account_recovery,
    }


def mark_account_after_loop_error(
    accounts_path: Path,
    *,
    request_id: str,
    session_path: Path | None,
    exc: BaseException,
    target_mjchip: int,
    bankruptcy_mjchip: int,
) -> dict[str, Any]:
    summary = summarize_session(session_path) if session_path is not None and session_path.exists() else {}
    terminal = classify_terminal(
        summary,
        target_mjchip=target_mjchip,
        bankruptcy_mjchip=bankruptcy_mjchip,
    )
    if terminal["terminal"]:
        status = terminal["accountStatus"]
        terminal_reason = terminal["terminalReason"]
    else:
        terminal_reason = "loop_exception:" + exception_text(exc)
        status = "stopped_" + sanitize(terminal_reason)

    try:
        update = update_account_result(
            accounts_path,
            request_id,
            current_mjchip=summary.get("mjchip"),
            status=status,
            terminal_reason=terminal_reason,
            session_path=str(session_path) if session_path is not None else None,
            completed_hands=summary.get("completedHands"),
        )
    except Exception as update_exc:
        return {
            "updated": False,
            "error": exception_text(update_exc),
            "status": status,
            "terminalReason": terminal_reason,
            "summary": summary,
        }
    return {
        "updated": True,
        "status": status,
        "terminalReason": terminal_reason,
        "summary": summary,
        "account": update,
    }


def should_attempt_exit_to_login(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    return summary.get("phase") in SAFE_EXIT_PHASES


def start_game(args: argparse.Namespace) -> None:
    existing_pids = copied_mj_pids()
    if existing_pids:
        hide_or_minimize_game_windows(
            existing_pids,
            hide=window_hide_mode(args),
            width=args.game_width,
            height=args.game_height,
        )
        return
    if BEPINEX_LOG.exists():
        try:
            BEPINEX_LOG.unlink()
        except OSError:
            pass
    command = [
        str(GAME_PATH),
        "-screen-fullscreen",
        "0",
        "-screen-width",
        str(args.game_width),
        "-screen-height",
        str(args.game_height),
        "-force-d3d11",
        "-force-gfx-direct",
    ]
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0 if args.hidden_game else (1 if args.show_game else 6)
    process = subprocess.Popen(command, cwd=str(GAME_DIR), startupinfo=startupinfo)
    hide_or_minimize_game_windows(
        [process.pid],
        hide=window_hide_mode(args),
        width=args.game_width,
        height=args.game_height,
    )


def restart_game(args: argparse.Namespace) -> None:
    cleanup_bridge_working_files()
    stop_copied_mj()
    time.sleep(3)
    start_game(args)
    wait_probe_loaded()
    hide_or_minimize_game_windows(
        copied_mj_pids(),
        hide=window_hide_mode(args),
        width=args.game_width,
        height=args.game_height,
    )


def cleanup_bridge_working_files(bridge_dir: Path = BRIDGE_DIR) -> None:
    for directory in (bridge_dir / "commands", bridge_dir / "results"):
        if not directory.exists():
            continue
        for pattern in ("*.json", "*.working", "*.tmp", ".*.tmp"):
            for path in directory.glob(pattern):
                try:
                    path.unlink()
                except OSError:
                    pass


def stop_copied_mj() -> None:
    game_path = json.dumps(str(GAME_PATH))
    script = (
        f"$game=[System.IO.Path]::GetFullPath({game_path});"
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'MJ.exe' -and [string]::Equals($_.ExecutablePath,$game,[System.StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=False, text=True)


def copied_mj_pids() -> list[int]:
    game_path = json.dumps(str(GAME_PATH))
    script = (
        f"$game=[System.IO.Path]::GetFullPath({game_path});"
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'MJ.exe' -and [string]::Equals($_.ExecutablePath,$game,[System.StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { $_.ProcessId }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
    )
    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            pass
    return pids


def hide_or_minimize_game_windows(
    pids: list[int],
    *,
    hide: bool | None,
    width: int = 320,
    height: int = 180,
) -> None:
    if not pids:
        return
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return

    target_pids = {int(pid) for pid in pids}
    enum_windows = user32.EnumWindows
    enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    get_window_thread_process_id = user32.GetWindowThreadProcessId
    is_window_visible = user32.IsWindowVisible
    show_window = user32.ShowWindow
    move_window = user32.MoveWindow
    SW_HIDE = 0
    SW_MINIMIZE = 6

    def apply_once() -> None:
        def callback(hwnd: int, _: int) -> bool:
            pid = ctypes.c_ulong()
            get_window_thread_process_id(hwnd, ctypes.byref(pid))
            if pid.value in target_pids:
                move_window(hwnd, 0, 0, max(1, int(width)), max(1, int(height)), True)
                if hide is not None and is_window_visible(hwnd):
                    show_window(hwnd, SW_HIDE if hide else SW_MINIMIZE)
            return True

        enum_windows(enum_proc_type(callback), 0)

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        apply_once()
        time.sleep(0.5)


def window_hide_mode(args: argparse.Namespace) -> bool | None:
    if args.hidden_game:
        return True
    if args.show_game:
        return None
    return False


def wait_probe_loaded(timeout_seconds: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not copied_mj_pids():
            raise RuntimeError("MJ.exe exited before JanqProbe loaded")
        if BEPINEX_LOG.exists():
            text = tail_text(BEPINEX_LOG, max_lines=120)
            if "JanQ Probe loaded" in text:
                return
        time.sleep(1)
    raise TimeoutError("timed out waiting for JanqProbe load marker")


def send_bridge_command(
    kind: str,
    *,
    timeout_seconds: float,
    bridge_dir: Path = BRIDGE_DIR,
    poll_seconds: float = 1.0,
) -> dict[str, Any]:
    commands_dir = bridge_dir / "commands"
    results_dir = bridge_dir / "results"
    commands_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    command_id = uuid.uuid4().hex
    command_path = commands_dir / f"{command_id}.json"
    result_path = results_dir / f"{command_id}.json"
    temp_path = commands_dir / f".{command_id}.tmp"
    temp_path.write_text(
        json.dumps({"id": command_id, "kind": kind, "createdAt": utc_now()}, ensure_ascii=True, separators=(",", ":")),
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
        "normalCompletedHands": state_payload.get("normal_completed_hands") if isinstance(state_payload, dict) else None,
        "phase": state_payload.get("phase") if isinstance(state_payload, dict) else None,
        "pauseReason": pause_payload.get("reason") if isinstance(pause_payload, dict) else None,
        "hasSummary": latest_summary is not None,
    }


def classify_terminal(summary: dict[str, Any], *, target_mjchip: int, bankruptcy_mjchip: int) -> dict[str, Any]:
    mjchip = summary.get("mjchip")
    pause_reason = summary.get("pauseReason")
    if isinstance(mjchip, int) and mjchip >= target_mjchip:
        return {"terminal": True, "terminalReason": "target_mjchip", "accountStatus": "target_reached"}
    if isinstance(mjchip, int) and mjchip <= bankruptcy_mjchip:
        return {"terminal": True, "terminalReason": "bankruptcy_mjchip", "accountStatus": "bankrupt"}
    reason = pause_reason or "nonterminal_stop"
    return {"terminal": False, "terminalReason": reason, "accountStatus": "stopped_" + sanitize(str(reason))}


def latest_session_report(path: Path) -> tuple[Any, ...] | None:
    rows = read_jsonl(path)
    latest_state = last_of_type(rows, "bot_state")
    latest_pause = last_of_type(rows, "bot_pause")
    if latest_pause and isinstance(latest_pause.get("payload"), dict):
        return ("pause", latest_pause["payload"].get("reason"))
    if latest_state and isinstance(latest_state.get("payload"), dict):
        payload = latest_state["payload"]
        currency = payload.get("currency") if isinstance(payload.get("currency"), dict) else {}
        return (
            payload.get("phase"),
            payload.get("mode"),
            payload.get("completed_hands"),
            currency.get("mjchip"),
            payload.get("last_event_type"),
        )
    return None


def prep_signature(status: dict[str, Any]) -> tuple[Any, ...]:
    return (
        status.get("stage"),
        status.get("scene"),
        status.get("sequence"),
        status.get("currentChapterId"),
        len(status.get("completedStories") or []),
        status.get("currentMjchip"),
        status.get("error"),
    )


def status_summary(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": status.get("stage"),
        "scene": status.get("scene"),
        "sequence": status.get("sequence"),
        "chapter": status.get("currentChapterId"),
        "completedStories": len(status.get("completedStories") or []),
        "mjchip": status.get("currentMjchip"),
        "accountCaptured": status.get("accountCaptured"),
        "active": status.get("active"),
        "error": status.get("error"),
    }


def write_loop_status(payload: dict[str, Any]) -> None:
    atomic_write(LOOP_STATUS, payload)
    print(json.dumps({"loop": payload}, ensure_ascii=False), flush=True)


def public_bridge_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": result.get("id"),
        "kind": result.get("kind"),
        "success": result.get("success"),
        "error": result.get("error"),
    }


def exception_text(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def archive_prep_state(reason: str) -> None:
    archive_dir = PREP_DIR / "archived_loop" / datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in (PREP_REQUEST, PREP_STATUS):
        if path.exists():
            path.replace(archive_dir / f"{path.name}.{reason}")


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    for attempt in range(8):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            if attempt == 7:
                raise
            time.sleep(0.05)
    return {}


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


def tail_text(path: Path, *, max_lines: int) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[-max_lines:])
    except FileNotFoundError:
        return ""


def run_nickname(prefix: str, iteration: int, count: int) -> str:
    base = (prefix or "JanQ").strip()
    if count <= 1:
        return base[:14]
    suffix = f"{iteration:02d}"
    return (base[: max(1, 14 - len(suffix))] + suffix)[:14]


def sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:64] or "account"


def find_resumable_account(args: argparse.Namespace) -> dict[str, Any] | None:
    try:
        raw = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return None
    rows = raw.get("accounts") if isinstance(raw, dict) and isinstance(raw.get("accounts"), list) else raw
    if not isinstance(rows, list):
        return None
    candidates = [row for row in rows if isinstance(row, dict) and is_resumable_account(row, args)]
    if not candidates:
        return None
    candidates.sort(key=lambda row: str(row.get("lastRunAt") or row.get("createdAt") or ""), reverse=True)
    return candidates[0]


def find_active_prep_request() -> dict[str, Any] | None:
    status = read_json(PREP_STATUS)
    request = read_json(PREP_REQUEST)
    if status.get("active") and (status.get("requestId") or request.get("id")):
        return {
            "requestId": status.get("requestId") or request.get("id"),
            "id": request.get("id"),
            "nickname": status.get("nickname") or request.get("nickname"),
        }
    return None


def is_resumable_account(row: dict[str, Any], args: argparse.Namespace) -> bool:
    request_id = row.get("requestId")
    status = row.get("status")
    mjchip = row.get("currentMjchip", row.get("finalMjchip"))
    if not isinstance(request_id, str) or not request_id.strip():
        return False
    if not isinstance(status, str) or not status.startswith("stopped_"):
        return False
    if not isinstance(mjchip, int):
        return True
    return args.bankruptcy_mjchip < mjchip < args.target_mjchip


def public_account_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    login_id = row.get("loginId")
    return {
        "requestId": row.get("requestId"),
        "loginId": mask_login_id(login_id) if isinstance(login_id, str) else None,
        "nickname": row.get("nickname"),
        "currentMjchip": row.get("currentMjchip", row.get("finalMjchip")),
        "status": row.get("status"),
        "lastTerminalReason": row.get("lastTerminalReason"),
    }


def mask_login_id(login_id: str) -> str:
    return "***" if len(login_id) <= 3 else f"{login_id[:3]}***"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
