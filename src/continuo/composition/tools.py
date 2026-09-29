from __future__ import annotations

from typing import Any

from ..model import (
    AutomationPoint,
    DomainValidationError,
    InstrumentSpec,
    KeyRegion,
    MusicProject,
    NoteEvent,
    Phrase,
    Section,
    Track,
)
from .plans import ModelPlan, ToolCall


def _require_exact(
    arguments: dict[str, Any],
    required: set[str],
    optional: set[str] = set(),
) -> None:
    missing = required - set(arguments)
    unknown = set(arguments) - required - optional
    if missing:
        raise DomainValidationError(f"missing tool arguments: {sorted(missing)}")
    if unknown:
        raise DomainValidationError(f"unknown tool arguments: {sorted(unknown)}")


class MusicToolRuntime:
    """Apply model-proposed musical edits to one shared project state."""

    def __init__(self, project: MusicProject | None = None) -> None:
        self.project = project
        self.finalized = False

    def apply_plan(
        self,
        plan: ModelPlan,
        *,
        require_finalize: bool = False,
    ) -> MusicProject:
        for call in plan.tool_calls:
            self.apply(call)
        if self.project is None:
            raise DomainValidationError("model plan did not create a project")
        if require_finalize and not self.finalized:
            raise DomainValidationError(
                "arrangement plan must end with finalize_project"
            )
        return self.project

    def apply(self, call: ToolCall) -> None:
        if self.finalized:
            raise DomainValidationError("no edits are allowed after finalize_project")
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

    def _tool_add_key_region(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {"id", "section_id", "start_beat", "end_beat", "tonic", "mode"},
        )
        self._require_project().key_regions.append(KeyRegion(**args))

    def _tool_add_phrase(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {"id", "label", "start_beat", "end_beat", "motif_id", "variation_of"},
        )
        project = self._require_project()
        if any(phrase.id == args["id"] for phrase in project.phrases):
            raise DomainValidationError(f"duplicate phrase id: {args['id']}")
        project.phrases.append(Phrase(**args))

    def _tool_add_track(self, args: dict[str, Any]) -> None:
        _require_exact(args, {"id", "name", "role", "instrument"}, {"gain", "pan"})
        instrument_payload = args.pop("instrument")
        if not isinstance(instrument_payload, dict):
            raise DomainValidationError("instrument must be an object")
        _require_exact(instrument_payload, {"id"})
        project = self._require_project()
        if any(track.id == args["id"] for track in project.tracks):
            raise DomainValidationError(f"duplicate track id: {args['id']}")
        project.tracks.append(Track(instrument=InstrumentSpec(**instrument_payload), **args))

    def _tool_add_note(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {
                "track_id",
                "start_beat",
                "duration_beats",
                "pitch",
                "section_id",
                "phrase_id",
                "articulation",
            },
            {"velocity"},
        )
        track_id = args.pop("track_id")
        self._track(track_id).events.append(NoteEvent(**args))

    def _tool_add_note_pattern(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {
                "track_id",
                "start_beat",
                "step_beats",
                "duration_beats",
                "pitches",
                "repeats",
                "section_id",
                "phrase_id",
                "articulations",
            },
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
        velocities = args.get("velocities") or [0.7]
        if not isinstance(velocities, list) or not velocities:
            raise DomainValidationError("velocities must be a non-empty list")
        articulations = args["articulations"]
        if not isinstance(articulations, list) or not articulations:
            raise DomainValidationError("articulations must be a non-empty list")
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
                        section_id=args["section_id"],
                        phrase_id=args["phrase_id"],
                        articulation=str(articulations[index % len(articulations)]),
                    )
                )

    def _tool_add_chord_sequence(self, args: dict[str, Any]) -> None:
        _require_exact(
            args,
            {
                "track_id",
                "start_beat",
                "beats_per_chord",
                "note_duration_beats",
                "chords",
                "section_id",
                "phrase_id",
                "articulation",
            },
            {"velocity"},
        )
        track = self._track(str(args["track_id"]))
        chords = args["chords"]
        if not isinstance(chords, list) or not chords:
            raise DomainValidationError("chords must be a non-empty list")
        start = float(args["start_beat"])
        beats_per_chord = float(args["beats_per_chord"])
        note_duration = float(args["note_duration_beats"])
        if beats_per_chord <= 0 or note_duration <= 0:
            raise DomainValidationError("chord spacing and duration must be positive")
        if note_duration > beats_per_chord + 1e-6:
            raise DomainValidationError("note_duration_beats cannot exceed beats_per_chord")
        for index, chord in enumerate(chords):
            if not isinstance(chord, list) or not chord:
                raise DomainValidationError("every chord must contain pitches")
            for pitch in chord:
                track.events.append(
                    NoteEvent(
                        start_beat=start + index * beats_per_chord,
                        duration_beats=note_duration,
                        pitch=int(pitch),
                        velocity=float(args.get("velocity", 0.55)),
                        section_id=args["section_id"],
                        phrase_id=args["phrase_id"],
                        articulation=str(args["articulation"]),
                    )
                )

    def _tool_add_automation(self, args: dict[str, Any]) -> None:
        _require_exact(args, {"track_id", "beat", "parameter", "value"})
        track_id = args.pop("track_id")
        self._track(track_id).automation.append(AutomationPoint(**args))

    def _tool_add_note_sequence(self, args: dict[str, Any]) -> None:
        _require_exact(args, {"track_id", "notes"})
        track = self._track(str(args["track_id"]))
        notes = args["notes"]
        if not isinstance(notes, list) or not notes:
            raise DomainValidationError("notes must be a non-empty list")
        fields = {
            "start_beat",
            "duration_beats",
            "pitch",
            "velocity",
            "section_id",
            "phrase_id",
            "articulation",
            "connection_to_next",
        }
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                raise DomainValidationError(
                    f"note sequence item {index} must be an object"
                )
            payload = dict(note)
            _require_exact(payload, fields)
            track.events.append(NoteEvent(**payload))

    def _tool_replace_phrase_notes(self, args: dict[str, Any]) -> None:
        _require_exact(args, {"track_id", "phrase_id", "notes"})
        project = self._require_project()
        track = self._track(str(args["track_id"]))
        phrase_id = str(args["phrase_id"])
        if not any(phrase.id == phrase_id for phrase in project.phrases):
            raise DomainValidationError(f"unknown phrase: {phrase_id}")
        if not any(event.phrase_id == phrase_id for event in track.events):
            raise DomainValidationError(
                f"track {track.id} has no notes in phrase {phrase_id} to replace"
            )
        notes = args["notes"]
        if not isinstance(notes, list) or not notes:
            raise DomainValidationError("replacement notes must be a non-empty list")
        fields = {
            "start_beat",
            "duration_beats",
            "pitch",
            "velocity",
            "section_id",
            "phrase_id",
            "articulation",
            "connection_to_next",
        }
        replacements: list[NoteEvent] = []
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                raise DomainValidationError(
                    f"replacement note item {index} must be an object"
                )
            payload = dict(note)
            _require_exact(payload, fields)
            if payload["phrase_id"] != phrase_id:
                raise DomainValidationError(
                    "every replacement note must keep the replaced phrase_id"
                )
            replacements.append(NoteEvent(**payload))
        track.events = [
            event for event in track.events if event.phrase_id != phrase_id
        ] + replacements

    def _tool_finalize_project(self, args: dict[str, Any]) -> None:
        _require_exact(args, set())
        self._require_project()
        self.finalized = True
