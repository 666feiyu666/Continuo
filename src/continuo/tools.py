from __future__ import annotations

from dataclasses import fields
from typing import Any

from .domain import (
    AutomationPoint,
    DomainValidationError,
    MasterSpec,
    MusicProject,
    NoteEvent,
    Section,
    SynthSpec,
    Track,
)
from .planning import ModelPlan, ToolCall


def _require_exact(arguments: dict[str, Any], required: set[str], optional: set[str] = set()) -> None:
    missing = required - set(arguments)
    unknown = set(arguments) - required - optional
    if missing:
        raise DomainValidationError(f"missing tool arguments: {sorted(missing)}")
    if unknown:
        raise DomainValidationError(f"unknown tool arguments: {sorted(unknown)}")


class MusicToolRuntime:
    """Applies validated, domain-level edits proposed by a model."""

    def __init__(self) -> None:
        self.project: MusicProject | None = None

    def apply_plan(self, plan: ModelPlan) -> MusicProject:
        for call in plan.tool_calls:
            self.apply(call)
        if self.project is None:
            raise DomainValidationError("model plan did not create a project")
        return self.project

    def apply(self, call: ToolCall) -> None:
        handler = getattr(self, f"_tool_{call.name}", None)
        if handler is None:
            raise DomainValidationError(f"tool is not implemented: {call.name}")
        handler(dict(call.arguments))

    def _require_project(self) -> MusicProject:
        if self.project is None:
            raise DomainValidationError("create_project must be called first")
        return self.project

    def _track(self, track_id: str) -> Track:
        project = self._require_project()
        for track in project.tracks:
            if track.id == track_id:
                return track
        raise DomainValidationError(f"unknown track: {track_id}")

    def _tool_create_project(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {
                "title",
                "duration_seconds",
                "tempo_bpm",
                "meter_numerator",
                "meter_denominator",
                "swing",
                "seed",
            },
        )
        if self.project is not None:
            raise DomainValidationError("a plan may create only one project")
        self.project = MusicProject(schema_version="1.0", **args)

    def _tool_add_section(self, args: dict[str, Any]) -> None:
        _require_exact(args, {"id", "label", "start_beat", "end_beat"})
        self._require_project().sections.append(Section(**args))

    def _tool_add_track(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {"id", "name", "role", "synth"},
            {"gain", "pan", "midi_channel"},
        )
        synth_payload = args.pop("synth")
        if not isinstance(synth_payload, dict):
            raise DomainValidationError("synth must be an object")
        allowed_synth = {item.name for item in fields(SynthSpec)}
        unknown = set(synth_payload) - allowed_synth
        if unknown:
            raise DomainValidationError(f"unknown synth fields: {sorted(unknown)}")
        project = self._require_project()
        if any(track.id == args["id"] for track in project.tracks):
            raise DomainValidationError(f"duplicate track id: {args['id']}")
        project.tracks.append(Track(synth=SynthSpec(**synth_payload), **args))

    def _tool_add_note(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {"track_id", "start_beat", "duration_beats", "pitch"},
            {"velocity"},
        )
        track_id = args.pop("track_id")
        self._track(track_id).events.append(NoteEvent(**args))

    def _tool_add_note_pattern(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {"track_id", "start_beat", "step_beats", "duration_beats", "pitches", "repeats"},
            {"velocities", "swing"},
        )
        track = self._track(str(args["track_id"]))
        pitches = args["pitches"]
        if not isinstance(pitches, list) or not pitches:
            raise DomainValidationError("pitches must be a non-empty list")
        repeats = int(args["repeats"])
        if not 1 <= repeats <= 1024:
            raise DomainValidationError("repeats must be between 1 and 1024")
        step = float(args["step_beats"])
        if step <= 0:
            raise DomainValidationError("step_beats must be positive")
        velocities = args.get("velocities")
        if velocities is None:
            velocities = [0.7]
        if not isinstance(velocities, list) or not velocities:
            raise DomainValidationError("velocities must be a non-empty list")
        swing = float(args.get("swing", 0.5))
        if not 0.5 <= swing <= 0.75:
            raise DomainValidationError("pattern swing must be between 0.5 and 0.75")
        start = float(args["start_beat"])
        for repeat in range(repeats):
            for index, pitch in enumerate(pitches):
                if pitch is None:
                    continue
                global_index = repeat * len(pitches) + index
                nominal = start + global_index * step
                swung = nominal
                if abs(step - 0.5) < 1e-9 and global_index % 2 == 1:
                    swung += (swing - 0.5) * 2.0 * step
                track.events.append(
                    NoteEvent(
                        start_beat=swung,
                        duration_beats=float(args["duration_beats"]),
                        pitch=int(pitch),
                        velocity=float(velocities[index % len(velocities)]),
                    )
                )

    def _tool_add_chord_sequence(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {"track_id", "start_beat", "beats_per_chord", "duration_beats", "chords"},
            {"velocity"},
        )
        track = self._track(str(args["track_id"]))
        chords = args["chords"]
        if not isinstance(chords, list) or not chords:
            raise DomainValidationError("chords must be a non-empty list")
        start = float(args["start_beat"])
        beats_per_chord = float(args["beats_per_chord"])
        for index, chord in enumerate(chords):
            if not isinstance(chord, list) or not chord:
                raise DomainValidationError("every chord must contain pitches")
            for pitch in chord:
                track.events.append(
                    NoteEvent(
                        start_beat=start + index * beats_per_chord,
                        duration_beats=float(args["duration_beats"]),
                        pitch=int(pitch),
                        velocity=float(args.get("velocity", 0.55)),
                    )
                )

    def _tool_add_automation(self, args: dict[str, Any]) -> None:
        _require_exact(args, {"track_id", "beat", "parameter", "value"})
        track_id = args.pop("track_id")
        self._track(track_id).automation.append(AutomationPoint(**args))

    def _tool_set_master(self, args: dict[str, Any]) -> None:
        allowed = {item.name for item in fields(MasterSpec)}
        if not args or set(args) - allowed:
            raise DomainValidationError("set_master contains unknown or empty arguments")
        project = self._require_project()
        values = {item.name: getattr(project.master, item.name) for item in fields(MasterSpec)}
        values.update(args)
        project.master = MasterSpec(**values)
