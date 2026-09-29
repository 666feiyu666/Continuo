from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..composition import (
    ARRANGEMENT_STAGE,
    ARRANGEMENT_TOOL_NAMES,
    CORE_STAGE,
    CORE_TOOL_NAMES,
    MusicToolRuntime,
    PlanningGraphRunner,
    PlanningProvider,
    parse_model_plan,
)
from ..model import DomainValidationError, MusicProject, score_sha256
from ..rendering import ReferenceWavRenderer, RenderBackend, inspect_wav
from ..rendering.midi import write_midi
from ..skills import SkillRegistry
from ..rendering.soundfont.mapping import (
    SoundFontMapping,
    parse_soundfont_mapping,
    resolve_soundfont_mapping,
    validate_soundfont_compatibility,
)
from ..rendering.soundfont.profile import SoundFontProfile


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
    require_cross_section_phrase: bool = False


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
    core_attempts: int = 0
    arrangement_attempts: int = 0
    soundfont_mapping_attempts: int = 0
    skills: list[dict[str, str]] = field(default_factory=list)
    events: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    def transition(self, state: str, detail: str) -> None:
        self.state = state
        self.updated_at = _utc_now()
        self.events.append({"at": self.updated_at, "state": state, "detail": detail})


@dataclass(frozen=True, slots=True)
class _CompositionStageResult:
    project: MusicProject
    attempts: int
    plan_payload: dict[str, Any]
    skills: tuple[dict[str, str], ...]


class AgentRuntime:
    def __init__(
        self,
        renderer: RenderBackend | None = None,
        *,
        max_plan_attempts: int = 5,
        max_mapping_attempts: int = 3,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        if min(max_plan_attempts, max_mapping_attempts) < 1:
            raise ValueError("all attempt limits must be positive")
        self.renderer = renderer or ReferenceWavRenderer()
        self.max_plan_attempts = max_plan_attempts
        self.max_mapping_attempts = max_mapping_attempts
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
            core = self._run_composition_stage(
                stage=CORE_STAGE,
                current_project=None,
                prompt=prompt,
                provider=provider,
                output_dir=output_dir,
                policy=policy,
                record=record,
            )
            record.core_attempts = core.attempts
            _atomic_json(output_dir / "core_plan.json", core.plan_payload)
            _atomic_json(output_dir / "core_score_ir.json", core.project.to_dict())
            self._save_record(output_dir, record)

            arrangement = self._run_composition_stage(
                stage=ARRANGEMENT_STAGE,
                current_project=core.project,
                prompt=prompt,
                provider=provider,
                output_dir=output_dir,
                policy=policy,
                record=record,
            )
            record.arrangement_attempts = arrangement.attempts
            _atomic_json(output_dir / "arrangement_plan.json", arrangement.plan_payload)
            project = arrangement.project
            _atomic_json(output_dir / "score_ir.json", project.to_dict())
            frozen_score_sha256 = score_sha256(project)
            record.transition(
                "SCORE_FROZEN",
                "Core and arrangement accepted; the complete Score IR is now immutable",
            )
            self._save_record(output_dir, record)

            soundfont_profile = self._load_soundfont_profile()
            mapping, mapping_summary = self._map_soundfont(
                prompt=prompt,
                project=project,
                frozen_score_sha256=frozen_score_sha256,
                provider=provider,
                profile=soundfont_profile,
                output_dir=output_dir,
                record=record,
            )

            midi_path = output_dir / "composition.mid"
            write_midi(project, midi_path, mapping)
            wav_path = output_dir / "audio.wav"
            render_report = self.renderer.render(project, wav_path, mapping)
            record.transition("RENDERED", f"Rendered by {render_report.backend}")
            self._save_record(output_dir, record)

            skills = self._merge_skills(core.skills, arrangement.skills)
            verification = self._verify(
                project,
                wav_path,
                render_report,
                policy,
                run_id=record.run_id,
                case_id=record.case_id,
                provider_name=provider.provider_name,
                model_name=provider.model_name,
                core_attempts=core.attempts,
                arrangement_attempts=arrangement.attempts,
                skills=skills,
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

    def _run_composition_stage(
        self,
        *,
        stage: str,
        current_project: MusicProject | None,
        prompt: str,
        provider: PlanningProvider,
        output_dir: Path,
        policy: RunPolicy,
        record: RunRecord,
    ) -> _CompositionStageResult:
        allowed_tools = (
            CORE_TOOL_NAMES if stage == CORE_STAGE else ARRANGEMENT_TOOL_NAMES
        )
        validated: dict[str, Any] = {}

        def on_skills_resolved(skills: list[dict[str, str]]) -> None:
            record.skills = self._merge_skills(record.skills, skills)
            record.transition(
                f"{stage.upper()}_SKILLS_RESOLVED",
                "Activated composition skills: "
                + ", ".join(skill["id"] for skill in skills),
            )
            self._save_record(output_dir, record)

        def on_attempt(
            attempt: int,
            raw_response: str,
            provider_envelope: dict[str, Any] | None,
        ) -> None:
            if stage == CORE_STAGE:
                record.core_attempts = attempt
            else:
                record.arrangement_attempts = attempt
            attempt_name = f"attempt-{attempt:02d}"
            (output_dir / f"{stage}_response.{attempt_name}.raw.json").write_text(
                raw_response,
                encoding="utf-8",
            )
            if provider_envelope is not None:
                _atomic_json(
                    output_dir / f"{stage}_provider_response.{attempt_name}.json",
                    provider_envelope,
                )
            record.transition(
                f"{stage.upper()}_MODELLED",
                f"Raw {stage} response persisted for attempt {attempt}",
            )
            self._save_record(output_dir, record)

        def validate_response(raw_response: str, attempt: int) -> None:
            validated.clear()
            plan = parse_model_plan(raw_response, allowed_tools=allowed_tools)
            plan_payload = {
                "schema_version": plan.schema_version,
                "brief": plan.brief,
                "rationale": plan.rationale,
                "tool_calls": [asdict(call) for call in plan.tool_calls],
            }
            _atomic_json(
                output_dir / f"{stage}_plan.attempt-{attempt:02d}.json",
                plan_payload,
            )
            if current_project is None:
                project = MusicToolRuntime().apply_plan(plan)
            else:
                project = MusicToolRuntime(copy.deepcopy(current_project)).apply_plan(
                    plan,
                    require_finalize=True,
                )
                if (
                    provider.provider_name != "recorded"
                    and project.to_dict() == current_project.to_dict()
                ):
                    raise DomainValidationError(
                        "arrangement stage finalized without adding musical material"
                    )
            self._validate_project(project, policy)
            validated.update({"plan_payload": plan_payload, "project": project})

        def on_rejected(attempt: int, exc: DomainValidationError) -> None:
            _atomic_json(
                output_dir / f"{stage}_error.attempt-{attempt:02d}.json",
                {
                    "schema_version": "1.0",
                    "stage": stage,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            record.transition(
                f"{stage.upper()}_REJECTED",
                f"{stage.title()} attempt {attempt} rejected: {exc}",
            )
            self._save_record(output_dir, record)

        result = PlanningGraphRunner(
            stage=stage,
            current_project=current_project,
            provider=provider,
            renderer_name=self.renderer.name,
            skill_registry=self.skill_registry,
            max_attempts=self.max_plan_attempts,
            validate_response=validate_response,
            on_skills_resolved=on_skills_resolved,
            on_attempt=on_attempt,
            on_rejected=on_rejected,
        ).run(prompt)
        (output_dir / f"{stage}_response.raw.json").write_text(
            result.raw_response,
            encoding="utf-8",
        )
        record.transition(
            f"{stage.upper()}_ACCEPTED",
            f"{stage.title()} accepted on attempt {result.attempts}",
        )
        self._save_record(output_dir, record)
        return _CompositionStageResult(
            project=validated["project"],
            attempts=result.attempts,
            plan_payload=validated["plan_payload"],
            skills=result.active_skills,
        )

    def _load_soundfont_profile(self) -> SoundFontProfile | None:
        profile_loader = getattr(self.renderer, "soundfont_profile", None)
        return profile_loader() if callable(profile_loader) else None

    def _map_soundfont(
        self,
        *,
        prompt: str,
        project: MusicProject,
        frozen_score_sha256: str,
        provider: PlanningProvider,
        profile: SoundFontProfile | None,
        output_dir: Path,
        record: RunRecord,
    ) -> tuple[SoundFontMapping | None, dict[str, Any] | None]:
        if profile is None:
            return None, None
        validate_soundfont_compatibility(project)
        mapper = getattr(provider, "map_soundfont", None)
        if not callable(mapper):
            raise DomainValidationError(
                "the selected provider cannot map the active SoundFont"
            )
        selection = self.skill_registry.resolve(renderer_name=self.renderer.name)
        instructions = selection.instructions_for(("soundfont-mapping",))
        audit_record = getattr(provider, "audit_record", None)
        raw_mapping = ""
        mapping: SoundFontMapping | None = None
        mapping_envelope: dict[str, Any] | None = None
        mapping_error = ""
        for attempt in range(1, self.max_mapping_attempts + 1):
            if attempt == 1:
                raw_mapping = mapper(
                    prompt=prompt,
                    project=project,
                    soundfont_profile=profile,
                    skill_instructions=instructions,
                )
            else:
                repair = getattr(provider, "repair_soundfont_mapping", None)
                if not callable(repair):
                    raise DomainValidationError(mapping_error)
                raw_mapping = repair(
                    prompt=prompt,
                    project=project,
                    soundfont_profile=profile,
                    previous_response=raw_mapping,
                    validation_error=mapping_error,
                    skill_instructions=instructions,
                )
            if score_sha256(project) != frozen_score_sha256:
                raise DomainValidationError(
                    "the SoundFont mapping provider modified the frozen Score IR"
                )
            record.soundfont_mapping_attempts = attempt
            attempt_name = f"attempt-{attempt:02d}"
            (
                output_dir / f"soundfont_mapping_response.{attempt_name}.raw.json"
            ).write_text(raw_mapping, encoding="utf-8")
            mapping_envelope = None
            if callable(audit_record):
                mapping_envelope = audit_record()
                if mapping_envelope is not None:
                    if not isinstance(mapping_envelope, dict):
                        raise TypeError("provider audit record must be an object")
                    _atomic_json(
                        output_dir
                        / f"soundfont_mapping_response.{attempt_name}.provider.json",
                        mapping_envelope,
                    )
            try:
                mapping = resolve_soundfont_mapping(
                    project,
                    parse_soundfont_mapping(raw_mapping),
                    profile,
                )
            except DomainValidationError as exc:
                mapping_error = str(exc)
                _atomic_json(
                    output_dir / f"soundfont_mapping_error.{attempt_name}.json",
                    {
                        "schema_version": "1.0",
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": mapping_error,
                    },
                )
                record.transition(
                    "SOUNDFONT_MAPPING_REJECTED",
                    f"SoundFont mapping attempt {attempt} rejected: {exc}",
                )
                self._save_record(output_dir, record)
                can_repair = (
                    attempt < self.max_mapping_attempts
                    and callable(getattr(provider, "repair_soundfont_mapping", None))
                )
                if not can_repair:
                    raise
                continue
            break
        if mapping is None:
            raise AssertionError("mapping attempt loop produced no result")
        (output_dir / "soundfont_mapping_response.raw.json").write_text(
            raw_mapping,
            encoding="utf-8",
        )
        if mapping_envelope is not None:
            _atomic_json(
                output_dir / "soundfont_mapping_response.provider.json",
                mapping_envelope,
            )
        _atomic_json(output_dir / "soundfont_mapping_ir.json", mapping.to_dict())
        record.transition(
            "SOUNDFONT_MAPPED",
            "SoundFont Mapping IR bound to the frozen score and active profile",
        )
        self._save_record(output_dir, record)
        return mapping, {
            "profile": {
                "id": profile.id,
                "sha256": profile.sha256,
                "bank_select": profile.bank_select,
            },
            "score_sha256": mapping.score_sha256,
            "assignments": len(mapping.tracks),
            "model": getattr(provider, "soundfont_mapping_model", provider.model_name),
            "source": (
                "recorded-provider deterministic test substitute"
                if provider.provider_name == "recorded"
                else "model"
            ),
        }

    def _validate_project(self, project: MusicProject, policy: RunPolicy) -> None:
        project.validate(forbid_vocals=policy.forbid_vocals)
        if policy.expected_duration_seconds is not None:
            difference = abs(project.duration_seconds - policy.expected_duration_seconds)
            if difference > policy.duration_tolerance_seconds:
                raise DomainValidationError(
                    "project duration does not satisfy the user-bound run policy"
                )
        if policy.require_cross_section_phrase and not self._has_cross_section_phrase(project):
            raise DomainValidationError(
                "run policy requires at least one musical phrase to cross a formal "
                "section boundary; sections must not become automatic phrase cuts"
            )

    @staticmethod
    def _has_cross_section_phrase(project: MusicProject) -> bool:
        boundaries = [
            section.end_beat
            for section in sorted(project.sections, key=lambda item: item.start_beat)[:-1]
        ]
        for phrase in project.phrases:
            for boundary in boundaries:
                if not phrase.start_beat < boundary < phrase.end_beat:
                    continue
                for track in project.tracks:
                    events = [
                        event for event in track.events if event.phrase_id == phrase.id
                    ]
                    if any(
                        event.start_beat < boundary < event.start_beat + event.duration_beats
                        for event in events
                    ):
                        return True
                    if any(event.start_beat < boundary for event in events) and any(
                        event.start_beat >= boundary for event in events
                    ):
                        return True
        return False

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
        core_attempts: int,
        arrangement_attempts: int,
        skills: list[dict[str, str]],
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
        if policy.require_cross_section_phrase:
            checks["cross_section_phrase"] = self._has_cross_section_phrase(project)
        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            raise DomainValidationError(f"verification checks failed: {failed}")
        limitations = [
            "This report proves runtime correctness, not subjective musical quality."
        ]
        if provider_name == "recorded":
            limitations.append("The recorded provider is not evidence of real model quality.")
        else:
            limitations.append(
                "A single real-provider run is not broad evidence of model quality."
            )
        if render_report.backend == "python-reference":
            limitations.append(
                "The Python renderer is a portable reference backend, not the final DSP backend."
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
                "composition": {
                    "core": {
                        "attempts": core_attempts,
                        "repaired": core_attempts > 1,
                    },
                    "arrangement": {
                        "attempts": arrangement_attempts,
                        "repaired": arrangement_attempts > 1,
                    },
                },
                "skills": skills,
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
    def _merge_skills(
        *skill_lists: Any,
    ) -> list[dict[str, str]]:
        merged: dict[str, dict[str, str]] = {}
        for skills in skill_lists:
            for skill in skills:
                merged[skill["id"]] = dict(skill)
        return list(merged.values())

    @staticmethod
    def _save_record(output_dir: Path, record: RunRecord) -> None:
        _atomic_json(output_dir / "run.json", asdict(record))
