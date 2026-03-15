from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    require_non_empty_string,
    require_positive_int,
    require_string_list,
    utc_now,
)

VALID_HANDOFF_STATUSES = {"pending", "claimed", "completed", "blocked"}
VALID_TEST_STATUSES = {"pass", "fail", "not_run", "blocked"}

HANDOFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    rationale TEXT NOT NULL,
    from_role TEXT NOT NULL,
    to_role TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    resolution_note TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL,
    test_status TEXT NOT NULL,
    test_command TEXT NOT NULL,
    failure_summary TEXT NOT NULL,
    top_stack_frame TEXT NOT NULL,
    file_scope_json TEXT NOT NULL,
    function_scope_json TEXT NOT NULL,
    acceptance_criteria_json TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    artifacts_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_handoffs_target_status_priority
ON handoffs (to_role, status, priority, created_at);
"""


def normalize_handoff_payload(
    payload: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    from_role = require_non_empty_string(payload.get("from_role"), "from_role")
    to_role = require_non_empty_string(payload.get("to_role"), "to_role")
    if from_role not in config["role_names"]:
        raise ValueError(f"Unknown from_role: {from_role}")
    if to_role not in config["role_names"]:
        raise ValueError(f"Unknown to_role: {to_role}")

    file_scope = require_string_list(payload.get("file_scope"), "file_scope")
    function_scope = require_string_list(payload.get("function_scope"), "function_scope")
    acceptance_criteria = require_string_list(
        payload.get("acceptance_criteria"),
        "acceptance_criteria",
    )
    artifacts = require_string_list(payload.get("artifacts"), "artifacts")
    dependencies = require_string_list(payload.get("dependencies"), "dependencies")

    if not file_scope:
        raise ValueError("file_scope must contain at least one file.")
    if not function_scope:
        raise ValueError("function_scope must contain at least one function or symbol.")
    if not acceptance_criteria:
        raise ValueError("acceptance_criteria must contain at least one entry.")

    max_files = config["execution_policy"]["max_files_per_task"]
    max_functions = config["execution_policy"]["max_functions_per_task"]
    if len(file_scope) > max_files:
        raise ValueError(
            f"file_scope exceeds the configured limit of {max_files} file(s)."
        )
    if len(function_scope) > max_functions:
        raise ValueError(
            f"function_scope exceeds the configured limit of {max_functions} function(s)."
        )

    status = require_non_empty_string(payload.get("status", "pending"), "status")
    if status not in VALID_HANDOFF_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_HANDOFF_STATUSES)}.")

    priority = require_positive_int(payload.get("priority", 3), "priority")
    test_status = require_non_empty_string(
        payload.get("test_status", "not_run"),
        "test_status",
    )
    if test_status not in VALID_TEST_STATUSES:
        raise ValueError(f"test_status must be one of {sorted(VALID_TEST_STATUSES)}.")

    failure_summary = str(payload.get("failure_summary", "")).strip()
    top_stack_frame = str(payload.get("top_stack_frame", "")).strip()
    if test_status == "fail" and not top_stack_frame:
        raise ValueError("top_stack_frame is required when test_status is 'fail'.")

    return {
        "handoff_id": require_non_empty_string(
            payload.get("handoff_id", uuid.uuid4().hex),
            "handoff_id",
        ),
        "run_id": require_non_empty_string(payload.get("run_id"), "run_id"),
        "task_id": require_non_empty_string(payload.get("task_id"), "task_id"),
        "title": require_non_empty_string(payload.get("title"), "title"),
        "summary": require_non_empty_string(payload.get("summary"), "summary"),
        "rationale": str(payload.get("rationale", "")).strip(),
        "from_role": from_role,
        "to_role": to_role,
        "status": status,
        "priority": priority,
        "created_at": require_non_empty_string(
            payload.get("created_at", utc_now()),
            "created_at",
        ),
        "next_action": require_non_empty_string(
            payload.get("next_action"),
            "next_action",
        ),
        "test_status": test_status,
        "test_command": str(payload.get("test_command", "")).strip(),
        "failure_summary": failure_summary,
        "top_stack_frame": top_stack_frame,
        "file_scope": file_scope,
        "function_scope": function_scope,
        "acceptance_criteria": acceptance_criteria,
        "dependencies": dependencies,
        "artifacts": artifacts,
    }


def row_to_handoff(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    payload.update(
        {
            "status": row["status"],
            "priority": row["priority"],
            "created_at": row["created_at"],
            "claimed_at": row["claimed_at"],
            "completed_at": row["completed_at"],
            "resolution_note": row["resolution_note"],
            "next_action": row["next_action"],
            "test_status": row["test_status"],
            "test_command": row["test_command"],
            "failure_summary": row["failure_summary"],
            "top_stack_frame": row["top_stack_frame"],
        }
    )
    return payload


@dataclass
class HandoffRepository:
    db_path: str | Path

    def ensure_db(self) -> None:
        database = Path(self.db_path)
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as connection:
            connection.executescript(HANDOFF_SCHEMA)

    def record(self, config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_handoff_payload(payload, config)
        self.ensure_db()
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id,
                    run_id,
                    task_id,
                    title,
                    summary,
                    rationale,
                    from_role,
                    to_role,
                    status,
                    priority,
                    created_at,
                    next_action,
                    test_status,
                    test_command,
                    failure_summary,
                    top_stack_frame,
                    file_scope_json,
                    function_scope_json,
                    acceptance_criteria_json,
                    dependencies_json,
                    artifacts_json,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["handoff_id"],
                    normalized["run_id"],
                    normalized["task_id"],
                    normalized["title"],
                    normalized["summary"],
                    normalized["rationale"],
                    normalized["from_role"],
                    normalized["to_role"],
                    normalized["status"],
                    normalized["priority"],
                    normalized["created_at"],
                    normalized["next_action"],
                    normalized["test_status"],
                    normalized["test_command"],
                    normalized["failure_summary"],
                    normalized["top_stack_frame"],
                    json.dumps(normalized["file_scope"]),
                    json.dumps(normalized["function_scope"]),
                    json.dumps(normalized["acceptance_criteria"]),
                    json.dumps(normalized["dependencies"]),
                    json.dumps(normalized["artifacts"]),
                    json.dumps(normalized, sort_keys=True),
                ),
            )
        return normalized

    def claim_next(self, role_name: str) -> dict[str, Any] | None:
        self.ensure_db()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM handoffs
                WHERE to_role = ? AND status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (role_name,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            claimed_at = utc_now()
            connection.execute(
                """
                UPDATE handoffs
                SET status = 'claimed', claimed_at = ?
                WHERE handoff_id = ?
                """,
                (claimed_at, row["handoff_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?",
                (row["handoff_id"],),
            ).fetchone()
            connection.commit()
            return row_to_handoff(updated)

    def update_status(
        self,
        handoff_id: str,
        status: str,
        resolution_note: str = "",
    ) -> dict[str, Any]:
        if status not in {"completed", "blocked"}:
            raise ValueError("status must be 'completed' or 'blocked'.")
        self.ensure_db()
        completed_at = utc_now()
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                UPDATE handoffs
                SET status = ?, completed_at = ?, resolution_note = ?
                WHERE handoff_id = ?
                """,
                (status, completed_at, resolution_note.strip(), handoff_id),
            )
            if connection.total_changes == 0:
                raise ValueError(f"Unknown handoff_id: {handoff_id}")
            row = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
        return row_to_handoff(row)

    def list(self, role_name: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        self.ensure_db()
        query = "SELECT * FROM handoffs"
        parameters: list[Any] = []
        clauses: list[str] = []
        if role_name:
            clauses.append("to_role = ?")
            parameters.append(role_name)
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY priority ASC, created_at ASC"

        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters).fetchall()
        return [row_to_handoff(row) for row in rows]


def ensure_handoff_db(db_path: str | Path) -> None:
    HandoffRepository(db_path).ensure_db()


def record_handoff(
    db_path: str | Path,
    config: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return HandoffRepository(db_path).record(config, payload)


def claim_next_handoff(db_path: str | Path, role_name: str) -> dict[str, Any] | None:
    return HandoffRepository(db_path).claim_next(role_name)


def update_handoff_status(
    db_path: str | Path,
    handoff_id: str,
    status: str,
    resolution_note: str = "",
) -> dict[str, Any]:
    return HandoffRepository(db_path).update_status(handoff_id, status, resolution_note)


def list_handoffs(
    db_path: str | Path,
    role_name: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    return HandoffRepository(db_path).list(role_name, status)
