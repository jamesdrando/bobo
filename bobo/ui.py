from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
import threading

from .chat.models import ChatSession
from .chat.service import ChatService
from .projects import ProjectRecord, ProjectService

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

    TEXTUAL_AVAILABLE = True
except ModuleNotFoundError:
    TEXTUAL_AVAILABLE = False


def _resolve_bobo_version() -> str:
    try:
        return version("bobo")
    except PackageNotFoundError:
        return "0.1.0"


@dataclass
class ChatLaunchOptions:
    resume: str | None = None
    title: str | None = None
    team_config_path: str | None = None
    provider: str | None = None
    model: str | None = None
    region: str | None = None
    profile: str | None = None


if TEXTUAL_AVAILABLE:
    class LaunchOptionItem(ListItem):
        def __init__(self, key: str, label: str) -> None:
            self.key = key
            super().__init__(Label(label))


    class SessionListItem(ListItem):
        def __init__(self, session: ChatSession) -> None:
            self.session = session
            status_suffix = " (terminated)" if session.status == "terminated" else ""
            label = f"{session.title}{status_suffix} [{session.session_id}]"
            super().__init__(Label(label))


    class ProjectListItem(ListItem):
        def __init__(self, project: ProjectRecord) -> None:
            self.project = project
            label = f"{project.name} [{project.status}]"
            super().__init__(Label(label))


    class BoboChatApp(App[None]):
        CSS = """
        Screen {
            layout: vertical;
        }

        #body {
            height: 1fr;
        }

        #sidebar {
            width: 40;
            min-width: 30;
            border: round $panel;
            padding: 1;
        }

        #main {
            width: 1fr;
            padding: 1;
        }

        #log_section {
            height: 16;
            min-height: 12;
            margin-bottom: 1;
        }

        #workspace_section {
            height: 1fr;
            overflow-y: auto;
        }

        #project_form, #meta-bar, #composer, #project_actions {
            height: auto;
            margin-top: 1;
        }

        #brand, #launch_help, #workflow_help, #storage_path, #planner_callout {
            margin-bottom: 1;
        }

        #transcript, #activity {
            height: 1fr;
            border: round $panel;
            min-height: 4;
        }

        .pane_title {
            margin-bottom: 0;
            text-style: bold;
        }

        Input {
            margin-right: 1;
        }
        """

        BINDINGS = [
            ("ctrl+r", "resend_last_prompt", "Resend"),
            ("ctrl+s", "save_metadata", "Save"),
            ("ctrl+k", "kill_session", "Kill"),
            ("ctrl+p", "ask_planner", "Plan"),
        ]

        def __init__(
            self,
            service: ChatService,
            options: ChatLaunchOptions,
            project_service: ProjectService | None = None,
        ) -> None:
            super().__init__()
            self.service = service
            self.options = options
            self.project_service = project_service
            self.active_session: ChatSession | None = None
            self.active_project: ProjectRecord | None = None
            self.browser_mode = "projects"
            self._send_thread: threading.Thread | None = None

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id="body"):
                with Vertical(id="sidebar"):
                    yield Static(f"bobo\n------\nversion {_resolve_bobo_version()}", id="brand")
                    yield Static("Select an option\nUse Up/Down and Enter.", id="launch_help")
                    yield ListView(id="launch_menu")
                    yield Static("Items")
                    yield ListView(id="item_list")
                    yield Static("", id="storage_path")
                with Vertical(id="main"):
                    yield Static("Planner-first launcher", id="view_title")
                    with Vertical(id="log_section"):
                        yield Static("Transcript", classes="pane_title")
                        yield RichLog(id="transcript", wrap=True, markup=False)
                        yield Static("Activity", classes="pane_title")
                        yield RichLog(id="activity", wrap=True, markup=False)
                    with Vertical(id="workspace_section"):
                        yield Static(
                            "Project intake checklist: clarify the project, target outcome, scope, architecture, tech stack, allowed deps, style, and compliance. Then ask Planner for the first plan.",
                            id="workflow_help",
                        )
                        yield Static(
                            "Primary action: focus `Ask Planner (Ctrl+P)` and press Enter, or press Ctrl+P from anywhere.",
                            id="planner_callout",
                        )
                        with Vertical(id="project_form"):
                            yield Input(placeholder="Project name", id="project_name")
                            yield Input(placeholder="One-line summary", id="project_summary")
                            yield Input(placeholder="End result: toy, demo, mvp, production", id="project_end_result")
                            yield Input(placeholder="Scope", id="project_scope")
                            yield Input(placeholder="Architecture", id="project_architecture")
                            yield Input(placeholder="Tech stack", id="project_stack")
                            yield Input(placeholder="Allowed deps (comma separated)", id="project_allowed_deps")
                            yield Input(placeholder="Style guidance", id="project_style")
                            yield Input(placeholder="Compliance, law, standards", id="project_compliance")
                            yield Input(placeholder="Extra notes (press Enter here to ask Planner)", id="project_notes")
                            yield Input(placeholder="Feedback for planner revisions", id="project_feedback")
                        with Horizontal(id="project_actions"):
                            yield Button("Create Project", id="create_project")
                            yield Button("Ask Planner (Ctrl+P)", id="ask_planner", variant="primary")
                            yield Button("Request Changes", id="request_changes", variant="warning")
                            yield Button("Approve Plan", id="approve_plan")
                            yield Button("Proceed", id="proceed_plan", variant="success")
                        with Horizontal(id="meta-bar"):
                            yield Input(placeholder="Session title", id="session_title")
                            yield Input(placeholder="Provider", id="session_provider")
                            yield Input(placeholder="Model", id="session_model")
                            yield Input(placeholder="Region", id="session_region")
                            yield Input(placeholder="Profile", id="session_profile")
                            yield Button("Save", id="save_meta")
                            yield Button("Kill", id="kill_session", variant="error")
                        with Horizontal(id="composer"):
                            yield Input(placeholder="Type a prompt or planner follow-up", id="prompt")
                            yield Button("Send", id="send_prompt")
                            yield Button("Resend", id="resend_prompt")
                        yield Static("", id="session_status")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_launch_menu()
            self._update_storage_banner()
            self._apply_chat_defaults()
            self.query_one("#project_end_result", Input).value = "mvp"
            self.set_focus(self.query_one("#launch_menu", ListView))

            if self.options.resume or self.options.title:
                self.browser_mode = "chats"
                self._refresh_item_list()
                try:
                    self.active_session = self.service.prepare_session(
                        resume=self.options.resume,
                        title=self.options.title,
                        provider=self.options.provider,
                        model=self.options.model,
                        region_name=self.options.region,
                        profile_name=self.options.profile,
                    )
                except Exception as exc:
                    self._write_activity(f"Session load failed: {exc}")
                    return
                if self.active_session is not None:
                    self._sync_session_inputs(self.active_session)
                    self.set_focus(self.query_one("#prompt", Input))
            else:
                self.browser_mode = "projects"
                self._refresh_item_list()
            self._render_active_content()

        def _refresh_launch_menu(self) -> None:
            launch_menu = self.query_one("#launch_menu", ListView)
            launch_menu.clear()
            launch_menu.append(LaunchOptionItem("create_project", "Create project"))
            launch_menu.append(LaunchOptionItem("open_project", "Open project"))
            launch_menu.append(LaunchOptionItem("open_chat", "Open chat"))
            launch_menu.append(LaunchOptionItem("quit", "Quit"))

        def _refresh_item_list(self) -> None:
            item_list = self.query_one("#item_list", ListView)
            item_list.clear()
            if self.browser_mode == "projects":
                if self.project_service is None:
                    return
                for project in self.project_service.list_projects():
                    item_list.append(ProjectListItem(project))
                return
            for session in self.service.list_sessions():
                item_list.append(SessionListItem(session))

        def _update_storage_banner(self) -> None:
            pieces = [f"Chats: {self.service.store.render_storage_path()}"]
            if self.project_service is not None:
                pieces.append(f"Projects: {self.project_service.store.render_storage_path()}")
            self.query_one("#storage_path", Static).update(" | ".join(pieces))

        def _apply_chat_defaults(self) -> None:
            self.query_one("#session_provider", Input).value = (
                self.options.provider or self.service.workspace_settings.chat.default_provider
            )
            self.query_one("#session_model", Input).value = (
                self.options.model or self.service.workspace_settings.chat.default_model
            )
            self.query_one("#session_region", Input).value = (
                self.options.region or self.service.workspace_settings.bedrock.region or ""
            )
            self.query_one("#session_profile", Input).value = (
                self.options.profile or self.service.workspace_settings.bedrock.profile or ""
            )

        def _write_activity(self, line: str) -> None:
            self.query_one("#activity", RichLog).write(line)

        def _sync_session_inputs(self, session: ChatSession) -> None:
            self.query_one("#session_title", Input).value = session.title
            self.query_one("#session_provider", Input).value = session.provider
            self.query_one("#session_model", Input).value = session.model
            self.query_one("#session_region", Input).value = session.region_name or ""
            self.query_one("#session_profile", Input).value = session.profile_name or ""

        def _sync_project_inputs(self, project: ProjectRecord) -> None:
            self.query_one("#project_name", Input).value = project.name
            self.query_one("#project_summary", Input).value = project.summary
            self.query_one("#project_end_result", Input).value = project.end_result
            self.query_one("#project_scope", Input).value = project.scope
            self.query_one("#project_architecture", Input).value = project.architecture
            self.query_one("#project_stack", Input).value = project.tech_stack
            self.query_one("#project_allowed_deps", Input).value = ", ".join(project.allowed_dependencies)
            self.query_one("#project_style", Input).value = project.style
            self.query_one("#project_compliance", Input).value = project.compliance
            self.query_one("#project_notes", Input).value = project.notes

        def _clear_project_inputs(self) -> None:
            for input_id in [
                "#project_name",
                "#project_summary",
                "#project_scope",
                "#project_architecture",
                "#project_stack",
                "#project_allowed_deps",
                "#project_style",
                "#project_compliance",
                "#project_notes",
                "#project_feedback",
            ]:
                self.query_one(input_id, Input).value = ""
            self.query_one("#project_end_result", Input).value = "mvp"

        def _render_active_content(self) -> None:
            transcript_log = self.query_one("#transcript", RichLog)
            activity_log = self.query_one("#activity", RichLog)
            transcript_log.clear()
            activity_log.clear()
            transcript_has_content = False
            activity_has_content = False

            if self.active_project is not None and self.active_session is None:
                transcript_log.write(f"project: {self.active_project.name}")
                transcript_log.write(f"summary: {self.active_project.summary}")
                transcript_log.write(f"end result: {self.active_project.end_result}")
                transcript_log.write("")
                transcript_log.write("next step: Ask Planner to produce the initial plan.")
                transcript_has_content = True
            if self.active_session is not None:
                for message in self.service.transcript(self.active_session):
                    transcript_log.write(f"{message.role}: {message.content}")
                    transcript_has_content = True
            if self.active_project is not None and self.project_service is not None:
                for record in self.project_service.project_history(self.active_project):
                    activity_log.write(f"{record.kind}: {record.summary}")
                    activity_has_content = True
            if self.active_session is not None:
                for event in self.service.events(self.active_session):
                    activity_log.write(f"{event.kind}: {event.summary}")
                    activity_has_content = True
            if not transcript_has_content:
                transcript_log.write("Transcript will appear here.")
            if not activity_has_content:
                activity_log.write("Activity and errors will appear here.")

            self._update_view_title()
            self._update_session_status()

        def _update_view_title(self) -> None:
            title = "Create or open a project"
            if self.active_project is not None:
                title = f"Project: {self.active_project.name}"
            elif self.browser_mode == "chats" and self.active_session is not None:
                title = f"Chat: {self.active_session.title}"
            elif self.browser_mode == "chats":
                title = "Open chat"
            self.query_one("#view_title", Static).update(title)

        def _update_session_status(self) -> None:
            status_widget = self.query_one("#session_status", Static)
            if self.active_project is not None:
                next_step = "Ask Planner"
                if self.active_project.status == "awaiting_review":
                    next_step = "Review, request changes, or approve"
                elif self.active_project.status == "approved":
                    next_step = "Proceed with plan"
                elif self.active_project.status == "ready":
                    next_step = "Execution can begin"
                status_widget.update(
                    f"Project: {self.active_project.project_id} | Status: {self.active_project.status} | Revision: {self.active_project.plan_revision} | Next: {next_step}"
                )
                return
            if self.active_session is None:
                status_widget.update("No active session.")
                return
            status_widget.update(
                f"Session: {self.active_session.session_id} | Provider: {self.active_session.provider} | Status: {self.active_session.status}"
            )

        def _collect_project_form(self) -> dict[str, object]:
            allowed_deps_raw = self.query_one("#project_allowed_deps", Input).value
            allowed_dependencies = [
                item.strip()
                for item in allowed_deps_raw.replace("\n", ",").split(",")
                if item.strip()
            ]
            return {
                "name": self.query_one("#project_name", Input).value.strip(),
                "summary": self.query_one("#project_summary", Input).value.strip(),
                "end_result": self.query_one("#project_end_result", Input).value.strip(),
                "scope": self.query_one("#project_scope", Input).value.strip(),
                "architecture": self.query_one("#project_architecture", Input).value.strip(),
                "tech_stack": self.query_one("#project_stack", Input).value.strip(),
                "allowed_dependencies": allowed_dependencies,
                "style": self.query_one("#project_style", Input).value.strip(),
                "compliance": self.query_one("#project_compliance", Input).value.strip(),
                "notes": self.query_one("#project_notes", Input).value.strip(),
            }

        def _load_project(self, project: ProjectRecord) -> None:
            self.active_project = project
            self._sync_project_inputs(project)
            warning_message: str | None = None
            if project.planner_session_id:
                try:
                    self.active_session = self.service.store.load_session(project.planner_session_id)
                except ValueError:
                    self.active_session = None
                    self._apply_chat_defaults()
                    self.query_one("#session_title", Input).value = f"Planner - {project.name}"
                    warning_message = (
                        "The saved planner session could not be found. Ask Planner to generate a fresh plan."
                    )
                else:
                    self._sync_session_inputs(self.active_session)
            else:
                self.active_session = None
                self._apply_chat_defaults()
                self.query_one("#session_title", Input).value = f"Planner - {project.name}"
            self._render_active_content()
            if warning_message:
                self._write_activity(warning_message)

        def _save_or_create_project(self) -> ProjectRecord | None:
            if self.project_service is None:
                self._write_activity("Project creation requires a team config and project service.")
                return None
            payload = self._collect_project_form()
            if not payload["name"] or not payload["summary"]:
                self._write_activity("Project name and summary are required.")
                return None
            if self.active_project is None:
                project = self.project_service.create_project(**payload)
                activity = f"Created project {project.name}."
            else:
                project = self.project_service.update_project_brief(self.active_project, **payload)
                activity = f"Saved project brief for {project.name}."
            self.browser_mode = "projects"
            self._refresh_item_list()
            self._load_project(project)
            self._write_activity(activity)
            return project

        def _create_project(self) -> None:
            self._save_or_create_project()

        def _ensure_project(self) -> ProjectRecord | None:
            return self._save_or_create_project()

        def _save_metadata(self) -> None:
            if self.active_session is None:
                return
            session = self.service.update_session_title(
                self.active_session,
                self.query_one("#session_title", Input).value or self.active_session.title,
            )
            session = self.service.update_runtime_settings(
                session,
                provider=self.query_one("#session_provider", Input).value or session.provider,
                model=self.query_one("#session_model", Input).value or session.model,
                region_name=self.query_one("#session_region", Input).value or None,
                profile_name=self.query_one("#session_profile", Input).value or None,
            )
            self.active_session = session
            self._refresh_item_list()
            self._render_active_content()

        def _send_prompt(self) -> None:
            if self._send_thread is not None and self._send_thread.is_alive():
                self._write_activity("A provider request is already running.")
                return
            if self.active_project is not None and self.active_session is None:
                self._write_activity("Use Ask Planner first so the planner session owns the project thread.")
                return
            prompt_input = self.query_one("#prompt", Input)
            prompt = prompt_input.value.strip()
            if not prompt:
                return
            session_title = self.query_one("#session_title", Input).value or None
            provider = self.query_one("#session_provider", Input).value or None
            model = self.query_one("#session_model", Input).value or None
            region = self.query_one("#session_region", Input).value or None
            profile = self.query_one("#session_profile", Input).value or None
            self._write_activity("Provider request started.")
            prompt_input.value = ""
            self._send_thread = threading.Thread(
                target=self._run_send_prompt,
                args=(prompt, session_title, provider, model, region, profile),
                daemon=True,
            )
            self._send_thread.start()

        def _run_send_prompt(
            self,
            prompt: str,
            session_title: str | None,
            provider: str | None,
            model: str | None,
            region: str | None,
            profile: str | None,
        ) -> None:
            try:
                session, _, _ = self.service.send_prompt(
                    prompt,
                    session=self.active_session,
                    title=session_title,
                    provider=provider,
                    model=model,
                    region_name=region,
                    profile_name=profile,
                )
            except Exception as exc:
                self.call_from_thread(self._on_send_failed, str(exc))
                return
            self.call_from_thread(self._on_send_succeeded, session)

        def _on_send_succeeded(self, session: ChatSession) -> None:
            self.active_session = session
            self._sync_session_inputs(session)
            if self.active_project is not None and self.active_project.planner_session_id is None:
                self.active_project.planner_session_id = session.session_id
            self._refresh_item_list()
            self._render_active_content()

        def _on_send_failed(self, error: str) -> None:
            self._write_activity(error if error.startswith("Resend failed:") else f"Provider failure: {error}")
            if self.active_session is not None:
                self.active_session = self.service.store.load_session(self.active_session.session_id)
                self._refresh_item_list()
                self._render_active_content()

        def _run_plan_project(self, project_id: str, feedback: str | None) -> None:
            if self.project_service is None:
                self.call_from_thread(self._on_send_failed, "Planner flow is unavailable without a team config.")
                return
            try:
                project = self.project_service.load_project(project_id)
                updated_project = self.project_service.plan_project(project, feedback=feedback)
            except Exception as exc:
                prefix = "Planner revision failed" if feedback else "Planner run failed"
                self.call_from_thread(self._on_send_failed, f"{prefix}: {exc}")
                return
            self.call_from_thread(self._on_plan_succeeded, updated_project)

        def _on_plan_succeeded(self, project: ProjectRecord) -> None:
            self.active_project = project
            if project.planner_session_id:
                self.active_session = self.service.store.load_session(project.planner_session_id)
                self._sync_session_inputs(self.active_session)
            self.query_one("#project_feedback", Input).value = ""
            self._refresh_item_list()
            self._render_active_content()
            self._write_activity(f"Planner updated plan revision {project.plan_revision}.")

        def action_ask_planner(self) -> None:
            if self._send_thread is not None and self._send_thread.is_alive():
                self._write_activity("A provider request is already running.")
                return
            project = self._ensure_project()
            if project is None:
                return
            self._write_activity("Planner request started.")
            self._send_thread = threading.Thread(
                target=self._run_plan_project,
                args=(project.project_id, None),
                daemon=True,
            )
            self._send_thread.start()

        def _request_changes(self) -> None:
            if self._send_thread is not None and self._send_thread.is_alive():
                self._write_activity("A provider request is already running.")
                return
            project = self._ensure_project()
            if project is None:
                return
            feedback = self.query_one("#project_feedback", Input).value.strip()
            if not feedback:
                self._write_activity("Add revision feedback before requesting changes.")
                return
            self._write_activity("Planner revision started.")
            self._send_thread = threading.Thread(
                target=self._run_plan_project,
                args=(project.project_id, feedback),
                daemon=True,
            )
            self._send_thread.start()

        def _approve_plan(self) -> None:
            if self.project_service is None or self.active_project is None:
                self._write_activity("No project plan is loaded.")
                return
            try:
                project = self.project_service.approve_plan(self.active_project)
            except Exception as exc:
                self._write_activity(f"Approval failed: {exc}")
                return
            self.active_project = project
            self._refresh_item_list()
            self._render_active_content()
            self._write_activity("Approved the current plan.")

        def _proceed_with_plan(self) -> None:
            if self.project_service is None or self.active_project is None:
                self._write_activity("No project plan is loaded.")
                return
            try:
                project = self.project_service.proceed_with_plan(self.active_project)
            except Exception as exc:
                self._write_activity(f"Proceed failed: {exc}")
                return
            self.active_project = project
            self._refresh_item_list()
            self._render_active_content()
            self._write_activity("Plan marked ready to execute.")

        def action_resend_last_prompt(self) -> None:
            if self.active_session is None:
                return
            if self._send_thread is not None and self._send_thread.is_alive():
                self._write_activity("A provider request is already running.")
                return
            self._write_activity("Re-sending the last prompt.")
            self._send_thread = threading.Thread(
                target=self._run_resend_last_prompt,
                args=(self.active_session,),
                daemon=True,
            )
            self._send_thread.start()

        def _run_resend_last_prompt(self, session: ChatSession) -> None:
            try:
                updated_session, _, _ = self.service.resend_last_prompt(session)
            except Exception as exc:
                self.call_from_thread(self._on_send_failed, f"Resend failed: {exc}")
                return
            self.call_from_thread(self._on_send_succeeded, updated_session)

        def action_save_metadata(self) -> None:
            self._save_metadata()

        def action_kill_session(self) -> None:
            if self.active_session is None:
                return
            result = self.service.kill_session(self.active_session)
            self.active_session = self.service.store.load_session(self.active_session.session_id)
            self._write_activity(f"Kill requested for {result['session_id']}.")
            self._refresh_item_list()
            self._render_active_content()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            input_id = event.input.id
            if input_id == "prompt":
                self._send_prompt()
            elif input_id == "project_notes":
                self.action_ask_planner()
            elif input_id == "project_feedback":
                if event.input.value.strip():
                    self._request_changes()
                else:
                    self.action_ask_planner()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "send_prompt":
                self._send_prompt()
            elif event.button.id == "resend_prompt":
                self.action_resend_last_prompt()
            elif event.button.id == "save_meta":
                self._save_metadata()
            elif event.button.id == "kill_session":
                self.action_kill_session()
            elif event.button.id == "create_project":
                self._create_project()
            elif event.button.id == "ask_planner":
                self.action_ask_planner()
            elif event.button.id == "request_changes":
                self._request_changes()
            elif event.button.id == "approve_plan":
                self._approve_plan()
            elif event.button.id == "proceed_plan":
                self._proceed_with_plan()

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            if event.list_view.id == "launch_menu":
                item = event.item
                if not isinstance(item, LaunchOptionItem):
                    return
                if item.key == "create_project":
                    self.browser_mode = "projects"
                    self.active_project = None
                    self.active_session = None
                    self._clear_project_inputs()
                    self._apply_chat_defaults()
                    self.query_one("#session_title", Input).value = ""
                    self._refresh_item_list()
                    self._render_active_content()
                    self.set_focus(self.query_one("#project_name", Input))
                elif item.key == "open_project":
                    self.browser_mode = "projects"
                    self._refresh_item_list()
                    self._render_active_content()
                    self.set_focus(self.query_one("#item_list", ListView))
                elif item.key == "open_chat":
                    self.browser_mode = "chats"
                    self.active_project = None
                    self._refresh_item_list()
                    self._render_active_content()
                    self.set_focus(self.query_one("#item_list", ListView))
                elif item.key == "quit":
                    self.exit()
                return

            item = event.item
            if isinstance(item, ProjectListItem):
                self.browser_mode = "projects"
                if self.project_service is None:
                    return
                project = self.project_service.load_project(item.project.project_id)
                self._load_project(project)
                return
            if isinstance(item, SessionListItem):
                self.browser_mode = "chats"
                self.active_project = None
                self.active_session = self.service.store.load_session(item.session.session_id)
                self._sync_session_inputs(self.active_session)
                self._render_active_content()


def run_chat_app(
    service: ChatService,
    options: ChatLaunchOptions,
    project_service: ProjectService | None = None,
) -> None:
    if not TEXTUAL_AVAILABLE:
        raise ValueError(
            "The 'chat' command requires textual. Install dependencies from pyproject.toml first."
        )
    BoboChatApp(service, options, project_service=project_service).run()
