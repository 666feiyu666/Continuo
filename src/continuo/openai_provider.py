from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .domain import SUPPORTED_SYNTH_VOICES


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-astra"


def load_env_file(path: Path) -> None:
    """Load only Continuo's supported variables without printing or persisting values."""
    if not path.exists():
        return
    supported = {"OPENAI_API_KEY", "OPENAI_MODEL"}
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
    synth = {
        "voice": {
            "type": "string",
            "enum": list(SUPPORTED_SYNTH_VOICES),
        },
        "oscillator": {"type": "string", "enum": ["sine", "triangle", "square", "saw"]},
        "partials": {
            "type": "array",
            "items": unit,
            "minItems": 1,
            "maxItems": 8,
        },
        "noise_mix": unit,
        "attack_seconds": _number(minimum=0.0, maximum=10.0),
        "decay_seconds": _number(minimum=0.0, maximum=10.0),
        "sustain_level": unit,
        "release_seconds": _number(minimum=0.0, maximum=20.0),
        "gain": unit,
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
                "synth": _object(synth),
                "gain": _number(minimum=0.0, maximum=2.0),
                "pan": _number(minimum=-1.0, maximum=1.0),
                "midi_channel": _integer(minimum=0, maximum=15),
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


SYSTEM_INSTRUCTIONS = """You are the composition model inside Continuo, a programmable music design studio.
Translate the user's creative request into one executable JSON music plan using only the supplied schema.

The core is genre-independent. Never emit genre preset flags or executable code. Make the musical decisions yourself: tempo, meter, form, harmony, melody, instrumentation, synthesis, dynamics, and mix.

Tool semantics:
- create_project must be first and called exactly once. Timeline length in beats is duration_seconds * tempo_bpm / 60.
- add_section describes form; all section bounds must be inside the timeline.
- add_track creates a voice from a generic Sound Spec. Choose voice=oscillator for abstract
  synthesis, or an acoustic physical-model voice when the requested instrumentation calls for it.
  Acoustic voices are acoustic_piano, upright_bass, vibraphone, tenor_sax, soft_kick,
  brush_snare, and ride_cymbal. When the user requests saxophone, use tenor_sax rather
  than oscillator. MIDI channel 9 is appropriate for percussion.
- add_note writes one event.
- add_note_pattern expands a pitch/rest sequence at step_beats; null is a rest. Its total expanded span must stay inside the timeline. Use this compactly for rhythmic and melodic material.
- add_chord_sequence writes simultaneous MIDI pitches for each chord at beats_per_chord spacing.
- add_automation records an editable parameter point.
- set_master controls room ambience, peak ceiling, and ending fade.

All MIDI pitches must be 0..127. Velocities are normalized floating-point values from 0.0 through 1.0; never use MIDI-style 0..127 velocity values. All pan, gain, noise mix, sustain, room mix, and target peak values must stay within the exact JSON Schema bounds. Swing must be 0.5..0.75. Use a stable integer seed.

Create enough actual musical material to sustain the requested duration without a single unvaried loop. Prefer a small number of compact patterns and chord sequences over hundreds of add_note calls. Keep the result editable. User duration and no-vocal requirements are strict. Do not include vocal, voice, choir, or speech tracks or samples when vocals are forbidden.
"""


class OpenAIResponsesProvider:
    provider_name = "openai-responses"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        max_output_tokens: int = 24_000,
        timeout_seconds: float = 240.0,
        max_request_attempts: int = 3,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is missing")
        self._api_key = api_key
        self.model_name = model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        if max_request_attempts < 1:
            raise ValueError("max_request_attempts must be positive")
        self.max_request_attempts = max_request_attempts
        self._audit_record: dict[str, Any] | None = None

    def generate(self, prompt: str, tool_manifest: dict[str, Any]) -> str:
        del tool_manifest
        return self._request(prompt)

    def repair(
        self,
        *,
        prompt: str,
        previous_response: str,
        validation_error: str,
        tool_manifest: dict[str, Any],
    ) -> str:
        del tool_manifest
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
        return self._request(repair_prompt)

    def _request(self, input_payload: str) -> str:
        payload = {
            "model": self.model_name,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": input_payload,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "continuo_music_plan",
                    "strict": True,
                    "schema": music_plan_schema(),
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
