from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import DomainValidationError, MusicProject
from .expressive_performance import (
    ExpressivePerformance,
    parse_expressive_performance,
    resolve_expressive_performance,
)
from .midi import write_midi
from .planning import PlanningProvider, parse_model_plan
from .planning_graph import PlanningGraphRunner
from .rendering import ReferenceWavRenderer, RenderBackend, inspect_wav
from .skills import SkillRegistry
from .soundfont_mapping import (
    SoundFontMapping,
    parse_soundfont_mapping,
    resolve_soundfont_mapping,
)
from .soundfont_profile import SoundFontProfile
from .score_identity import score_sha256
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
    plan_attempts: int = 0
    expressive_performance_attempts: int = 0
    soundfont_mapping_attempts: int = 0
    skills: list[dict[str, str]] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    def transition(self, state: str, detail: str) -> None:
        self.state = state
        self.updated_at = _utc_now()
        self.events.append({"at": self.updated_at, "state": state, "detail": detail})


class AgentRuntime:
    def __init__(
        self,
        renderer: RenderBackend | None = None,
        *,
        max_plan_attempts: int = 5,
        skill_registry: SkillRegistry | None = None,
    ):
        if max_plan_attempts < 1:
            raise ValueError("max_plan_attempts must be positive")
        self.renderer = renderer or ReferenceWavRenderer()
        self.max_plan_attempts = max_plan_attempts
        self.skill_registry = skill_registry or SkillRegistry.default()

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
            soundfont_profile: SoundFontProfile | None = None
            profile_loader = getattr(self.renderer, "soundfont_profile", None)
            if callable(profile_loader):
                soundfont_profile = profile_loader()
            render_target: dict[str, Any] = {"backend": self.renderer.name}
            if soundfont_profile is not None:
                render_target["soundfont_profile"] = soundfont_profile.manifest()

            validated: dict[str, Any] = {}
            latest_provider_envelope: dict[str, Any] = {"value": None}

            def on_skills_resolved(skills: list[dict[str, str]]) -> None:
                record.skills = [dict(skill) for skill in skills]
                skill_ids = ", ".join(skill["id"] for skill in skills)
                record.transition("SKILLS_RESOLVED", f"Activated skills: {skill_ids}")
                self._save_record(output_dir, record)

            def on_attempt(
                attempt: int,
                raw_response: str,
                provider_envelope: dict[str, Any] | None,
            ) -> None:
                record.plan_attempts = attempt
                attempt_name = f"attempt-{attempt:02d}"
                (output_dir / f"model_response.{attempt_name}.raw.json").write_text(
                    raw_response,
                    encoding="utf-8",
                )
                if provider_envelope is not None:
                    _atomic_json(
                        output_dir / f"provider_response.{attempt_name}.json",
                        provider_envelope,
                    )
                latest_provider_envelope["value"] = provider_envelope
                record.transition(
                    "MODELLED",
                    f"Raw provider response persisted for planning attempt {attempt}",
                )
                self._save_record(output_dir, record)

            def validate_response(raw_response: str, attempt: int) -> None:
                validated.clear()
                attempt_name = f"attempt-{attempt:02d}"
                plan = parse_model_plan(raw_response)
                plan_payload = {
                    "schema_version": plan.schema_version,
                    "brief": plan.brief,
                    "rationale": plan.rationale,
                    "tool_calls": [asdict(call) for call in plan.tool_calls],
                }
                _atomic_json(
                    output_dir / f"plan.{attempt_name}.json",
                    plan_payload,
                )
                project = MusicToolRuntime().apply_plan(plan)
                self._validate_project(project, policy)
                validated.update(
                    {"plan": plan, "plan_payload": plan_payload, "project": project}
                )

            def on_rejected(attempt: int, exc: DomainValidationError) -> None:
                attempt_name = f"attempt-{attempt:02d}"
                _atomic_json(
                    output_dir / f"validation_error.{attempt_name}.json",
                    {
                        "schema_version": "1.0",
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                record.transition(
                    "PLAN_REJECTED",
                    f"Planning attempt {attempt} rejected: {exc}",
                )
                self._save_record(output_dir, record)

            planning = PlanningGraphRunner(
                provider=provider,
                render_target=render_target,
                skill_registry=self.skill_registry,
                max_plan_attempts=self.max_plan_attempts,
                validate_response=validate_response,
                on_skills_resolved=on_skills_resolved,
                on_attempt=on_attempt,
                on_rejected=on_rejected,
            ).run(prompt)
            raw_response = planning.raw_response
            attempt = planning.attempts
            provider_envelope = latest_provider_envelope["value"]
            plan_payload = validated["plan_payload"]
            project = validated["project"]

            (output_dir / "model_response.raw.json").write_text(
                raw_response,
                encoding="utf-8",
            )
            if provider_envelope is not None:
                _atomic_json(output_dir / "provider_response.json", provider_envelope)
            _atomic_json(output_dir / "plan.json", plan_payload)
            record.transition(
                "PLAN_VALIDATED",
                f"Model plan and domain constraints accepted on attempt {attempt}",
            )
            self._save_record(output_dir, record)

            _atomic_json(output_dir / "score_ir.json", project.to_dict())
            frozen_score_sha256 = score_sha256(project)
            record.transition(
                "SCORE_VALIDATED",
                "Complete Score IR accepted and frozen for performance interpretation",
            )
            self._save_record(output_dir, record)

            performance: ExpressivePerformance | None = None
            soundfont_mapping: SoundFontMapping | None = None
            performance_summary: dict[str, Any] | None = None
            mapping_summary: dict[str, Any] | None = None
            if soundfont_profile is not None:
                stage_skills = self.skill_registry.resolve(
                    renderer_name=self.renderer.name
                )
                interpreter = getattr(provider, "interpret_performance", None)
                if not callable(interpreter):
                    raise DomainValidationError(
                        "the selected provider cannot interpret expressive performance"
                    )
                raw_performance = interpreter(
                    prompt=prompt,
                    project=project,
                    skill_instructions=stage_skills.instructions_for(
                        ("expressive-performance",)
                    ),
                )
                if score_sha256(project) != frozen_score_sha256:
                    raise DomainValidationError(
                        "the performance provider modified the frozen Score IR"
                    )
                record.expressive_performance_attempts = 1
                (output_dir / "expressive_performance_response.raw.json").write_text(
                    raw_performance,
                    encoding="utf-8",
                )
                audit_record = getattr(provider, "audit_record", None)
                if callable(audit_record):
                    performance_envelope = audit_record()
                    if performance_envelope is not None:
                        if not isinstance(performance_envelope, dict):
                            raise TypeError("provider audit record must be an object")
                        _atomic_json(
                            output_dir / "expressive_performance_response.provider.json",
                            performance_envelope,
                        )
                performance_request = parse_expressive_performance(raw_performance)
                performance = resolve_expressive_performance(
                    project,
                    performance_request,
                )
                performance_summary = {
                    "score_sha256": performance.score_sha256,
                    "tracks": len(performance.tracks),
                    "model": getattr(
                        provider,
                        "expressive_performance_model",
                        provider.model_name,
                    ),
                    "source": (
                        "recorded-provider deterministic test substitute"
                        if provider.provider_name == "recorded"
                        else "model"
                    ),
                }
                _atomic_json(
                    output_dir / "expressive_performance_ir.json",
                    performance.to_dict(),
                )
                record.transition(
                    "PERFORMANCE_INTERPRETED",
                    "Expressive Performance IR bound to the frozen Score IR",
                )
                self._save_record(output_dir, record)

                mapper = getattr(provider, "map_soundfont", None)
                if not callable(mapper):
                    raise DomainValidationError(
                        "the selected provider cannot map the active SoundFont"
                    )
                raw_mapping = mapper(
                    prompt=prompt,
                    project=project,
                    performance=performance,
                    soundfont_profile=soundfont_profile,
                    skill_instructions=stage_skills.instructions_for(
                        ("soundfont-mapping",)
                    ),
                )
                if score_sha256(project) != frozen_score_sha256:
                    raise DomainValidationError(
                        "the SoundFont mapping provider modified the frozen Score IR"
                    )
                record.soundfont_mapping_attempts = 1
                (output_dir / "soundfont_mapping_response.raw.json").write_text(
                    raw_mapping,
                    encoding="utf-8",
                )
                if callable(audit_record):
                    mapping_envelope = audit_record()
                    if mapping_envelope is not None:
                        if not isinstance(mapping_envelope, dict):
                            raise TypeError("provider audit record must be an object")
                        _atomic_json(
                            output_dir / "soundfont_mapping_response.provider.json",
                            mapping_envelope,
                        )
                mapping_request = parse_soundfont_mapping(raw_mapping)
                soundfont_mapping = resolve_soundfont_mapping(
                    project,
                    mapping_request,
                    soundfont_profile,
                )
                mapping_summary = {
                    "profile": {
                        "id": soundfont_profile.id,
                        "sha256": soundfont_profile.sha256,
                        "bank_select": soundfont_profile.bank_select,
                    },
                    "score_sha256": soundfont_mapping.score_sha256,
                    "assignments": len(soundfont_mapping.tracks),
                    "model": getattr(
                        provider,
                        "soundfont_mapping_model",
                        provider.model_name,
                    ),
                    "source": (
                        "recorded-provider deterministic test substitute"
                        if provider.provider_name == "recorded"
                        else "model"
                    ),
                }
                _atomic_json(
                    output_dir / "soundfont_mapping_ir.json",
                    soundfont_mapping.to_dict(),
                )
                record.transition(
                    "SOUNDFONT_MAPPED",
                    "SoundFont Mapping IR bound to the score and active profile",
                )
                self._save_record(output_dir, record)

            midi_path = output_dir / "composition.mid"
            write_midi(project, midi_path, performance, soundfont_mapping)
            wav_path = output_dir / "audio.wav"
            render_report = self.renderer.render(
                project,
                wav_path,
                performance,
                soundfont_mapping,
            )
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
                plan_attempts=attempt,
                skills=record.skills,
                performance=performance_summary,
                soundfont_mapping=mapping_summary,
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
        plan_attempts: int,
        skills: list[dict[str, str]],
        performance: dict[str, Any] | None,
        soundfont_mapping: dict[str, Any] | None,
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
            "planning": {
                "attempts": plan_attempts,
                "repaired": plan_attempts > 1,
                "skills": skills,
                "expressive_performance": performance,
                "soundfont_mapping": soundfont_mapping,
            },
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
