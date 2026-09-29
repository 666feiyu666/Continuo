from __future__ import annotations

from typing import Any, Collection

from .plans import ALL_TOOL_NAMES
from ..model import (
    SUPPORTED_ARTICULATIONS,
    SUPPORTED_AUTOMATION_PARAMETERS,
    SUPPORTED_INSTRUMENT_IDS,
    SUPPORTED_NOTE_CONNECTIONS,
)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _object(
        {
            "name": {"type": "string", "const": name},
            "arguments": _object(arguments),
        }
    )


def _number(
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "number"}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    if exclusive_minimum is not None:
        schema["exclusiveMinimum"] = exclusive_minimum
    return schema


def _integer(
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def music_plan_schema(
    allowed_tools: Collection[str] = ALL_TOOL_NAMES,
    *,
    allowed_instrument_ids: Collection[str] = SUPPORTED_INSTRUMENT_IDS,
) -> dict[str, Any]:
    allowed_instruments = tuple(allowed_instrument_ids)
    unknown_instruments = set(allowed_instruments) - set(SUPPORTED_INSTRUMENT_IDS)
    if unknown_instruments:
        raise ValueError(
            f"schema requested unknown instruments: {sorted(unknown_instruments)}"
        )
    if not allowed_instruments:
        raise ValueError("schema requires at least one available instrument")
    number = _number()
    nonnegative = _number(minimum=0.0)
    positive = _number(exclusive_minimum=0.0)
    unit = _number(minimum=0.0, maximum=1.0)
    string = {"type": "string", "minLength": 1}
    nullable_string = {"anyOf": [string, {"type": "null"}]}
    articulation = {"type": "string", "enum": list(SUPPORTED_ARTICULATIONS)}
    connection = {"type": "string", "enum": list(SUPPORTED_NOTE_CONNECTIONS)}
    midi_pitch = _integer(minimum=0, maximum=127)
    swing = _number(minimum=0.5, maximum=0.75)
    schemas = {
        "create_project": _tool_call(
            "create_project",
            {
                "title": string,
                "duration_seconds": _number(minimum=0.25, maximum=3600.0),
                "tempo_bpm": _number(minimum=20.0, maximum=320.0),
                "meter_numerator": _integer(minimum=1),
                "meter_denominator": {"type": "integer", "enum": [1, 2, 4, 8, 16]},
                "swing": swing,
                "seed": {"type": "integer"},
            },
        ),
        "add_section": _tool_call(
            "add_section",
            {
                "id": string,
                "label": string,
                "start_beat": nonnegative,
                "end_beat": positive,
            },
        ),
        "add_key_region": _tool_call(
            "add_key_region",
            {
                "id": string,
                "section_id": string,
                "start_beat": nonnegative,
                "end_beat": positive,
                "tonic": string,
                "mode": string,
            },
        ),
        "add_phrase": _tool_call(
            "add_phrase",
            {
                "id": string,
                "label": string,
                "start_beat": nonnegative,
                "end_beat": positive,
                "motif_id": string,
                "variation_of": nullable_string,
            },
        ),
        "add_track": _tool_call(
            "add_track",
            {
                "id": string,
                "name": string,
                "role": string,
                "instrument": _object(
                    {
                        "id": {
                            "type": "string",
                            "enum": list(allowed_instruments),
                        }
                    }
                ),
                "gain": _number(minimum=0.0, maximum=2.0),
                "pan": _number(minimum=-1.0, maximum=1.0),
            },
        ),
        "add_note": _tool_call(
            "add_note",
            {
                "track_id": string,
                "start_beat": nonnegative,
                "duration_beats": positive,
                "pitch": midi_pitch,
                "velocity": unit,
                "section_id": nullable_string,
                "phrase_id": nullable_string,
                "articulation": articulation,
            },
        ),
        "add_note_pattern": _tool_call(
            "add_note_pattern",
            {
                "track_id": string,
                "start_beat": nonnegative,
                "step_beats": positive,
                "duration_beats": positive,
                "pitches": {
                    "type": "array",
                    "items": {"anyOf": [midi_pitch, {"type": "null"}]},
                    "minItems": 1,
                },
                "repeats": _integer(minimum=1, maximum=1024),
                "velocities": {"type": "array", "items": unit, "minItems": 1},
                "swing": swing,
                "section_id": nullable_string,
                "phrase_id": nullable_string,
                "articulations": {
                    "type": "array",
                    "items": articulation,
                    "minItems": 1,
                },
            },
        ),
        "add_chord_sequence": _tool_call(
            "add_chord_sequence",
            {
                "track_id": string,
                "start_beat": nonnegative,
                "beats_per_chord": positive,
                "note_duration_beats": positive,
                "chords": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": midi_pitch,
                        "minItems": 1,
                    },
                    "minItems": 1,
                },
                "velocity": unit,
                "section_id": nullable_string,
                "phrase_id": nullable_string,
                "articulation": articulation,
            },
        ),
        "add_automation": _tool_call(
            "add_automation",
            {
                "track_id": string,
                "beat": nonnegative,
                "parameter": {
                    "type": "string",
                    "enum": list(SUPPORTED_AUTOMATION_PARAMETERS),
                },
                "value": _number(minimum=-1.0, maximum=1.0),
            },
        ),
        "add_note_sequence": _tool_call(
            "add_note_sequence",
            {
                "track_id": string,
                "notes": {
                    "type": "array",
                    "minItems": 1,
                    "items": _object(
                        {
                            "start_beat": nonnegative,
                            "duration_beats": positive,
                            "pitch": midi_pitch,
                            "velocity": unit,
                            "section_id": nullable_string,
                            "phrase_id": nullable_string,
                            "articulation": articulation,
                            "connection_to_next": connection,
                        }
                    ),
                },
            },
        ),
        "finalize_project": _tool_call("finalize_project", {}),
    }
    unknown = set(allowed_tools) - set(schemas)
    if unknown:
        raise ValueError(f"schema requested unknown tools: {sorted(unknown)}")
    calls = [schemas[name] for name in schemas if name in allowed_tools]
    return _object(
        {
            "schema_version": {"type": "string", "const": "1.0"},
            "brief": _object(
                {
                    "request": string,
                    "duration_seconds": _number(minimum=0.25, maximum=3600.0),
                    "vocals": {"type": "boolean"},
                    "style": {"type": "array", "items": string},
                    "creative_summary": string,
                }
            ),
            "tool_calls": {
                "type": "array",
                "items": {"anyOf": calls},
                "minItems": 1,
            },
            "rationale": string,
        }
    )
