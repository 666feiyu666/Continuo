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
    CORE_REVIEW_STAGE,
    CORE_REVIEW_TOOL_NAMES,
    CORE_STAGE,
    CORE_TOOL_NAMES,
    MusicToolRuntime,
    PlanningGraphRunner,
    PlanningProvider,
    build_symbolic_score_audit,
    parse_model_plan,
)
from ..model import (
    MAX_PROJECT_DURATION_SECONDS,
    DomainValidationError,
    MusicProject,
    score_sha256,
)
from ..rendering import SoundFontRenderBackend, inspect_wav
from ..rendering.midi import write_midi
from ..skills import SkillRegistry
from ..rendering.soundfont.mapping import (
    SoundFontMapping,
    available_instrument_ids,
    deterministic_soundfont_mapping,
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
    expected_track_count: int | None = None
    required_instrument_ids: tuple[str, ...] = ()
    minimum_slur_connections: int = 0
    minimum_breath_connections: int = 0
    required_automation_parameters: tuple[str, ...] = ()


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
    core_review_attempts: int = 0
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
        renderer: SoundFontRenderBackend,
        *,
        max_plan_attempts: int = 5,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        if max_plan_attempts < 1:
            raise ValueError("max_plan_attempts must be positive")
        self.renderer = renderer
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
            soundfont_profile = self._load_soundfont_profile()
            available_instruments = available_instrument_ids(soundfont_profile)
            if not available_instruments:
                raise DomainValidationError(
                    "the active SoundFont cannot realize any semantic instruments"
                )
            unavailable_required = (
                set(policy.required_instrument_ids) - set(available_instruments)
            )
            if unavailable_required:
                raise DomainValidationError(
                    "the active SoundFont cannot realize required instruments: "
                    f"{sorted(unavailable_required)}"
                )
            record.transition(
                "SOUNDFONT_PROFILED",
                "Active SoundFont exposes "
                f"{len(available_instruments)} semantic instruments to composition",
            )
            self._save_record(output_dir, record)

            core = self._run_composition_stage(
                stage=CORE_STAGE,
                current_project=None,
                prompt=prompt,
                provider=provider,
                output_dir=output_dir,
                policy=policy,
                record=record,
                soundfont_profile=soundfont_profile,
                available_instruments=available_instruments,
            )
            record.core_attempts = core.attempts
            _atomic_json(output_dir / "core_plan.json", core.plan_payload)
            _atomic_json(output_dir / "core_score_ir.json", core.project.to_dict())
            self._save_record(output_dir, record)

            core_audit = build_symbolic_score_audit(core.project)
            _atomic_json(output_dir / "core_score_audit.json", core_audit)
            core_review = self._run_composition_stage(
                stage=CORE_REVIEW_STAGE,
                current_project=core.project,
                prompt=prompt,
                provider=provider,
                output_dir=output_dir,
                policy=policy,
                record=record,
                soundfont_profile=soundfont_profile,
                available_instruments=available_instruments,
                score_audit=core_audit,
            )
            record.core_review_attempts = core_review.attempts
            _atomic_json(output_dir / "core_review_plan.json", core_review.plan_payload)
            _atomic_json(
                output_dir / "reviewed_core_score_ir.json",
                core_review.project.to_dict(),
            )
            self._save_record(output_dir, record)

            arrangement = self._run_composition_stage(
                stage=ARRANGEMENT_STAGE,
                current_project=core_review.project,
                prompt=prompt,
                provider=provider,
                output_dir=output_dir,
                policy=policy,
                record=record,
                soundfont_profile=soundfont_profile,
                available_instruments=available_instruments,
            )
            record.arrangement_attempts = arrangement.attempts
            _atomic_json(output_dir / "arrangement_plan.json", arrangement.plan_payload)
            project = arrangement.project
            _atomic_json(output_dir / "score_ir.json", project.to_dict())
            frozen_score_sha256 = score_sha256(project)
            record.transition(
                "SCORE_FROZEN",
                "Core, symbolic review, and arrangement accepted; the complete Score "
                "IR is now immutable",
            )
            self._save_record(output_dir, record)

            mapping, mapping_summary = self._map_soundfont(
                project=project,
                frozen_score_sha256=frozen_score_sha256,
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

            skills = self._merge_skills(
                core.skills,
                core_review.skills,
                arrangement.skills,
            )
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
                core_review_attempts=core_review.attempts,
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
        soundfont_profile: SoundFontProfile,
        available_instruments: tuple[str, ...],
        score_audit: dict[str, Any] | None = None,
    ) -> _CompositionStageResult:
        if stage == CORE_STAGE:
            allowed_tools = CORE_TOOL_NAMES
        elif stage == CORE_REVIEW_STAGE:
            allowed_tools = CORE_REVIEW_TOOL_NAMES
        elif stage == ARRANGEMENT_STAGE:
            allowed_tools = ARRANGEMENT_TOOL_NAMES
        else:
            raise ValueError(f"unknown composition stage: {stage}")
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
            elif stage == CORE_REVIEW_STAGE:
                record.core_review_attempts = attempt
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
                    stage == ARRANGEMENT_STAGE
                    and provider.provider_name != "recorded"
                    and project.to_dict() == current_project.to_dict()
                ):
                    raise DomainValidationError(
                        "arrangement stage finalized without adding musical material"
                    )
            duration_adjustment = project.fit_duration_to_score()
            if duration_adjustment is not None:
                proposed_seconds, score_seconds = duration_adjustment
                _atomic_json(
                    output_dir / f"{stage}_duration.attempt-{attempt:02d}.json",
                    {
                        "schema_version": "1.0",
                        "stage": stage,
                        "attempt": attempt,
                        "proposed_duration_seconds": proposed_seconds,
                        "score_duration_seconds": score_seconds,
                        "reason": "Duration derived from the complete authored score",
                    },
                )
            self._validate_project(project, policy, soundfont_profile)
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
            available_instrument_ids=available_instruments,
            skill_registry=self.skill_registry,
            policy_rules=self._policy_rules(policy),
            score_audit=score_audit,
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

    def _load_soundfont_profile(self) -> SoundFontProfile:
        return self.renderer.soundfont_profile()

    def _map_soundfont(
        self,
        *,
        project: MusicProject,
        frozen_score_sha256: str,
        profile: SoundFontProfile,
        output_dir: Path,
        record: RunRecord,
    ) -> tuple[SoundFontMapping, dict[str, Any]]:
        score_before = score_sha256(project)
        mapping = deterministic_soundfont_mapping(project, profile)
        if score_before != frozen_score_sha256 or score_sha256(project) != score_before:
            raise DomainValidationError(
                "deterministic SoundFont mapping changed the frozen Score IR"
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
            "model": None,
            "source": "deterministic-profile",
        }

    def _validate_project(
        self,
        project: MusicProject,
        policy: RunPolicy,
        soundfont_profile: SoundFontProfile,
    ) -> None:
        project.validate(forbid_vocals=policy.forbid_vocals)
        validate_soundfont_compatibility(project, soundfont_profile)
        if policy.require_cross_section_phrase and not self._has_cross_section_phrase(project):
            raise DomainValidationError(
                "run policy requires at least one musical phrase to cross a formal "
                "section boundary; sections must not become automatic phrase cuts"
            )
        if (
            policy.expected_track_count is not None
            and len(project.tracks) != policy.expected_track_count
        ):
            raise DomainValidationError(
                f"run policy requires exactly {policy.expected_track_count} tracks"
            )
        present_instruments = {track.instrument.id for track in project.tracks}
        missing_instruments = set(policy.required_instrument_ids) - present_instruments
        if missing_instruments:
            raise DomainValidationError(
                f"run policy requires instruments: {sorted(missing_instruments)}"
            )
        performance = self._performance_summary(project)
        if performance["slur_connections"] < policy.minimum_slur_connections:
            raise DomainValidationError(
                "run policy requires at least "
                f"{policy.minimum_slur_connections} slur connections"
            )
        if performance["breath_connections"] < policy.minimum_breath_connections:
            raise DomainValidationError(
                "run policy requires at least "
                f"{policy.minimum_breath_connections} breath connections"
            )
        for parameter in policy.required_automation_parameters:
            points = [
                point
                for track in project.tracks
                for point in track.automation
                if point.parameter == parameter
            ]
            if len(points) < 2 or len({point.value for point in points}) < 2:
                raise DomainValidationError(
                    f"run policy requires a non-constant {parameter} automation curve"
                )

    @staticmethod
    def _policy_rules(policy: RunPolicy) -> tuple[str, ...]:
        rules: list[str] = []
        if policy.expected_duration_seconds is not None:
            rules.append(
                f"Use {policy.expected_duration_seconds:g} seconds as an approximate "
                "creative target, not a hard cutoff. Finish the piece naturally."
            )
        if policy.require_cross_section_phrase:
            rules.append(
                "At least one musical phrase must cross a formal section boundary; "
                "section boundaries must not become automatic phrase cuts."
            )
        if policy.expected_track_count is not None:
            rules.append(f"Use exactly {policy.expected_track_count} score tracks.")
        if policy.required_instrument_ids:
            rules.append(
                "Include these semantic instruments: "
                + ", ".join(policy.required_instrument_ids)
                + "."
            )
        if policy.minimum_slur_connections:
            rules.append(
                f"Encode at least {policy.minimum_slur_connections} actual slur "
                "connections with connection_to_next."
            )
        if policy.minimum_breath_connections:
            rules.append(
                f"Encode at least {policy.minimum_breath_connections} explicit breath "
                "connections with enough written space."
            )
        if policy.required_automation_parameters:
            rules.append(
                "Write non-constant automation curves with at least two points for: "
                + ", ".join(policy.required_automation_parameters)
                + "."
            )
        return tuple(rules)

    @staticmethod
    def _performance_summary(project: MusicProject) -> dict[str, Any]:
        automation: dict[str, int] = {}
        for track in project.tracks:
            for point in track.automation:
                automation[point.parameter] = automation.get(point.parameter, 0) + 1
        return {
            "slur_connections": sum(
                event.connection_to_next == "slur"
                for track in project.tracks
                for event in track.events
            ),
            "breath_connections": sum(
                event.connection_to_next == "breath"
                for track in project.tracks
                for event in track.events
            ),
            "automation_points": automation,
        }

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
        core_review_attempts: int,
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
            "duration_within_five_minutes": (
                inspection["duration_seconds"]
                <= MAX_PROJECT_DURATION_SECONDS + 1e-6
            ),
        }
        duration_target = None
        if policy.expected_duration_seconds is not None:
            difference = abs(
                inspection["duration_seconds"] - policy.expected_duration_seconds
            )
            duration_target = {
                "target_seconds": policy.expected_duration_seconds,
                "actual_seconds": inspection["duration_seconds"],
                "difference_seconds": difference,
                "tolerance_seconds": policy.duration_tolerance_seconds,
                "within_target_tolerance": (
                    difference <= policy.duration_tolerance_seconds
                ),
                "acceptance": "advisory",
            }
        if policy.require_cross_section_phrase:
            checks["cross_section_phrase"] = self._has_cross_section_phrase(project)
        performance = self._performance_summary(project)
        if policy.expected_track_count is not None:
            checks["track_count_matches"] = (
                len(project.tracks) == policy.expected_track_count
            )
        if policy.required_instrument_ids:
            present_instruments = {track.instrument.id for track in project.tracks}
            checks["required_instruments"] = set(
                policy.required_instrument_ids
            ).issubset(present_instruments)
        if policy.minimum_slur_connections:
            checks["minimum_slur_connections"] = (
                performance["slur_connections"]
                >= policy.minimum_slur_connections
            )
        if policy.minimum_breath_connections:
            checks["minimum_breath_connections"] = (
                performance["breath_connections"]
                >= policy.minimum_breath_connections
            )
        if policy.required_automation_parameters:
            checks["required_automation"] = all(
                performance["automation_points"].get(parameter, 0) >= 2
                for parameter in policy.required_automation_parameters
            )
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
                    "core_review": {
                        "attempts": core_review_attempts,
                        "repaired": core_review_attempts > 1,
                    },
                    "arrangement": {
                        "attempts": arrangement_attempts,
                        "repaired": arrangement_attempts > 1,
                    },
                },
                "skills": skills,
                "soundfont_mapping": soundfont_mapping,
            },
            "performance": performance,
            "project": {
                "title": project.title,
                "duration_seconds": project.duration_seconds,
                "tempo_bpm": project.tempo_bpm,
                "track_count": len(project.tracks),
                "note_event_count": sum(len(track.events) for track in project.tracks),
            },
            "duration_target": duration_target,
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
