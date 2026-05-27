from __future__ import annotations

import json
import re
from typing import Any


def parse_codex_json(output_text: str) -> dict[str, Any]:
    stripped = output_text.strip()
    if not stripped:
        raise ValueError("Codex output is empty; expected JSON.")
    if "\n" in stripped:
        event_payload = _parse_codex_jsonl_events(stripped)
        if event_payload is not None:
            return event_payload
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = json.loads(_extract_json_object(stripped))
    if not isinstance(parsed, dict):
        raise ValueError("Codex JSON output must be an object.")
    return parsed


def observe_commands(output_text: str) -> list[str]:
    commands: list[str] = []
    for block in re.findall(r"```(?:bash|sh|shell)?\n(.*?)```", output_text, flags=re.S | re.I):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                commands.append(stripped)
    return commands


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Codex output did not contain a JSON object.")
    return text[start : end + 1]


def _parse_codex_jsonl_events(text: str) -> dict[str, Any] | None:
    messages: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
    for message in reversed(messages):
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(_extract_json_object(message))
            except (json.JSONDecodeError, ValueError):
                continue
        if isinstance(parsed, dict):
            return parsed
    return None
