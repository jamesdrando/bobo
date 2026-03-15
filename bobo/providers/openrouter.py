from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..common import require_non_empty_string, require_object
from ..workspace import DEFAULT_OPENROUTER_BASE_URL
from .base import ChatResult, ProviderRequest


def _normalize_openrouter_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(part for part in parts if part)


class OpenRouterProvider:
    def send(self, request: ProviderRequest) -> ChatResult:
        provider_options = require_object(request.provider_options, "provider_options")
        request_body_overrides = require_object(
            provider_options.get("body", {}),
            "provider_options.body",
        )
        extra_headers = require_object(
            provider_options.get("headers", {}),
            "provider_options.headers",
        )
        if "model" in request_body_overrides or "messages" in request_body_overrides:
            raise ValueError("provider_options.body cannot override model or messages.")

        base_url = str(provider_options.get("base_url", DEFAULT_OPENROUTER_BASE_URL)).strip() or DEFAULT_OPENROUTER_BASE_URL
        api_key = str(provider_options.get("api_key", "")).strip()
        api_key_env = str(provider_options.get("api_key_env", "OPENROUTER_API_KEY")).strip() or "OPENROUTER_API_KEY"
        if not api_key:
            api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                f"OpenRouter requires an API key via provider_options.api_key or env {api_key_env!r}."
            )
        site_url = str(provider_options.get("site_url", "")).strip() or None
        app_name = str(provider_options.get("app_name", "bobo")).strip() or None
        timeout_seconds = int(provider_options.get("timeout_seconds", 600))

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": False,
            **request_body_overrides,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop_sequences:
            payload["stop"] = request.stop_sequences

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **{key: str(value) for key, value in extra_headers.items()},
        }
        if site_url:
            headers["HTTP-Referer"] = site_url
        if app_name:
            headers["X-Title"] = app_name

        http_request = Request(
            base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=timeout_seconds) as response:
                raw_text = response.read().decode("utf-8")
                response_payload = json.loads(raw_text)
                request_id = ""
                if hasattr(response, "headers"):
                    request_id = str(response.headers.get("x-request-id", "")).strip()
        except HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload = json.loads(raw_error)
            except json.JSONDecodeError:
                error_payload = {"error": {"message": raw_error}}
            message = raw_error
            if isinstance(error_payload.get("error"), dict):
                message = str(error_payload["error"].get("message", raw_error))
            raise ValueError(f"OpenRouter request failed with HTTP {exc.code}: {message}") from exc
        except URLError as exc:
            raise ValueError(f"OpenRouter request failed: {exc.reason}") from exc

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenRouter response did not include choices.")
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message_payload = first_choice.get("message")
        if not isinstance(message_payload, dict):
            message_payload = {}
        assistant_text = _normalize_openrouter_message_content(message_payload.get("content"))

        request_id = request_id or require_non_empty_string(
            str(response_payload.get("id", "")).strip() or "openrouter-response",
            "request_id",
        )
        return ChatResult(
            provider="openrouter",
            model=request.model,
            message={
                "role": "assistant",
                "content": assistant_text,
                "raw": message_payload,
            },
            stop_reason=str(first_choice.get("finish_reason", "")).strip() or None,
            usage=dict(response_payload.get("usage", {})),
            metrics={},
            request_id=request_id,
            raw_response=response_payload,
        )
