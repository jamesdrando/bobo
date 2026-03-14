#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CODE_STYLE = [
    "Build with a data-oriented design.",
    "Favor pure functions and composition over inheritance.",
    "Keep abstractions implicit and readable.",
    "Avoid widening scope when the planner has already constrained the task.",
]

DEFAULT_MANTRA = [
    "DEMYSTIFY intent.",
    "INTEGRATE fragmentation.",
    "EMBRACE simplicity.",
    "STRIVE for elegance.",
    "EXHIBIT restraint.",
    "LOVE your work.",
]

DEFAULT_SHARED_INSTRUCTIONS = [
    "Complete the assigned task packet and keep the scope narrow.",
    "Use the handoff payload as the task log.",
    "Add dependencies only when they are necessary, and front-load that work.",
    "Do not make things up. Verify facts that matter.",
]

DEFAULT_AGENT_PROTOCOL = {
    "response_rules": [
        "Return exactly one JSON object.",
        "No prose, markdown, or code fences.",
        "Use tools for every action.",
        "You do not have shell access.",
    ],
    "tools": [
        {
            "name": "claim_handoff",
            "args_preview": "{}",
            "description": "Get your next task.",
            "dispatch": "builtin",
        },
        {
            "name": "read_file_or_directory",
            "args_preview": '{"path":"path"}',
            "description": "Read file or list dir.",
            "dispatch": "external",
        },
        {
            "name": "create_file",
            "args_preview": '{"path":"path","content":"text"}',
            "description": "Create file.",
            "dispatch": "external",
        },
        {
            "name": "patch_code_file",
            "args_preview": '{"path":"path","search":"old","replace":"new"}',
            "description": "Patch file.",
            "dispatch": "external",
        },
        {
            "name": "run_linter_and_tests",
            "args_preview": '{"cmd":"pytest tests/test_x.py"}',
            "description": "Run targeted tests.",
            "dispatch": "external",
        },
        {
            "name": "manage_dependencies",
            "args_preview": '{"pm":"pip","act":"install","pkgs":["name"]}',
            "description": "Change deps safely.",
            "dispatch": "external",
        },
        {
            "name": "handoff",
            "args_preview": (
                '{"run_id":"r","task_id":"t","to":"Role","title":"t","sum":"s",'
                '"files":["f"],"funcs":["fn"],"ok":["done"],"arts":["artifact"],'
                '"ts":"pass","tc":"cmd","fsum":"","top":"","next":"next"}'
            ),
            "description": "Save task log and queue next role.",
            "dispatch": "builtin",
        },
    ],
}

VALID_HANDOFF_STATUSES = {"pending", "claimed", "completed", "blocked"}
VALID_TEST_STATUSES = {"pass", "fail", "not_run", "blocked"}
VALID_TOOL_APPROVALS = {"none", "auto", "manual"}
VALID_APPROVAL_MODES = {"auto", "high_impact", "manual"}

HANDOFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    rationale TEXT NOT NULL,
    from_role TEXT NOT NULL,
    to_role TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    resolution_note TEXT NOT NULL DEFAULT '',
    next_action TEXT NOT NULL,
    test_status TEXT NOT NULL,
    test_command TEXT NOT NULL,
    failure_summary TEXT NOT NULL,
    top_stack_frame TEXT NOT NULL,
    file_scope_json TEXT NOT NULL,
    function_scope_json TEXT NOT NULL,
    acceptance_criteria_json TEXT NOT NULL,
    dependencies_json TEXT NOT NULL,
    artifacts_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_handoffs_target_status_priority
ON handoffs (to_role, status, priority, created_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def require_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    return value


def require_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings.")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{index}] must be a non-empty string.")
        normalized.append(item.strip())
    return normalized


def require_positive_int(value: Any, field_name: str, minimum: int = 1) -> int:
    if not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}.")
    return value


def require_choice(value: Any, field_name: str, choices: set[str]) -> str:
    normalized = require_non_empty_string(value, field_name)
    if normalized not in choices:
        raise ValueError(f"{field_name} must be one of {sorted(choices)}.")
    return normalized


def slugify(value: str) -> str:
    pieces: list[str] = []
    last_was_dash = False
    for character in value.lower():
        if character.isalnum():
            pieces.append(character)
            last_was_dash = False
            continue
        if not last_was_dash:
            pieces.append("-")
            last_was_dash = True
    slug = "".join(pieces).strip("-")
    return slug or "agent"


def render_bullets(items: list[str]) -> str:
    if not items:
        return "- None specified."
    return "\n".join(f"- {item}" for item in items)


def default_tool_approval(name: str, dispatch: str) -> str:
    if dispatch == "builtin":
        return "none"
    if name == "manage_dependencies":
        return "manual"
    return "auto"


def normalize_tool_spec(tool_payload: dict[str, Any], index: int) -> dict[str, str]:
    name = require_non_empty_string(tool_payload.get("name"), f"agent_protocol.tools[{index}].name")
    args_preview = require_non_empty_string(
        tool_payload.get("args_preview"),
        f"agent_protocol.tools[{index}].args_preview",
    )
    description = require_non_empty_string(
        tool_payload.get("description"),
        f"agent_protocol.tools[{index}].description",
    )
    dispatch = require_non_empty_string(
        tool_payload.get("dispatch", "external"),
        f"agent_protocol.tools[{index}].dispatch",
    )
    if dispatch not in {"builtin", "external"}:
        raise ValueError("tool dispatch must be 'builtin' or 'external'.")
    approval = require_choice(
        tool_payload.get("approval", default_tool_approval(name, dispatch)),
        f"agent_protocol.tools[{index}].approval",
        VALID_TOOL_APPROVALS,
    )
    return {
        "name": name,
        "args_preview": args_preview,
        "description": description,
        "dispatch": dispatch,
        "approval": approval,
    }


def normalize_agent_protocol(raw_protocol: Any) -> dict[str, Any]:
    protocol = raw_protocol if raw_protocol is not None else DEFAULT_AGENT_PROTOCOL
    if not isinstance(protocol, dict):
        raise ValueError("agent_protocol must be an object.")

    response_rules = require_string_list(
        protocol.get("response_rules", DEFAULT_AGENT_PROTOCOL["response_rules"]),
        "agent_protocol.response_rules",
    )
    tools_payload = protocol.get("tools", DEFAULT_AGENT_PROTOCOL["tools"])
    if not isinstance(tools_payload, list) or not tools_payload:
        raise ValueError("agent_protocol.tools must be a non-empty list.")

    tools: list[dict[str, str]] = []
    tool_names: set[str] = set()
    for index, tool_payload in enumerate(tools_payload):
        if not isinstance(tool_payload, dict):
            raise ValueError(f"agent_protocol.tools[{index}] must be an object.")
        tool = normalize_tool_spec(tool_payload, index)
        if tool["name"] in tool_names:
            raise ValueError(f"Duplicate tool name: {tool['name']}")
        tool_names.add(tool["name"])
        tools.append(tool)

    return {
        "response_rules": response_rules,
        "tools": tools,
        "tool_names": tool_names,
    }


def render_tool_contract(protocol: dict[str, Any]) -> str:
    lines = []
    for tool in protocol["tools"]:
        approval_suffix = ""
        if tool["approval"] == "manual":
            approval_suffix = " Requires approval before execution."
        lines.append(
            f"- `{tool['name']}` {tool['args_preview']} : {tool['description']}{approval_suffix}"
        )
    return "\n".join(lines)


def normalize_role(
    role_payload: dict[str, Any],
    default_task_details: str,
) -> dict[str, Any]:
    name = require_non_empty_string(role_payload.get("name"), "roles[].name")
    summary = require_non_empty_string(role_payload.get("summary"), f"{name}.summary")
    responsibilities = require_string_list(
        role_payload.get("responsibilities"),
        f"{name}.responsibilities",
    )
    if not responsibilities:
        raise ValueError(f"{name}.responsibilities must contain at least one entry.")
    resources = require_string_list(role_payload.get("resources"), f"{name}.resources")
    instructions = require_string_list(
        role_payload.get("instructions"),
        f"{name}.instructions",
    )
    handoff_targets = require_string_list(
        role_payload.get("handoff_targets"),
        f"{name}.handoff_targets",
    )
    model_tier = require_non_empty_string(
        role_payload.get("model_tier", "unspecified"),
        f"{name}.model_tier",
    )
    task_details = require_non_empty_string(
        role_payload.get("task_details", default_task_details),
        f"{name}.task_details",
    )
    return {
        "name": name,
        "summary": summary,
        "responsibilities": responsibilities,
        "resources": resources,
        "instructions": instructions,
        "handoff_targets": handoff_targets,
        "model_tier": model_tier,
        "task_details": task_details,
        "slug": slugify(name),
    }


def normalize_config(raw_config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    project_name = require_non_empty_string(raw_config.get("project_name"), "project_name")
    project_description = require_non_empty_string(
        raw_config.get("project_description"),
        "project_description",
    )
    project_resources = require_string_list(
        raw_config.get("project_resources"),
        "project_resources",
    )
    output_payload = raw_config.get("output", {})
    if output_payload is None:
        output_payload = {}
    if not isinstance(output_payload, dict):
        raise ValueError("output must be an object.")

    execution_policy = raw_config.get("execution_policy", {})
    if execution_policy is None:
        execution_policy = {}
    if not isinstance(execution_policy, dict):
        raise ValueError("execution_policy must be an object.")

    database_path = str(output_payload.get("database_path", ".bobo/handoffs.sqlite3"))
    agents_dir = str(output_payload.get("agents_dir", "generated_agents"))
    default_task_details = require_non_empty_string(
        raw_config.get(
            "task_details",
            "Claim your next task from the handoff queue and stay inside the assigned scope.",
        ),
        "task_details",
    )

    roles_payload = raw_config.get("roles")
    if not isinstance(roles_payload, list) or not roles_payload:
        raise ValueError("roles must be a non-empty list.")
    normalized_roles: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, role_payload in enumerate(roles_payload):
        if not isinstance(role_payload, dict):
            raise ValueError(f"roles[{index}] must be an object.")
        role = normalize_role(role_payload, default_task_details)
        if role["name"] in seen_names:
            raise ValueError(f"Duplicate role name: {role['name']}")
        seen_names.add(role["name"])
        normalized_roles.append(role)

    shared_instructions = require_string_list(
        raw_config.get("shared_instructions", DEFAULT_SHARED_INSTRUCTIONS),
        "shared_instructions",
    )
    code_style = require_string_list(
        raw_config.get("code_style", DEFAULT_CODE_STYLE),
        "code_style",
    )
    mantra = require_string_list(raw_config.get("mantra", DEFAULT_MANTRA), "mantra")
    agent_protocol = normalize_agent_protocol(raw_config.get("agent_protocol"))

    normalized = {
        "project_name": project_name,
        "project_description": project_description,
        "project_resources": project_resources,
        "roles": normalized_roles,
        "role_names": {role["name"] for role in normalized_roles},
        "shared_instructions": shared_instructions,
        "code_style": code_style,
        "mantra": mantra,
        "task_details": default_task_details,
        "agent_protocol": agent_protocol,
        "output": {
            "database_path": database_path,
            "agents_dir": agents_dir,
        },
        "execution_policy": {
            "max_files_per_task": require_positive_int(
                execution_policy.get("max_files_per_task", 1),
                "execution_policy.max_files_per_task",
            ),
            "max_functions_per_task": require_positive_int(
                execution_policy.get("max_functions_per_task", 3),
                "execution_policy.max_functions_per_task",
            ),
            "minimal_test_feedback": bool(
                execution_policy.get("minimal_test_feedback", True)
            ),
            "failure_feedback_contract": require_non_empty_string(
                execution_policy.get(
                    "failure_feedback_contract",
                    "If tests fail, report only pass/fail, the test command, a short summary, and the top stack frame.",
                ),
                "execution_policy.failure_feedback_contract",
            ),
            "approval_mode": require_choice(
                execution_policy.get("approval_mode", "high_impact"),
                "execution_policy.approval_mode",
                VALID_APPROVAL_MODES,
            ),
        },
    }
    return normalized


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path)
    return normalize_config(load_json(path), path)


def render_roster_breakdown(roles: list[dict[str, Any]]) -> str:
    lines = []
    for role in roles:
        lines.append(f"- {role['name']} ({role['model_tier']}): {role['summary']}")
    return "\n".join(lines)


def render_agent_markdown(config: dict[str, Any], role: dict[str, Any]) -> str:
    project_name = config["project_name"]
    project_description = config["project_description"]
    roster_breakdown = render_roster_breakdown(config["roles"])
    agent_role = role["name"]
    role_responsibilities = render_bullets(role["responsibilities"])
    role_instructions = render_bullets(config["shared_instructions"] + role["instructions"])
    role_resources = render_bullets(config["project_resources"] + role["resources"])
    code_style = render_bullets(config["code_style"])
    mantra = render_bullets(config["mantra"])
    response_rules = render_bullets(config["agent_protocol"]["response_rules"])
    tool_contract = render_tool_contract(config["agent_protocol"])
    task_details = role["task_details"]
    max_files_per_task = config["execution_policy"]["max_files_per_task"]
    max_functions_per_task = config["execution_policy"]["max_functions_per_task"]
    minimal_test_feedback = config["execution_policy"]["minimal_test_feedback"]
    failure_feedback_contract = config["execution_policy"]["failure_feedback_contract"]
    handoff_targets = ", ".join(role["handoff_targets"]) if role["handoff_targets"] else "the next appropriate role"
    test_feedback_mode = "compact" if minimal_test_feedback else "full"

    return f"""# Context

You are an AI agent. You are part of a team working on {project_name}: {project_description}

The team consists of the following roles:

{roster_breakdown}

Your role is {agent_role}. Your usual handoff targets are {handoff_targets}.

# Responsibilities

{role_responsibilities}

# Instructions

{role_instructions}

# Scope Budget

- Stay within {max_files_per_task} file(s) per task unless the planner explicitly widens scope.
- Stay within {max_functions_per_task} function(s) per task unless the planner explicitly widens scope.
- Keep test feedback in {test_feedback_mode} mode.
- Keep handoffs compact. Do not paste full logs, full diffs, or irrelevant context.
- {failure_feedback_contract}

# Wire Protocol

- Envelope: `{{"tool":"<tool_name>","args":{{...}}}}`
{response_rules}
- The harness parses your JSON and runs tools on your behalf.
- Use `claim_handoff` to get work and `handoff` to finish or report blocked work.
- The orchestrator writes to SQLite for you. Never read or modify the handoff database directly.

# Requirements

- Complete the assigned scope or explicitly explain why you are blocked.
- Write clean, correct code.
- Do not make things up. If something matters, verify it.

# Code Style

{code_style}

# Mantra

{mantra}

# Resources

{role_resources}

# Tool Access

{tool_contract}

# Current Task

{task_details}
"""


def write_agents(config: dict[str, Any], base_path: Path | None = None) -> dict[str, str]:
    root = base_path or Path.cwd()
    agents_dir = root / config["output"]["agents_dir"]
    agents_dir.mkdir(parents=True, exist_ok=True)
    written_files: dict[str, str] = {}
    for role in config["roles"]:
        role_dir = agents_dir / role["slug"]
        role_dir.mkdir(parents=True, exist_ok=True)
        agent_path = role_dir / "AGENTS.md"
        agent_path.write_text(render_agent_markdown(config, role), encoding="utf-8")
        written_files[role["name"]] = str(agent_path)
    return written_files


def ensure_handoff_db(db_path: str | Path) -> None:
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(HANDOFF_SCHEMA)


def normalize_handoff_payload(
    payload: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    from_role = require_non_empty_string(payload.get("from_role"), "from_role")
    to_role = require_non_empty_string(payload.get("to_role"), "to_role")
    if from_role not in config["role_names"]:
        raise ValueError(f"Unknown from_role: {from_role}")
    if to_role not in config["role_names"]:
        raise ValueError(f"Unknown to_role: {to_role}")

    file_scope = require_string_list(payload.get("file_scope"), "file_scope")
    function_scope = require_string_list(payload.get("function_scope"), "function_scope")
    acceptance_criteria = require_string_list(
        payload.get("acceptance_criteria"),
        "acceptance_criteria",
    )
    artifacts = require_string_list(payload.get("artifacts"), "artifacts")
    dependencies = require_string_list(payload.get("dependencies"), "dependencies")

    if not file_scope:
        raise ValueError("file_scope must contain at least one file.")
    if not function_scope:
        raise ValueError("function_scope must contain at least one function or symbol.")
    if not acceptance_criteria:
        raise ValueError("acceptance_criteria must contain at least one entry.")

    max_files = config["execution_policy"]["max_files_per_task"]
    max_functions = config["execution_policy"]["max_functions_per_task"]
    if len(file_scope) > max_files:
        raise ValueError(
            f"file_scope exceeds the configured limit of {max_files} file(s)."
        )
    if len(function_scope) > max_functions:
        raise ValueError(
            f"function_scope exceeds the configured limit of {max_functions} function(s)."
        )

    status = require_non_empty_string(payload.get("status", "pending"), "status")
    if status not in VALID_HANDOFF_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_HANDOFF_STATUSES)}.")

    priority = require_positive_int(payload.get("priority", 3), "priority")
    test_status = require_non_empty_string(
        payload.get("test_status", "not_run"),
        "test_status",
    )
    if test_status not in VALID_TEST_STATUSES:
        raise ValueError(f"test_status must be one of {sorted(VALID_TEST_STATUSES)}.")

    failure_summary = str(payload.get("failure_summary", "")).strip()
    top_stack_frame = str(payload.get("top_stack_frame", "")).strip()
    if test_status == "fail" and not top_stack_frame:
        raise ValueError("top_stack_frame is required when test_status is 'fail'.")

    normalized = {
        "handoff_id": require_non_empty_string(
            payload.get("handoff_id", uuid.uuid4().hex),
            "handoff_id",
        ),
        "run_id": require_non_empty_string(payload.get("run_id"), "run_id"),
        "task_id": require_non_empty_string(payload.get("task_id"), "task_id"),
        "title": require_non_empty_string(payload.get("title"), "title"),
        "summary": require_non_empty_string(payload.get("summary"), "summary"),
        "rationale": str(payload.get("rationale", "")).strip(),
        "from_role": from_role,
        "to_role": to_role,
        "status": status,
        "priority": priority,
        "created_at": require_non_empty_string(
            payload.get("created_at", utc_now()),
            "created_at",
        ),
        "next_action": require_non_empty_string(
            payload.get("next_action"),
            "next_action",
        ),
        "test_status": test_status,
        "test_command": str(payload.get("test_command", "")).strip(),
        "failure_summary": failure_summary,
        "top_stack_frame": top_stack_frame,
        "file_scope": file_scope,
        "function_scope": function_scope,
        "acceptance_criteria": acceptance_criteria,
        "dependencies": dependencies,
        "artifacts": artifacts,
    }
    return normalized


def row_to_handoff(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    payload.update(
        {
            "status": row["status"],
            "priority": row["priority"],
            "created_at": row["created_at"],
            "claimed_at": row["claimed_at"],
            "completed_at": row["completed_at"],
            "resolution_note": row["resolution_note"],
            "next_action": row["next_action"],
            "test_status": row["test_status"],
            "test_command": row["test_command"],
            "failure_summary": row["failure_summary"],
            "top_stack_frame": row["top_stack_frame"],
        }
    )
    return payload


def resolve_workspace_root(base_path: str | Path | None = None) -> Path:
    return Path(base_path or ".").resolve(strict=False)


def resolve_workspace_path(base_path: str | Path | None, raw_path: str) -> Path:
    root = resolve_workspace_root(base_path)
    candidate = Path(raw_path)
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (root / candidate).resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes the workspace root: {raw_path}")
    return resolved


def render_relative_path(path: Path, base_path: str | Path | None) -> str:
    root = resolve_workspace_root(base_path)
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def normalize_command_argv(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list of strings.")
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{index}] must be a non-empty string.")
        normalized.append(item.strip())
    return normalized


def normalize_timeout_seconds(value: Any, field_name: str, default: int) -> int:
    if value is None:
        return default
    return require_positive_int(value, field_name)


def resolve_command_cwd(base_path: str | Path | None, raw_cwd: str) -> Path:
    if not raw_cwd:
        return resolve_workspace_root(base_path)
    return resolve_workspace_path(base_path, raw_cwd)


def run_subprocess(argv: list[str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "returncode": None,
            "argv": argv,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }

    return {
        "ok": completed.returncode == 0,
        "timed_out": False,
        "returncode": completed.returncode,
        "argv": argv,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def read_file_or_directory_tool(base_path: str | Path | None, path_value: str) -> dict[str, Any]:
    resolved = resolve_workspace_path(base_path, path_value)
    if not resolved.exists():
        raise ValueError(f"path does not exist: {path_value}")
    if resolved.is_dir():
        entries = []
        for child in sorted(resolved.iterdir(), key=lambda item: item.name):
            entries.append(
                {
                    "name": child.name,
                    "path": render_relative_path(child, base_path),
                    "kind": "directory" if child.is_dir() else "file",
                }
            )
        return {
            "path": render_relative_path(resolved, base_path),
            "kind": "directory",
            "entries": entries,
        }
    return {
        "path": render_relative_path(resolved, base_path),
        "kind": "file",
        "content": resolved.read_text(encoding="utf-8"),
    }


def create_file_tool(
    base_path: str | Path | None,
    path_value: str,
    content: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    resolved = resolve_workspace_path(base_path, path_value)
    existed = resolved.exists()
    if existed and not overwrite:
        raise ValueError(f"file already exists: {path_value}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return {
        "path": render_relative_path(resolved, base_path),
        "created": not existed,
        "overwrote": existed,
        "bytes_written": len(content.encode("utf-8")),
    }


def patch_code_file_tool(
    base_path: str | Path | None,
    path_value: str,
    search_string: str,
    replacement_string: str,
    expected_replacements: int,
) -> dict[str, Any]:
    resolved = resolve_workspace_path(base_path, path_value)
    if not resolved.exists():
        raise ValueError(f"path does not exist: {path_value}")
    original = resolved.read_text(encoding="utf-8")
    match_count = original.count(search_string)
    if match_count == 0:
        raise ValueError(f"search string not found in {path_value}")
    if match_count != expected_replacements:
        raise ValueError(
            f"expected {expected_replacements} replacement(s) in {path_value}, found {match_count}"
        )
    updated = original.replace(search_string, replacement_string, expected_replacements)
    resolved.write_text(updated, encoding="utf-8")
    return {
        "path": render_relative_path(resolved, base_path),
        "replacements_applied": expected_replacements,
    }


def run_linter_and_tests_tool(
    base_path: str | Path | None,
    argv: list[str],
    cwd: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    resolved_cwd = resolve_command_cwd(base_path, cwd)
    result = run_subprocess(argv, resolved_cwd, timeout_seconds)
    result["cwd"] = render_relative_path(resolved_cwd, base_path)
    return result


def build_dependency_command(package_manager: str, action: str, packages: list[str]) -> list[str]:
    normalized_manager = package_manager.strip().lower()
    normalized_action = action.strip().lower()
    install_actions = {"install", "add"}
    remove_actions = {"remove", "uninstall"}

    if normalized_manager == "pip":
        if normalized_action in install_actions:
            return [sys.executable, "-m", "pip", "install", *packages]
        if normalized_action in remove_actions:
            return [sys.executable, "-m", "pip", "uninstall", "-y", *packages]
    if normalized_manager == "npm":
        if normalized_action in install_actions:
            return ["npm", "install", *packages]
        if normalized_action in remove_actions:
            return ["npm", "uninstall", *packages]
    if normalized_manager == "pnpm":
        if normalized_action in install_actions:
            return ["pnpm", "add", *packages]
        if normalized_action in remove_actions:
            return ["pnpm", "remove", *packages]
    if normalized_manager == "yarn":
        if normalized_action in install_actions:
            return ["yarn", "add", *packages]
        if normalized_action in remove_actions:
            return ["yarn", "remove", *packages]
    if normalized_manager == "poetry":
        if normalized_action in install_actions:
            return ["poetry", "add", *packages]
        if normalized_action in remove_actions:
            return ["poetry", "remove", *packages]
    if normalized_manager == "uv":
        if normalized_action in install_actions:
            return ["uv", "add", *packages]
        if normalized_action in remove_actions:
            return ["uv", "remove", *packages]

    raise ValueError(
        f"Unsupported dependency command: package_manager={package_manager!r}, action={action!r}"
    )


def manage_dependencies_tool(
    base_path: str | Path | None,
    package_manager: str,
    action: str,
    packages: list[str],
    cwd: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    argv = build_dependency_command(package_manager, action, packages)
    resolved_cwd = resolve_command_cwd(base_path, cwd)
    result = run_subprocess(argv, resolved_cwd, timeout_seconds)
    result["cwd"] = render_relative_path(resolved_cwd, base_path)
    result["package_manager"] = package_manager
    result["action"] = action
    result["packages"] = packages
    return result


def require_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object.")
    return value


def extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, end_index = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if text[index + end_index :].strip():
            raise ValueError("Agent output must contain exactly one JSON object.")
        if not isinstance(payload, dict):
            raise ValueError("Agent output must decode to a JSON object.")
        return payload
    raise ValueError("No JSON object found in agent output.")


def get_tool_spec(config: dict[str, Any], tool_name: str) -> dict[str, str]:
    for tool in config["agent_protocol"]["tools"]:
        if tool["name"] == tool_name:
            return tool
    raise ValueError(f"Unknown tool: {tool_name}")


def normalize_tool_call(
    payload: dict[str, Any],
    config: dict[str, Any],
    role_name: str,
) -> dict[str, Any]:
    tool_name = require_non_empty_string(payload.get("tool"), "tool")
    if tool_name not in config["agent_protocol"]["tool_names"]:
        raise ValueError(f"Unknown tool: {tool_name}")
    args = require_object(payload.get("args", {}), "args")
    tool_spec = get_tool_spec(config, tool_name)

    if tool_name == "claim_handoff":
        normalized_args: dict[str, Any] = {}
    elif tool_name == "read_file_or_directory":
        normalized_args = {
            "path": require_non_empty_string(args.get("path"), "args.path"),
        }
    elif tool_name == "create_file":
        normalized_args = {
            "path": require_non_empty_string(args.get("path"), "args.path"),
            "content": require_string(args.get("content"), "args.content"),
            "overwrite": bool(args.get("overwrite", False)),
        }
    elif tool_name == "patch_code_file":
        normalized_args = {
            "path": require_non_empty_string(args.get("path"), "args.path"),
            "search_string": require_non_empty_string(
                args.get("search", args.get("search_string")),
                "args.search",
            ),
            "replacement_string": require_string(
                args.get("replace", args.get("replacement_string")),
                "args.replace",
            ),
            "expected_replacements": normalize_timeout_seconds(
                args.get("expected_replacements"),
                "args.expected_replacements",
                1,
            ),
        }
    elif tool_name == "run_linter_and_tests":
        argv = args.get("argv")
        if argv is None:
            argv = shlex.split(
                require_non_empty_string(
                    args.get("cmd", args.get("test_command")),
                    "args.cmd",
                )
            )
        else:
            argv = normalize_command_argv(argv, "args.argv")
        normalized_args = {
            "argv": argv,
            "cwd": str(args.get("cwd", "")).strip(),
            "timeout_seconds": normalize_timeout_seconds(
                args.get("timeout_seconds", args.get("timeout")),
                "args.timeout_seconds",
                120,
            ),
        }
    elif tool_name == "manage_dependencies":
        normalized_args = {
            "package_manager": require_non_empty_string(
                args.get("pm", args.get("package_manager")),
                "args.pm",
            ),
            "action": require_non_empty_string(
                args.get("act", args.get("action")),
                "args.act",
            ),
            "packages": require_string_list(
                args.get("pkgs", args.get("packages")),
                "args.pkgs",
            ),
            "cwd": str(args.get("cwd", "")).strip(),
            "timeout_seconds": normalize_timeout_seconds(
                args.get("timeout_seconds", args.get("timeout")),
                "args.timeout_seconds",
                600,
            ),
        }
        if not normalized_args["packages"]:
            raise ValueError("args.pkgs must contain at least one package.")
    elif tool_name == "handoff":
        normalized_args = {
            "run_id": require_non_empty_string(args.get("run_id"), "args.run_id"),
            "task_id": require_non_empty_string(args.get("task_id"), "args.task_id"),
            "title": require_non_empty_string(args.get("title"), "args.title"),
            "summary": require_non_empty_string(
                args.get("sum", args.get("summary")),
                "args.sum",
            ),
            "rationale": str(args.get("why", args.get("rationale", ""))).strip(),
            "from_role": role_name,
            "to_role": require_non_empty_string(
                args.get("to", args.get("to_role")),
                "args.to",
            ),
            "priority": args.get("prio", args.get("priority", 3)),
            "file_scope": require_string_list(
                args.get("files", args.get("file_scope")),
                "args.files",
            ),
            "function_scope": require_string_list(
                args.get("funcs", args.get("function_scope")),
                "args.funcs",
            ),
            "acceptance_criteria": require_string_list(
                args.get("ok", args.get("acceptance_criteria")),
                "args.ok",
            ),
            "dependencies": require_string_list(
                args.get("deps", args.get("dependencies")),
                "args.deps",
            ),
            "artifacts": require_string_list(
                args.get("arts", args.get("artifacts")),
                "args.arts",
            ),
            "test_status": require_non_empty_string(
                args.get("ts", args.get("test_status", "not_run")),
                "args.ts",
            ),
            "test_command": str(args.get("tc", args.get("test_command", ""))).strip(),
            "failure_summary": str(
                args.get("fsum", args.get("failure_summary", ""))
            ).strip(),
            "top_stack_frame": str(
                args.get("top", args.get("top_stack_frame", ""))
            ).strip(),
            "next_action": require_non_empty_string(
                args.get("next", args.get("next_action")),
                "args.next",
            ),
        }
    elif tool_spec["dispatch"] == "external":
        normalized_args = args
    else:
        raise ValueError(f"Unhandled builtin tool: {tool_name}")

    return {
        "tool": tool_name,
        "dispatch": tool_spec["dispatch"],
        "args": normalized_args,
    }


def parse_agent_output(
    text: str,
    config: dict[str, Any],
    role_name: str,
) -> dict[str, Any]:
    payload = extract_json_object(text)
    return normalize_tool_call(payload, config, role_name)


def dispatch_agent_output(
    db_path: str | Path,
    config: dict[str, Any],
    role_name: str,
    text: str,
    base_path: str | Path | None = None,
    approval_mode: str | None = None,
    approve: bool = False,
) -> dict[str, Any]:
    parsed = parse_agent_output(text, config, role_name)
    tool_spec = get_tool_spec(config, parsed["tool"])
    normalized_approval_mode = require_choice(
        approval_mode or config["execution_policy"]["approval_mode"],
        "approval_mode",
        VALID_APPROVAL_MODES,
    )
    approval_required = False
    if parsed["dispatch"] == "external":
        if normalized_approval_mode == "manual":
            approval_required = True
        elif normalized_approval_mode == "high_impact":
            approval_required = tool_spec["approval"] == "manual"

    approval = {
        "required": approval_required,
        "approved": (not approval_required) or approve,
        "mode": normalized_approval_mode,
        "policy": tool_spec["approval"],
    }

    if approval_required and not approve:
        return {
            "dispatch": parsed["dispatch"],
            "tool": parsed["tool"],
            "args": parsed["args"],
            "approval": approval,
            "execution_status": "approval_required",
            "result": None,
        }

    if parsed["tool"] == "claim_handoff":
        result = claim_next_handoff(db_path, role_name)
    elif parsed["tool"] == "handoff":
        result = record_handoff(db_path, config, parsed["args"])
    elif parsed["tool"] == "read_file_or_directory":
        result = read_file_or_directory_tool(base_path, parsed["args"]["path"])
    elif parsed["tool"] == "create_file":
        result = create_file_tool(
            base_path,
            parsed["args"]["path"],
            parsed["args"]["content"],
            parsed["args"]["overwrite"],
        )
    elif parsed["tool"] == "patch_code_file":
        result = patch_code_file_tool(
            base_path,
            parsed["args"]["path"],
            parsed["args"]["search_string"],
            parsed["args"]["replacement_string"],
            parsed["args"]["expected_replacements"],
        )
    elif parsed["tool"] == "run_linter_and_tests":
        result = run_linter_and_tests_tool(
            base_path,
            parsed["args"]["argv"],
            parsed["args"]["cwd"],
            parsed["args"]["timeout_seconds"],
        )
    elif parsed["tool"] == "manage_dependencies":
        result = manage_dependencies_tool(
            base_path,
            parsed["args"]["package_manager"],
            parsed["args"]["action"],
            parsed["args"]["packages"],
            parsed["args"]["cwd"],
            parsed["args"]["timeout_seconds"],
        )
    else:
        raise ValueError(f"No executor registered for external tool: {parsed['tool']}")

    return {
        "dispatch": parsed["dispatch"],
        "tool": parsed["tool"],
        "args": parsed["args"],
        "approval": approval,
        "execution_status": "completed",
        "result": result,
    }


def record_handoff(
    db_path: str | Path,
    config: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_handoff_payload(payload, config)
    ensure_handoff_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO handoffs (
                handoff_id,
                run_id,
                task_id,
                title,
                summary,
                rationale,
                from_role,
                to_role,
                status,
                priority,
                created_at,
                next_action,
                test_status,
                test_command,
                failure_summary,
                top_stack_frame,
                file_scope_json,
                function_scope_json,
                acceptance_criteria_json,
                dependencies_json,
                artifacts_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["handoff_id"],
                normalized["run_id"],
                normalized["task_id"],
                normalized["title"],
                normalized["summary"],
                normalized["rationale"],
                normalized["from_role"],
                normalized["to_role"],
                normalized["status"],
                normalized["priority"],
                normalized["created_at"],
                normalized["next_action"],
                normalized["test_status"],
                normalized["test_command"],
                normalized["failure_summary"],
                normalized["top_stack_frame"],
                json.dumps(normalized["file_scope"]),
                json.dumps(normalized["function_scope"]),
                json.dumps(normalized["acceptance_criteria"]),
                json.dumps(normalized["dependencies"]),
                json.dumps(normalized["artifacts"]),
                json.dumps(normalized, sort_keys=True),
            ),
        )
    return normalized


def claim_next_handoff(db_path: str | Path, role_name: str) -> dict[str, Any] | None:
    ensure_handoff_db(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT *
            FROM handoffs
            WHERE to_role = ? AND status = 'pending'
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
            """,
            (role_name,),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        claimed_at = utc_now()
        connection.execute(
            """
            UPDATE handoffs
            SET status = 'claimed', claimed_at = ?
            WHERE handoff_id = ?
            """,
            (claimed_at, row["handoff_id"]),
        )
        updated = connection.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (row["handoff_id"],),
        ).fetchone()
        connection.commit()
        return row_to_handoff(updated)


def update_handoff_status(
    db_path: str | Path,
    handoff_id: str,
    status: str,
    resolution_note: str = "",
) -> dict[str, Any]:
    if status not in {"completed", "blocked"}:
        raise ValueError("status must be 'completed' or 'blocked'.")
    ensure_handoff_db(db_path)
    completed_at = utc_now()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            UPDATE handoffs
            SET status = ?, completed_at = ?, resolution_note = ?
            WHERE handoff_id = ?
            """,
            (status, completed_at, resolution_note.strip(), handoff_id),
        )
        if connection.total_changes == 0:
            raise ValueError(f"Unknown handoff_id: {handoff_id}")
        row = connection.execute(
            "SELECT * FROM handoffs WHERE handoff_id = ?",
            (handoff_id,),
        ).fetchone()
    return row_to_handoff(row)


def list_handoffs(
    db_path: str | Path,
    role_name: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    ensure_handoff_db(db_path)
    query = "SELECT * FROM handoffs"
    parameters: list[Any] = []
    clauses: list[str] = []
    if role_name:
        clauses.append("to_role = ?")
        parameters.append(role_name)
    if status:
        clauses.append("status = ?")
        parameters.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY priority ASC, created_at ASC"

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, parameters).fetchall()
    return [row_to_handoff(row) for row in rows]


def read_text_input(input_file: str) -> str:
    if input_file == "-":
        return sys.stdin.read()
    return Path(input_file).read_text(encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render role-specific AGENTS.md files and manage SQLite-backed handoffs.",
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

    return parser.parse_args(argv)


def print_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
