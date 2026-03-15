from __future__ import annotations

import json
import unittest
from unittest import mock

import bobo


class FakeHTTPResponse:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class OpenRouterProviderTests(unittest.TestCase):
    def test_llm_complete_openrouter_posts_expected_payload(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse(
                {
                    "id": "resp-openrouter-1",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "OpenRouter reply",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                },
                headers={"x-request-id": "req-openrouter-1"},
            )

        with mock.patch("bobo.providers.openrouter.urlopen", side_effect=fake_urlopen):
            response = bobo.llm_complete(
                {
                    "provider": "openrouter",
                    "model": "openai/gpt-4o-mini",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 32,
                    "temperature": 0.2,
                    "stop_sequences": ["STOP"],
                    "provider_options": {
                        "api_key": "openrouter-key",
                        "site_url": "https://example.com",
                        "app_name": "bobo-test",
                    },
                }
            )

        self.assertEqual("openrouter", response["provider"])
        self.assertEqual("OpenRouter reply", response["message"]["content"])
        self.assertEqual("https://openrouter.ai/api/v1/chat/completions", captured["url"])
        self.assertEqual(600, captured["timeout"])
        assert isinstance(captured["headers"], dict)
        self.assertEqual("Bearer openrouter-key", captured["headers"]["Authorization"])
        self.assertEqual("https://example.com", captured["headers"]["Http-referer"])
        self.assertEqual("bobo-test", captured["headers"]["X-title"])
        assert isinstance(captured["body"], dict)
        self.assertEqual("openai/gpt-4o-mini", captured["body"]["model"])
        self.assertEqual([{"role": "user", "content": "Hello"}], captured["body"]["messages"])
        self.assertEqual(["STOP"], captured["body"]["stop"])
