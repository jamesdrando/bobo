from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from ..common import utc_now
from ..providers import DEFAULT_PROVIDER_REGISTRY
from ..providers.base import ChatResult, ProviderRegistry, ProviderRequest
from .models import ChatRuntimeState, ChatSession
from .store import ChatStore


class ChatTerminationError(RuntimeError):
    pass


class InlineProviderRunner:
    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self.registry = registry or DEFAULT_PROVIDER_REGISTRY

    def run(self, session: ChatSession, request: ProviderRequest) -> ChatResult:
        return self.registry.complete(request)

    def kill(self, session: ChatSession, reason: str = "user_requested") -> dict[str, object]:
        return {
            "session_id": session.session_id,
            "killed_pid": False,
            "active_pid": None,
            "reason": reason,
        }


class SubprocessProviderRunner:
    def __init__(self, store: ChatStore, python_executable: str | None = None) -> None:
        self.store = store
        self.python_executable = python_executable or sys.executable

    def run(self, session: ChatSession, request: ProviderRequest) -> ChatResult:
        refreshed_session = self.store.load_session(session.session_id)
        if refreshed_session.status == "terminated":
            raise ChatTerminationError("This chat session has been terminated.")

        run_id = uuid.uuid4().hex
        session_dir = self.store.session_dir(refreshed_session)
        request_path = session_dir / f".provider_request_{run_id}.json"
        response_path = session_dir / f".provider_response_{run_id}.json"
        request_path.write_text(json.dumps(request.to_dict(), sort_keys=True), encoding="utf-8")

        runtime = ChatRuntimeState(
            state="running",
            run_id=run_id,
            started_at=utc_now(),
            updated_at=utc_now(),
        )
        self.store.write_runtime(refreshed_session, runtime)

        process = subprocess.Popen(
            [
                self.python_executable,
                "-m",
                "bobo",
                "_provider-call",
                "--request-file",
                str(request_path),
                "--response-file",
                str(response_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        runtime.active_pid = process.pid
        runtime.updated_at = utc_now()
        self.store.write_runtime(refreshed_session, runtime)

        try:
            stdout, stderr = process.communicate()
        finally:
            request_path.unlink(missing_ok=True)

        refreshed_session = self.store.load_session(session.session_id)
        runtime = self.store.load_runtime(refreshed_session)
        killed = refreshed_session.status == "terminated" or runtime.state == "terminated"

        try:
            if process.returncode != 0:
                error_payload = self._load_response_payload(response_path)
                error_message = str(error_payload.get("error", "")).strip()
                if not error_message:
                    error_message = stderr.strip() or stdout.strip() or "Provider process failed."
                runtime.active_pid = None
                runtime.run_id = None
                runtime.updated_at = utc_now()
                if killed:
                    runtime.state = "terminated"
                    runtime.last_error = "terminated"
                    self.store.write_runtime(refreshed_session, runtime)
                    raise ChatTerminationError("Chat session was terminated during provider execution.")
                runtime.state = "idle"
                runtime.last_error = error_message
                self.store.write_runtime(refreshed_session, runtime)
                raise ValueError(error_message)

            if refreshed_session.status == "terminated":
                runtime.active_pid = None
                runtime.run_id = None
                runtime.state = "terminated"
                runtime.last_error = "terminated"
                runtime.updated_at = utc_now()
                self.store.write_runtime(refreshed_session, runtime)
                raise ChatTerminationError("Chat session was terminated during provider execution.")

            response_payload = self._load_response_payload(response_path)
            if not bool(response_payload.get("ok", False)):
                error_message = str(response_payload.get("error", "Provider process failed.")).strip()
                runtime.active_pid = None
                runtime.run_id = None
                runtime.state = "idle"
                runtime.last_error = error_message
                runtime.updated_at = utc_now()
                self.store.write_runtime(refreshed_session, runtime)
                raise ValueError(error_message)

            result = ChatResult.from_dict(dict(response_payload["result"]))
            runtime.active_pid = None
            runtime.run_id = None
            runtime.state = "idle"
            runtime.last_error = None
            runtime.updated_at = utc_now()
            self.store.write_runtime(refreshed_session, runtime)
            return result
        finally:
            response_path.unlink(missing_ok=True)

    def kill(self, session: ChatSession, reason: str = "user_requested") -> dict[str, object]:
        refreshed_session = self.store.load_session(session.session_id)
        runtime = self.store.load_runtime(refreshed_session)
        active_pid = runtime.active_pid
        killed_pid = False
        if active_pid is not None:
            try:
                os.kill(active_pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
                killed_pid = True
            except ProcessLookupError:
                killed_pid = False
            except PermissionError:
                killed_pid = False
        runtime.state = "terminated"
        runtime.active_pid = None
        runtime.run_id = None
        runtime.stop_requested_at = utc_now()
        runtime.updated_at = runtime.stop_requested_at
        runtime.termination_reason = reason
        runtime.last_error = "terminated"
        self.store.write_runtime(refreshed_session, runtime)
        return {
            "session_id": refreshed_session.session_id,
            "killed_pid": killed_pid,
            "active_pid": active_pid,
            "reason": reason,
        }

    def _load_response_payload(self, path: Path) -> dict[str, object]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
