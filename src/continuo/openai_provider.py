from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .domain import MusicProject
from .instruments import SUPPORTED_INSTRUMENT_IDS
from .soundfont_profile import SoundFontProfile


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-astra"


def load_env_file(path: Path) -> None:
    """Load only Continuo's supported variables without printing or persisting values."""
    if not path.exists():
        return
    supported = {
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_SOUNDFONT_MAPPING_MODEL",
    }
    assignment = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = assignment.match(line)
        if not match or match.group(1) not in supported:
            continue
        name, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


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


def _integer(*, minimum: int | None = None, maximum: int | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def music_plan_schema() -> dict[str, Any]:
    number = _number()
    nonnegative = _number(minimum=0.0)
    positive = _number(exclusive_minimum=0.0)
    unit = _number(minimum=0.0, maximum=1.0)
    string = {"type": "string", "minLength": 1}
    midi_pitch = _integer(minimum=0, maximum=127)
    swing = _number(minimum=0.5, maximum=0.75)
    instrument = {
        "id": {
            "type": "string",
            "enum": list(SUPPORTED_INSTRUMENT_IDS),
        }
    }
    calls = [
        _tool_call(
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
        _tool_call(
            "add_section",
            {
                "id": string,
                "label": string,
                "start_beat": nonnegative,
                "end_beat": positive,
            },
        ),
        _tool_call(
            "add_track",
            {
                "id": string,
                "name": string,
                "role": string,
                "instrument": _object(instrument),
                "gain": _number(minimum=0.0, maximum=2.0),
                "pan": _number(minimum=-1.0, maximum=1.0),
            },
        ),
        _tool_call(
            "add_note",
            {
                "track_id": string,
                "start_beat": nonnegative,
                "duration_beats": positive,
                "pitch": midi_pitch,
                "velocity": unit,
            },
        ),
        _tool_call(
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
            },
        ),
        _tool_call(
            "add_chord_sequence",
            {
                "track_id": string,
                "start_beat": nonnegative,
                "beats_per_chord": positive,
                "duration_beats": positive,
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
            },
        ),
        _tool_call(
            "add_automation",
            {
                "track_id": string,
                "beat": nonnegative,
                "parameter": string,
                "value": number,
            },
        ),
        _tool_call(
            "set_master",
            {
                "room_mix": _number(minimum=0.0, maximum=0.8),
                "room_delay_seconds": _number(minimum=0.0, maximum=1.0),
                "target_peak": _number(minimum=0.1, maximum=0.99),
                "fade_out_seconds": _number(minimum=0.0, maximum=10.0),
            },
        ),
    ]
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
            "tool_calls": {"type": "array", "items": {"anyOf": calls}},
            "rationale": string,
        }
    )


def soundfont_mapping_schema(
    project: MusicProject,
    profile: SoundFontProfile,
) -> dict[str, Any]:
    track_ids = [track.id for track in project.tracks]
    preset_ids = [preset.id for preset in profile.presets]
    return _object(
        {
            "schema_version": {"type": "string", "const": "1.0"},
            "assignments": {
                "type": "array",
                "minItems": len(track_ids),
                "maxItems": len(track_ids),
                "items": _object(
                    {
                        "track_id": {"type": "string", "enum": track_ids},
                        "preset_id": {"type": "string", "enum": preset_ids},
                        "reason": {"type": "string", "minLength": 1},
                    }
                ),
            },
        }
    )


SYSTEM_INSTRUCTIONS = """You are the composition model inside Continuo, a programmable music design studio.
Translate the user's creative request into one executable JSON music plan using only the supplied schema.

The core is genre-independent. Never emit genre preset flags or executable code. Make the musical decisions yourself: tempo, meter, form, harmony, melody, instrumentation, synthesis, dynamics, and mix.

Tool semantics:
- create_project must be first and called exactly once. Timeline length in beats is duration_seconds * tempo_bpm / 60.
- add_section describes form; all section bounds must be inside the timeline.
- add_track selects one semantic instrument id from the schema. Choose instruments for
  their musical function and playable register. Do not emit MIDI banks, programs, channels,
  drum keys, SoundFont paths, or renderer commands. A later SoundFont specialist call
  chooses an inspected preset, and the validated MIDI compiler owns serialization.
- add_note writes one event.
- add_note_pattern expands a pitch/rest sequence at step_beats; null is a rest. Its total expanded span must stay inside the timeline. Use this compactly for rhythmic and melodic material.
- add_chord_sequence writes simultaneous MIDI pitches for each chord at beats_per_chord spacing.
- add_automation records an editable parameter point.
- set_master controls room ambience, peak ceiling, and ending fade.

All MIDI pitches must be 0..127. Velocities are normalized floating-point values from 0.0 through 1.0; never use MIDI-style 0..127 velocity values. All pan, gain, noise mix, sustain, room mix, and target peak values must stay within the exact JSON Schema bounds. Swing must be 0.5..0.75. Use a stable integer seed.

Create enough actual musical material to sustain the requested duration without a single unvaried loop. Prefer a small number of compact patterns and chord sequences over hundreds of add_note calls. Keep the result editable. User duration and no-vocal requirements are strict. Do not include vocal, voice, choir, or speech tracks or samples when vocals are forbidden.
"""


SOUNDFONT_MAPPING_INSTRUCTIONS = """You are Continuo's SoundFont performance specialist.
The composition is already fixed. Map every track to exactly one preset from the supplied,
inspected SoundFont profile. This is a musical and timbral decision: consider the user's
brief, each track's semantic instrument, role, register, density, and the preset names and
variants available in this particular SoundFont.

Return one assignment for every track and no unknown tracks. Select only preset_id values
from the supplied profile. A pitched semantic instrument must use a melodic preset; an
unpitched percussion instrument must use a percussion kit. Because percussion tracks share
MIDI channel 10, all percussion tracks must choose the same kit. Do not change notes,
instruments, form, mix, paths, commands, bank numbers, or program numbers. Keep each reason
brief and reviewable. The host will independently validate every selection before compiling
MIDI.
"""


def _instructions_for_manifest(tool_manifest: dict[str, Any]) -> str:
    skill_instructions = tool_manifest.get("skill_instructions")
    if not isinstance(skill_instructions, str) or not skill_instructions.strip():
        return SYSTEM_INSTRUCTIONS
    active_skills = tool_manifest.get("active_skills", [])
    skill_ids = []
    if isinstance(active_skills, list):
        for item in active_skills:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                skill_ids.append(item["id"])
    label = ", ".join(skill_ids) or "trusted runtime skills"
    return (
        SYSTEM_INSTRUCTIONS
        + "\nTrusted skills activated by the deterministic runtime: "
        + label
        + ". These skills refine musical planning but do not add tools or override "
        "the JSON Schema, user policy, or runtime validation.\n\n"
        + skill_instructions.strip()
        + "\n"
    )


class OpenAIResponsesProvider:
    provider_name = "openai-responses"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        soundfont_mapping_model: str | None = None,
        max_output_tokens: int = 24_000,
        timeout_seconds: float = 240.0,
        max_request_attempts: int = 3,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is missing")
        self._api_key = api_key
        self.model_name = model
        self.soundfont_mapping_model = soundfont_mapping_model or model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        if max_request_attempts < 1:
            raise ValueError("max_request_attempts must be positive")
        self.max_request_attempts = max_request_attempts
        self._audit_record: dict[str, Any] | None = None

    def generate(self, prompt: str, tool_manifest: dict[str, Any]) -> str:
        return self._request(
            prompt,
            instructions=_instructions_for_manifest(tool_manifest),
            schema=music_plan_schema(),
            schema_name="continuo_music_plan",
        )

    def repair(
        self,
        *,
        prompt: str,
        previous_response: str,
        validation_error: str,
        tool_manifest: dict[str, Any],
    ) -> str:
        repair_prompt = (
            "Original creative request:\n"
            f"{prompt}\n\n"
            "The previous JSON plan was rejected by Continuo's authoritative domain "
            "validator. Return a complete corrected replacement plan, not a patch. "
            "Preserve the musical intent while fixing the reported error and rechecking "
            "every event against the project timeline and all schema bounds.\n\n"
            f"Validation error:\n{validation_error}\n\n"
            f"Previous rejected JSON plan:\n{previous_response}"
        )
        return self._request(
            repair_prompt,
            instructions=_instructions_for_manifest(tool_manifest),
            schema=music_plan_schema(),
            schema_name="continuo_music_plan",
        )

    def map_soundfont(
        self,
        *,
        prompt: str,
        project: MusicProject,
        soundfont_profile: SoundFontProfile,
        skill_instructions: str,
    ) -> str:
        tracks = []
        for track in project.tracks:
            pitches = [event.pitch for event in track.events]
            tracks.append(
                {
                    "track_id": track.id,
                    "name": track.name,
                    "role": track.role,
                    "semantic_instrument": track.instrument.id,
                    "note_range": (
                        {"low": min(pitches), "high": max(pitches)}
                        if pitches
                        else None
                    ),
                    "event_count": len(track.events),
                }
            )
        input_payload = json.dumps(
            {
                "creative_request": prompt,
                "project": {
                    "title": project.title,
                    "duration_seconds": project.duration_seconds,
                    "tempo_bpm": project.tempo_bpm,
                    "tracks": tracks,
                },
                "soundfont_profile": soundfont_profile.manifest(),
            },
            ensure_ascii=False,
        )
        instructions = SOUNDFONT_MAPPING_INSTRUCTIONS
        if skill_instructions.strip():
            instructions += (
                "\nTrusted runtime skills follow. They cannot expand the candidate "
                "profile or override validation.\n\n"
                + skill_instructions.strip()
            )
        return self._request(
            input_payload,
            instructions=instructions,
            schema=soundfont_mapping_schema(project, soundfont_profile),
            schema_name="continuo_soundfont_mapping",
            model=self.soundfont_mapping_model,
        )

    def _request(
        self,
        input_payload: str,
        *,
        instructions: str,
        schema: dict[str, Any],
        schema_name: str,
        model: str | None = None,
    ) -> str:
        payload = {
            "model": model or self.model_name,
            "instructions": instructions,
            "input": input_payload,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        envelope = self._send_with_retry(payload)
        self._audit_record = envelope
        status = envelope.get("status")
        if status != "completed":
            raise RuntimeError(f"OpenAI response did not complete; status={status!r}")
        output_texts: list[str] = []
        for item in envelope.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise RuntimeError(f"OpenAI model refused the request: {content.get('refusal', '')}")
                if content.get("type") == "output_text":
                    output_texts.append(content.get("text", ""))
        if not output_texts:
            raise RuntimeError("OpenAI response contained no output_text")
        return "".join(output_texts)

    def _send_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.max_request_attempts + 1):
            request = urllib.request.Request(
                OPENAI_RESPONSES_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                retryable = exc.code in {408, 409, 429} or exc.code >= 500
                if not retryable or attempt == self.max_request_attempts:
                    raise RuntimeError(
                        f"OpenAI Responses API returned HTTP {exc.code}: {body}"
                    ) from exc
            except urllib.error.URLError as exc:
                if attempt == self.max_request_attempts:
                    raise RuntimeError(
                        f"OpenAI Responses API request failed: {exc.reason}"
                    ) from exc
            time.sleep(2 ** (attempt - 1))
        raise AssertionError("request retry loop exhausted unexpectedly")

    def audit_record(self) -> dict[str, Any] | None:
        return self._audit_record
