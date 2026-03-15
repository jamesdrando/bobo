from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..common import load_json, slugify, utc_now
from ..workspace import render_relative_path, resolve_workspace_root
from .models import ChatEventRecord, ChatMessageRecord, ChatRuntimeState, ChatSession

SESSION_FILENAME = "session.json"
MESSAGES_FILENAME = "messages.jsonl"
EVENTS_FILENAME = "events.jsonl"
RUNTIME_FILENAME = "runtime.json"


def derive_session_slug(title: str) -> str:
    tokens = title.strip().split()
    shortened = " ".join(tokens[:8]).strip()
    return slugify(shortened)[:48] or "chat"


class ChatStore:
    def __init__(self, workspace_root: str | Path, storage_dir: str | Path) -> None:
        self.workspace_root = resolve_workspace_root(workspace_root)
        self.storage_dir = Path(storage_dir).resolve(strict=False)

    def ensure_storage_dir(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        return self.storage_dir / session_id

    def _session_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / SESSION_FILENAME

    def _messages_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / MESSAGES_FILENAME

    def _events_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / EVENTS_FILENAME

    def _runtime_file(self, session_id: str) -> Path:
        return self._session_dir(session_id) / RUNTIME_FILENAME

    def session_dir(self, session: ChatSession | str) -> Path:
        session_id = session.session_id if isinstance(session, ChatSession) else session
        return self._session_dir(session_id)

    def create_session(
        self,
        *,
        title: str,
        provider: str,
        model: str,
        region_name: str | None,
        profile_name: str | None,
        provider_options: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> ChatSession:
        self.ensure_storage_dir()
        timestamp = (created_at or utc_now()).replace("+00:00", "Z")
        timestamp = timestamp.replace("-", "").replace(":", "")
        timestamp = timestamp.replace(".000000", "")
        base_session_id = f"{timestamp}_{derive_session_slug(title)}"
        session_id = base_session_id
        session_dir = self._session_dir(session_id)
        suffix = 2
        while session_dir.exists():
            session_id = f"{base_session_id}-{suffix}"
            session_dir = self._session_dir(session_id)
            suffix += 1
        session_dir.mkdir(parents=True, exist_ok=False)
        now = created_at or utc_now()
        session = ChatSession(
            session_id=session_id,
            title=title,
            provider=provider,
            model=model,
            workspace_root=str(self.workspace_root),
            created_at=now,
            updated_at=now,
            region_name=region_name,
            profile_name=profile_name,
            provider_options=provider_options or {},
            storage_path=str(session_dir),
        )
        self._write_json(self._session_file(session_id), session.to_dict())
        self._messages_file(session_id).write_text("", encoding="utf-8")
        self._events_file(session_id).write_text("", encoding="utf-8")
        self._write_json(
            self._runtime_file(session_id),
            ChatRuntimeState(state="idle", updated_at=now).to_dict(),
        )
        return session

    def load_session(self, session_id: str) -> ChatSession:
        session_path = self._session_file(session_id)
        if not session_path.exists():
            raise ValueError(f"Unknown chat session: {session_id}")
        return ChatSession.from_dict(load_json(session_path), storage_path=str(session_path.parent))

    def load_latest_session(self) -> ChatSession | None:
        sessions = self.list_sessions()
        if not sessions:
            return None
        return sessions[0]

    def list_sessions(self) -> list[ChatSession]:
        self.ensure_storage_dir()
        sessions: list[ChatSession] = []
        for child in sorted(self.storage_dir.iterdir(), key=lambda item: item.name, reverse=True):
            if not child.is_dir():
                continue
            session_path = child / SESSION_FILENAME
            if not session_path.exists():
                continue
            sessions.append(ChatSession.from_dict(load_json(session_path), storage_path=str(child)))
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions

    def update_session(
        self,
        session: ChatSession,
        *,
        title: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        region_name: str | None = None,
        profile_name: str | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> ChatSession:
        session.title = title or session.title
        session.provider = provider or session.provider
        session.model = model or session.model
        session.region_name = region_name if region_name is not None else session.region_name
        session.profile_name = profile_name if profile_name is not None else session.profile_name
        if provider_options is not None:
            session.provider_options = provider_options
        session.updated_at = utc_now()
        self._write_json(self._session_file(session.session_id), session.to_dict())
        return session

    def terminate_session(
        self,
        session: ChatSession,
        reason: str,
        terminated_at: str | None = None,
    ) -> ChatSession:
        session.status = "terminated"
        session.terminated_at = terminated_at or utc_now()
        session.termination_reason = reason
        session.updated_at = session.terminated_at
        self._write_json(self._session_file(session.session_id), session.to_dict())
        return session

    def append_message(
        self,
        session: ChatSession,
        role: str,
        content: str,
        raw: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> ChatMessageRecord:
        record = ChatMessageRecord(
            role=role,
            content=content,
            created_at=created_at or utc_now(),
            raw=raw,
        )
        self._append_jsonl(self._messages_file(session.session_id), record.to_dict())
        self.touch_session(session)
        return record

    def append_event(
        self,
        session: ChatSession,
        kind: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        approval_state: str | None = None,
        created_at: str | None = None,
    ) -> ChatEventRecord:
        record = ChatEventRecord(
            kind=kind,
            summary=summary,
            payload=payload or {},
            created_at=created_at or utc_now(),
            approval_state=approval_state,
        )
        self._append_jsonl(self._events_file(session.session_id), record.to_dict())
        self.touch_session(session)
        return record

    def read_messages(self, session: ChatSession) -> list[ChatMessageRecord]:
        return [
            ChatMessageRecord.from_dict(item)
            for item in self._read_jsonl(self._messages_file(session.session_id))
        ]

    def read_events(self, session: ChatSession) -> list[ChatEventRecord]:
        return [
            ChatEventRecord.from_dict(item)
            for item in self._read_jsonl(self._events_file(session.session_id))
        ]

    def load_runtime(self, session: ChatSession) -> ChatRuntimeState:
        runtime_path = self._runtime_file(session.session_id)
        if not runtime_path.exists():
            runtime = ChatRuntimeState(state="idle", updated_at=utc_now())
            self._write_json(runtime_path, runtime.to_dict())
            return runtime
        return ChatRuntimeState.from_dict(load_json(runtime_path))

    def write_runtime(self, session: ChatSession, runtime: ChatRuntimeState) -> ChatRuntimeState:
        runtime.updated_at = runtime.updated_at or utc_now()
        self._write_json(self._runtime_file(session.session_id), runtime.to_dict())
        return runtime

    def touch_session(self, session: ChatSession) -> None:
        session.updated_at = utc_now()
        self._write_json(self._session_file(session.session_id), session.to_dict())

    def render_storage_path(self) -> str:
        return render_relative_path(self.storage_dir, self.workspace_root)

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
