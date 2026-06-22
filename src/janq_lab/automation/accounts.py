"""Account store helpers for live JanQ automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AutomationAccount:
    request_id: str | None
    login_id: str
    password: str
    nickname: str | None = None
    final_mjchip: int | None = None
    status: str | None = None

    @property
    def masked_login_id(self) -> str:
        if len(self.login_id) <= 3:
            return "***"
        return f"{self.login_id[:3]}***"

    def public_payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "login_id": self.masked_login_id,
            "nickname": self.nickname,
            "final_mjchip": self.final_mjchip,
            "status": self.status,
        }


def select_account(path: str | Path, selector: str) -> AutomationAccount:
    text = selector.strip()
    if not text:
        raise ValueError("login_account selector cannot be empty")

    accounts = load_accounts(path)
    matches = [
        account
        for account in accounts
        if _matches_selector(account, text)
    ]
    if not matches:
        raise ValueError("account selector did not match any account")
    if len(matches) > 1:
        raise ValueError("account selector matched multiple accounts")
    return matches[0]


def load_accounts(path: str | Path) -> list[AutomationAccount]:
    account_path = Path(path)
    raw, rows = _load_account_rows(account_path)
    del raw
    if not isinstance(rows, list):
        raise ValueError("account store must be a JSON array or object with accounts")
    accounts = [_account_from_row(row) for row in rows]
    if not accounts:
        raise ValueError("account store is empty")
    return accounts


def update_account_result(
    path: str | Path,
    selector: str,
    *,
    current_mjchip: int | None,
    status: str,
    terminal_reason: str | None = None,
    session_path: str | None = None,
    completed_hands: int | None = None,
    resume_failure_count: int | None = None,
    resume_failure_limit: int | None = None,
    resume_failure_reason: str | None = None,
    interrupted_at: str | None = None,
) -> dict[str, Any]:
    account_path = Path(path)
    raw, rows = _load_account_rows(account_path)
    if not isinstance(rows, list):
        raise ValueError("account store must be a JSON array or object with accounts")

    matches = [
        row
        for row in rows
        if isinstance(row, dict) and _matches_row_selector(row, selector)
    ]
    if not matches:
        raise ValueError("account selector did not match any account")
    if len(matches) > 1:
        raise ValueError("account selector matched multiple accounts")

    row = matches[0]
    if current_mjchip is not None:
        row["currentMjchip"] = current_mjchip
        row["finalMjchip"] = current_mjchip
    row["status"] = status
    row["lastRunAt"] = datetime.now(timezone.utc).isoformat()
    if terminal_reason is not None:
        row["lastTerminalReason"] = terminal_reason
    if session_path is not None:
        row["lastSession"] = session_path
    if completed_hands is not None:
        row["lastCompletedHands"] = completed_hands
    if resume_failure_count is None:
        row.pop("resumeFailureCount", None)
        row.pop("resumeFailureLimit", None)
        row.pop("resumeFailureReason", None)
    else:
        row["resumeFailureCount"] = resume_failure_count
        if resume_failure_limit is not None:
            row["resumeFailureLimit"] = resume_failure_limit
        if resume_failure_reason is not None:
            row["resumeFailureReason"] = resume_failure_reason
    if interrupted_at is None:
        if not str(status).startswith("interrupted_"):
            row.pop("interruptedAt", None)
    else:
        row["interruptedAt"] = interrupted_at

    _atomic_write_json(account_path, raw)
    return {
        "request_id": _optional_string(row.get("requestId")),
        "login_id": _mask_login_id(_optional_string(row.get("loginId"))),
        "nickname": _optional_string(row.get("nickname")),
        "current_mjchip": current_mjchip,
        "status": status,
        "terminal_reason": terminal_reason,
        "session_path": session_path,
        "completed_hands": completed_hands,
        "resume_failure_count": resume_failure_count,
        "resume_failure_limit": resume_failure_limit,
        "resume_failure_reason": resume_failure_reason,
        "interrupted_at": interrupted_at,
    }


def _account_from_row(row: Any) -> AutomationAccount:
    if not isinstance(row, dict):
        raise ValueError("account entry must be an object")
    login_id = _required_string(row, "loginId")
    password = _required_string(row, "password")
    return AutomationAccount(
        request_id=_optional_string(row.get("requestId")),
        login_id=login_id,
        password=password,
        nickname=_optional_string(row.get("nickname")),
        final_mjchip=row.get("finalMjchip") if isinstance(row.get("finalMjchip"), int) else None,
        status=_optional_string(row.get("status")),
    )


def _matches_selector(account: AutomationAccount, selector: str) -> bool:
    normalized = selector.casefold()
    values = (account.request_id, account.login_id, account.nickname)
    return any(value is not None and value.casefold() == normalized for value in values)


def _matches_row_selector(row: dict[str, Any], selector: str) -> bool:
    normalized = selector.casefold()
    values = (row.get("requestId"), row.get("loginId"), row.get("nickname"))
    return any(isinstance(value, str) and value.casefold() == normalized for value in values)


def _load_account_rows(path: Path) -> tuple[Any, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = raw.get("accounts", []) if isinstance(raw, dict) else raw
    return raw, rows


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def _mask_login_id(login_id: str | None) -> str | None:
    if login_id is None:
        return None
    if len(login_id) <= 3:
        return "***"
    return f"{login_id[:3]}***"


def _required_string(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"account entry missing {key}")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
