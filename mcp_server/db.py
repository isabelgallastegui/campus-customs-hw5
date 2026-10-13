"""SQLite helpers for the Campus Customs MCP server.

All tools use the working copy (data/campus_customs_new.db).
The original data/campus_customs.db is never opened here.

Tests can point the server at a throwaway copy with CAMPUS_CUSTOMS_DB.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ORIGINAL_DB = DATA_DIR / "campus_customs.db"
DB_PATH = Path(os.getenv("CAMPUS_CUSTOMS_DB", DATA_DIR / "campus_customs_new.db")).resolve()

if DB_PATH == ORIGINAL_DB.resolve():
    raise RuntimeError("Refusing to use the original database. Use data/campus_customs_new.db.")

# Tables the original schema does not have. They hold requests that wait for a
# human (payments, restock orders) and message drafts that are never sent.
# They are created lazily, on the first write, so read-only use of the server
# never changes the database.
SUPPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('invoice', 'rent', 'restock')),
    ref_id INTEGER NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    account TEXT NOT NULL DEFAULT 'checking',
    ticket_id INTEGER,
    reason TEXT,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_approval', 'approved', 'rejected', 'executed')),
    approved_by TEXT,
    decided_at TEXT,
    decision_note TEXT,
    payment_id INTEGER REFERENCES payments(id)
);
-- At most one live request per obligation: blocks duplicate payments.
CREATE UNIQUE INDEX IF NOT EXISTS one_live_request_per_obligation
    ON payment_requests(kind, ref_id) WHERE status IN ('pending_approval', 'approved');

CREATE TABLE IF NOT EXISTS restock_orders (
    id INTEGER PRIMARY KEY,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    sku TEXT NOT NULL,
    size TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_cost REAL NOT NULL,
    total_cost REAL NOT NULL,
    ticket_id INTEGER,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_approval', 'ordered', 'rejected')),
    expected_arrival TEXT
);

CREATE TABLE IF NOT EXISTS customer_message_drafts (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    recipient TEXT NOT NULL,
    body TEXT NOT NULL,
    drafted_by TEXT NOT NULL,
    drafted_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'superseded'))
);

-- A person confirms they actually sent a draft (no email is sent by the system).
-- UNIQUE(draft_id): the same message can never be marked as sent twice.
CREATE TABLE IF NOT EXISTS customer_message_sends (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER NOT NULL UNIQUE REFERENCES customer_message_drafts(id),
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    sent_by TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    customer_reply_needed INTEGER NOT NULL DEFAULT 0
);
"""


def get_connection() -> sqlite3.Connection:
    """Open the working database. Fails instead of creating an empty file if it is missing."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Working database not found: {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=rw", uri=True, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def write_transaction():
    """One all-or-nothing write. If anything fails or the process dies, nothing is saved.

    BEGIN IMMEDIATE takes the write lock up front, so two calls cannot both
    read "not yet paid" and then both pay.
    """
    conn = get_connection()
    try:
        conn.executescript(SUPPORT_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def get_today(conn: sqlite3.Connection) -> str:
    """The shop's current date always comes from desk.date_today."""
    return conn.execute("SELECT date_today FROM desk LIMIT 1").fetchone()["date_today"]


def get_checking_balance(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()
    return row["balance"] if row else 0.0


def committed_amount(conn: sqlite3.Connection, exclude_request_id: int | None = None) -> float:
    """Cash already promised to requests that are pending or approved but not yet paid."""
    if not table_exists(conn, "payment_requests"):
        return 0.0
    row = conn.execute(
        """SELECT COALESCE(SUM(amount), 0) AS total FROM payment_requests
           WHERE status IN ('pending_approval', 'approved') AND id != COALESCE(?, -1)""",
        (exclude_request_id,),
    ).fetchone()
    return round(row["total"], 2)
