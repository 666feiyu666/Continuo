from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from ..model import (
    DomainValidationError,
    MusicProject,
    SUPPORTED_INSTRUMENT_IDS,
    instrument_catalog_manifest,
)


CORE_STAGE = "core"
CORE_REVIEW_STAGE = "core_review"
ARRANGEMENT_STAGE = "arrangement"
CompositionStage = Literal["core", "core_review", "arrangement"]

CORE_TOOL_NAMES = frozenset(
    {
        "create_project",
        "add_section",
        "add_key_region",
        "add_phrase",
        "add_track",
        "add_note",
        "add_note_pattern",
        "add_chord_sequence",
        "add_automation",
        "add_note_sequence",
    }
)
ARRANGEMENT_TOOL_NAMES = frozenset(
    {
        "add_phrase",
        "add_track",
        "add_note",
        "add_note_pattern",
        "add_chord_sequence",
        "add_automation",
        "add_note_sequence",
        "finalize_project",
    }
)
CORE_REVIEW_TOOL_NAMES = frozenset(
    {
        "replace_phrase_notes",
        "finalize_project",
    }
)
ALL_TOOL_NAMES = CORE_TOOL_NAMES | CORE_REVIEW_TOOL_NAMES | ARRANGEMENT_TOOL_NAMES


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
        """Return one raw, untrusted composition-stage proposal."""


class RecordedProvider:
    """Deterministic provider used for path tests, never as quality evidence."""

    provider_name = "recorded"
    model_name = "fixture"

    def __init__(self, response_path: Path):
        self.response_path = response_path

    def generate(self, prompt: str, manifest: dict[str, Any]) -> str:
        if manifest.get("composition_stage") == CORE_REVIEW_STAGE:
            project = manifest.get("current_project", {})
            return json.dumps(
                {
                    "schema_version": "1.0",
                    "brief": {
                        "request": prompt,
                        "duration_seconds": project.get("duration_seconds", 1),
                        "vocals": False,
                        "style": ["recorded fixture"],
                        "creative_summary": "Preserve the recorded core score.",
                    },
                    "tool_calls": [
                        {"name": "finalize_project", "arguments": {}}
                    ],
                    "rationale": "Recorded fixtures bypass model-authored score review.",
                },
                ensure_ascii=False,
            )
        if manifest.get("composition_stage") == ARRANGEMENT_STAGE:
            project = manifest.get("current_project", {})
            return json.dumps(
                {
                    "schema_version": "1.0",
                    "brief": {
                        "request": prompt,
                        "duration_seconds": project.get("duration_seconds", 1),
                        "vocals": False,
                        "style": ["recorded fixture"],
                        "creative_summary": "Preserve the recorded complete score.",
                    },
                    "tool_calls": [
                        {"name": "finalize_project", "arguments": {}}
                    ],
                    "rationale": "Recorded fixtures already contain a complete score.",
                },
                ensure_ascii=False,
            )
        return self.response_path.read_text(encoding="utf-8")

def parse_model_plan(
    raw_response: str,
    *,
    allowed_tools: frozenset[str] = ALL_TOOL_NAMES,
) -> ModelPlan:
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
        if name not in allowed_tools:
            raise DomainValidationError(f"tool is not allowed in this stage: {name}")
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


def tool_manifest(
    stage: CompositionStage,
    *,
    current_project: MusicProject | None = None,
    available_instrument_ids: Collection[str] = SUPPORTED_INSTRUMENT_IDS,
) -> dict[str, Any]:
    if stage == CORE_STAGE:
        tools = CORE_TOOL_NAMES
        rules = [
            "Create exactly one project before editing it.",
            "Write a complete musical spine across the full timeline.",
            "The spine must coordinate anchor material, harmony, bass, and pulse.",
            "Treat a requested duration as an approximate target. Complete the musical "
            "ending naturally and keep the score at or below five minutes.",
        ]
    elif stage == CORE_REVIEW_STAGE:
        if current_project is None:
            raise ValueError("core review stage requires the current project")
        tools = CORE_REVIEW_TOOL_NAMES
        rules = [
            "Review the supplied playable core without adding structural material.",
            "Replace notes only through a named existing phrase on one track.",
            "Treat score-audit diagnostics as evidence, never as universal quotas.",
            "Call finalize_project exactly once and as the final operation.",
        ]
    elif stage == ARRANGEMENT_STAGE:
        if current_project is None:
            raise ValueError("arrangement stage requires the current project")
        tools = ARRANGEMENT_TOOL_NAMES
        rules = [
            "Continue the supplied project; never create or replace it.",
            "Arrange against existing events rather than filling tracks independently.",
            "Call finalize_project exactly once and as the final operation.",
        ]
    else:
        raise ValueError(f"unknown composition stage: {stage}")
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "composition_stage": stage,
        "tools": sorted(tools),
        "available_instruments": instrument_catalog_manifest(
            available_instrument_ids
        ),
        "rules": rules
        + [
            "Use MIDI pitches from 0 through 127.",
            "Use normalized velocities from 0.0 through 1.0.",
            "Use beat-relative timing and keep all events inside the project.",
            "Keep every key region inside its named section.",
            "A note must start inside its named section. Its phrase_id must name an "
            "existing phrase, but pickups and releases may cross phrase boundaries.",
            "Notes on a monophonic instrument track must never overlap.",
            "A variation phrase must preserve the motif_id of its source phrase.",
            "Use add_note_sequence for expressive monophonic lines and encode slurs "
            "or breaths with connection_to_next; a legato articulation label alone "
            "does not connect notes. Express the musical transition intent without "
            "solving a fixed numeric gap threshold; the host realizes the connection.",
            "Use automation for authored expression, breath, modulation, and pitch bend.",
            "Do not invent tools or executable code.",
        ],
    }
    if current_project is not None:
        manifest["current_project"] = current_project.to_dict()
    return manifest
