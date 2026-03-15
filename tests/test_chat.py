from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import bobo
from bobo.providers.base import ChatResult, ProviderRegistry


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class FakeProvider:
    def __init__(self) -> None:
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return ChatResult(
            provider=request.provider,
            model=request.model,
            message={
                "role": "assistant",
                "content": f"reply:{request.messages[-1]['content']}",
                "raw": {"echo": request.messages[-1]["content"]},
            },
            stop_reason="end_turn",
            request_id="req-chat",
        )


class WorkspaceSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_workspace_settings_defaults(self) -> None:
        settings = bobo.load_workspace_settings(self.root)

        self.assertEqual(".bobo/chats", settings.chat.storage_dir)
        self.assertEqual("bedrock", settings.chat.default_provider)
        self.assertEqual(
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
            settings.chat.default_model,
        )
        self.assertIsNone(settings.bedrock.region)
        self.assertIsNone(settings.bedrock.profile)
        self.assertEqual("OPENROUTER_API_KEY", settings.openrouter.api_key_env)

    def test_load_workspace_settings_and_cli_chat_dir_override(self) -> None:
        write_json(
            self.root / ".bobo" / "config.json",
            {
                "chat": {
                    "storage_dir": ".bobo/custom-chats",
                    "default_model": "model-x",
                },
                "bedrock": {
                    "region": "us-west-2",
                    "profile": "dev",
                },
                "openrouter": {
                    "base_url": "https://openrouter.example/api",
                    "api_key_env": "ALT_OPENROUTER_KEY",
                    "site_url": "https://example.com",
                    "app_name": "bobo-test",
                },
            },
        )

        settings = bobo.load_workspace_settings(self.root)
        resolved_default = bobo.resolve_chat_storage_dir(self.root, settings)
        resolved_override = bobo.resolve_chat_storage_dir(
            self.root,
            settings,
            ".bobo/override-chats",
        )

        self.assertEqual("model-x", settings.chat.default_model)
        self.assertEqual("us-west-2", settings.bedrock.region)
        self.assertEqual("dev", settings.bedrock.profile)
        self.assertEqual("https://openrouter.example/api", settings.openrouter.base_url)
        self.assertEqual("ALT_OPENROUTER_KEY", settings.openrouter.api_key_env)
        self.assertEqual(self.root / ".bobo" / "custom-chats", resolved_default)
        self.assertEqual(self.root / ".bobo" / "override-chats", resolved_override)

    def test_resolve_chat_storage_dir_rejects_escape(self) -> None:
        settings = bobo.load_workspace_settings(self.root)

        with self.assertRaisesRegex(ValueError, "path escapes the workspace root"):
            bobo.resolve_chat_storage_dir(self.root, settings, "../outside")


class ChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = bobo.load_workspace_settings(self.root)
        self.storage_dir = bobo.resolve_chat_storage_dir(self.root, self.settings)
        self.store = bobo.ChatStore(self.root, self.storage_dir)
        self.registry = ProviderRegistry()
        self.fake_provider = FakeProvider()
        self.registry.register("bedrock", self.fake_provider)
        self.service = bobo.ChatService(self.store, self.settings, registry=self.registry)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_send_prompt_persists_messages_and_events(self) -> None:
        session, user_record, assistant_record = self.service.send_prompt("Hello from session")

        self.assertIn("hello-from-session", session.session_id)
        self.assertEqual("Hello from session", user_record.content)
        self.assertEqual("reply:Hello from session", assistant_record.content)
        self.assertTrue((self.storage_dir / session.session_id / "session.json").exists())
        self.assertTrue((self.storage_dir / session.session_id / "messages.jsonl").exists())
        self.assertTrue((self.storage_dir / session.session_id / "events.jsonl").exists())

        messages = self.store.read_messages(session)
        self.assertEqual(["user", "assistant"], [item.role for item in messages])
        self.assertEqual(
            ["session_created", "provider_request", "provider_response"],
            [item.kind for item in self.store.read_events(session)],
        )
        self.assertEqual(1, len(self.fake_provider.requests))
        self.assertEqual("Hello from session", self.fake_provider.requests[0].messages[0]["content"])

    def test_update_title_does_not_rename_directory(self) -> None:
        session = self.service.create_session(title="Initial title")
        original_path = Path(session.storage_path or "")

        updated = self.service.update_session_title(session, "Renamed chat")

        self.assertEqual("Renamed chat", updated.title)
        self.assertEqual(original_path, Path(updated.storage_path or ""))
        self.assertTrue(original_path.exists())
        self.assertTrue((original_path / "session.json").exists())

    def test_prepare_session_latest_and_resend_last_prompt(self) -> None:
        session, _, _ = self.service.send_prompt("First prompt")
        latest = self.service.prepare_session(resume="latest")

        self.assertIsNotNone(latest)
        assert latest is not None
        resent_session, _, resent_assistant = self.service.resend_last_prompt(latest)

        self.assertEqual(session.session_id, latest.session_id)
        self.assertEqual("reply:First prompt", resent_assistant.content)
        self.assertEqual(2, len([item for item in self.store.read_messages(resent_session) if item.role == "user"]))
        self.assertEqual(2, len(self.fake_provider.requests))
        self.assertEqual(3, len(self.fake_provider.requests[-1].messages))

    def test_kill_session_terminates_active_process_and_blocks_future_prompts(self) -> None:
        service = bobo.ChatService(
            self.store,
            self.settings,
            provider_runner=bobo.SubprocessProviderRunner(self.store, python_executable=sys.executable),
        )
        session = service.create_session(title="Kill me")
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        runtime = self.store.load_runtime(session)
        runtime.state = "running"
        runtime.active_pid = process.pid
        runtime.run_id = "run-1"
        runtime.started_at = "2026-01-01T00:00:00+00:00"
        runtime.updated_at = runtime.started_at
        self.store.write_runtime(session, runtime)

        result = service.kill_session(session, reason="test_stop")

        process.wait(timeout=2)
        terminated_session = self.store.load_session(session.session_id)
        terminated_runtime = self.store.load_runtime(terminated_session)
        self.assertEqual("terminated", terminated_session.status)
        self.assertEqual("test_stop", terminated_session.termination_reason)
        self.assertTrue(result["killed_pid"])
        self.assertEqual("terminated", terminated_runtime.state)
        with self.assertRaisesRegex(ValueError, "terminated"):
            service.send_prompt("Do not run", session=terminated_session)
