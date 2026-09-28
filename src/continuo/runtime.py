from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import DomainValidationError, MusicProject
from .midi import write_midi
from .planning import PlanningProvider, parse_model_plan, tool_manifest
from .rendering import ReferenceWavRenderer, RenderBackend, inspect_wav
from .tools import MusicToolRuntime


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class RunPolicy:
    expected_duration_seconds: float | None = None
    forbid_vocals: bool = False
    duration_tolerance_seconds: float = 0.1


@dataclass(slots=True)
class RunRecord:
    run_id: str
    case_id: str | None
    prompt: str
    state: str
    provider: str
    model: str
    backend: str
    created_at: str
    updated_at: str
    events: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    def transition(self, state: str, detail: str) -> None:
        self.state = state
        self.updated_at = _utc_now()
        self.events.append({"at": self.updated_at, "state": state, "detail": detail})


class AgentRuntime:
    def __init__(self, renderer: RenderBackend | None = None):
        self.renderer = renderer or ReferenceWavRenderer()

    def run(
        self,
        *,
        prompt: str,
        provider: PlanningProvider,
        output_dir: Path,
        policy: RunPolicy,
        run_id: str | None = None,
        case_id: str | None = None,
    ) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("prompt cannot be empty")
        output_dir.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        record = RunRecord(
            run_id=run_id or str(uuid.uuid4()),
            case_id=case_id,
            prompt=prompt,
            state="RECEIVED",
            provider=provider.provider_name,
            model=provider.model_name,
            backend=self.renderer.name,
            created_at=now,
            updated_at=now,
        )
        record.transition("RECEIVED", "User request accepted")
        self._save_record(output_dir, record)
        try:
            raw_response = provider.generate(prompt, tool_manifest())
            (output_dir / "model_response.raw.json").write_text(raw_response, encoding="utf-8")
            audit_record = getattr(provider, "audit_record", None)
            if callable(audit_record):
                provider_envelope = audit_record()
                if provider_envelope is not None:
                    _atomic_json(output_dir / "provider_response.json", provider_envelope)
            record.transition("MODELLED", "Raw provider response persisted")
            self._save_record(output_dir, record)

            plan = parse_model_plan(raw_response)
            _atomic_json(
                output_dir / "plan.json",
                {
                    "schema_version": plan.schema_version,
                    "brief": plan.brief,
                    "rationale": plan.rationale,
                    "tool_calls": [asdict(call) for call in plan.tool_calls],
                },
            )
            record.transition("PLAN_VALIDATED", "Model plan schema accepted")
            self._save_record(output_dir, record)

            project = MusicToolRuntime().apply_plan(plan)
            self._validate_project(project, policy)
            _atomic_json(output_dir / "music_ir.json", project.to_dict())
            record.transition("PROJECT_VALIDATED", "Music IR domain invariants accepted")
            self._save_record(output_dir, record)

            midi_path = output_dir / "composition.mid"
            write_midi(project, midi_path)
            wav_path = output_dir / "audio.wav"
            render_report = self.renderer.render(project, wav_path)
            record.transition("RENDERED", f"Rendered by {render_report.backend}")
            self._save_record(output_dir, record)

            verification = self._verify(
                project,
                wav_path,
                render_report,
                policy,
                run_id=record.run_id,
                case_id=record.case_id,
                provider_name=provider.provider_name,
                model_name=provider.model_name,
            )
            _atomic_json(output_dir / "report.json", verification)
            record.transition("VERIFIED", "All deterministic acceptance checks passed")
            self._save_record(output_dir, record)
            return verification
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"
            record.transition("FAILED", record.error)
            self._save_record(output_dir, record)
            raise

    def _validate_project(self, project: MusicProject, policy: RunPolicy) -> None:
        project.validate(forbid_vocals=policy.forbid_vocals)
        if policy.expected_duration_seconds is not None:
            difference = abs(project.duration_seconds - policy.expected_duration_seconds)
            if difference > policy.duration_tolerance_seconds:
                raise DomainValidationError(
                    "project duration does not satisfy the user-bound run policy"
                )

    def _verify(
        self,
        project: MusicProject,
        wav_path: Path,
        render_report: Any,
        policy: RunPolicy,
        *,
        run_id: str,
        case_id: str | None,
        provider_name: str,
        model_name: str,
    ) -> dict[str, Any]:
        inspection = inspect_wav(wav_path)
        checks = {
            "stereo": inspection["channels"] == 2,
            "sample_rate_44100": inspection["sample_rate"] == 44_100,
            "not_silent": render_report.rms > 0.0005,
            "not_clipped": inspection["peak"] < 0.995,
            "project_valid": True,
            "vocal_policy_satisfied": True,
        }
        if policy.expected_duration_seconds is not None:
            checks["duration_matches"] = (
                abs(inspection["duration_seconds"] - policy.expected_duration_seconds)
                <= policy.duration_tolerance_seconds
            )
        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            raise DomainValidationError(f"verification checks failed: {failed}")
        limitations = [
            "This report proves runtime correctness, not subjective musical quality.",
        ]
        if provider_name == "recorded":
            limitations.append("The recorded provider is not evidence of real model quality.")
        else:
            limitations.append(
                "A single real-provider run is not broad evidence of model quality."
            )
        if render_report.backend == "python-reference":
            limitations.append(
                "The Python renderer is a portable reference backend, not the final SuperCollider DSP backend."
            )
        return {
            "schema_version": "1.0",
            "status": "passed",
            "run": {"id": run_id, "case_id": case_id},
            "checks": checks,
            "audio": inspection,
            "render": asdict(render_report),
            "provider": {"name": provider_name, "model": model_name},
            "project": {
                "title": project.title,
                "duration_seconds": project.duration_seconds,
                "tempo_bpm": project.tempo_bpm,
                "track_count": len(project.tracks),
                "note_event_count": sum(len(track.events) for track in project.tracks),
            },
            "limitations": limitations,
        }

    @staticmethod
    def _save_record(output_dir: Path, record: RunRecord) -> None:
        _atomic_json(output_dir / "run.json", asdict(record))
