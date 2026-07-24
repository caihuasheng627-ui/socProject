"""SQLite persistence for public multi-agent conversation state."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from pydantic import BaseModel

from .base import model_dump
from .schemas import (
    BearOpinion,
    BullOpinion,
    JudgeDecision,
    MarketSnapshot,
    UserProfile,
)


AGENT_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    id                TEXT PRIMARY KEY,
    skin_id           TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    user_profile_json TEXT NOT NULL,
    snapshot_json     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    role            TEXT NOT NULL,
    agent_name      TEXT,
    content         TEXT NOT NULL,
    structured_json TEXT,
    round_no        INTEGER,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_session
ON agent_messages(session_id, id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    round_no    INTEGER,
    model       TEXT,
    status      TEXT NOT NULL,
    output_json TEXT,
    latency_ms  INTEGER,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session
ON agent_runs(session_id, id);
"""


ConnectionFactory = Callable[[], sqlite3.Connection]


def _default_connection_factory() -> sqlite3.Connection:
    from database import get_connection

    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate(schema: type[BaseModel], value: Any) -> BaseModel:
    if hasattr(schema, "model_validate"):
        return schema.model_validate(value)  # type: ignore[attr-defined]
    return schema.parse_obj(value)


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    skin_id: str
    status: str
    user_profile: UserProfile
    snapshot: MarketSnapshot
    bull_history: tuple[BullOpinion, ...]
    bear_history: tuple[BearOpinion, ...]
    judge_history: tuple[JudgeDecision, ...]
    messages: tuple[dict[str, Any], ...]
    created_at: str
    updated_at: str


class SessionNotFoundError(LookupError):
    pass


class SessionStore:
    """Persist only public messages and validated outputs, never hidden CoT."""

    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._connection_factory = connection_factory or _default_connection_factory
        with self._connection() as connection:
            connection.executescript(AGENT_SESSION_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection_factory()
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create(
        self,
        *,
        skin_id: str,
        user_profile: UserProfile,
        snapshot: MarketSnapshot,
    ) -> str:
        session_id = str(uuid.uuid4())
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO agent_sessions
                   (id, skin_id, status, user_profile_json, snapshot_json,
                    created_at, updated_at)
                   VALUES (?, ?, 'active', ?, ?, ?, ?)""",
                (
                    session_id,
                    skin_id,
                    json.dumps(model_dump(user_profile), ensure_ascii=False),
                    json.dumps(model_dump(snapshot), ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return session_id

    def append_user_message(
        self,
        session_id: str,
        content: str,
        target: str,
        round_no: int | None = None,
    ) -> None:
        self._append_message(
            session_id=session_id,
            role="user",
            agent_name=target,
            content=content,
            structured=None,
            round_no=round_no,
        )

    def update_user_profile(self, session_id: str, profile: UserProfile) -> None:
        timestamp = _now()
        with self._connection() as connection:
            self._require_session(connection, session_id)
            connection.execute(
                "UPDATE agent_sessions SET user_profile_json=?, updated_at=? WHERE id=?",
                (
                    json.dumps(model_dump(profile), ensure_ascii=False),
                    timestamp,
                    session_id,
                ),
            )

    def append_agent_result(
        self,
        session_id: str,
        *,
        agent_name: str,
        content: str,
        result: BaseModel,
        round_no: int | None,
        model: str | None,
    ) -> None:
        structured = model_dump(result)
        self._append_message(
            session_id=session_id,
            role="agent",
            agent_name=agent_name,
            content=content,
            structured=structured,
            round_no=round_no,
        )
        with self._connection() as connection:
            self._require_session(connection, session_id)
            connection.execute(
                """INSERT INTO agent_runs
                   (session_id, agent_name, round_no, model, status,
                    output_json, latency_ms, created_at)
                   VALUES (?, ?, ?, ?, 'completed', ?, NULL, ?)""",
                (
                    session_id,
                    agent_name,
                    round_no,
                    model,
                    json.dumps(structured, ensure_ascii=False),
                    _now(),
                ),
            )

    def _append_message(
        self,
        *,
        session_id: str,
        role: str,
        agent_name: str | None,
        content: str,
        structured: dict[str, Any] | None,
        round_no: int | None,
    ) -> None:
        if not content.strip():
            raise ValueError("message content must not be empty")
        timestamp = _now()
        with self._connection() as connection:
            self._require_session(connection, session_id)
            connection.execute(
                """INSERT INTO agent_messages
                   (session_id, role, agent_name, content, structured_json,
                    round_no, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    role,
                    agent_name,
                    content,
                    json.dumps(structured, ensure_ascii=False) if structured is not None else None,
                    round_no,
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE agent_sessions SET updated_at=? WHERE id=?",
                (timestamp, session_id),
            )

    def get(self, session_id: str) -> SessionRecord:
        with self._connection() as connection:
            session = self._require_session(connection, session_id)
            rows = connection.execute(
                "SELECT * FROM agent_messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()

        profile = _validate(UserProfile, json.loads(session["user_profile_json"]))
        snapshot = _validate(MarketSnapshot, json.loads(session["snapshot_json"]))
        messages: list[dict[str, Any]] = []
        bulls: list[BullOpinion] = []
        bears: list[BearOpinion] = []
        judges: list[JudgeDecision] = []
        for row in rows:
            structured = json.loads(row["structured_json"]) if row["structured_json"] else None
            messages.append(
                {
                    "id": row["id"],
                    "role": row["role"],
                    "agentName": row["agent_name"],
                    "content": row["content"],
                    "structured": structured,
                    "round": row["round_no"],
                    "createdAt": row["created_at"],
                }
            )
            if structured and row["agent_name"] == "bull":
                bulls.append(_validate(BullOpinion, structured))
            elif structured and row["agent_name"] == "bear":
                bears.append(_validate(BearOpinion, structured))
            elif structured and row["agent_name"] == "judge":
                judges.append(_validate(JudgeDecision, structured))

        return SessionRecord(
            session_id=session["id"],
            skin_id=session["skin_id"],
            status=session["status"],
            user_profile=profile,  # type: ignore[arg-type]
            snapshot=snapshot,  # type: ignore[arg-type]
            bull_history=tuple(bulls),
            bear_history=tuple(bears),
            judge_history=tuple(judges),
            messages=tuple(messages),
            created_at=session["created_at"],
            updated_at=session["updated_at"],
        )

    @staticmethod
    def _require_session(
        connection: sqlite3.Connection, session_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM agent_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            raise SessionNotFoundError(f"agent session not found: {session_id}")
        return row
