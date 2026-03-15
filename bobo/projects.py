from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents import resolve_role_llm_settings
from .chat.service import ChatService
from .common import normalize_optional_string, render_bullets, slugify, utc_now
from .workspace import render_relative_path, resolve_workspace_root

PROJECT_FILENAME = "project.json"
PROJECT_HISTORY_FILENAME = "history.jsonl"


@dataclass
class ProjectHistoryRecord:
    kind: str
    summary: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProjectHistoryRecord":
        return cls(
            kind=str(payload["kind"]),
            summary=str(payload["summary"]),
            payload=dict(payload.get("payload", {})),
            created_at=str(payload["created_at"]),
        )


@dataclass
class ProjectRecord:
    project_id: str
    name: str
    summary: str
    end_result: str
    scope: str
    architecture: str
    tech_stack: str
    allowed_dependencies: list[str] = field(default_factory=list)
    style: str = ""
    compliance: str = ""
    notes: str = ""
    status: str = "draft"
    latest_plan: str = ""
    plan_revision: int = 0
    planner_session_id: str | None = None
    approved_at: str | None = None
    ready_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "summary": self.summary,
            "end_result": self.end_result,
            "scope": self.scope,
            "architecture": self.architecture,
            "tech_stack": self.tech_stack,
            "allowed_dependencies": self.allowed_dependencies,
            "style": self.style,
            "compliance": self.compliance,
            "notes": self.notes,
            "status": self.status,
            "latest_plan": self.latest_plan,
            "plan_revision": self.plan_revision,
            "planner_session_id": self.planner_session_id,
            "approved_at": self.approved_at,
            "ready_at": self.ready_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProjectRecord":
        return cls(
            project_id=str(payload["project_id"]),
            name=str(payload["name"]),
            summary=str(payload["summary"]),
            end_result=str(payload["end_result"]),
            scope=str(payload["scope"]),
            architecture=str(payload["architecture"]),
            tech_stack=str(payload["tech_stack"]),
            allowed_dependencies=list(payload.get("allowed_dependencies", [])),
            style=str(payload.get("style", "")),
            compliance=str(payload.get("compliance", "")),
            notes=str(payload.get("notes", "")),
            status=str(payload.get("status", "draft")),
            latest_plan=str(payload.get("latest_plan", "")),
            plan_revision=int(payload.get("plan_revision", 0)),
            planner_session_id=normalize_optional_string(payload.get("planner_session_id")),
            approved_at=normalize_optional_string(payload.get("approved_at")),
            ready_at=normalize_optional_string(payload.get("ready_at")),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


class ProjectStore:
    def __init__(self, workspace_root: str | Path, storage_dir: str | Path | None = None) -> None:
        self.workspace_root = resolve_workspace_root(workspace_root)
        self.storage_dir = Path(storage_dir or (self.workspace_root / ".bobo/projects")).resolve(strict=False)

    def ensure_storage_dir(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def render_storage_path(self) -> str:
        return render_relative_path(self.storage_dir, self.workspace_root)

    def create_project(
        self,
        *,
        name: str,
        summary: str,
        end_result: str,
        scope: str,
        architecture: str,
        tech_stack: str,
        allowed_dependencies: list[str],
        style: str,
        compliance: str,
        notes: str,
    ) -> ProjectRecord:
        self.ensure_storage_dir()
        timestamp = utc_now().replace("+00:00", "Z").replace("-", "").replace(":", "")
        timestamp = timestamp.replace(".000000", "")
        base_id = f"{timestamp}_{slugify(name)[:48] or 'project'}"
        project_id = base_id
        suffix = 2
        while self._project_dir(project_id).exists():
            project_id = f"{base_id}-{suffix}"
            suffix += 1
        project_dir = self._project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=False)
        now = utc_now()
        project = ProjectRecord(
            project_id=project_id,
            name=name,
            summary=summary,
            end_result=end_result,
            scope=scope,
            architecture=architecture,
            tech_stack=tech_stack,
            allowed_dependencies=allowed_dependencies,
            style=style,
            compliance=compliance,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        self.write_project(project)
        self.append_history(
            project,
            kind="project_created",
            summary="Created project brief.",
            payload={"project_id": project.project_id, "name": project.name},
        )
        return project

    def list_projects(self) -> list[ProjectRecord]:
        self.ensure_storage_dir()
        projects: list[ProjectRecord] = []
        for child in sorted(self.storage_dir.iterdir(), key=lambda item: item.name, reverse=True):
            if not child.is_dir():
                continue
            project_path = child / PROJECT_FILENAME
            if not project_path.exists():
                continue
            projects.append(ProjectRecord.from_dict(json.loads(project_path.read_text(encoding="utf-8"))))
        projects.sort(key=lambda item: item.updated_at, reverse=True)
        return projects

    def load_project(self, project_id: str) -> ProjectRecord:
        project_path = self._project_file(project_id)
        if not project_path.exists():
            raise ValueError(f"Unknown project: {project_id}")
        return ProjectRecord.from_dict(json.loads(project_path.read_text(encoding="utf-8")))

    def write_project(self, project: ProjectRecord) -> ProjectRecord:
        project.updated_at = utc_now()
        path = self._project_file(project.project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(project.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return project

    def append_history(
        self,
        project: ProjectRecord,
        *,
        kind: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> ProjectHistoryRecord:
        record = ProjectHistoryRecord(
            kind=kind,
            summary=summary,
            payload=payload or {},
            created_at=utc_now(),
        )
        path = self._history_file(project.project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True))
            handle.write("\n")
        project.updated_at = record.created_at
        self.write_project(project)
        return record

    def read_history(self, project: ProjectRecord) -> list[ProjectHistoryRecord]:
        path = self._history_file(project.project_id)
        if not path.exists():
            return []
        records: list[ProjectHistoryRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(ProjectHistoryRecord.from_dict(payload))
        return records

    def _project_dir(self, project_id: str) -> Path:
        return self.storage_dir / project_id

    def _project_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / PROJECT_FILENAME

    def _history_file(self, project_id: str) -> Path:
        return self._project_dir(project_id) / PROJECT_HISTORY_FILENAME


class ProjectService:
    def __init__(
        self,
        store: ProjectStore,
        chat_service: ChatService,
        team_config: dict[str, Any] | None = None,
        planner_role_name: str = "Planner",
    ) -> None:
        self.store = store
        self.chat_service = chat_service
        self.team_config = team_config
        self.planner_role_name = planner_role_name

    def list_projects(self) -> list[ProjectRecord]:
        return self.store.list_projects()

    def create_project(
        self,
        *,
        name: str,
        summary: str,
        end_result: str,
        scope: str,
        architecture: str,
        tech_stack: str,
        allowed_dependencies: list[str],
        style: str,
        compliance: str,
        notes: str,
    ) -> ProjectRecord:
        return self.store.create_project(
            name=name,
            summary=summary,
            end_result=end_result,
            scope=scope,
            architecture=architecture,
            tech_stack=tech_stack,
            allowed_dependencies=allowed_dependencies,
            style=style,
            compliance=compliance,
            notes=notes,
        )

    def update_project_brief(
        self,
        project: ProjectRecord,
        *,
        name: str,
        summary: str,
        end_result: str,
        scope: str,
        architecture: str,
        tech_stack: str,
        allowed_dependencies: list[str],
        style: str,
        compliance: str,
        notes: str,
    ) -> ProjectRecord:
        changed_fields: list[str] = []
        updates = {
            "name": name,
            "summary": summary,
            "end_result": end_result,
            "scope": scope,
            "architecture": architecture,
            "tech_stack": tech_stack,
            "allowed_dependencies": allowed_dependencies,
            "style": style,
            "compliance": compliance,
            "notes": notes,
        }
        for field_name, value in updates.items():
            if getattr(project, field_name) != value:
                setattr(project, field_name, value)
                changed_fields.append(field_name)

        if not changed_fields:
            return project

        if project.plan_revision > 0:
            project.status = "draft"
            project.approved_at = None
            project.ready_at = None

        self.store.write_project(project)
        if project.planner_session_id:
            session = self.chat_service.store.load_session(project.planner_session_id)
            planner_title = f"Planner - {project.name}"
            if session.title != planner_title:
                self.chat_service.update_session_title(session, planner_title)
        self.store.append_history(
            project,
            kind="project_brief_updated",
            summary="Updated the project brief.",
            payload={
                "changed_fields": changed_fields,
                "plan_revision": project.plan_revision,
                "status": project.status,
            },
        )
        return project

    def load_project(self, project_id: str) -> ProjectRecord:
        return self.store.load_project(project_id)

    def plan_project(
        self,
        project: ProjectRecord,
        feedback: str | None = None,
    ) -> ProjectRecord:
        planner_role = self._planner_role()
        llm_settings = resolve_role_llm_settings(
            planner_role,
            self.chat_service.workspace_settings,
        )
        if project.planner_session_id:
            session = self.chat_service.store.load_session(project.planner_session_id)
        else:
            session = self.chat_service.create_session(
                title=f"Planner - {project.name}",
                provider=llm_settings["provider"],
                model=llm_settings["model"],
                region_name=llm_settings["region_name"],
                profile_name=llm_settings["profile_name"],
                provider_options=llm_settings["provider_options"],
            )
            self.chat_service.store.append_message(
                session,
                "system",
                self._build_planner_system_prompt(planner_role),
            )

        prompt = self._build_planner_user_prompt(project, feedback)
        session, _, assistant = self.chat_service.send_prompt(
            prompt,
            session=session,
            provider=llm_settings["provider"],
            model=llm_settings["model"],
            region_name=llm_settings["region_name"],
            profile_name=llm_settings["profile_name"],
        )
        project.planner_session_id = session.session_id
        project.latest_plan = assistant.content
        project.plan_revision += 1
        project.status = "awaiting_review"
        self.store.write_project(project)
        self.store.append_history(
            project,
            kind="plan_generated" if feedback is None else "plan_revised",
            summary="Planner generated a project plan." if feedback is None else "Planner revised the project plan.",
            payload={
                "planner_session_id": session.session_id,
                "plan_revision": project.plan_revision,
                "feedback": feedback or "",
            },
        )
        return project

    def approve_plan(self, project: ProjectRecord) -> ProjectRecord:
        if not project.latest_plan.strip():
            raise ValueError("Generate a planner-authored plan before approving it.")
        project.status = "approved"
        project.approved_at = utc_now()
        self.store.write_project(project)
        self.store.append_history(
            project,
            kind="plan_approved",
            summary="Approved the current project plan.",
            payload={"plan_revision": project.plan_revision},
        )
        return project

    def proceed_with_plan(self, project: ProjectRecord) -> ProjectRecord:
        if not project.latest_plan.strip():
            raise ValueError("Generate and approve a plan before proceeding.")
        if project.status != "approved":
            raise ValueError("Approve the current plan before proceeding.")
        project.status = "ready"
        project.ready_at = utc_now()
        self.store.write_project(project)
        self.store.append_history(
            project,
            kind="plan_ready",
            summary="Marked the plan ready to execute.",
            payload={"plan_revision": project.plan_revision},
        )
        return project

    def project_history(self, project: ProjectRecord) -> list[ProjectHistoryRecord]:
        return self.store.read_history(project)

    def _planner_role(self) -> dict[str, Any]:
        if self.team_config is None:
            raise ValueError("Project planning requires a team config with a Planner role.")
        for role in self.team_config["roles"]:
            if role["name"] == self.planner_role_name:
                return role
        raise ValueError(f"Planner role {self.planner_role_name!r} was not found in the team config.")

    def _build_planner_system_prompt(self, planner_role: dict[str, Any]) -> str:
        role_summary = planner_role["summary"]
        responsibilities = render_bullets(planner_role["responsibilities"])
        instructions = render_bullets(planner_role.get("instructions", []))
        return (
            f"You are the {planner_role['name']} for this software project.\n"
            f"Role summary: {role_summary}\n"
            "Produce an actionable project plan that is explicit, concrete, and easy to review.\n"
            "Always include these sections: Vision, Scope, End Result, Architecture, Tech Stack, "
            "Allowed Dependencies, Style, Compliance, Risks, Milestones, and Initial Execution Packets.\n"
            "If important details are missing, state assumptions clearly instead of hiding them.\n"
            f"Responsibilities:\n{responsibilities}\n"
            f"Extra instructions:\n{instructions}"
        )

    def _build_planner_user_prompt(self, project: ProjectRecord, feedback: str | None) -> str:
        sections = [
            f"Project name: {project.name}",
            f"Project summary: {project.summary}",
            f"Intended end result: {project.end_result}",
            f"Scope: {project.scope}",
            f"Architecture constraints or preferences: {project.architecture}",
            f"Tech stack: {project.tech_stack}",
            f"Allowed dependencies: {', '.join(project.allowed_dependencies) if project.allowed_dependencies else 'None specified'}",
            f"Style guidance: {project.style or 'None specified'}",
            f"Compliance requirements: {project.compliance or 'None specified'}",
            f"Additional notes: {project.notes or 'None specified'}",
        ]
        if feedback:
            sections.append(f"Revision feedback from the user: {feedback}")
            sections.append("Revise the plan directly and address the feedback explicitly.")
        else:
            sections.append("Create the initial project plan for user review.")
        return "\n".join(sections)
