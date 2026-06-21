"""Submit and monitor one background account-preparation request."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid


def _default_root() -> Path:
    env_root = os.environ.get("JANQ_WORKSPACE")
    if env_root and env_root.strip():
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def configure_root(root: str | Path | None = None, *, update_environment: bool = True) -> Path:
    global ROOT, RUNTIME, REQUEST_PATH, STATUS_PATH, ACCOUNTS_PATH

    ROOT = (Path(root).expanduser() if root is not None else _default_root()).resolve()
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    RUNTIME = ROOT / "_runtime" / "account_prep"
    REQUEST_PATH = RUNTIME / "request.json"
    STATUS_PATH = RUNTIME / "status.json"
    ACCOUNTS_PATH = ROOT / "_runtime" / "accounts" / "accounts.json"
    if update_environment:
        os.environ["JANQ_WORKSPACE"] = str(ROOT)
    return ROOT


configure_root(update_environment=False)


def _read_json(path: Path) -> dict:
    for attempt in range(8):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return {}
        except (PermissionError, OSError, json.JSONDecodeError):
            if attempt == 7:
                raise
            time.sleep(0.05)
    return {}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nickname")
    parser.add_argument("--request-id")
    parser.add_argument("--max-stories", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--root", default=None, help="JanQ workspace root; defaults to this script's repository.")
    args = parser.parse_args()
    configure_root(args.root)

    existing_request = _read_json(REQUEST_PATH)
    existing_status = _read_json(STATUS_PATH)
    if existing_request and existing_status.get("active"):
        request_id = str(existing_request["id"])
    else:
        request_id = args.request_id or uuid.uuid4().hex
        _atomic_write(
            REQUEST_PATH,
            {"id": request_id, "nickname": args.nickname, "maxStories": args.max_stories},
        )

    deadline = time.monotonic() + args.timeout_seconds
    last_signature: tuple | None = None
    while time.monotonic() < deadline:
        status = _read_json(STATUS_PATH)
        if status.get("requestId") != request_id:
            time.sleep(1)
            continue

        signature = (
            status.get("stage"),
            status.get("scene"),
            status.get("sequence"),
            status.get("currentChapterId"),
            len(status.get("completedStories") or []),
            status.get("currentMjchip"),
        )
        if signature != last_signature:
            print(
                json.dumps(
                    {
                        "stage": signature[0],
                        "scene": signature[1],
                        "sequence": signature[2],
                        "chapter": signature[3],
                        "completedStories": signature[4],
                        "mjchip": signature[5],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            last_signature = signature

        stage = status.get("stage")
        if stage in {"complete", "complete_accessible_stories"}:
            print(
                json.dumps(
                    {
                        "result": stage,
                        "mjchip": status.get("currentMjchip"),
                        "completedStoryIds": status.get("completedStories") or [],
                        "inaccessibleChapterIds": status.get("inaccessibleChapters") or [],
                        "accountsPath": str(ACCOUNTS_PATH),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0
        if stage == "failed":
            print(json.dumps(status, ensure_ascii=False), flush=True)
            return 1
        time.sleep(1)

    print(f"account preparation timed out after {args.timeout_seconds}s", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
