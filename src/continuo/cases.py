from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CaseValidationError(ValueError):
    """Raised when a research case cannot safely drive a run."""


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _number_or_none(name: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaseValidationError(f"{name} must be a number or null")
    return float(value)


@dataclass(frozen=True, slots=True)
class ResearchCase:
    id: str
    prompt: str
    expected_duration_seconds: float | None
    forbid_vocals: bool
    duration_tolerance_seconds: float
    require_cross_section_phrase: bool
    source_path: Path
    model: str | None = None
    recorded_response: str | None = None

    @classmethod
    def load(cls, path: Path) -> "ResearchCase":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CaseValidationError(f"case is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise CaseValidationError("case must be a JSON object")
        allowed = {
            "schema_version",
            "id",
            "prompt",
            "policy",
            "model",
            "recorded_response",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise CaseValidationError(f"unknown case fields: {sorted(unknown)}")
        if payload.get("schema_version") != "1.0":
            raise CaseValidationError("unsupported case schema version")

        case_id = payload.get("id")
        if not isinstance(case_id, str) or not CASE_ID_PATTERN.fullmatch(case_id):
            raise CaseValidationError(
                "case id must use lowercase letters, numbers, underscores, or hyphens"
            )
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise CaseValidationError("case prompt cannot be empty")

        policy = payload.get("policy")
        if not isinstance(policy, dict):
            raise CaseValidationError("case policy must be an object")
        allowed_policy = {
            "expected_duration_seconds",
            "forbid_vocals",
            "duration_tolerance_seconds",
            "require_cross_section_phrase",
        }
        unknown_policy = set(policy) - allowed_policy
        if unknown_policy:
            raise CaseValidationError(
                f"unknown case policy fields: {sorted(unknown_policy)}"
            )
        expected_duration = _number_or_none(
            "expected_duration_seconds", policy.get("expected_duration_seconds")
        )
        forbid_vocals = policy.get("forbid_vocals", False)
        if not isinstance(forbid_vocals, bool):
            raise CaseValidationError("forbid_vocals must be a boolean")
        tolerance = _number_or_none(
            "duration_tolerance_seconds",
            policy.get("duration_tolerance_seconds", 0.1),
        )
        if tolerance is None or tolerance < 0:
            raise CaseValidationError("duration_tolerance_seconds must be non-negative")
        if expected_duration is not None and expected_duration <= 0:
            raise CaseValidationError("expected_duration_seconds must be positive")
        require_cross_section_phrase = policy.get(
            "require_cross_section_phrase",
            False,
        )
        if not isinstance(require_cross_section_phrase, bool):
            raise CaseValidationError(
                "require_cross_section_phrase must be a boolean"
            )

        model = payload.get("model")
        if model is not None and (
            not isinstance(model, str) or not model.strip()
        ):
            raise CaseValidationError("model must be a non-empty string")

        recorded_response = payload.get("recorded_response")
        if recorded_response is not None and (
            not isinstance(recorded_response, str) or not recorded_response.strip()
        ):
            raise CaseValidationError("recorded_response must be a non-empty path")
        return cls(
            id=case_id,
            prompt=prompt,
            expected_duration_seconds=expected_duration,
            forbid_vocals=forbid_vocals,
            duration_tolerance_seconds=tolerance,
            require_cross_section_phrase=require_cross_section_phrase,
            source_path=path.resolve(),
            model=model,
            recorded_response=recorded_response,
        )

    def recorded_response_path(self) -> Path | None:
        if self.recorded_response is None:
            return None
        return (self.source_path.parent / self.recorded_response).resolve()

    def snapshot(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "id": self.id,
            "prompt": self.prompt,
            "policy": {
                "expected_duration_seconds": self.expected_duration_seconds,
                "forbid_vocals": self.forbid_vocals,
                "duration_tolerance_seconds": self.duration_tolerance_seconds,
                "require_cross_section_phrase": self.require_cross_section_phrase,
            },
        }
        if self.model is not None:
            payload["model"] = self.model
        if self.recorded_response is not None:
            payload["recorded_response"] = self.recorded_response
        return payload


@dataclass(frozen=True, slots=True)
class RunWorkspace:
    case_id: str
    run_id: str
    path: Path


class ArtifactStore:
    """Create isolated run workspaces grouped under a stable research case."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def create_run(self, case: ResearchCase) -> RunWorkspace:
        now = datetime.now(timezone.utc)
        run_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:8]}"
        path = self.root / case.id / "runs" / run_id
        path.mkdir(parents=True, exist_ok=False)
        _atomic_json(path / "case.json", case.snapshot())
        return RunWorkspace(case_id=case.id, run_id=run_id, path=path)
