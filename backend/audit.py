"""Append-only audit trail in output/audit_trail.json.

Each event is written as soon as it happens. The file is rewritten through a
temp file plus os.replace, so a crash mid-write leaves the previous complete
file in place: completed records are never lost or half-written. A file lock
(fcntl) makes this safe when several processes write at once (e.g. the
backend and a test run), so no event overwrites another.
"""

import fcntl
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import OUTPUT_DIR, secrets_to_redact

AUDIT_PATH = Path(os.getenv("CAMPUS_CUSTOMS_AUDIT", OUTPUT_DIR / "audit_trail.json"))
_lock = threading.Lock()
_MAX_TEXT = 600
_SECRET_KEYS = ("api_key", "apikey", "authorization", "access_token", "bearer", "secret", "password")


def _clean(value: Any) -> Any:
    """Shorten long values and strip anything that looks like a secret."""
    if isinstance(value, dict):
        return {k: "[redacted]" if any(s in k.lower() for s in _SECRET_KEYS) else _clean(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    for secret in secrets_to_redact():
        text = text.replace(secret, "[redacted]")
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "...[truncated]"


def _read_all() -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    try:
        return json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Never overwrite a file we cannot parse; keep it and start a side file.
        AUDIT_PATH.rename(AUDIT_PATH.with_suffix(f".corrupt-{datetime.now():%Y%m%d%H%M%S}.json"))
        return []


def record(run, agent: str, action: str, status: str, *, tool: str | None = None,
           target: str | None = None, detail: Any = None, result: Any = None) -> dict:
    """Append one event. `run` is the TicketRun (or None for events outside a run)."""
    event = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "execution_id": getattr(run, "execution_id", None),
        "context": getattr(run, "context", None),
        "ticket_id": getattr(run, "ticket_id", None),
        "agent": agent,
        "action": action,
        "tool": tool,
        "target": target,
        "detail": _clean(detail),
        "result": _clean(result),
        "status": status,
        "agent_steps": getattr(run, "agent_steps", None),
        "mcp_tool_calls": getattr(run, "mcp_tool_calls", None),
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(AUDIT_PATH.with_suffix(".lock"), "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)          # one writer at a time, across processes
        try:
            events = _read_all()
            events.append(event)
            tmp = AUDIT_PATH.with_name(f".{AUDIT_PATH.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps(events, indent=2), encoding="utf-8")
            os.replace(tmp, AUDIT_PATH)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    return event
