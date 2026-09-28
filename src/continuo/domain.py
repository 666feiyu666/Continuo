from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
class SynthSpec:
    voice: str = "oscillator"
    oscillator: str = "sine"
    partials: list[float] = field(default_factory=lambda: [1.0])
    noise_mix: float = 0.0
    attack_seconds: float = 0.01
    decay_seconds: float = 0.08
    sustain_level: float = 0.7
    release_seconds: float = 0.15
    gain: float = 0.25

    def validate(self) -> None:
        supported_voices = {
            "oscillator",
            "acoustic_piano",
            "upright_bass",
            "vibraphone",
            "soft_kick",
            "brush_snare",
            "ride_cymbal",
        }
        if self.voice not in supported_voices:
            raise DomainValidationError(f"unsupported synth voice: {self.voice}")
        if self.oscillator not in {"sine", "triangle", "square", "saw"}:
            raise DomainValidationError(f"unsupported oscillator: {self.oscillator}")
        if not self.partials or len(self.partials) > 8:
            raise DomainValidationError("partials must contain between 1 and 8 values")
        if any(weight < 0 or weight > 1 for weight in self.partials):
            raise DomainValidationError("partial weights must be between 0 and 1")
        _bounded("noise_mix", self.noise_mix, 0.0, 1.0)
        _bounded("attack_seconds", self.attack_seconds, 0.0, 10.0)
        _bounded("decay_seconds", self.decay_seconds, 0.0, 10.0)
        _bounded("sustain_level", self.sustain_level, 0.0, 1.0)
        _bounded("release_seconds", self.release_seconds, 0.0, 20.0)
        _bounded("synth gain", self.gain, 0.0, 1.0)


@dataclass(slots=True)
class Track:
    id: str
    name: str
    role: str
    synth: SynthSpec
    gain: float = 1.0
    pan: float = 0.0
    midi_channel: int = 0
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
        if not 0 <= self.midi_channel <= 15:
            raise DomainValidationError("MIDI channel must be between 0 and 15")
        self.synth.validate()
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
        self.master.validate()
        if not self.tracks:
            raise DomainValidationError("project must contain at least one track")
        if not any(track.events for track in self.tracks):
            raise DomainValidationError("project must contain at least one note event")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
