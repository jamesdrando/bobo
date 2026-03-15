from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..common import normalize_optional_string


@dataclass
class ProviderRequest:
    provider: str
    model: str
    messages: list[dict[str, str]]
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] = field(default_factory=list)
    region_name: str | None = None
    profile_name: str | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "messages": self.messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stop_sequences": self.stop_sequences,
            "region_name": self.region_name,
            "profile_name": self.profile_name,
            "provider_options": self.provider_options,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProviderRequest":
        return cls(
            provider=str(payload["provider"]),
            model=str(payload["model"]),
            messages=list(payload["messages"]),
            max_tokens=payload.get("max_tokens"),
            temperature=payload.get("temperature"),
            top_p=payload.get("top_p"),
            stop_sequences=list(payload.get("stop_sequences", [])),
            region_name=normalize_optional_string(payload.get("region_name")),
            profile_name=normalize_optional_string(payload.get("profile_name")),
            provider_options=dict(payload.get("provider_options", {})),
        )


@dataclass
class ChatResult:
    provider: str
    model: str
    message: dict[str, Any]
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "message": self.message,
            "stop_reason": self.stop_reason,
            "usage": self.usage,
            "metrics": self.metrics,
            "request_id": self.request_id,
            "raw_response": self.raw_response,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatResult":
        return cls(
            provider=str(payload["provider"]),
            model=str(payload["model"]),
            message=dict(payload["message"]),
            stop_reason=str(payload.get("stop_reason")) if payload.get("stop_reason") is not None else None,
            usage=dict(payload.get("usage", {})),
            metrics=dict(payload.get("metrics", {})),
            request_id=str(payload.get("request_id")) if payload.get("request_id") is not None else None,
            raw_response=dict(payload.get("raw_response", {})),
        )


class LLMProvider(Protocol):
    def send(self, request: ProviderRequest) -> ChatResult: ...


@dataclass
class ProviderRegistry:
    providers: dict[str, LLMProvider] = field(default_factory=dict)

    def register(self, name: str, provider: LLMProvider) -> None:
        self.providers[name] = provider

    def get(self, name: str) -> LLMProvider:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported provider: {name!r}. Registered providers: {sorted(self.providers)}"
            ) from exc

    def complete(self, request: ProviderRequest) -> ChatResult:
        return self.get(request.provider).send(request)
