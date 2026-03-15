from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bobo
from bobo.projects import ProjectRecord, ProjectService, ProjectStore
from bobo.providers.base import ChatResult, ProviderRegistry


class FakePlannerProvider:
    def __init__(self) -> None:
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return ChatResult(
            provider=request.provider,
            model=request.model,
            message={
                "role": "assistant",
                "content": f"plan for: {request.messages[-1]['content']}",
                "raw": {"message_count": len(request.messages)},
            },
            stop_reason="end_turn",
            request_id=f"req-{len(self.requests)}",
        )


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.settings = bobo.load_workspace_settings(self.root)
        self.chat_store = bobo.ChatStore(
            self.root,
            bobo.resolve_chat_storage_dir(self.root, self.settings),
        )
        self.registry = ProviderRegistry()
        self.fake_provider = FakePlannerProvider()
        self.registry.register("openrouter", self.fake_provider)
        self.chat_service = bobo.ChatService(self.chat_store, self.settings, registry=self.registry)
        self.project_store = ProjectStore(self.root)
        self.team_config = {
            "roles": [
                {
                    "name": "Planner",
                    "summary": "Breaks the work into a plan.",
                    "responsibilities": ["Plan the architecture and milestones."],
                    "instructions": ["Keep the plan explicit and reviewable."],
                    "llm": {
                        "provider": "openrouter",
                        "model": "anthropic/test-planner",
                        "provider_options": {"app_name": "planner-test"},
                    },
                }
            ]
        }
        self.project_service = ProjectService(
            self.project_store,
            self.chat_service,
            team_config=self.team_config,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_project(self):
        return self.project_service.create_project(
            name="Rocket Planner",
            summary="Build a guided planning flow.",
            end_result="mvp",
            scope="Terminal launcher plus planner-first approval flow.",
            architecture="Package-backed CLI/TUI with persistent storage.",
            tech_stack="Python, Textual, boto3",
            allowed_dependencies=["textual", "boto3"],
            style="Readable, explicit, review-friendly.",
            compliance="None",
            notes="Planner should go first.",
        )

    def test_plan_project_uses_planner_role_llm_settings(self) -> None:
        project = self._create_project()

        planned = self.project_service.plan_project(project)

        self.assertEqual("awaiting_review", planned.status)
        self.assertEqual(1, planned.plan_revision)
        self.assertIsNotNone(planned.planner_session_id)
        self.assertTrue(planned.latest_plan.startswith("plan for:"))

        session = self.chat_store.load_session(planned.planner_session_id or "")
        self.assertEqual("openrouter", session.provider)
        self.assertEqual("anthropic/test-planner", session.model)
        self.assertEqual("planner-test", session.provider_options["app_name"])
        self.assertEqual(1, len(self.fake_provider.requests))
        self.assertEqual("system", self.fake_provider.requests[0].messages[0]["role"])
        self.assertEqual("user", self.fake_provider.requests[0].messages[-1]["role"])
        self.assertIn("Allowed dependencies: textual, boto3", self.fake_provider.requests[0].messages[-1]["content"])

    def test_update_project_brief_invalidates_prior_approval_and_renames_planner_session(self) -> None:
        project = self._create_project()
        project = self.project_service.plan_project(project)
        project = self.project_service.approve_plan(project)

        updated = self.project_service.update_project_brief(
            project,
            name="Rocket Planner v2",
            summary="Build a guided planning flow with revisions.",
            end_result="production",
            scope="Terminal launcher plus planner-first approval and proceed flow.",
            architecture="Package-backed CLI/TUI with session stores.",
            tech_stack="Python, Textual, boto3, sqlite",
            allowed_dependencies=["textual", "boto3", "sqlite3"],
            style="Readable, explicit, review-friendly.",
            compliance="SOC 2 awareness",
            notes="Planner should go first and revisions should reset approval.",
        )

        self.assertEqual("draft", updated.status)
        self.assertIsNone(updated.approved_at)
        self.assertIsNone(updated.ready_at)
        self.assertEqual(1, updated.plan_revision)
        session = self.chat_store.load_session(updated.planner_session_id or "")
        self.assertEqual("Planner - Rocket Planner v2", session.title)

        history_kinds = [item.kind for item in self.project_service.project_history(updated)]
        self.assertIn("project_brief_updated", history_kinds)

    def test_planner_revision_and_proceed_flow_require_review_then_approval(self) -> None:
        project = self._create_project()

        with self.assertRaisesRegex(ValueError, "planner-authored plan"):
            self.project_service.approve_plan(project)

        project = self.project_service.plan_project(project)
        session_id = project.planner_session_id

        with self.assertRaisesRegex(ValueError, "Approve the current plan"):
            self.project_service.proceed_with_plan(project)

        revised = self.project_service.plan_project(project, feedback="Narrow the scope and cut nice-to-haves.")

        self.assertEqual(2, revised.plan_revision)
        self.assertEqual(session_id, revised.planner_session_id)
        self.assertEqual(2, len(self.fake_provider.requests))
        self.assertIn("Revision feedback from the user", self.fake_provider.requests[-1].messages[-1]["content"])

        approved = self.project_service.approve_plan(revised)
        ready = self.project_service.proceed_with_plan(approved)

        self.assertEqual("ready", ready.status)
        history_kinds = [item.kind for item in self.project_service.project_history(ready)]
        self.assertEqual(
            [
                "project_created",
                "plan_generated",
                "plan_revised",
                "plan_approved",
                "plan_ready",
            ],
            history_kinds,
        )

    def test_project_record_from_dict_normalizes_missing_optional_fields(self) -> None:
        record = ProjectRecord.from_dict(
            {
                "project_id": "20260314T161139Z_bandcms",
                "name": "BandCMS",
                "summary": "CMS for music artists.",
                "end_result": "mvp",
                "scope": "Functional CMS.",
                "architecture": "Monolith.",
                "tech_stack": "FastAPI + SQLite + jinja",
                "allowed_dependencies": ["fastapi", "jinja2", "sqlmodel"],
                "style": "Make it hip.",
                "compliance": "none",
                "notes": "Do it up",
                "status": "draft",
                "latest_plan": "",
                "plan_revision": 0,
                "planner_session_id": None,
                "approved_at": None,
                "ready_at": "None",
                "created_at": "2026-03-14T16:11:39+00:00",
                "updated_at": "2026-03-14T16:11:39+00:00",
            }
        )

        self.assertIsNone(record.planner_session_id)
        self.assertIsNone(record.approved_at)
        self.assertIsNone(record.ready_at)

    def test_project_store_load_project_treats_legacy_none_string_as_missing_session(self) -> None:
        self.project_store.ensure_storage_dir()
        project_dir = self.project_store.storage_dir / "legacy-bandcms"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "project.json").write_text(
            """{
  "project_id": "legacy-bandcms",
  "name": "BandCMS",
  "summary": "CMS for music artists.",
  "end_result": "mvp",
  "scope": "Functional CMS.",
  "architecture": "Monolith.",
  "tech_stack": "FastAPI + SQLite + jinja",
  "allowed_dependencies": ["fastapi", "jinja2", "sqlmodel"],
  "style": "Make it hip.",
  "compliance": "none",
  "notes": "Do it up",
  "status": "draft",
  "latest_plan": "",
  "plan_revision": 0,
  "planner_session_id": "None",
  "approved_at": null,
  "ready_at": null,
  "created_at": "2026-03-14T16:11:39+00:00",
  "updated_at": "2026-03-14T16:11:39+00:00"
}""",
            encoding="utf-8",
        )

        loaded = self.project_store.load_project("legacy-bandcms")

        self.assertIsNone(loaded.planner_session_id)
