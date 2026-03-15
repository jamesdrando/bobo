from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..common import normalize_optional_string


@dataclass
class ChatSession:
    session_id: str
    title: str
    provider: str
    model: str
    workspace_root: str
    created_at: str
    updated_at: str
    region_name: str | None = None
    profile_name: str | None = None
    status: str = "active"
    terminated_at: str | None = None
    termination_reason: str | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)
    storage_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "provider": self.provider,
            "model": self.model,
            "workspace_root": self.workspace_root,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "region_name": self.region_name,
            "profile_name": self.profile_name,
            "status": self.status,
            "terminated_at": self.terminated_at,
            "termination_reason": self.termination_reason,
            "provider_options": self.provider_options,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], storage_path: str | None = None) -> "ChatSession":
        return cls(
            session_id=str(payload["session_id"]),
            title=str(payload["title"]),
            provider=str(payload["provider"]),
            model=str(payload["model"]),
            workspace_root=str(payload["workspace_root"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            region_name=normalize_optional_string(payload.get("region_name")),
            profile_name=normalize_optional_string(payload.get("profile_name")),
            status=str(payload.get("status", "active")),
            terminated_at=normalize_optional_string(payload.get("terminated_at")),
            termination_reason=normalize_optional_string(payload.get("termination_reason")),
            provider_options=dict(payload.get("provider_options", {})),
            storage_path=storage_path,
        )


@dataclass
class ChatRuntimeState:
    state: str = "idle"
    active_pid: int | None = None
    run_id: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    stop_requested_at: str | None = None
    termination_reason: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "active_pid": self.active_pid,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "stop_requested_at": self.stop_requested_at,
            "termination_reason": self.termination_reason,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatRuntimeState":
        active_pid_raw = payload.get("active_pid")
        active_pid = int(active_pid_raw) if isinstance(active_pid_raw, int) else None
        return cls(
            state=str(payload.get("state", "idle")),
            active_pid=active_pid,
            run_id=normalize_optional_string(payload.get("run_id")),
            started_at=normalize_optional_string(payload.get("started_at")),
            updated_at=normalize_optional_string(payload.get("updated_at")),
            stop_requested_at=normalize_optional_string(payload.get("stop_requested_at")),
            termination_reason=normalize_optional_string(payload.get("termination_reason")),
            last_error=normalize_optional_string(payload.get("last_error")),
        )


@dataclass
class ChatMessageRecord:
    role: str
    content: str
    created_at: str
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }
        if self.raw is not None:
            payload["raw"] = self.raw
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatMessageRecord":
        raw_payload = payload.get("raw")
        raw = dict(raw_payload) if isinstance(raw_payload, dict) else None
        return cls(
            role=str(payload["role"]),
            content=str(payload["content"]),
            created_at=str(payload["created_at"]),
            raw=raw,
        )


@dataclass
class ChatEventRecord:
    kind: str
    summary: str
    payload: dict[str, Any]
    created_at: str
    approval_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "payload": self.payload,
            "created_at": self.created_at,
            "approval_state": self.approval_state,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatEventRecord":
        approval_value = payload.get("approval_state")
        approval_state = str(approval_value) if approval_value is not None else None
        raw_payload = payload.get("payload")
        return cls(
            kind=str(payload["kind"]),
            summary=str(payload["summary"]),
            payload=dict(raw_payload) if isinstance(raw_payload, dict) else {},
            created_at=str(payload["created_at"]),
            approval_state=approval_state,
        )
