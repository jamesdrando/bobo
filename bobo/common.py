from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or normalized.lower() in {"none", "null"}:
        return None
    return normalized


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
    return slug or "item"


def render_bullets(items: list[str]) -> str:
    if not items:
        return "- None specified."
    return "\n".join(f"- {item}" for item in items)


def require_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object.")
    return value


def read_text_input(input_file: str) -> str:
    if input_file == "-":
        return sys.stdin.read()
    return Path(input_file).read_text(encoding="utf-8")


def parse_json_text(raw_text: str, field_name: str) -> Any:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON: {exc.msg}") from exc


def parse_optional_json_object(
    raw_json: str | None,
    input_file: str | None,
    field_name: str,
) -> dict[str, Any]:
    if raw_json is None and input_file is None:
        return {}
    if raw_json is not None and input_file is not None:
        raise ValueError(f"Provide only one of inline JSON or file input for {field_name}.")

    source_text = raw_json if raw_json is not None else read_text_input(input_file or "")
    payload = parse_json_text(source_text, field_name)
    return require_object(payload, field_name)


def print_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
