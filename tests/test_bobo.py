from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
        )

        self.assertEqual("claim_handoff", dispatched["tool"])
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
        )

        self.assertEqual("handoff", dispatched["tool"])
        self.assertEqual("Implementer", dispatched["result"]["from_role"])
        self.assertEqual("Verifier", dispatched["result"]["to_role"])
        self.assertEqual(["bobo.py"], dispatched["result"]["file_scope"])


if __name__ == "__main__":
    unittest.main()
