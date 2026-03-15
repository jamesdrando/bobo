from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    load_json,
    render_bullets,
    require_choice,
    require_non_empty_string,
    require_object,
    require_positive_int,
    require_string_list,
    slugify,
)
from .workspace import WorkspaceSettings

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

VALID_TOOL_APPROVALS = {"none", "auto", "manual"}
VALID_APPROVAL_MODES = {"auto", "high_impact", "manual"}


def normalize_role_llm_config(role_payload: dict[str, Any], role_name: str) -> dict[str, Any]:
    llm_payload = role_payload.get("llm")
    if llm_payload is None:
        return {}
    llm = require_object(llm_payload, f"{role_name}.llm")

    provider_value = llm.get("provider")
    model_value = llm.get("model")
    region_value = llm.get("region")
    profile_value = llm.get("profile")
    provider_options_payload = llm.get("provider_options", {})
    provider_options = require_object(
        provider_options_payload,
        f"{role_name}.llm.provider_options",
    )

    normalized: dict[str, Any] = {"provider_options": provider_options}
    if provider_value is not None:
        normalized["provider"] = require_non_empty_string(provider_value, f"{role_name}.llm.provider")
    if model_value is not None:
        normalized["model"] = require_non_empty_string(model_value, f"{role_name}.llm.model")
    if region_value is not None:
        normalized["region"] = require_non_empty_string(region_value, f"{role_name}.llm.region")
    if profile_value is not None:
        normalized["profile"] = require_non_empty_string(profile_value, f"{role_name}.llm.profile")
    return normalized


def render_role_llm_contract(role: dict[str, Any]) -> str:
    llm = role.get("llm", {})
    if not llm:
        return "- Inherit provider and model from the workspace defaults."

    lines = []
    provider_value = llm.get("provider")
    model_value = llm.get("model")
    region_value = llm.get("region")
    profile_value = llm.get("profile")
    if provider_value:
        lines.append(f"- Preferred provider: `{provider_value}`")
    if model_value:
        lines.append(f"- Preferred model: `{model_value}`")
    if region_value:
        lines.append(f"- Preferred region: `{region_value}`")
    if profile_value:
        lines.append(f"- Preferred profile: `{profile_value}`")
    if llm.get("provider_options"):
        lines.append("- Provider options: present in config and merged at runtime.")
    return "\n".join(lines) or "- Inherit provider and model from the workspace defaults."


def resolve_role_llm_settings(
    role: dict[str, Any],
    workspace_settings: WorkspaceSettings,
) -> dict[str, Any]:
    llm = role.get("llm", {})
    provider = str(llm.get("provider", workspace_settings.chat.default_provider)).strip()
    model = str(llm.get("model", workspace_settings.chat.default_model)).strip()
    region = str(llm.get("region", "")).strip() or None
    profile = str(llm.get("profile", "")).strip() or None
    provider_options = dict(llm.get("provider_options", {}))

    if provider == "bedrock":
        region = region or workspace_settings.bedrock.region
        profile = profile or workspace_settings.bedrock.profile
    elif provider == "openrouter":
        provider_defaults = {
            "base_url": workspace_settings.openrouter.base_url,
            "api_key_env": workspace_settings.openrouter.api_key_env,
        }
        if workspace_settings.openrouter.site_url:
            provider_defaults["site_url"] = workspace_settings.openrouter.site_url
        if workspace_settings.openrouter.app_name:
            provider_defaults["app_name"] = workspace_settings.openrouter.app_name
        provider_options = {
            **provider_defaults,
            **provider_options,
        }

    return {
        "provider": provider,
        "model": model,
        "region_name": region,
        "profile_name": profile,
        "provider_options": provider_options,
    }


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
    llm = normalize_role_llm_config(role_payload, name)
    return {
        "name": name,
        "summary": summary,
        "responsibilities": responsibilities,
        "resources": resources,
        "instructions": instructions,
        "handoff_targets": handoff_targets,
        "model_tier": model_tier,
        "task_details": task_details,
        "llm": llm,
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
        "source_config_path": str(config_path),
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
    llm_contract = render_role_llm_contract(role)

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

# Assigned LLM

{llm_contract}

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
