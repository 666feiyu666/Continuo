"""Canonical structured music project and validation rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .instruments import SUPPORTED_INSTRUMENT_IDS, instrument_definition


SUPPORTED_ARTICULATIONS = (
    "normal",
    "legato",
    "tenuto",
    "staccato",
    "accent",
    "marcato",
)

SUPPORTED_NOTE_CONNECTIONS = (
    "separate",
    "slur",
    "breath",
)

SUPPORTED_AUTOMATION_PARAMETERS = (
    "gain",
    "pan",
    "expression",
    "breath",
    "modulation",
    "pitch_bend",
)


class DomainValidationError(ValueError):
    """Raised when untrusted model output violates an authoritative IR contract."""


def _bounded(name: str, value: float, minimum: float, maximum: float) -> None:
    if not minimum <= value <= maximum:
        raise DomainValidationError(
            f"{name} must be between {minimum} and {maximum}; got {value}"
        )


@dataclass(slots=True)
class Section:
    id: str
    label: str
    start_beat: float
    end_beat: float

    def validate(self, total_beats: float) -> None:
        if not self.id.strip():
            raise DomainValidationError("section id cannot be empty")
        if self.start_beat < 0 or self.end_beat <= self.start_beat:
            raise DomainValidationError(f"invalid section bounds for {self.id}")
        if self.end_beat > total_beats + 1e-6:
            raise DomainValidationError(f"section {self.id} exceeds project duration")


@dataclass(slots=True)
class NoteEvent:
    start_beat: float
    duration_beats: float
    pitch: int
    velocity: float = 0.7
    section_id: str | None = None
    phrase_id: str | None = None
    articulation: str = "normal"
    connection_to_next: str = "separate"

    def validate(self, total_beats: float) -> None:
        if self.start_beat < 0 or self.start_beat >= total_beats + 1e-6:
            raise DomainValidationError(f"note starts outside timeline: {self.start_beat}")
        if self.duration_beats <= 0:
            raise DomainValidationError("note duration must be positive")
        if self.start_beat + self.duration_beats > total_beats + 1e-6:
            raise DomainValidationError("note extends beyond the project timeline")
        if not 0 <= self.pitch <= 127:
            raise DomainValidationError(f"MIDI pitch out of range: {self.pitch}")
        _bounded("velocity", self.velocity, 0.0, 1.0)
        if self.articulation not in SUPPORTED_ARTICULATIONS:
            raise DomainValidationError(
                f"unsupported articulation: {self.articulation}"
            )
        if self.connection_to_next not in SUPPORTED_NOTE_CONNECTIONS:
            raise DomainValidationError(
                f"unsupported note connection: {self.connection_to_next}"
            )


@dataclass(slots=True)
class AutomationPoint:
    beat: float
    parameter: str
    value: float

    def validate(self, total_beats: float) -> None:
        if not 0 <= self.beat <= total_beats:
            raise DomainValidationError("automation point is outside timeline")
        if self.parameter not in SUPPORTED_AUTOMATION_PARAMETERS:
            raise DomainValidationError(
                f"unsupported automation parameter: {self.parameter}"
            )
        minimum = -1.0 if self.parameter in {"pan", "pitch_bend"} else 0.0
        _bounded(f"{self.parameter} automation", self.value, minimum, 1.0)


@dataclass(slots=True)
class InstrumentSpec:
    id: str

    def validate(self) -> None:
        if self.id not in SUPPORTED_INSTRUMENT_IDS:
            raise DomainValidationError(f"unsupported instrument: {self.id}")


@dataclass(slots=True)
class KeyRegion:
    id: str
    section_id: str
    start_beat: float
    end_beat: float
    tonic: str
    mode: str

    def validate(self, total_beats: float) -> None:
        if not self.id.strip() or not self.section_id.strip():
            raise DomainValidationError("key region id and section_id are required")
        if self.start_beat < 0 or self.end_beat <= self.start_beat:
            raise DomainValidationError(f"invalid key-region bounds for {self.id}")
        if self.end_beat > total_beats + 1e-6:
            raise DomainValidationError(f"key region {self.id} exceeds project duration")
        if not self.tonic.strip() or not self.mode.strip():
            raise DomainValidationError("key region tonic and mode are required")


@dataclass(slots=True)
class Phrase:
    id: str
    label: str
    start_beat: float
    end_beat: float
    motif_id: str
    variation_of: str | None = None

    def validate(self, total_beats: float) -> None:
        if not all((self.id.strip(), self.label.strip())):
            raise DomainValidationError("phrase id and label are required")
        if not self.motif_id.strip():
            raise DomainValidationError("phrase motif_id is required")
        if self.start_beat < 0 or self.end_beat <= self.start_beat:
            raise DomainValidationError(f"invalid phrase bounds for {self.id}")
        if self.end_beat > total_beats + 1e-6:
            raise DomainValidationError(f"phrase {self.id} exceeds project duration")
        if self.variation_of == self.id:
            raise DomainValidationError(f"phrase {self.id} cannot vary itself")


@dataclass(slots=True)
class Track:
    id: str
    name: str
    role: str
    instrument: InstrumentSpec
    gain: float = 1.0
    pan: float = 0.0
    events: list[NoteEvent] = field(default_factory=list)
    automation: list[AutomationPoint] = field(default_factory=list)

    def validate(self, total_beats: float, forbid_vocals: bool) -> None:
        if not self.id.strip() or not self.name.strip() or not self.role.strip():
            raise DomainValidationError("track id, name, and role are required")
        if forbid_vocals:
            searchable = f"{self.id} {self.name} {self.role}".lower()
            if any(token in searchable for token in ("vocal", "voice", "choir", "人声")):
                raise DomainValidationError(f"vocal track is forbidden: {self.id}")
        _bounded("track gain", self.gain, 0.0, 2.0)
        _bounded("track pan", self.pan, -1.0, 1.0)
        self.instrument.validate()
        for event in self.events:
            try:
                event.validate(total_beats)
            except DomainValidationError as exc:
                raise DomainValidationError(
                    f"invalid note in track {self.id}: start={event.start_beat}, "
                    f"duration={event.duration_beats}, pitch={event.pitch}; {exc}"
                ) from exc
        for point in self.automation:
            point.validate(total_beats)
        automation_positions: set[tuple[str, float]] = set()
        for point in self.automation:
            position = (point.parameter, point.beat)
            if position in automation_positions:
                raise DomainValidationError(
                    f"duplicate {point.parameter} automation at beat {point.beat} "
                    f"in track {self.id}"
                )
            automation_positions.add(position)

        definition = instrument_definition(self.instrument.id)
        ordered_events = sorted(self.events, key=lambda item: item.start_beat)
        for index, event in enumerate(ordered_events):
            if event.connection_to_next == "separate":
                continue
            if not definition.monophonic:
                raise DomainValidationError(
                    f"note connections require a monophonic instrument track: {self.id}"
                )
            if index + 1 >= len(ordered_events):
                raise DomainValidationError(
                    f"last note in track {self.id} cannot connect to a following note"
                )
            following = ordered_events[index + 1]
            gap = following.start_beat - (
                event.start_beat + event.duration_beats
            )
            if event.connection_to_next == "slur":
                if event.phrase_id != following.phrase_id:
                    raise DomainValidationError(
                        f"slur in track {self.id} cannot cross phrase identities"
                    )
                if gap < -1e-6 or gap > 0.25 + 1e-6:
                    raise DomainValidationError(
                        f"slur in track {self.id} requires a gap from 0 to 0.25 "
                        f"beats; got {gap}"
                    )
            elif gap < 0.25 - 1e-6:
                raise DomainValidationError(
                    f"breath in track {self.id} requires at least 0.25 beats of space; "
                    f"got {gap}"
                )


@dataclass(slots=True)
class MasterSpec:
    room_mix: float = 0.08
    room_delay_seconds: float = 0.075
    target_peak: float = 0.88
    fade_out_seconds: float = 0.75

    def validate(self) -> None:
        _bounded("room_mix", self.room_mix, 0.0, 0.8)
        _bounded("room_delay_seconds", self.room_delay_seconds, 0.0, 1.0)
        _bounded("target_peak", self.target_peak, 0.1, 0.99)
        _bounded("fade_out_seconds", self.fade_out_seconds, 0.0, 10.0)


@dataclass(slots=True)
class MusicProject:
    """Canonical score artifact produced by the composer stage."""

    schema_version: str
    title: str
    duration_seconds: float
    tempo_bpm: float
    meter_numerator: int
    meter_denominator: int
    swing: float
    seed: int
    sections: list[Section] = field(default_factory=list)
    key_regions: list[KeyRegion] = field(default_factory=list)
    phrases: list[Phrase] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)

    @property
    def total_beats(self) -> float:
        return self.duration_seconds * self.tempo_bpm / 60.0

    def validate(self, *, forbid_vocals: bool = False) -> None:
        if self.schema_version != "1.0":
            raise DomainValidationError(
                f"unsupported Score IR version: {self.schema_version}"
            )
        if not self.title.strip():
            raise DomainValidationError("project title cannot be empty")
        _bounded("duration_seconds", self.duration_seconds, 0.25, 3600.0)
        _bounded("tempo_bpm", self.tempo_bpm, 20.0, 320.0)
        if self.meter_numerator <= 0 or self.meter_denominator not in {1, 2, 4, 8, 16}:
            raise DomainValidationError("invalid time signature")
        _bounded("swing", self.swing, 0.5, 0.75)
        track_ids = [track.id for track in self.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise DomainValidationError("track ids must be unique")
        section_ids = [section.id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise DomainValidationError("section ids must be unique")
        for section in self.sections:
            section.validate(self.total_beats)
        if self.sections:
            ordered_sections = sorted(self.sections, key=lambda item: item.start_beat)
            if abs(ordered_sections[0].start_beat) > 1e-6:
                raise DomainValidationError("score sections must start at beat 0")
            for previous, current in zip(
                ordered_sections,
                ordered_sections[1:],
                strict=False,
            ):
                if abs(previous.end_beat - current.start_beat) > 1e-6:
                    raise DomainValidationError(
                        "score sections must form one contiguous timeline"
                    )
            if abs(ordered_sections[-1].end_beat - self.total_beats) > 1e-6:
                raise DomainValidationError(
                    "score sections must cover the complete project timeline"
                )
        section_by_id = {section.id: section for section in self.sections}
        key_ids = [region.id for region in self.key_regions]
        if len(key_ids) != len(set(key_ids)):
            raise DomainValidationError("key region ids must be unique")
        for region in self.key_regions:
            region.validate(self.total_beats)
            section = section_by_id.get(region.section_id)
            if section is None:
                raise DomainValidationError(
                    f"key region {region.id} references unknown section"
                )
            if (
                region.start_beat < section.start_beat - 1e-6
                or region.end_beat > section.end_beat + 1e-6
            ):
                raise DomainValidationError(
                    f"key region {region.id} crosses its section boundary"
                )
        phrase_ids = [phrase.id for phrase in self.phrases]
        if len(phrase_ids) != len(set(phrase_ids)):
            raise DomainValidationError("phrase ids must be unique")
        phrase_by_id = {phrase.id: phrase for phrase in self.phrases}
        for phrase in self.phrases:
            phrase.validate(self.total_beats)
            if (
                phrase.variation_of is not None
                and phrase.variation_of not in phrase_by_id
            ):
                raise DomainValidationError(
                    f"phrase {phrase.id} varies unknown phrase {phrase.variation_of}"
                )
            if phrase.variation_of is not None:
                source = phrase_by_id[phrase.variation_of]
                if source.start_beat >= phrase.start_beat - 1e-6:
                    raise DomainValidationError(
                        f"phrase {phrase.id} must vary an earlier phrase"
                    )
                if source.motif_id != phrase.motif_id:
                    raise DomainValidationError(
                        f"phrase {phrase.id} variation must preserve motif_id"
                    )
        for track in self.tracks:
            track.validate(self.total_beats, forbid_vocals)
            definition = instrument_definition(track.instrument.id)
            for event in track.events:
                if self.sections:
                    if event.section_id is None:
                        raise DomainValidationError(
                            f"note in track {track.id} has no section_id"
                        )
                    section = section_by_id.get(event.section_id)
                    if section is None:
                        raise DomainValidationError(
                            f"note in track {track.id} references unknown section"
                        )
                    if not (
                        section.start_beat - 1e-6
                        <= event.start_beat
                        < section.end_beat - 1e-6
                    ):
                        raise DomainValidationError(
                            f"note in track {track.id} must name the section where "
                            f"it begins: start={event.start_beat}, "
                            f"section={event.section_id}"
                        )
                if event.phrase_id is not None:
                    phrase = phrase_by_id.get(event.phrase_id)
                    if phrase is None:
                        raise DomainValidationError(
                            f"note in track {track.id} references unknown phrase"
                        )
                    if (
                        event.start_beat < phrase.start_beat - 1e-6
                        or event.start_beat + event.duration_beats
                        > phrase.end_beat + 1e-6
                    ):
                        raise DomainValidationError(
                            f"note in track {track.id} crosses phrase {event.phrase_id}: "
                            f"note=[{event.start_beat}, "
                            f"{event.start_beat + event.duration_beats}], "
                            f"phrase=[{phrase.start_beat}, {phrase.end_beat}]"
                        )
            maximum_polyphony = _maximum_polyphony(track.events)
            if definition.monophonic and maximum_polyphony > 1:
                raise DomainValidationError(
                    f"monophonic instrument track overlaps notes: {track.id}"
                )
            if maximum_polyphony > 16:
                raise DomainValidationError(
                    f"track exceeds the score polyphony limit: {track.id}"
                )
        if not self.tracks:
            raise DomainValidationError("project must contain at least one track")
        if not any(track.events for track in self.tracks):
            raise DomainValidationError("project must contain at least one note event")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _maximum_polyphony(events: list[NoteEvent]) -> int:
    points: list[tuple[float, int]] = []
    for event in events:
        points.append((event.start_beat, 1))
        points.append((event.start_beat + event.duration_beats, -1))
    active = 0
    maximum = 0
    for _, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


ScoreProject = MusicProject
