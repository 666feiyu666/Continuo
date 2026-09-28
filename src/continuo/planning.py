from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import DomainValidationError


ALLOWED_TOOL_NAMES = {
    "create_project",
    "add_section",
    "add_track",
    "add_note",
    "add_note_pattern",
    "add_chord_sequence",
    "add_automation",
    "set_master",
}


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelPlan:
    schema_version: str
    brief: dict[str, Any]
    tool_calls: tuple[ToolCall, ...]
    rationale: str


class PlanningProvider(Protocol):
    provider_name: str
    model_name: str

    def generate(self, prompt: str, tool_manifest: dict[str, Any]) -> str:
        """Return the raw, untrusted model response."""


class RecordedProvider:
    """Deterministic provider used for path tests, never as model-quality evidence."""

    provider_name = "recorded"
    model_name = "fixture"

    def __init__(self, response_path: Path):
        self.response_path = response_path

    def generate(self, prompt: str, tool_manifest: dict[str, Any]) -> str:
        del prompt, tool_manifest
        return self.response_path.read_text(encoding="utf-8")


def parse_model_plan(raw_response: str) -> ModelPlan:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise DomainValidationError(f"model response is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DomainValidationError("model response must be a JSON object")
    allowed_top_level = {"schema_version", "brief", "tool_calls", "rationale"}
    unknown = set(payload) - allowed_top_level
    if unknown:
        raise DomainValidationError(f"unknown model response fields: {sorted(unknown)}")
    if payload.get("schema_version") != "1.0":
        raise DomainValidationError("unsupported model plan schema version")
    brief = payload.get("brief")
    if not isinstance(brief, dict):
        raise DomainValidationError("brief must be an object")
    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise DomainValidationError("tool_calls must be a non-empty list")
    if len(raw_calls) > 4096:
        raise DomainValidationError("model plan exceeds the tool-call budget")
    calls: list[ToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict) or set(raw_call) != {"name", "arguments"}:
            raise DomainValidationError(f"invalid tool call at index {index}")
        name = raw_call["name"]
        arguments = raw_call["arguments"]
        if name not in ALLOWED_TOOL_NAMES:
            raise DomainValidationError(f"unknown tool requested: {name}")
        if not isinstance(arguments, dict):
            raise DomainValidationError(f"tool arguments must be an object: {name}")
        calls.append(ToolCall(name=name, arguments=arguments))
    rationale = payload.get("rationale", "")
    if not isinstance(rationale, str):
        raise DomainValidationError("rationale must be a string")
    return ModelPlan(
        schema_version="1.0",
        brief=brief,
        tool_calls=tuple(calls),
        rationale=rationale,
    )


def tool_manifest() -> dict[str, Any]:
    """Compact provider-facing contract; runtime validation remains authoritative."""
    return {
        "schema_version": "1.0",
        "tools": sorted(ALLOWED_TOOL_NAMES),
        "rules": [
            "Create exactly one project before editing it.",
            "Use MIDI pitches from 0 through 127.",
            "Use beat-relative timing and keep all events inside the project.",
            "Do not invent tools or executable code.",
        ],
    }
