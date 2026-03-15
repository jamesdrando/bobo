from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agents import VALID_APPROVAL_MODES, load_config, write_agents
from .chat.service import ChatService
from .chat.store import ChatStore
from .common import load_json, print_json, read_text_input
from .handoffs import (
    claim_next_handoff,
    ensure_handoff_db,
    list_handoffs,
    record_handoff,
    update_handoff_status,
)
from .llm import build_llm_request_from_args, llm_complete
from .providers import DEFAULT_PROVIDER_REGISTRY
from .providers.base import ProviderRequest
from .projects import ProjectService, ProjectStore
from .tools import dispatch_agent_output, parse_agent_output
from .ui import ChatLaunchOptions, run_chat_app
from .workspace import load_workspace_settings, resolve_chat_storage_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw_argv = argv if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] == "_provider-call":
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("command")
        parser.add_argument("--request-file", required=True)
        parser.add_argument("--response-file", required=True)
        return parser.parse_args(raw_argv)

    parser = argparse.ArgumentParser(
        description=(
            "Render role-specific AGENTS.md files, manage SQLite-backed handoffs, "
            "run provider-agnostic LLM completions, and launch chat sessions."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render-agents")
    render_parser.add_argument("--config", required=True)
    render_parser.add_argument("--base-path", default=".")

    init_db_parser = subparsers.add_parser("init-db")
    init_db_parser.add_argument("--config", required=True)

    record_parser = subparsers.add_parser("record-handoff")
    record_parser.add_argument("--config", required=True)
    record_parser.add_argument("--payload-file", required=True)

    claim_parser = subparsers.add_parser("claim-handoff")
    claim_parser.add_argument("--config", required=True)
    claim_parser.add_argument("--role", required=True)

    parse_output_parser = subparsers.add_parser("parse-agent-output")
    parse_output_parser.add_argument("--config", required=True)
    parse_output_parser.add_argument("--role", required=True)
    parse_output_parser.add_argument("--input-file", required=True)

    dispatch_output_parser = subparsers.add_parser("dispatch-agent-output")
    dispatch_output_parser.add_argument("--config", required=True)
    dispatch_output_parser.add_argument("--role", required=True)
    dispatch_output_parser.add_argument("--input-file", required=True)
    dispatch_output_parser.add_argument("--base-path", default=".")
    dispatch_output_parser.add_argument(
        "--approval-mode",
        choices=sorted(VALID_APPROVAL_MODES),
    )
    dispatch_output_parser.add_argument("--approve", action="store_true")

    complete_parser = subparsers.add_parser("complete-handoff")
    complete_parser.add_argument("--config", required=True)
    complete_parser.add_argument("--handoff-id", required=True)
    complete_parser.add_argument(
        "--status",
        choices=["completed", "blocked"],
        default="completed",
    )
    complete_parser.add_argument("--resolution-note", default="")

    list_parser = subparsers.add_parser("list-handoffs")
    list_parser.add_argument("--config", required=True)
    list_parser.add_argument("--role")
    list_parser.add_argument("--status")

    llm_complete_parser = subparsers.add_parser("llm-complete")
    llm_complete_parser.add_argument("--provider", required=True)
    llm_complete_parser.add_argument("--model", required=True)
    input_group = llm_complete_parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--prompt")
    input_group.add_argument("--messages-json")
    input_group.add_argument("--messages-file")
    llm_complete_parser.add_argument("--system", action="append", default=[])
    llm_complete_parser.add_argument("--max-tokens", type=int)
    llm_complete_parser.add_argument("--temperature", type=float)
    llm_complete_parser.add_argument("--top-p", type=float)
    llm_complete_parser.add_argument("--stop-sequence", action="append", default=[])
    llm_complete_parser.add_argument("--region")
    llm_complete_parser.add_argument("--profile")
    provider_options_group = llm_complete_parser.add_mutually_exclusive_group()
    provider_options_group.add_argument("--provider-options-json")
    provider_options_group.add_argument("--provider-options-file")

    chat_parser = subparsers.add_parser("chat")
    chat_parser.add_argument("--resume")
    chat_parser.add_argument("--title")
    chat_parser.add_argument("--chat-dir")
    chat_parser.add_argument("--provider")
    chat_parser.add_argument("--model")
    chat_parser.add_argument("--region")
    chat_parser.add_argument("--profile")
    chat_parser.add_argument("--config")
    chat_parser.add_argument("--team-config")

    kill_chat_parser = subparsers.add_parser("kill-chat")
    kill_chat_parser.add_argument("--session", required=True)
    kill_chat_parser.add_argument("--chat-dir")
    kill_chat_parser.add_argument("--config")
    kill_chat_parser.add_argument("--reason", default="user_requested")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "_provider-call":
        request = ProviderRequest.from_dict(load_json(Path(args.request_file)))
        try:
            result = DEFAULT_PROVIDER_REGISTRY.complete(request)
            Path(args.response_file).write_text(
                json.dumps({"ok": True, "result": result.to_dict()}, indent=2),
                encoding="utf-8",
            )
            return 0
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
            Path(args.response_file).write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
            return 1

    if args.command == "chat":
        workspace_root = Path.cwd()
        workspace_settings = load_workspace_settings(workspace_root, args.config)
        storage_dir = resolve_chat_storage_dir(workspace_root, workspace_settings, args.chat_dir)
        store = ChatStore(workspace_root, storage_dir)
        service = ChatService(store, workspace_settings)
        team_config = _load_optional_team_config(workspace_root, args.team_config)
        project_service = ProjectService(
            ProjectStore(workspace_root),
            service,
            team_config=team_config,
        )
        run_chat_app(
            service,
            ChatLaunchOptions(
                resume=args.resume,
                title=args.title,
                team_config_path=args.team_config,
                provider=args.provider,
                model=args.model,
                region=args.region,
                profile=args.profile,
            ),
            project_service=project_service,
        )
        return 0

    if args.command == "kill-chat":
        workspace_root = Path.cwd()
        workspace_settings = load_workspace_settings(workspace_root, args.config)
        storage_dir = resolve_chat_storage_dir(workspace_root, workspace_settings, args.chat_dir)
        store = ChatStore(workspace_root, storage_dir)
        service = ChatService(store, workspace_settings)
        session = service.prepare_session(resume=args.session)
        if session is None:
            raise ValueError("No chat session was selected.")
        print_json(service.kill_session(session, reason=args.reason))
        return 0

    if args.command == "llm-complete":
        print_json(llm_complete(build_llm_request_from_args(args)))
        return 0

    config = load_config(args.config)
    db_path = config["output"]["database_path"]

    if args.command == "render-agents":
        written_files = write_agents(config, Path(args.base_path))
        print_json(written_files)
        return 0

    if args.command == "init-db":
        ensure_handoff_db(db_path)
        print_json({"database_path": db_path, "initialized": True})
        return 0

    if args.command == "record-handoff":
        payload = load_json(Path(args.payload_file))
        print_json(record_handoff(db_path, config, payload))
        return 0

    if args.command == "claim-handoff":
        print_json(claim_next_handoff(db_path, args.role))
        return 0

    if args.command == "parse-agent-output":
        print_json(
            parse_agent_output(
                read_text_input(args.input_file),
                config,
                args.role,
            )
        )
        return 0

    if args.command == "dispatch-agent-output":
        print_json(
            dispatch_agent_output(
                db_path,
                config,
                args.role,
                read_text_input(args.input_file),
                base_path=args.base_path,
                approval_mode=args.approval_mode,
                approve=args.approve,
            )
        )
        return 0

    if args.command == "complete-handoff":
        print_json(
            update_handoff_status(
                db_path,
                args.handoff_id,
                args.status,
                args.resolution_note,
            )
        )
        return 0

    if args.command == "list-handoffs":
        print_json(list_handoffs(db_path, args.role, args.status))
        return 0

    raise ValueError(f"Unhandled command: {args.command}")


def _load_optional_team_config(
    workspace_root: Path,
    team_config_path: str | None,
) -> dict | None:
    if team_config_path:
        return load_config(team_config_path)
    default_path = workspace_root / "examples" / "software_team.json"
    if default_path.exists():
        return load_config(default_path)
    return None
