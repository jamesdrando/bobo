from __future__ import annotations

from typing import Any

from ..common import require_non_empty_string
from ..providers.base import ProviderRegistry, ProviderRequest
from ..workspace import WorkspaceSettings, build_session_title
from .models import ChatEventRecord, ChatMessageRecord, ChatSession
from .runner import ChatTerminationError, InlineProviderRunner, SubprocessProviderRunner
from .store import ChatStore


class ChatService:
    def __init__(
        self,
        store: ChatStore,
        workspace_settings: WorkspaceSettings,
        registry: ProviderRegistry | None = None,
        provider_runner: InlineProviderRunner | SubprocessProviderRunner | None = None,
    ) -> None:
        self.store = store
        self.workspace_settings = workspace_settings
        self.registry = registry
        if provider_runner is not None:
            self.provider_runner = provider_runner
        elif registry is not None:
            self.provider_runner = InlineProviderRunner(registry)
        else:
            self.provider_runner = SubprocessProviderRunner(store)

    def list_sessions(self) -> list[ChatSession]:
        return self.store.list_sessions()

    def create_session(
        self,
        *,
        title: str,
        provider: str | None = None,
        model: str | None = None,
        region_name: str | None = None,
        profile_name: str | None = None,
        provider_options: dict[str, Any] | None = None,
    ) -> ChatSession:
        resolved_provider = provider or self.workspace_settings.chat.default_provider
        session = self.store.create_session(
            title=build_session_title(title, "Untitled chat"),
            provider=resolved_provider,
            model=model or self.workspace_settings.chat.default_model,
            region_name=region_name if region_name is not None else self.workspace_settings.bedrock.region,
            profile_name=profile_name if profile_name is not None else self.workspace_settings.bedrock.profile,
            provider_options=self._provider_options_for(resolved_provider, provider_options),
        )
        self.store.append_event(
            session,
            kind="session_created",
            summary="Created chat session.",
            payload={"title": session.title, "session_id": session.session_id},
            approval_state="approved",
        )
        return session

    def prepare_session(
        self,
        *,
        resume: str | None = None,
        title: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        region_name: str | None = None,
        profile_name: str | None = None,
    ) -> ChatSession | None:
        if resume:
            if resume == "latest":
                session = self.store.load_latest_session()
                if session is None:
                    raise ValueError("No saved chat sessions were found.")
                self.store.append_event(
                    session,
                    kind="session_resumed",
                    summary="Resumed latest chat session.",
                    payload={"session_id": session.session_id},
                    approval_state="approved",
                )
                return session
            session = self.store.load_session(resume)
            self.store.append_event(
                session,
                kind="session_resumed",
                summary="Resumed chat session.",
                payload={"session_id": session.session_id},
                approval_state="approved",
            )
            return session
        if title:
            return self.create_session(
                title=title,
                provider=provider,
                model=model,
                region_name=region_name,
                profile_name=profile_name,
            )
        return None

    def update_session_title(self, session: ChatSession, title: str) -> ChatSession:
        updated = self.store.update_session(session, title=require_non_empty_string(title, "title"))
        self.store.append_event(
            updated,
            kind="session_updated",
            summary="Updated session title.",
            payload={"title": updated.title},
        )
        return updated

    def update_runtime_settings(
        self,
        session: ChatSession,
        *,
        provider: str | None = None,
        model: str | None = None,
        region_name: str | None = None,
        profile_name: str | None = None,
    ) -> ChatSession:
        resolved_provider = provider or session.provider
        updated = self.store.update_session(
            session,
            provider=resolved_provider,
            model=model,
            region_name=region_name,
            profile_name=profile_name,
            provider_options=self._provider_options_for(
                resolved_provider,
                session.provider_options if resolved_provider == session.provider else None,
            ),
        )
        self.store.append_event(
            updated,
            kind="session_updated",
            summary="Updated runtime settings.",
            payload={
                "provider": updated.provider,
                "model": updated.model,
                "region_name": updated.region_name,
                "profile_name": updated.profile_name,
            },
        )
        return updated

    def transcript(self, session: ChatSession) -> list[ChatMessageRecord]:
        return self.store.read_messages(session)

    def events(self, session: ChatSession) -> list[ChatEventRecord]:
        return self.store.read_events(session)

    def send_prompt(
        self,
        prompt: str,
        *,
        session: ChatSession | None = None,
        title: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        region_name: str | None = None,
        profile_name: str | None = None,
    ) -> tuple[ChatSession, ChatMessageRecord, ChatMessageRecord]:
        normalized_prompt = require_non_empty_string(prompt, "prompt")
        active_session = session or self.create_session(
            title=build_session_title(title, normalized_prompt),
            provider=provider,
            model=model,
            region_name=region_name,
            profile_name=profile_name,
        )
        if session is not None and (
            provider is not None
            or model is not None
            or region_name is not None
            or profile_name is not None
        ):
            active_session = self.store.update_session(
                active_session,
                provider=provider or active_session.provider,
                model=model,
                region_name=region_name,
                profile_name=profile_name,
                provider_options=self._provider_options_for(
                    provider or active_session.provider,
                    active_session.provider_options if provider is None or provider == active_session.provider else None,
                ),
            )
        if active_session.status == "terminated":
            raise ValueError("This chat session has been terminated and cannot accept new prompts.")
        user_record = self.store.append_message(active_session, "user", normalized_prompt)
        messages = [
            {"role": item.role, "content": item.content}
            for item in self.store.read_messages(active_session)
        ]
        request = ProviderRequest(
            provider=active_session.provider,
            model=active_session.model,
            messages=messages,
            region_name=active_session.region_name,
            profile_name=active_session.profile_name,
            provider_options=active_session.provider_options,
        )
        self.store.append_event(
            active_session,
            kind="provider_request",
            summary="Sent prompt to provider.",
            payload={
                "provider": request.provider,
                "model": request.model,
                "message_count": len(request.messages),
            },
            approval_state="approved",
        )
        try:
            result = self.provider_runner.run(active_session, request)
        except ChatTerminationError as exc:
            self.store.append_event(
                active_session,
                kind="session_terminated",
                summary="Terminated chat session during provider execution.",
                payload={"reason": active_session.termination_reason or "user_requested"},
                approval_state="approved",
            )
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            self.store.append_event(
                active_session,
                kind="provider_error",
                summary="Provider request failed.",
                payload={"error": str(exc)},
                approval_state="approved",
            )
            raise
        assistant_record = self.store.append_message(
            active_session,
            "assistant",
            str(result.message.get("content", "")),
            raw=result.message.get("raw"),
        )
        self.store.append_event(
            active_session,
            kind="provider_response",
            summary="Received provider response.",
            payload={
                "provider": result.provider,
                "model": result.model,
                "stop_reason": result.stop_reason,
                "request_id": result.request_id,
            },
            approval_state="approved",
        )
        return active_session, user_record, assistant_record

    def resend_last_prompt(self, session: ChatSession) -> tuple[ChatSession, ChatMessageRecord, ChatMessageRecord]:
        if self.store.load_session(session.session_id).status == "terminated":
            raise ValueError("This chat session has been terminated and cannot accept new prompts.")
        prompts = [item for item in self.store.read_messages(session) if item.role == "user"]
        if not prompts:
            raise ValueError("No previous user prompt exists for this session.")
        self.store.append_event(
            session,
            kind="prompt_resend",
            summary="Re-sent the last prompt.",
            payload={"prompt": prompts[-1].content},
            approval_state="approved",
        )
        return self.send_prompt(prompts[-1].content, session=session)

    def kill_session(self, session: ChatSession, reason: str = "user_requested") -> dict[str, Any]:
        refreshed = self.store.load_session(session.session_id)
        if refreshed.status != "terminated":
            refreshed = self.store.terminate_session(refreshed, reason)
        kill_result = self.provider_runner.kill(refreshed, reason=reason)
        self.store.append_event(
            refreshed,
            kind="session_terminated",
            summary="Terminated chat session.",
            payload=kill_result,
            approval_state="approved",
        )
        return {
            "session_id": refreshed.session_id,
            "status": refreshed.status,
            "terminated_at": refreshed.terminated_at,
            "termination_reason": refreshed.termination_reason,
            **kill_result,
        }

    def _provider_options_for(
        self,
        provider: str,
        provider_options: dict[str, Any] | None,
    ) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        if provider == "openrouter":
            defaults = {
                "base_url": self.workspace_settings.openrouter.base_url,
                "api_key_env": self.workspace_settings.openrouter.api_key_env,
            }
            if self.workspace_settings.openrouter.site_url:
                defaults["site_url"] = self.workspace_settings.openrouter.site_url
            if self.workspace_settings.openrouter.app_name:
                defaults["app_name"] = self.workspace_settings.openrouter.app_name
        merged = {**defaults}
        if provider_options:
            merged.update(provider_options)
        return merged
