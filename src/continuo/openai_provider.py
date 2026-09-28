from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


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


def music_plan_schema() -> dict[str, Any]:
    number = {"type": "number"}
    integer = {"type": "integer"}
    string = {"type": "string"}
    synth = {
        "oscillator": {"type": "string", "enum": ["sine", "triangle", "square", "saw"]},
        "partials": {"type": "array", "items": number},
        "noise_mix": number,
        "attack_seconds": number,
        "decay_seconds": number,
        "sustain_level": number,
        "release_seconds": number,
        "gain": number,
    }
    calls = [
        _tool_call(
            "create_project",
            {
                "title": string,
                "duration_seconds": number,
                "tempo_bpm": number,
                "meter_numerator": integer,
                "meter_denominator": integer,
                "swing": number,
                "seed": integer,
            },
        ),
        _tool_call(
            "add_section",
            {
                "id": string,
                "label": string,
                "start_beat": number,
                "end_beat": number,
            },
        ),
        _tool_call(
            "add_track",
            {
                "id": string,
                "name": string,
                "role": string,
                "synth": _object(synth),
                "gain": number,
                "pan": number,
                "midi_channel": integer,
            },
        ),
        _tool_call(
            "add_note",
            {
                "track_id": string,
                "start_beat": number,
                "duration_beats": number,
                "pitch": integer,
                "velocity": number,
            },
        ),
        _tool_call(
            "add_note_pattern",
            {
                "track_id": string,
                "start_beat": number,
                "step_beats": number,
                "duration_beats": number,
                "pitches": {
                    "type": "array",
                    "items": {"anyOf": [integer, {"type": "null"}]},
                },
                "repeats": integer,
                "velocities": {"type": "array", "items": number},
                "swing": number,
            },
        ),
        _tool_call(
            "add_chord_sequence",
            {
                "track_id": string,
                "start_beat": number,
                "beats_per_chord": number,
                "duration_beats": number,
                "chords": {
                    "type": "array",
                    "items": {"type": "array", "items": integer},
                },
                "velocity": number,
            },
        ),
        _tool_call(
            "add_automation",
            {
                "track_id": string,
                "beat": number,
                "parameter": string,
                "value": number,
            },
        ),
        _tool_call(
            "set_master",
            {
                "room_mix": number,
                "room_delay_seconds": number,
                "target_peak": number,
                "fade_out_seconds": number,
            },
        ),
    ]
    return _object(
        {
            "schema_version": {"type": "string", "const": "1.0"},
            "brief": _object(
                {
                    "request": string,
                    "duration_seconds": number,
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
- add_track creates a generic synthesized voice. MIDI channel 9 is appropriate for percussion.
- add_note writes one event.
- add_note_pattern expands a pitch/rest sequence at step_beats; null is a rest. Its total expanded span must stay inside the timeline. Use this compactly for rhythmic and melodic material.
- add_chord_sequence writes simultaneous MIDI pitches for each chord at beats_per_chord spacing.
- add_automation records an editable parameter point.
- set_master controls room ambience, peak ceiling, and ending fade.

All MIDI pitches must be 0..127. All velocities, pan, gain, noise mix, sustain, room mix, and target peak must stay within the domain ranges implied by their names. Swing must be 0.5..0.75. Use a stable integer seed.

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
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is missing")
        self._api_key = api_key
        self.model_name = model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self._audit_record: dict[str, Any] | None = None

    def generate(self, prompt: str, tool_manifest: dict[str, Any]) -> str:
        del tool_manifest
        payload = {
            "model": self.model_name,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": prompt,
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
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI Responses API returned HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI Responses API request failed: {exc.reason}") from exc
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

    def audit_record(self) -> dict[str, Any] | None:
        return self._audit_record
