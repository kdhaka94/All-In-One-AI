"""SQLite-backed persistence for chat sessions (the 'previous chats' list)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "db/agent_sessions.db"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    config TEXT,
                    payload TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_session(
        self,
        session_id: str,
        session: dict[str, Any],
        *,
        title: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        payload = json.dumps(
            {"messages": session.get("messages", []), "artifacts": session.get("artifacts", [])},
            default=str,
        )
        with closing(self._connect()) as conn, conn:
            existing = conn.execute(
                "SELECT created_at, title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            final_title = title or (existing["title"] if existing else None) or "New chat"
            conn.execute(
                """
                INSERT INTO sessions (id, title, created_at, updated_at, config, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at,
                    config = excluded.config,
                    payload = excluded.payload
                """,
                (session_id, final_title, created_at, now, json.dumps(config or {}), payload),
            )

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"] or "{}")
        return {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "config": json.loads(row["config"] or "{}"),
            "messages": payload.get("messages", []),
            "artifacts": payload.get("artifacts", []),
        }

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT id, title, updated_at, payload FROM sessions
                ORDER BY updated_at DESC, rowid DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        sessions = []
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            sessions.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "updated_at": row["updated_at"],
                    "message_count": len(payload.get("messages", [])),
                }
            )
        return sessions

    def delete_session(self, session_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
