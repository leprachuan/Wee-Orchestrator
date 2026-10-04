"""Durable approval storage primitives, not a public API or execution permit.

Callers must supply server-authenticated ownership and actor identities. This
module persists only action fingerprints, never arguments, credentials or tool
results. Runtime wiring remains disabled until API authorization and action
policy revalidation are implemented.
"""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from autonomy_policy import Action, _text

_DECISIONS = {"approve_once": "approved", "reject": "rejected", "revise": "revision_requested"}


class ApprovalConflict(ValueError):
    """An idempotency key was reused for different work."""


class ApprovalStore:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create with private permissions before SQLite can write any state.
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise ValueError("Approval database must be private to the service user")
        with self._transaction() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported approval schema")
            db.execute('''CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, responsibility TEXT NOT NULL,
                intent_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL,
                status TEXT NOT NULL, decided_by TEXT, decided_at REAL,
                decision TEXT, UNIQUE(owner, intent_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS approval_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT NOT NULL, owner TEXT NOT NULL,
                kind TEXT NOT NULL, actor TEXT, occurred_at REAL NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS approvals_owner ON approvals(owner, created_at)')
            db.execute('CREATE INDEX IF NOT EXISTS events_owner ON approval_events(owner, sequence)')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _now(self):
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise ValueError("Invalid approval clock")
        return now

    @staticmethod
    def _event(db, row, kind, now, actor=None):
        db.execute('INSERT INTO approval_events(approval_id, owner, kind, actor, occurred_at) VALUES(?,?,?,?,?)',
                   (row['id'], row['owner'], kind, actor, now))

    @staticmethod
    def _row(db, approval_id, owner):
        row = db.execute('SELECT * FROM approvals WHERE id=? AND owner=?', (approval_id, owner)).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return dict(row)

    def _expire(self, db, row, now):
        if row['status'] in ('pending', 'approved') and row['expires_at'] <= now:
            db.execute("UPDATE approvals SET status='expired' WHERE id=?", (row['id'],))
            row['status'] = 'expired'
            self._event(db, row, 'expired', now)
        return row

    def create(self, action, *, owner, responsibility, intent_key, ttl_seconds=3600):
        if not isinstance(action, Action):
            raise ValueError("Expected a trusted canonical Action")
        for value in (owner, responsibility, intent_key):
            _text(value)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("Approval expiry must be 1..86400 seconds")
        now = self._now()
        with self._transaction() as db:
            existing = db.execute('SELECT * FROM approvals WHERE owner=? AND intent_key=?', (owner, intent_key)).fetchone()
            if existing:
                row = dict(existing)
                if row['fingerprint'] != action.fingerprint or row['responsibility'] != responsibility:
                    raise ApprovalConflict("Intent already belongs to a different action or responsibility")
                return self._expire(db, row, now)
            approval_id = str(uuid4())
            db.execute('''INSERT INTO approvals
                (id, owner, responsibility, intent_key, fingerprint, created_at, expires_at, status)
                VALUES(?,?,?,?,?,?,?, 'pending')''',
                (approval_id, owner, responsibility, intent_key, action.fingerprint, now, now + ttl_seconds))
            row = self._row(db, approval_id, owner)
            self._event(db, row, 'created', now)
            return row

    def get(self, approval_id, *, owner):
        _text(owner)
        with self._transaction() as db:
            return self._expire(db, self._row(db, approval_id, owner), self._now())

    def resolve(self, approval_id, *, owner, actor, decision, fingerprint):
        """Return (record, won). Only the first pending decision changes state.

        UI submits the fingerprint it reviewed; an old preview cannot authorize
        a different action. Always-allow is deliberately unsupported until the
        bounded rule-save transaction and its authorization are implemented.
        """
        for value in (owner, actor, fingerprint):
            _text(value)
        if decision not in _DECISIONS:
            raise ValueError("Unsupported approval decision")
        now = self._now()
        with self._transaction() as db:
            row = self._expire(db, self._row(db, approval_id, owner), now)
            if row['fingerprint'] != fingerprint:
                raise ApprovalConflict("Reviewed action fingerprint does not match")
            if row['status'] != 'pending':
                return row, False
            db.execute('UPDATE approvals SET status=?, decided_by=?, decided_at=?, decision=? WHERE id=?',
                       (_DECISIONS[decision], actor, now, decision, approval_id))
            row = self._row(db, approval_id, owner)
            self._event(db, row, row['status'], now, actor)
            return row, True

    def cancel(self, approval_id, *, owner, actor):
        _text(owner); _text(actor)
        now = self._now()
        with self._transaction() as db:
            row = self._expire(db, self._row(db, approval_id, owner), now)
            if row['status'] not in ('pending', 'approved'):
                return row, False
            db.execute("UPDATE approvals SET status='cancelled', decided_by=?, decided_at=? WHERE id=?", (actor, now, approval_id))
            row = self._row(db, approval_id, owner)
            self._event(db, row, 'cancelled', now, actor)
            return row, True

    def claim(self, approval_id, action, *, owner, responsibility):
        """Reserve once; caller must also revalidate current policy before use.

        A claim is never automatically replayed, including after a restart. It
        is a storage reservation, not a guarantee an external effect occurred.
        """
        if not isinstance(action, Action):
            raise ValueError("Expected a trusted canonical Action")
        _text(owner); _text(responsibility)
        now = self._now()
        with self._transaction() as db:
            row = self._expire(db, self._row(db, approval_id, owner), now)
            if row['responsibility'] != responsibility:
                raise ApprovalConflict("Approval belongs to a different responsibility")
            if row['status'] != 'approved':
                return False
            if row['fingerprint'] != action.fingerprint:
                db.execute("UPDATE approvals SET status='invalidated' WHERE id=?", (approval_id,))
                self._event(db, row, 'invalidated', now)
                return False
            db.execute("UPDATE approvals SET status='claimed' WHERE id=?", (approval_id,))
            self._event(db, row, 'claimed', now)
            return True

    def events(self, *, owner, after=0, limit=100):
        """Owner-filtered replay cursor; no raw action arguments in event data."""
        _text(owner)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid event cursor or limit")
        with self._transaction() as db:
            return [dict(row) for row in db.execute('''SELECT sequence, approval_id, kind, actor, occurred_at
                FROM approval_events WHERE owner=? AND sequence>? ORDER BY sequence LIMIT ?''', (owner, after, limit))]
