from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from .agents import VALID_APPROVAL_MODES
from .common import (
    require_choice,
    require_non_empty_string,
    require_object,
    require_positive_int,
    require_string,
    require_string_list,
)
from .handoffs import claim_next_handoff, record_handoff
from .workspace import render_relative_path, resolve_workspace_path, resolve_workspace_root


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
