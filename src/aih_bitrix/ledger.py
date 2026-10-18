"""Ledger SQLite: idempotência por hash do arquivo e cursor do poller.

Nunca guarda nome de arquivo nem dado pessoal: só hash, status, IDs do Bitrix e a origem.
Só `ok` e `not_aih` impedem reprocessar; `needs_review` e `error` podem ser refeitos depois da correção.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BLOCKING = ("ok", "not_aih")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    sha256 TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    contact_id INTEGER,
    item_id INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cursors (
    dialog TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def get(self, sha: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM files WHERE sha256 = ?", (sha,)).fetchone()
        return dict(row) if row else None

    def seen(self, sha: str) -> bool:
        row = self.get(sha)
        return bool(row and row["status"] in _BLOCKING)

    def record(
        self,
        sha: str,
        *,
        status: str,
        source: str = "",
        contact_id: int | None = None,
        item_id: int | None = None,
        detail: str = "",
    ) -> None:
        now = _now()
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT INTO files (sha256, status, source, contact_id, item_id, detail, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    status = excluded.status,
                    source = excluded.source,
                    contact_id = COALESCE(excluded.contact_id, files.contact_id),
                    item_id = COALESCE(excluded.item_id, files.item_id),
                    detail = excluded.detail,
                    updated_at = excluded.updated_at
                """,
                (sha, status, source, contact_id, item_id, detail, now, now),
            )

    def get_cursor(self, dialog: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT last_message_id FROM cursors WHERE dialog = ?", (dialog,)).fetchone()
        return int(row["last_message_id"]) if row else 0

    def set_cursor(self, dialog: str, message_id: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                """
                INSERT INTO cursors (dialog, last_message_id, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(dialog) DO UPDATE SET
                    last_message_id = MAX(cursors.last_message_id, excluded.last_message_id),
                    updated_at = excluded.updated_at
                """,
                (dialog, message_id, _now()),
            )
