from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bobo


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class BoboEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "software_team.json"
        self.db_path = self.root / ".bobo" / "handoffs.sqlite3"
        write_json(
            self.config_path,
            {
                "project_name": "bobo",
                "project_description": "Parallel software delivery with narrow agent packets.",
                "project_resources": ["README.md"],
                "output": {
                    "agents_dir": "generated_agents",
                    "database_path": str(self.db_path),
                },
                "execution_policy": {
                    "max_files_per_task": 1,
                    "max_functions_per_task": 3,
                    "failure_feedback_contract": "Report only compact test feedback.",
                },
                "roles": [
                    {
                        "name": "Planner",
                        "model_tier": "frontier",
                        "summary": "Plans narrow packets.",
                        "responsibilities": [
                            "Create one-file packets."
                        ],
                        "handoff_targets": ["Implementer"],
                        "task_details": "Plan the work."
                    },
                    {
                        "name": "Implementer",
                        "model_tier": "cheap",
                        "summary": "Implements one packet at a time.",
                        "responsibilities": [
                            "Change only the assigned file."
                        ],
                        "handoff_targets": ["Verifier"]
                    },
                    {
                        "name": "Verifier",
                        "model_tier": "cheap",
                        "summary": "Runs narrow validation.",
                        "responsibilities": [
                            "Report compact test feedback."
                        ],
                        "handoff_targets": ["Planner"]
                    }
                ]
            },
        )
        self.config = bobo.load_config(self.config_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_render_agents_writes_role_specific_files(self) -> None:
        written = bobo.write_agents(self.config, self.root)

        planner_path = Path(written["Planner"])
        implementer_path = Path(written["Implementer"])

        self.assertTrue(planner_path.exists())
        self.assertTrue(implementer_path.exists())

        planner_contents = planner_path.read_text(encoding="utf-8")
        implementer_contents = implementer_path.read_text(encoding="utf-8")

        self.assertIn("Your role is Planner.", planner_contents)
        self.assertIn("Plan the work.", planner_contents)
        self.assertIn("Your role is Implementer.", implementer_contents)
        self.assertIn('{"tool":"<tool_name>","args":{...}}', implementer_contents)
        self.assertIn("You do not have shell access.", implementer_contents)
        self.assertIn("Requires approval before execution.", implementer_contents)
        self.assertNotIn("python3 bobo.py", implementer_contents)

    def test_record_claim_and_complete_handoff_round_trip(self) -> None:
        payload = {
            "run_id": "run-001",
            "task_id": "task-001",
            "title": "Implement renderer",
            "summary": "Touch only the render path.",
            "from_role": "Planner",
            "to_role": "Implementer",
            "priority": 1,
            "file_scope": ["bobo.py"],
            "function_scope": ["render_agent_markdown"],
            "acceptance_criteria": ["Write one AGENTS.md per role."],
            "dependencies": [],
            "artifacts": ["generated_agents/implementer/AGENTS.md"],
            "test_status": "not_run",
            "test_command": "python3 -m unittest discover -v",
            "next_action": "Implement the change and hand off to Verifier."
        }

        recorded = bobo.record_handoff(self.db_path, self.config, payload)
        self.assertEqual("Planner", recorded["from_role"])
        self.assertTrue(self.db_path.exists())

        claimed = bobo.claim_next_handoff(self.db_path, "Implementer")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual("claimed", claimed["status"])
        self.assertIsNotNone(claimed["claimed_at"])

        completed = bobo.update_handoff_status(
            self.db_path,
            claimed["handoff_id"],
            "completed",
            "Merged and verified.",
        )
        self.assertEqual("completed", completed["status"])
        self.assertEqual("Merged and verified.", completed["resolution_note"])
        self.assertIsNotNone(completed["completed_at"])

    def test_record_handoff_enforces_scope_budget(self) -> None:
        payload = {
            "run_id": "run-001",
            "task_id": "task-overscoped",
            "title": "Too broad",
            "summary": "This should fail validation.",
            "from_role": "Planner",
            "to_role": "Implementer",
            "file_scope": ["bobo.py", "README.md"],
            "function_scope": ["render_agent_markdown"],
            "acceptance_criteria": ["Stay in one file."],
            "dependencies": [],
            "artifacts": [],
            "test_status": "not_run",
            "next_action": "Split the task."
        }

        with self.assertRaisesRegex(ValueError, "configured limit of 1 file"):
            bobo.record_handoff(self.db_path, self.config, payload)

    def test_failed_handoff_requires_top_stack_frame(self) -> None:
        payload = {
            "run_id": "run-001",
            "task_id": "task-failing-tests",
            "title": "Verifier feedback",
            "summary": "Tests failed.",
            "from_role": "Verifier",
            "to_role": "Planner",
            "file_scope": ["bobo.py"],
            "function_scope": ["render_agent_markdown"],
            "acceptance_criteria": ["Fix the regression."],
            "dependencies": [],
            "artifacts": [],
            "test_status": "fail",
            "failure_summary": "Renderer omitted handoff instructions.",
            "next_action": "Re-plan the packet."
        }

        with self.assertRaisesRegex(ValueError, "top_stack_frame is required"):
            bobo.record_handoff(self.db_path, self.config, payload)

    def test_parse_agent_output_normalizes_compact_external_tool_call(self) -> None:
        parsed = bobo.parse_agent_output(
            '{"tool":"patch_code_file","args":{"path":"bobo.py","search":"old","replace":"new"}}',
            self.config,
            "Implementer",
        )

        self.assertEqual("patch_code_file", parsed["tool"])
        self.assertEqual("external", parsed["dispatch"])
        self.assertEqual("old", parsed["args"]["search_string"])
        self.assertEqual("new", parsed["args"]["replacement_string"])

    def test_dispatch_agent_output_claims_handoff_for_role(self) -> None:
        bobo.record_handoff(
            self.db_path,
            self.config,
            {
                "run_id": "run-001",
                "task_id": "task-claim",
                "title": "Implement renderer",
                "summary": "Touch only the render path.",
                "from_role": "Planner",
                "to_role": "Implementer",
                "file_scope": ["bobo.py"],
                "function_scope": ["render_agent_markdown"],
                "acceptance_criteria": ["Write one AGENTS.md per role."],
                "dependencies": [],
                "artifacts": [],
                "test_status": "not_run",
                "next_action": "Implement the packet."
            },
        )

        dispatched = bobo.dispatch_agent_output(
            self.db_path,
            self.config,
            "Implementer",
            '{"tool":"claim_handoff","args":{}}',
            base_path=self.root,
        )

        self.assertEqual("claim_handoff", dispatched["tool"])
        self.assertEqual("completed", dispatched["execution_status"])
        self.assertEqual("claimed", dispatched["result"]["status"])
        self.assertEqual("Implementer", dispatched["result"]["to_role"])

    def test_dispatch_agent_output_records_compact_handoff_call(self) -> None:
        dispatched = bobo.dispatch_agent_output(
            self.db_path,
            self.config,
            "Implementer",
            (
                '{"tool":"handoff","args":{"run_id":"run-001","task_id":"task-compact",'
                '"to":"Verifier","title":"Done","sum":"Implemented renderer",'
                '"files":["bobo.py"],"funcs":["render_agent_markdown"],'
                '"ok":["Writes one AGENTS.md per role."],"arts":["generated_agents/implementer/AGENTS.md"],'
                '"ts":"pass","tc":"python3 -m unittest discover -v","next":"Verify the renderer."}}'
            ),
            base_path=self.root,
        )

        self.assertEqual("handoff", dispatched["tool"])
        self.assertEqual("completed", dispatched["execution_status"])
        self.assertEqual("Implementer", dispatched["result"]["from_role"])
        self.assertEqual("Verifier", dispatched["result"]["to_role"])
        self.assertEqual(["bobo.py"], dispatched["result"]["file_scope"])

    def test_dispatch_agent_output_executes_external_file_tools(self) -> None:
        created = bobo.dispatch_agent_output(
            self.db_path,
            self.config,
            "Implementer",
            json.dumps(
                {
                    "tool": "create_file",
                    "args": {"path": "notes.txt", "content": "alpha"},
                }
            ),
            base_path=self.root,
        )

        self.assertEqual("create_file", created["tool"])
        self.assertEqual("completed", created["execution_status"])
        self.assertTrue((self.root / "notes.txt").exists())
        self.assertEqual("alpha", (self.root / "notes.txt").read_text(encoding="utf-8"))

        read_back = bobo.dispatch_agent_output(
            self.db_path,
            self.config,
            "Implementer",
            json.dumps({"tool": "read_file_or_directory", "args": {"path": "notes.txt"}}),
            base_path=self.root,
        )

        self.assertEqual("file", read_back["result"]["kind"])
        self.assertEqual("alpha", read_back["result"]["content"])

        patched = bobo.dispatch_agent_output(
            self.db_path,
            self.config,
            "Implementer",
            json.dumps(
                {
                    "tool": "patch_code_file",
                    "args": {"path": "notes.txt", "search": "alpha", "replace": "beta"},
                }
            ),
            base_path=self.root,
        )

        self.assertEqual("completed", patched["execution_status"])
        self.assertEqual(1, patched["result"]["replacements_applied"])
        self.assertEqual("beta", (self.root / "notes.txt").read_text(encoding="utf-8"))

    def test_dispatch_agent_output_runs_commands_without_shell_wrapping(self) -> None:
        dispatched = bobo.dispatch_agent_output(
            self.db_path,
            self.config,
            "Verifier",
            json.dumps(
                {
                    "tool": "run_linter_and_tests",
                    "args": {
                        "argv": [sys.executable, "-c", "print('bobo-ok')"],
                    },
                }
            ),
            base_path=self.root,
        )

        self.assertEqual("completed", dispatched["execution_status"])
        self.assertTrue(dispatched["result"]["ok"])
        self.assertIn("bobo-ok", dispatched["result"]["stdout"])

    def test_dispatch_agent_output_requires_approval_for_dependency_changes(self) -> None:
        dispatched = bobo.dispatch_agent_output(
            self.db_path,
            self.config,
            "Implementer",
            json.dumps(
                {
                    "tool": "manage_dependencies",
                    "args": {"pm": "pip", "act": "install", "pkgs": ["requests"]},
                }
            ),
            base_path=self.root,
        )

        self.assertEqual("manage_dependencies", dispatched["tool"])
        self.assertEqual("approval_required", dispatched["execution_status"])
        self.assertTrue(dispatched["approval"]["required"])
        self.assertFalse(dispatched["approval"]["approved"])
        self.assertIsNone(dispatched["result"])

    def test_dispatch_agent_output_rejects_paths_outside_workspace(self) -> None:
        with self.assertRaisesRegex(ValueError, "path escapes the workspace root"):
            bobo.dispatch_agent_output(
                self.db_path,
                self.config,
                "Implementer",
                json.dumps(
                    {
                        "tool": "read_file_or_directory",
                        "args": {"path": "../outside.txt"},
                    }
                ),
                base_path=self.root,
            )


class LLMHarnessTests(unittest.TestCase):
    def test_build_llm_request_from_prompt_args(self) -> None:
        args = bobo.parse_args(
            [
                "llm-complete",
                "--provider",
                "bedrock",
                "--model",
                "anthropic.claude-3-5-sonnet-20240620-v1:0",
                "--prompt",
                "Hello from bobo",
                "--system",
                "You are concise.",
                "--max-tokens",
                "128",
                "--temperature",
                "0.2",
                "--top-p",
                "0.9",
                "--stop-sequence",
                "STOP",
            ]
        )

        request = bobo.build_llm_request_from_args(args)

        self.assertEqual("bedrock", request["provider"])
        self.assertEqual("anthropic.claude-3-5-sonnet-20240620-v1:0", request["model"])
        self.assertEqual("system", request["messages"][0]["role"])
        self.assertEqual("You are concise.", request["messages"][0]["content"])
        self.assertEqual("user", request["messages"][1]["role"])
        self.assertEqual("Hello from bobo", request["messages"][1]["content"])
        self.assertEqual(128, request["max_tokens"])
        self.assertEqual(0.2, request["temperature"])
        self.assertEqual(0.9, request["top_p"])
        self.assertEqual(["STOP"], request["stop_sequences"])

    def test_llm_complete_bedrock_uses_converse(self) -> None:
        class FakeBedrockClient:
            def __init__(self) -> None:
                self.last_request: dict | None = None

            def converse(self, **kwargs):
                self.last_request = kwargs
                return {
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [{"text": "Bedrock reply"}],
                        }
                    },
                    "stopReason": "end_turn",
                    "usage": {"inputTokens": 10, "outputTokens": 4, "totalTokens": 14},
                    "metrics": {"latencyMs": 123},
                    "ResponseMetadata": {"RequestId": "req-123"},
                }

        class FakeSession:
            def __init__(self, client: FakeBedrockClient, **session_kwargs) -> None:
                self.client_impl = client
                self.session_kwargs = session_kwargs
                self.client_calls: list[tuple[str, dict]] = []

            def client(self, service_name: str, **client_kwargs):
                self.client_calls.append((service_name, client_kwargs))
                return self.client_impl

        class FakeBoto3:
            def __init__(self) -> None:
                self.client_impl = FakeBedrockClient()
                self.created_sessions: list[FakeSession] = []
                self.session = self

            def Session(self, **session_kwargs):
                session = FakeSession(self.client_impl, **session_kwargs)
                self.created_sessions.append(session)
                return session

        fake_boto3 = FakeBoto3()
        with mock.patch("bobo.importlib.import_module", return_value=fake_boto3):
            response = bobo.llm_complete(
                {
                    "provider": "bedrock",
                    "model": "anthropic.claude-3-5-sonnet-20240620-v1:0",
                    "messages": [
                        {"role": "system", "content": "You are concise."},
                        {"role": "user", "content": "Say hello."},
                    ],
                    "max_tokens": 64,
                    "temperature": 0.1,
                    "top_p": 0.8,
                    "stop_sequences": ["STOP"],
                    "region_name": "us-east-1",
                    "profile_name": "default",
                    "provider_options": {
                        "converse_kwargs": {"additionalModelResponseFieldPaths": []}
                    },
                }
            )

        self.assertEqual("bedrock", response["provider"])
        self.assertEqual("Bedrock reply", response["message"]["content"])
        self.assertEqual("req-123", response["request_id"])
        self.assertEqual(1, len(fake_boto3.created_sessions))
        session = fake_boto3.created_sessions[0]
        self.assertEqual({"profile_name": "default"}, session.session_kwargs)
        self.assertEqual("bedrock-runtime", session.client_calls[0][0])
        self.assertEqual({"region_name": "us-east-1"}, session.client_calls[0][1])
        self.assertIsNotNone(fake_boto3.client_impl.last_request)
        assert fake_boto3.client_impl.last_request is not None
        self.assertEqual(
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
            fake_boto3.client_impl.last_request["modelId"],
        )
        self.assertEqual(
            [{"text": "You are concise."}],
            fake_boto3.client_impl.last_request["system"],
        )
        self.assertEqual(
            [{"role": "user", "content": [{"text": "Say hello."}]}],
            fake_boto3.client_impl.last_request["messages"],
        )
        self.assertEqual(
            64,
            fake_boto3.client_impl.last_request["inferenceConfig"]["maxTokens"],
        )
        self.assertEqual(
            0.1,
            fake_boto3.client_impl.last_request["inferenceConfig"]["temperature"],
        )
        self.assertEqual(
            0.8,
            fake_boto3.client_impl.last_request["inferenceConfig"]["topP"],
        )
        self.assertEqual(
            ["STOP"],
            fake_boto3.client_impl.last_request["inferenceConfig"]["stopSequences"],
        )


if __name__ == "__main__":
    unittest.main()
