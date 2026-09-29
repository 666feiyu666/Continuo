from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from ..model import DomainValidationError, MusicProject, instrument_definition
from ..rendering.soundfont.profile import SoundFontProfile


CORE_STAGE = "core"
ARRANGEMENT_STAGE = "arrangement"
CompositionStage = Literal["core", "arrangement"]

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
        "finalize_project",
    }
)
ALL_TOOL_NAMES = CORE_TOOL_NAMES | ARRANGEMENT_TOOL_NAMES


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


class SoundFontMappingProvider(Protocol):
    def map_soundfont(
        self,
        *,
        prompt: str,
        project: MusicProject,
        soundfont_profile: SoundFontProfile,
        skill_instructions: str,
    ) -> str:
        """Return a raw, untrusted SoundFont Mapping IR proposal."""


class RecordedProvider:
    """Deterministic provider used for path tests, never as quality evidence."""

    provider_name = "recorded"
    model_name = "fixture"

    def __init__(self, response_path: Path):
        self.response_path = response_path

    def generate(self, prompt: str, manifest: dict[str, Any]) -> str:
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

    def map_soundfont(
        self,
        *,
        prompt: str,
        project: MusicProject,
        soundfont_profile: SoundFontProfile,
        skill_instructions: str,
    ) -> str:
        """Deterministic mapping substitute for path tests, not quality evidence."""

        del prompt, skill_instructions
        assignments = []
        percussion_preset = next(
            (
                preset
                for preset in soundfont_profile.presets
                if preset.is_percussion and preset.bank == 128 and preset.program == 0
            ),
            next(
                (
                    preset
                    for preset in soundfont_profile.presets
                    if preset.is_percussion
                ),
                None,
            ),
        )
        for track in project.tracks:
            instrument = instrument_definition(track.instrument.id)
            if instrument.is_percussion:
                preferred = percussion_preset
            else:
                melodic = [
                    preset
                    for preset in soundfont_profile.presets
                    if not preset.is_percussion
                ]
                preferred = next(
                    (
                        preset
                        for preset in melodic
                        if preset.bank == 0 and preset.program == instrument.program
                    ),
                    next(
                        (
                            preset
                            for preset in melodic
                            if preset.program == instrument.program
                        ),
                        None,
                    ),
                )
            if preferred is None:
                raise DomainValidationError(
                    "recorded mapping substitute found no compatible preset for "
                    f"{track.instrument.id}"
                )
            assignments.append(
                {
                    "track_id": track.id,
                    "preset_id": preferred.id,
                    "reason": "recorded-provider deterministic test substitute",
                }
            )
        return json.dumps(
            {
                "schema_version": "1.0",
                "master_gain": 0.85,
                "reverb_enabled": True,
                "assignments": assignments,
            },
            ensure_ascii=False,
        )


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
) -> dict[str, Any]:
    if stage == CORE_STAGE:
        tools = CORE_TOOL_NAMES
        rules = [
            "Create exactly one project before editing it.",
            "Write a complete musical spine across the full timeline.",
            "The spine must coordinate anchor material, harmony, bass, and pulse.",
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
        "rules": rules
        + [
            "Use MIDI pitches from 0 through 127.",
            "Use normalized velocities from 0.0 through 1.0.",
            "Use beat-relative timing and keep all events inside the project.",
            "Do not invent tools or executable code.",
        ],
    }
    if current_project is not None:
        manifest["current_project"] = current_project.to_dict()
    return manifest
