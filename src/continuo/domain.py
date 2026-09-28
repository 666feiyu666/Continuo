from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .instruments import SUPPORTED_INSTRUMENT_IDS, instrument_definition


class DomainValidationError(ValueError):
    """Raised when untrusted model output violates the Music IR contract."""


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


@dataclass(slots=True)
class AutomationPoint:
    beat: float
    parameter: str
    value: float

    def validate(self, total_beats: float) -> None:
        if not 0 <= self.beat <= total_beats:
            raise DomainValidationError("automation point is outside timeline")
        if not self.parameter.strip():
            raise DomainValidationError("automation parameter cannot be empty")


@dataclass(slots=True)
class SoundFontPresetBinding:
    """A model-selected preset resolved against the active SoundFont profile."""

    id: str
    bank: int
    program: int
    name: str
    is_percussion: bool

    def validate(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise DomainValidationError("SoundFont preset id and name are required")
        if not 0 <= self.bank <= 16_383:
            raise DomainValidationError(f"SoundFont bank out of range: {self.bank}")
        if not 0 <= self.program <= 127:
            raise DomainValidationError(
                f"SoundFont program out of range: {self.program}"
            )


@dataclass(slots=True)
class InstrumentSpec:
    id: str
    soundfont_preset: SoundFontPresetBinding | None = None

    def validate(self) -> None:
        if self.id not in SUPPORTED_INSTRUMENT_IDS:
            raise DomainValidationError(f"unsupported instrument: {self.id}")
        if self.soundfont_preset is not None:
            self.soundfont_preset.validate()
            expected_percussion = instrument_definition(self.id).is_percussion
            if self.soundfont_preset.is_percussion != expected_percussion:
                raise DomainValidationError(
                    "SoundFont preset type does not match semantic instrument: "
                    f"{self.id} -> {self.soundfont_preset.id}"
                )


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
            event.validate(total_beats)
        for point in self.automation:
            point.validate(total_beats)


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
    schema_version: str
    title: str
    duration_seconds: float
    tempo_bpm: float
    meter_numerator: int
    meter_denominator: int
    swing: float
    seed: int
    sections: list[Section] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    master: MasterSpec = field(default_factory=MasterSpec)

    @property
    def total_beats(self) -> float:
        return self.duration_seconds * self.tempo_bpm / 60.0

    def validate(self, *, forbid_vocals: bool = False) -> None:
        if self.schema_version != "1.0":
            raise DomainValidationError(
                f"unsupported Music IR version: {self.schema_version}"
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
        for track in self.tracks:
            track.validate(self.total_beats, forbid_vocals)
        bound_tracks = [
            track for track in self.tracks if track.instrument.soundfont_preset is not None
        ]
        if bound_tracks and len(bound_tracks) != len(self.tracks):
            raise DomainValidationError(
                "SoundFont preset mapping must cover every track or no tracks"
            )
        percussion_presets = {
            track.instrument.soundfont_preset.id
            for track in bound_tracks
            if instrument_definition(track.instrument.id).is_percussion
            and track.instrument.soundfont_preset is not None
        }
        if len(percussion_presets) > 1:
            raise DomainValidationError(
                "all percussion tracks sharing MIDI channel 10 must use one drum kit"
            )
        pitched_tracks = sum(
            not instrument_definition(track.instrument.id).is_percussion
            for track in self.tracks
        )
        if pitched_tracks > 15:
            raise DomainValidationError(
                "SoundFont rendering supports at most 15 pitched instrument tracks"
            )
        self.master.validate()
        if not self.tracks:
            raise DomainValidationError("project must contain at least one track")
        if not any(track.events for track in self.tracks):
            raise DomainValidationError("project must contain at least one note event")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
