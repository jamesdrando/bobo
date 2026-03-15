from __future__ import annotations

import importlib
from typing import Any

from ..common import require_object
from .base import ChatResult, ProviderRequest


def split_messages_for_bedrock(
    messages: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    bedrock_messages: list[dict[str, Any]] = []
    system_messages: list[dict[str, str]] = []

    for index, message in enumerate(messages):
        role = message["role"]
        content = message["content"]
        if role == "system":
            system_messages.append({"text": content})
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(
                f"messages[{index}].role={role!r} is not supported by bedrock converse."
            )
        bedrock_messages.append({"role": role, "content": [{"text": content}]})

    if not bedrock_messages:
        raise ValueError("At least one user or assistant message is required for bedrock.")

    return bedrock_messages, system_messages


def extract_bedrock_text_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    text_fragments: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text_value = block.get("text")
        if isinstance(text_value, str):
            text_fragments.append(text_value)
    return "\n".join(fragment for fragment in text_fragments if fragment)


class BedrockProvider:
    def send(self, request: ProviderRequest) -> ChatResult:
        try:
            boto3 = importlib.import_module("boto3")
        except ModuleNotFoundError as exc:
            raise ValueError(
                "The 'bedrock' provider requires boto3. Install it with: pip install boto3"
            ) from exc

        provider_options = request.provider_options
        session_kwargs = require_object(
            provider_options.get("session_kwargs", {}),
            "provider_options.session_kwargs",
        )
        client_kwargs = require_object(
            provider_options.get("client_kwargs", {}),
            "provider_options.client_kwargs",
        )
        converse_overrides = require_object(
            provider_options.get("converse_kwargs", {}),
            "provider_options.converse_kwargs",
        )

        if "modelId" in converse_overrides or "messages" in converse_overrides:
            raise ValueError(
                "provider_options.converse_kwargs cannot override modelId or messages."
            )

        session = boto3.session.Session(
            **{
                **session_kwargs,
                **({"profile_name": request.profile_name} if request.profile_name else {}),
            }
        )
        client = session.client(
            "bedrock-runtime",
            **{
                **client_kwargs,
                **({"region_name": request.region_name} if request.region_name else {}),
            },
        )

        bedrock_messages, system_messages = split_messages_for_bedrock(request.messages)
        request_payload: dict[str, Any] = {
            "modelId": request.model,
            "messages": bedrock_messages,
            **converse_overrides,
        }
        if system_messages:
            request_payload["system"] = system_messages

        inference_config = require_object(
            request_payload.get("inferenceConfig", {}),
            "provider_options.converse_kwargs.inferenceConfig",
        )
        if request.max_tokens is not None:
            inference_config["maxTokens"] = request.max_tokens
        if request.temperature is not None:
            inference_config["temperature"] = request.temperature
        if request.top_p is not None:
            inference_config["topP"] = request.top_p
        if request.stop_sequences:
            inference_config["stopSequences"] = request.stop_sequences
        if inference_config:
            request_payload["inferenceConfig"] = inference_config

        response = client.converse(**request_payload)
        output_payload = response.get("output", {})
        output_object = output_payload if isinstance(output_payload, dict) else {}
        assistant_payload = output_object.get("message", {})
        assistant_message = assistant_payload if isinstance(assistant_payload, dict) else {}
        assistant_text = extract_bedrock_text_from_message(assistant_message)

        return ChatResult(
            provider="bedrock",
            model=request.model,
            message={
                "role": "assistant",
                "content": assistant_text,
                "raw": assistant_message,
            },
            stop_reason=response.get("stopReason"),
            usage=response.get("usage", {}),
            metrics=response.get("metrics", {}),
            request_id=response.get("ResponseMetadata", {}).get("RequestId"),
            raw_response=response,
        )
