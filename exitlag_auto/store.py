"""SQLite-backed account pool with trial expiry tracking.

The whole point of the project is rotating 3-day trials, so accounts are state,
not a log line. This keeps track of when each trial dies and which account is
currently in use.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

LOG = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    alias_id TEXT,
    password TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    created_at TEXT NOT NULL,
    trial_expires_at TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    last_used_at TEXT,
    notes TEXT
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


@dataclass
class Account:
    id: int
    email: str
    alias_id: str
    password: str
    first_name: str
    last_name: str
    created_at: str
    trial_expires_at: str
    verified: bool
    status: str
    last_used_at: Optional[str]

    def expires_at(self) -> datetime:
        return datetime.fromisoformat(self.trial_expires_at)

    def hours_left(self) -> float:
        return (self.expires_at() - _now()).total_seconds() / 3600.0


class AccountStore:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _row_to_account(self, row) -> Account:
        return Account(
            id=row["id"],
            email=row["email"],
            alias_id=row["alias_id"] or "",
            password=row["password"],
            first_name=row["first_name"] or "",
            last_name=row["last_name"] or "",
            created_at=row["created_at"],
            trial_expires_at=row["trial_expires_at"],
            verified=bool(row["verified"]),
            status=row["status"],
            last_used_at=row["last_used_at"],
        )

    def add(self, result, trial_days: int) -> Account:
        created = _now()
        expires = created + timedelta(days=trial_days)
        self.conn.execute(
            """INSERT INTO accounts
               (email, alias_id, password, first_name, last_name,
                created_at, trial_expires_at, verified, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                result.email,
                result.alias_id,
                result.password,
                result.first_name,
                result.last_name,
                _iso(created),
                _iso(expires),
                1 if result.verified else 0,
            ),
        )
        self.conn.commit()
        LOG.info("Stored %s (trial expires %s)", result.email, _iso(expires))
        return self.get_by_email(result.email)

    def get_by_email(self, email: str) -> Optional[Account]:
        row = self.conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        return self._row_to_account(row) if row else None

    def get_valid(self, renew_before_hours: int = 2) -> Optional[Account]:
        """Return the active account with the most remaining trial time.

        Accounts within `renew_before_hours` of expiry are treated as unusable so
        we replace them before they die mid-session.
        """
        cutoff = _iso(_now() + timedelta(hours=renew_before_hours))
        row = self.conn.execute(
            """SELECT * FROM accounts
               WHERE status = 'active' AND trial_expires_at > ?
               ORDER BY trial_expires_at DESC LIMIT 1""",
            (cutoff,),
        ).fetchone()
        return self._row_to_account(row) if row else None

    def mark_used(self, email: str):
        self.conn.execute(
            "UPDATE accounts SET last_used_at = ? WHERE email = ?", (_iso(_now()), email)
        )
        self.conn.commit()

    def set_status(self, email: str, status: str):
        self.conn.execute("UPDATE accounts SET status = ? WHERE email = ?", (status, email))
        self.conn.commit()
        LOG.info("Marked %s as %s", email, status)

    def list_all(self) -> List[Account]:
        rows = self.conn.execute(
            "SELECT * FROM accounts ORDER BY datetime(created_at) DESC"
        ).fetchall()
        return [self._row_to_account(r) for r in rows]

    def prune(self) -> int:
        """Flag active accounts whose trial has already run out."""
        cur = self.conn.execute(
            "UPDATE accounts SET status = 'expired' "
            "WHERE status = 'active' AND trial_expires_at <= ?",
            (_iso(_now()),),
        )
        self.conn.commit()
        if cur.rowcount:
            LOG.info("Marked %s account(s) as expired.", cur.rowcount)
        return cur.rowcount
