from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .domain import DomainValidationError, MusicProject
from .score_identity import score_sha256


CONNECTIONS = ("legato", "connected", "separated")


@dataclass(frozen=True, slots=True)
class PhrasePerformance:
    phrase_id: str
    connection: str
    start_expression: int
    peak_expression: int
    peak_beat: float
    end_expression: int
    breath_after_beats: float


@dataclass(frozen=True, slots=True)
class NotePerformance:
    note_index: int
    onset_offset_beats: float
    duration_scale: float
    velocity_scale: float


@dataclass(frozen=True, slots=True)
class TrackPerformance:
    track_id: str
    base_expression: int
    phrases: tuple[PhrasePerformance, ...]
    note_adjustments: tuple[NotePerformance, ...]

    def phrase(self, phrase_id: str | None) -> PhrasePerformance | None:
        if phrase_id is None:
            return None
        for item in self.phrases:
            if item.phrase_id == phrase_id:
                return item
        return None

    def note(self, note_index: int) -> NotePerformance | None:
        for item in self.note_adjustments:
            if item.note_index == note_index:
                return item
        return None


@dataclass(frozen=True, slots=True)
class ExpressivePerformanceRequest:
    schema_version: str
    tracks: tuple[TrackPerformance, ...]


@dataclass(frozen=True, slots=True)
class ExpressivePerformance:
    """Renderer-independent interpretation of a frozen score."""

    schema_version: str
    score_sha256: str
    tracks: tuple[TrackPerformance, ...]

    def for_track(self, track_id: str) -> TrackPerformance:
        for track in self.tracks:
            if track.track_id == track_id:
                return track
        raise KeyError(track_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "score_sha256": self.score_sha256,
            "tracks": [asdict(item) for item in self.tracks],
        }


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainValidationError(f"{label} must be a number")
    return float(value)


def _bounded_number(
    value: Any,
    label: str,
    minimum: float,
    maximum: float,
) -> float:
    result = _number(value, label)
    if not minimum <= result <= maximum:
        raise DomainValidationError(
            f"{label} must be between {minimum} and {maximum}"
        )
    return result


def _expression(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 127:
        raise DomainValidationError(f"{label} must be a MIDI value from 1 through 127")
    return value


def parse_expressive_performance(raw_response: str) -> ExpressivePerformanceRequest:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise DomainValidationError(
            f"expressive performance response is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "tracks"}:
        raise DomainValidationError(
            "expressive performance response has unknown or missing fields"
        )
    if payload["schema_version"] != "1.0":
        raise DomainValidationError("unsupported expressive performance schema version")
    raw_tracks = payload["tracks"]
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise DomainValidationError("expressive performance tracks must be non-empty")
    tracks: list[TrackPerformance] = []
    track_fields = {"track_id", "base_expression", "phrases", "note_adjustments"}
    phrase_fields = {
        "phrase_id",
        "connection",
        "start_expression",
        "peak_expression",
        "peak_beat",
        "end_expression",
        "breath_after_beats",
    }
    note_fields = {
        "note_index",
        "onset_offset_beats",
        "duration_scale",
        "velocity_scale",
    }
    for track_index, raw_track in enumerate(raw_tracks):
        if not isinstance(raw_track, dict) or set(raw_track) != track_fields:
            raise DomainValidationError(
                f"invalid expressive track at index {track_index}"
            )
        track_id = raw_track["track_id"]
        if not isinstance(track_id, str) or not track_id.strip():
            raise DomainValidationError(f"invalid track_id at index {track_index}")
        raw_phrases = raw_track["phrases"]
        raw_notes = raw_track["note_adjustments"]
        if not isinstance(raw_phrases, list) or not isinstance(raw_notes, list):
            raise DomainValidationError(
                f"phrases and note_adjustments must be arrays for track {track_id}"
            )
        phrases: list[PhrasePerformance] = []
        for phrase_index, raw_phrase in enumerate(raw_phrases):
            if not isinstance(raw_phrase, dict) or set(raw_phrase) != phrase_fields:
                raise DomainValidationError(
                    f"invalid phrase performance at track {track_id} index {phrase_index}"
                )
            phrase_id = raw_phrase["phrase_id"]
            connection = raw_phrase["connection"]
            if not isinstance(phrase_id, str) or not phrase_id.strip():
                raise DomainValidationError("phrase_id must be a non-empty string")
            if connection not in CONNECTIONS:
                raise DomainValidationError(
                    f"unsupported phrase connection for {phrase_id}: {connection}"
                )
            phrases.append(
                PhrasePerformance(
                    phrase_id=phrase_id,
                    connection=connection,
                    start_expression=_expression(
                        raw_phrase["start_expression"], "start_expression"
                    ),
                    peak_expression=_expression(
                        raw_phrase["peak_expression"], "peak_expression"
                    ),
                    peak_beat=_number(raw_phrase["peak_beat"], "peak_beat"),
                    end_expression=_expression(
                        raw_phrase["end_expression"], "end_expression"
                    ),
                    breath_after_beats=_bounded_number(
                        raw_phrase["breath_after_beats"],
                        "breath_after_beats",
                        0.0,
                        0.5,
                    ),
                )
            )
        adjustments: list[NotePerformance] = []
        for note_position, raw_note in enumerate(raw_notes):
            if not isinstance(raw_note, dict) or set(raw_note) != note_fields:
                raise DomainValidationError(
                    f"invalid note adjustment at track {track_id} index {note_position}"
                )
            note_index = raw_note["note_index"]
            if isinstance(note_index, bool) or not isinstance(note_index, int):
                raise DomainValidationError("note_index must be an integer")
            adjustments.append(
                NotePerformance(
                    note_index=note_index,
                    onset_offset_beats=_bounded_number(
                        raw_note["onset_offset_beats"],
                        "onset_offset_beats",
                        -0.125,
                        0.125,
                    ),
                    duration_scale=_bounded_number(
                        raw_note["duration_scale"],
                        "duration_scale",
                        0.8,
                        1.2,
                    ),
                    velocity_scale=_bounded_number(
                        raw_note["velocity_scale"],
                        "velocity_scale",
                        0.75,
                        1.25,
                    ),
                )
            )
        tracks.append(
            TrackPerformance(
                track_id=track_id,
                base_expression=_expression(
                    raw_track["base_expression"], "base_expression"
                ),
                phrases=tuple(phrases),
                note_adjustments=tuple(adjustments),
            )
        )
    return ExpressivePerformanceRequest(schema_version="1.0", tracks=tuple(tracks))


def resolve_expressive_performance(
    project: MusicProject,
    request: ExpressivePerformanceRequest,
) -> ExpressivePerformance:
    if request.schema_version != "1.0":
        raise DomainValidationError("unsupported expressive performance schema version")
    project.validate()
    track_ids = [track.id for track in project.tracks]
    performance_track_ids = [track.track_id for track in request.tracks]
    if len(performance_track_ids) != len(set(performance_track_ids)):
        raise DomainValidationError("expressive performance contains duplicate track ids")
    if set(performance_track_ids) != set(track_ids):
        missing = sorted(set(track_ids) - set(performance_track_ids))
        unknown = sorted(set(performance_track_ids) - set(track_ids))
        raise DomainValidationError(
            "expressive performance must cover exactly the score tracks; "
            f"missing={missing}, unknown={unknown}"
        )
    phrase_by_id = {phrase.id: phrase for phrase in project.phrases}
    request_by_track = {track.track_id: track for track in request.tracks}
    resolved_tracks: list[TrackPerformance] = []
    for score_track in project.tracks:
        performance = request_by_track[score_track.id]
        required_phrase_ids = {
            event.phrase_id
            for event in score_track.events
            if event.phrase_id is not None
        }
        supplied_phrase_ids = [phrase.phrase_id for phrase in performance.phrases]
        if len(supplied_phrase_ids) != len(set(supplied_phrase_ids)):
            raise DomainValidationError(
                f"duplicate phrase performance in track {score_track.id}"
            )
        unknown = sorted(set(supplied_phrase_ids) - required_phrase_ids)
        if unknown:
            raise DomainValidationError(
                f"track {score_track.id} performance references phrases not used "
                f"by that track: {unknown}"
            )
        for phrase_performance in performance.phrases:
            phrase = phrase_by_id.get(phrase_performance.phrase_id)
            if phrase is None:
                raise DomainValidationError(
                    f"unknown phrase in expressive performance: {phrase_performance.phrase_id}"
                )
            if not (
                phrase.start_beat - 1e-6
                <= phrase_performance.peak_beat
                <= phrase.end_beat + 1e-6
            ):
                raise DomainValidationError(
                    f"expression peak for {phrase.id} lies outside the phrase"
                )
        note_indices = [item.note_index for item in performance.note_adjustments]
        if len(note_indices) != len(set(note_indices)):
            raise DomainValidationError(
                f"duplicate note adjustment in track {score_track.id}"
            )
        for note_performance in performance.note_adjustments:
            if not 0 <= note_performance.note_index < len(score_track.events):
                raise DomainValidationError(
                    f"note adjustment index is outside track {score_track.id}: "
                    f"{note_performance.note_index}"
                )
            event = score_track.events[note_performance.note_index]
            performed_start = event.start_beat + note_performance.onset_offset_beats
            performed_end = performed_start + (
                event.duration_beats * note_performance.duration_scale
            )
            if performed_start < -1e-6 or performed_end > project.total_beats + 1e-6:
                raise DomainValidationError(
                    f"note adjustment leaves the project timeline in track {score_track.id}"
                )
        resolved_tracks.append(performance)
    return ExpressivePerformance(
        schema_version="1.0",
        score_sha256=score_sha256(project),
        tracks=tuple(resolved_tracks),
    )


def validate_expressive_performance(
    project: MusicProject,
    performance: ExpressivePerformance,
) -> None:
    if performance.score_sha256 != score_sha256(project):
        raise DomainValidationError(
            "Expressive Performance IR does not match the current Score IR"
        )
    if performance.schema_version != "1.0":
        raise DomainValidationError("unsupported Expressive Performance IR version")
    resolve_expressive_performance(
        project,
        ExpressivePerformanceRequest(
            schema_version=performance.schema_version,
            tracks=performance.tracks,
        ),
    )


def expression_curve(
    project: MusicProject,
    performance: TrackPerformance,
    *,
    step_beats: float = 0.25,
) -> tuple[tuple[float, int], ...]:
    """Expand phrase arcs into deterministic, smoothly sampled MIDI CC11 points."""

    phrase_by_id = {phrase.id: phrase for phrase in project.phrases}
    points: dict[float, int] = {0.0: performance.base_expression}
    for interpretation in performance.phrases:
        phrase = phrase_by_id[interpretation.phrase_id]
        anchors = (
            (phrase.start_beat, interpretation.start_expression),
            (interpretation.peak_beat, interpretation.peak_expression),
            (phrase.end_beat, interpretation.end_expression),
        )
        for (left_beat, left_value), (right_beat, right_value) in zip(
            anchors,
            anchors[1:],
            strict=False,
        ):
            span = right_beat - left_beat
            count = max(1, round(span / step_beats)) if span > 0 else 1
            for index in range(count + 1):
                fraction = index / count
                beat = left_beat + span * fraction
                value = round(left_value + (right_value - left_value) * fraction)
                points[round(beat, 9)] = max(1, min(127, value))
    ordered = sorted(
        performance.phrases,
        key=lambda item: phrase_by_id[item.phrase_id].start_beat,
    )
    for current, following in zip(ordered, ordered[1:], strict=False):
        current_phrase = phrase_by_id[current.phrase_id]
        following_phrase = phrase_by_id[following.phrase_id]
        if (
            current.breath_after_beats == 0
            and abs(current_phrase.end_beat - following_phrase.start_beat) <= 1e-6
        ):
            boundary_value = round(
                (current.end_expression + following.start_expression) / 2
            )
            points[round(current_phrase.end_beat, 9)] = boundary_value
    return tuple(sorted(points.items()))
