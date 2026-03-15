from __future__ import annotations

import argparse
from typing import Any

from .common import (
    normalize_optional_string,
    parse_json_text,
    parse_optional_json_object,
    read_text_input,
    require_non_empty_string,
    require_object,
    require_positive_int,
    require_string_list,
)
from .providers import DEFAULT_PROVIDER_REGISTRY
from .providers.base import ProviderRegistry, ProviderRequest

VALID_LLM_MESSAGE_ROLES = {"system", "user", "assistant"}


def normalize_message_content(content: Any, field_name: str) -> str:
    if isinstance(content, str):
        return require_non_empty_string(content, field_name)
    if not isinstance(content, list) or not content:
        raise ValueError(
            f"{field_name} must be a non-empty string or a non-empty list of text blocks."
        )

    pieces: list[str] = []
    for index, block in enumerate(content):
        block_obj = require_object(block, f"{field_name}[{index}]")
        text_value = block_obj.get("text")
        if not isinstance(text_value, str) or not text_value.strip():
            raise ValueError(f"{field_name}[{index}].text must be a non-empty string.")
        pieces.append(text_value.strip())
    return "\n".join(pieces)


def normalize_llm_messages(messages_payload: Any) -> list[dict[str, str]]:
    if not isinstance(messages_payload, list) or not messages_payload:
        raise ValueError("messages must be a non-empty list.")

    normalized: list[dict[str, str]] = []
    for index, message_payload in enumerate(messages_payload):
        message = require_object(message_payload, f"messages[{index}]")
        role = require_non_empty_string(message.get("role"), f"messages[{index}].role").lower()
        if role not in VALID_LLM_MESSAGE_ROLES:
            raise ValueError(
                f"messages[{index}].role must be one of {sorted(VALID_LLM_MESSAGE_ROLES)}."
            )
        content = normalize_message_content(
            message.get("content"),
            f"messages[{index}].content",
        )
        normalized.append({"role": role, "content": content})

    return normalized


def load_llm_messages_from_inputs(
    prompt: str | None,
    messages_json: str | None,
    messages_file: str | None,
    system_prompts: list[str],
) -> list[dict[str, str]]:
    if prompt is not None:
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    elif messages_json is not None:
        messages = normalize_llm_messages(parse_json_text(messages_json, "messages_json"))
    elif messages_file is not None:
        messages = normalize_llm_messages(parse_json_text(read_text_input(messages_file), "messages_file"))
    else:
        raise ValueError("One of prompt, messages_json, or messages_file is required.")

    prefixed_system_prompts = [
        {"role": "system", "content": require_non_empty_string(item, "system[]")}
        for item in system_prompts
    ]
    return prefixed_system_prompts + messages


def normalize_optional_float(value: Any, field_name: str, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number between {minimum} and {maximum}.")
    normalized = float(value)
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}.")
    return normalized


def normalize_llm_request(payload: dict[str, Any]) -> dict[str, Any]:
    provider = require_non_empty_string(payload.get("provider"), "provider").lower()
    model = require_non_empty_string(payload.get("model"), "model")
    messages = normalize_llm_messages(payload.get("messages"))

    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        max_tokens = require_positive_int(max_tokens, "max_tokens")

    stop_sequences = require_string_list(payload.get("stop_sequences"), "stop_sequences")

    provider_options_payload = payload.get("provider_options")
    if provider_options_payload is None:
        provider_options_payload = {}
    provider_options = require_object(provider_options_payload, "provider_options")

    region_name = normalize_optional_string(payload.get("region_name"))
    profile_name = normalize_optional_string(payload.get("profile_name"))

    return {
        "provider": provider,
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": normalize_optional_float(payload.get("temperature"), "temperature", 0.0, 2.0),
        "top_p": normalize_optional_float(payload.get("top_p"), "top_p", 0.0, 1.0),
        "stop_sequences": stop_sequences,
        "region_name": region_name,
        "profile_name": profile_name,
        "provider_options": provider_options,
    }


def build_llm_request_from_args(args: argparse.Namespace) -> dict[str, Any]:
    messages = load_llm_messages_from_inputs(
        prompt=args.prompt,
        messages_json=args.messages_json,
        messages_file=args.messages_file,
        system_prompts=args.system,
    )
    provider_options = parse_optional_json_object(
        args.provider_options_json,
        args.provider_options_file,
        "provider_options",
    )
    return normalize_llm_request(
        {
            "provider": args.provider,
            "model": args.model,
            "messages": messages,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "stop_sequences": args.stop_sequence,
            "region_name": args.region,
            "profile_name": args.profile,
            "provider_options": provider_options,
        }
    )


def llm_complete(
    payload: dict[str, Any],
    registry: ProviderRegistry | None = None,
) -> dict[str, Any]:
    normalized = normalize_llm_request(payload)
    provider_request = ProviderRequest(**normalized)
    result = (registry or DEFAULT_PROVIDER_REGISTRY).complete(provider_request)
    return result.to_dict()
