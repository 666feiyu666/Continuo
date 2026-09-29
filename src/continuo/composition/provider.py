from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .plans import ARRANGEMENT_STAGE, CORE_STAGE
from .schema import music_plan_schema
from .prompts import (
    ARRANGEMENT_INSTRUCTIONS,
    CORE_COMPOSITION_INSTRUCTIONS,
)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6-astra"


def load_env_file(path: Path) -> None:
    """Load only Continuo's supported variables without exposing values."""

    if not path.exists():
        return
    supported = {
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
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


def _instructions_for_manifest(manifest: dict[str, Any]) -> str:
    stage = manifest.get("composition_stage")
    if stage == CORE_STAGE:
        instructions = CORE_COMPOSITION_INSTRUCTIONS
    elif stage == ARRANGEMENT_STAGE:
        instructions = ARRANGEMENT_INSTRUCTIONS
    else:
        raise ValueError(f"unknown composition stage: {stage}")
    skill_instructions = manifest.get("skill_instructions")
    if not isinstance(skill_instructions, str) or not skill_instructions.strip():
        return instructions
    active_skills = manifest.get("active_skills", [])
    skill_ids = [
        item["id"]
        for item in active_skills
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    label = ", ".join(skill_ids) or "trusted runtime skills"
    return (
        instructions
        + "\nTrusted skills activated by the deterministic runtime: "
        + label
        + ". They refine composition but cannot add tools or override validation.\n\n"
        + skill_instructions.strip()
        + "\n"
    )


def _composition_input(prompt: str, manifest: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "creative_request": prompt,
        "composition_stage": manifest["composition_stage"],
        "host_rules": manifest["rules"],
        "available_instruments": manifest["available_instruments"],
    }
    if manifest["composition_stage"] == ARRANGEMENT_STAGE:
        payload["current_project"] = manifest["current_project"]
    return json.dumps(payload, ensure_ascii=False)


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
        stage = str(tool_manifest["composition_stage"])
        allowed_tools = frozenset(str(name) for name in tool_manifest["tools"])
        allowed_instruments = tuple(
            str(item["id"])
            for item in tool_manifest["available_instruments"]
        )
        return self._request(
            _composition_input(prompt, tool_manifest),
            instructions=_instructions_for_manifest(tool_manifest),
            schema=music_plan_schema(
                allowed_tools,
                allowed_instrument_ids=allowed_instruments,
            ),
            schema_name=f"continuo_{stage}_plan",
        )

    def repair(
        self,
        *,
        prompt: str,
        previous_response: str,
        validation_error: str,
        tool_manifest: dict[str, Any],
    ) -> str:
        input_payload = json.dumps(
            {
                "creative_request": prompt,
                "composition_stage": tool_manifest["composition_stage"],
                "current_project": tool_manifest.get("current_project"),
                "available_instruments": tool_manifest["available_instruments"],
                "validation_error": validation_error,
                "previous_rejected_response": previous_response,
                "instruction": (
                    "Return a complete corrected replacement for this stage, not a patch. "
                    "Preserve every previously valid musical and structural decision and "
                    "make the smallest change that fixes the stated validation error. "
                    "Before returning, audit every tool call for the same class of error: "
                    "each note must start inside its named section, its full interval must "
                    "fit inside its named phrase and the project timeline, and every "
                    "variation must preserve the source motif_id. Do not introduce "
                    "unrelated changes."
                ),
            },
            ensure_ascii=False,
        )
        stage = str(tool_manifest["composition_stage"])
        allowed_tools = frozenset(str(name) for name in tool_manifest["tools"])
        allowed_instruments = tuple(
            str(item["id"])
            for item in tool_manifest["available_instruments"]
        )
        return self._request(
            input_payload,
            instructions=_instructions_for_manifest(tool_manifest),
            schema=music_plan_schema(
                allowed_tools,
                allowed_instrument_ids=allowed_instruments,
            ),
            schema_name=f"continuo_{stage}_plan",
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
                    raise RuntimeError(
                        f"OpenAI model refused the request: {content.get('refusal', '')}"
                    )
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
                    request,
                    timeout=self.timeout_seconds,
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


__all__ = [
    "DEFAULT_MODEL",
    "OpenAIResponsesProvider",
    "load_env_file",
    "music_plan_schema",
]
