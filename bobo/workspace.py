from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .common import load_json, normalize_optional_string, require_non_empty_string, require_object

DEFAULT_BEDROCK_MODEL = "anthropic.claude-3-5-sonnet-20240620-v1:0"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_WORKSPACE_CONFIG_PATH = Path(".bobo/config.json")
DEFAULT_CHAT_STORAGE_DIR = ".bobo/chats"


@dataclass(frozen=True)
class ChatDefaults:
    storage_dir: str = DEFAULT_CHAT_STORAGE_DIR
    default_provider: str = "bedrock"
    default_model: str = DEFAULT_BEDROCK_MODEL


@dataclass(frozen=True)
class BedrockDefaults:
    region: str | None = None
    profile: str | None = None


@dataclass(frozen=True)
class OpenRouterDefaults:
    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    api_key_env: str = "OPENROUTER_API_KEY"
    site_url: str | None = None
    app_name: str | None = "bobo"


@dataclass(frozen=True)
class WorkspaceSettings:
    chat: ChatDefaults = ChatDefaults()
    bedrock: BedrockDefaults = BedrockDefaults()
    openrouter: OpenRouterDefaults = OpenRouterDefaults()
    config_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["config_path"] = self.config_path
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


def _normalize_chat_defaults(payload: Any) -> ChatDefaults:
    if payload is None:
        return ChatDefaults()
    chat = require_object(payload, "chat")
    storage_dir = str(chat.get("storage_dir", DEFAULT_CHAT_STORAGE_DIR)).strip() or DEFAULT_CHAT_STORAGE_DIR
    default_provider = str(chat.get("default_provider", "bedrock")).strip() or "bedrock"
    default_model = str(chat.get("default_model", DEFAULT_BEDROCK_MODEL)).strip() or DEFAULT_BEDROCK_MODEL
    return ChatDefaults(
        storage_dir=storage_dir,
        default_provider=default_provider,
        default_model=default_model,
    )


def _normalize_bedrock_defaults(payload: Any) -> BedrockDefaults:
    if payload is None:
        return BedrockDefaults()
    bedrock = require_object(payload, "bedrock")
    region_value = normalize_optional_string(bedrock.get("region"))
    profile_value = normalize_optional_string(bedrock.get("profile"))
    return BedrockDefaults(region=region_value, profile=profile_value)


def _normalize_openrouter_defaults(payload: Any) -> OpenRouterDefaults:
    if payload is None:
        return OpenRouterDefaults()
    openrouter = require_object(payload, "openrouter")
    base_url = str(openrouter.get("base_url", DEFAULT_OPENROUTER_BASE_URL)).strip() or DEFAULT_OPENROUTER_BASE_URL
    api_key_env = str(openrouter.get("api_key_env", "OPENROUTER_API_KEY")).strip() or "OPENROUTER_API_KEY"
    site_url = normalize_optional_string(openrouter.get("site_url"))
    app_name = normalize_optional_string(openrouter.get("app_name", "bobo"))
    return OpenRouterDefaults(
        base_url=base_url,
        api_key_env=api_key_env,
        site_url=site_url,
        app_name=app_name,
    )


def resolve_workspace_config_path(
    workspace_root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> Path:
    root = resolve_workspace_root(workspace_root)
    if config_path is None:
        return (root / DEFAULT_WORKSPACE_CONFIG_PATH).resolve(strict=False)
    candidate = Path(config_path)
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (root / candidate).resolve(strict=False)


def load_workspace_settings(
    workspace_root: str | Path | None = None,
    config_path: str | Path | None = None,
) -> WorkspaceSettings:
    resolved_config_path = resolve_workspace_config_path(workspace_root, config_path)
    if not resolved_config_path.exists():
        return WorkspaceSettings(config_path=str(resolved_config_path))

    payload = load_json(resolved_config_path)
    return WorkspaceSettings(
        chat=_normalize_chat_defaults(payload.get("chat")),
        bedrock=_normalize_bedrock_defaults(payload.get("bedrock")),
        openrouter=_normalize_openrouter_defaults(payload.get("openrouter")),
        config_path=str(resolved_config_path),
    )


def resolve_chat_storage_dir(
    workspace_root: str | Path | None = None,
    settings: WorkspaceSettings | None = None,
    override: str | Path | None = None,
) -> Path:
    root = resolve_workspace_root(workspace_root)
    config = settings or WorkspaceSettings()
    raw_storage_dir = str(override).strip() if override is not None else config.chat.storage_dir
    if not raw_storage_dir:
        raw_storage_dir = DEFAULT_CHAT_STORAGE_DIR
    return resolve_workspace_path(root, raw_storage_dir)


def build_session_title(raw_title: str | None, fallback: str) -> str:
    if raw_title and raw_title.strip():
        return require_non_empty_string(raw_title, "title")
    return fallback
