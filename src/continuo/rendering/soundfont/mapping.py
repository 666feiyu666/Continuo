from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from ...model import (
    DomainValidationError,
    INSTRUMENT_CATALOG,
    InstrumentDefinition,
    MusicProject,
    instrument_definition,
    score_sha256,
)
from .profile import SoundFontPreset, SoundFontProfile


@dataclass(frozen=True, slots=True)
class SoundFontMappingProposal:
    track_id: str
    preset_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SoundFontMappingRequest:
    schema_version: str
    master_gain: float
    reverb_enabled: bool
    assignments: tuple[SoundFontMappingProposal, ...]


@dataclass(frozen=True, slots=True)
class SoundFontPresetBinding:
    id: str
    bank: int
    program: int
    name: str
    is_percussion: bool


@dataclass(frozen=True, slots=True)
class SoundFontTrackMapping:
    track_id: str
    preset: SoundFontPresetBinding
    reason: str


@dataclass(frozen=True, slots=True)
class SoundFontMapping:
    """Validated preset binding for one frozen score and SoundFont profile."""

    schema_version: str
    score_sha256: str
    soundfont_id: str
    soundfont_sha256: str
    bank_select: str
    master_gain: float
    reverb_enabled: bool
    tracks: tuple[SoundFontTrackMapping, ...]

    def for_track(self, track_id: str) -> SoundFontTrackMapping:
        for track in self.tracks:
            if track.track_id == track_id:
                return track
        raise KeyError(track_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "score_sha256": self.score_sha256,
            "soundfont": {
                "id": self.soundfont_id,
                "sha256": self.soundfont_sha256,
                "bank_select": self.bank_select,
            },
            "master": {
                "gain": self.master_gain,
                "reverb_enabled": self.reverb_enabled,
            },
            "tracks": [asdict(item) for item in self.tracks],
        }


def _preferred_percussion_preset(
    profile: SoundFontProfile,
) -> SoundFontPreset | None:
    candidates = [preset for preset in profile.presets if preset.is_percussion]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda preset: (
            preset.bank != 128,
            preset.program != 0,
            preset.bank,
            preset.program,
            preset.id,
        ),
    )


def _preferred_melodic_preset(
    instrument: InstrumentDefinition,
    profile: SoundFontProfile,
) -> SoundFontPreset | None:
    candidates = [
        preset
        for preset in profile.presets
        if not preset.is_percussion and preset.program == instrument.program
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda preset: (preset.bank != 0, preset.bank, preset.id),
    )


def preset_for_instrument(
    instrument_id: str,
    profile: SoundFontProfile,
) -> SoundFontPreset:
    """Resolve one semantic instrument through a deterministic profile lookup."""

    instrument = instrument_definition(instrument_id)
    if instrument.is_percussion:
        preset = _preferred_percussion_preset(profile)
    else:
        preset = _preferred_melodic_preset(instrument, profile)
    if preset is None:
        raise DomainValidationError(
            "active SoundFont cannot realize semantic instrument: "
            f"{instrument_id}"
        )
    return preset


def available_instrument_ids(profile: SoundFontProfile) -> tuple[str, ...]:
    """Return the semantic catalog subset realizable by the active SoundFont."""

    return tuple(
        instrument.id
        for instrument in INSTRUMENT_CATALOG
        if (
            _preferred_percussion_preset(profile)
            if instrument.is_percussion
            else _preferred_melodic_preset(instrument, profile)
        )
        is not None
    )


def validate_soundfont_compatibility(
    project: MusicProject,
    profile: SoundFontProfile,
) -> None:
    """Validate backend limits without making them Score IR invariants."""

    project.validate()
    pitched_tracks = sum(
        not instrument_definition(track.instrument.id).is_percussion
        for track in project.tracks
    )
    if pitched_tracks > 15:
        raise DomainValidationError(
            "SoundFont rendering supports at most 15 pitched instrument tracks"
        )
    for instrument_id in {track.instrument.id for track in project.tracks}:
        preset_for_instrument(instrument_id, profile)


def deterministic_soundfont_mapping(
    project: MusicProject,
    profile: SoundFontProfile,
    *,
    master_gain: float = 0.85,
    reverb_enabled: bool = True,
) -> SoundFontMapping:
    """Bind a score to the active SoundFont without a model call."""

    validate_soundfont_compatibility(project, profile)
    assignments = []
    for track in project.tracks:
        instrument = instrument_definition(track.instrument.id)
        preset = preset_for_instrument(track.instrument.id, profile)
        if instrument.is_percussion:
            reason = (
                "Deterministic binding to the shared SoundFont percussion kit "
                f"for {track.instrument.id}."
            )
        else:
            reason = (
                "Deterministic binding from semantic instrument "
                f"{track.instrument.id} to GM program {instrument.program}."
            )
        assignments.append(
            SoundFontMappingProposal(
                track_id=track.id,
                preset_id=preset.id,
                reason=reason,
            )
        )
    return resolve_soundfont_mapping(
        project,
        SoundFontMappingRequest(
            schema_version="1.0",
            master_gain=master_gain,
            reverb_enabled=reverb_enabled,
            assignments=tuple(assignments),
        ),
        profile,
    )

def parse_soundfont_mapping(raw_response: str) -> SoundFontMappingRequest:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise DomainValidationError(
            f"SoundFont mapping response is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "master_gain",
        "reverb_enabled",
        "assignments",
    }:
        raise DomainValidationError(
            "SoundFont mapping response has unknown or missing fields"
        )
    if payload["schema_version"] != "1.0":
        raise DomainValidationError("unsupported SoundFont mapping schema version")
    master_gain = payload["master_gain"]
    reverb_enabled = payload["reverb_enabled"]
    if (
        isinstance(master_gain, bool)
        or not isinstance(master_gain, (int, float))
        or not 0.1 <= master_gain <= 2.0
    ):
        raise DomainValidationError("master_gain must be between 0.1 and 2.0")
    if not isinstance(reverb_enabled, bool):
        raise DomainValidationError("reverb_enabled must be a boolean")
    raw_assignments = payload["assignments"]
    if not isinstance(raw_assignments, list) or not raw_assignments:
        raise DomainValidationError(
            "SoundFont mapping assignments must be a non-empty list"
        )
    assignments: list[SoundFontMappingProposal] = []
    required = {"track_id", "preset_id", "reason"}
    for index, item in enumerate(raw_assignments):
        if not isinstance(item, dict) or set(item) != required:
            raise DomainValidationError(
                f"invalid SoundFont mapping assignment at index {index}"
            )
        if not isinstance(item["track_id"], str) or not item["track_id"].strip():
            raise DomainValidationError(f"invalid track_id at index {index}")
        if not isinstance(item["preset_id"], str) or not item["preset_id"].strip():
            raise DomainValidationError(f"invalid preset_id at index {index}")
        if not isinstance(item["reason"], str) or not item["reason"].strip():
            raise DomainValidationError(f"invalid reason at index {index}")
        assignments.append(SoundFontMappingProposal(**item))
    return SoundFontMappingRequest(
        schema_version="1.0",
        master_gain=float(master_gain),
        reverb_enabled=reverb_enabled,
        assignments=tuple(assignments),
    )


def _resolve_presets(
    project: MusicProject,
    request: SoundFontMappingRequest,
    profile: SoundFontProfile,
) -> dict[str, tuple[SoundFontMappingProposal, SoundFontPreset]]:
    track_ids = [track.id for track in project.tracks]
    assignment_ids = [assignment.track_id for assignment in request.assignments]
    if len(assignment_ids) != len(set(assignment_ids)):
        raise DomainValidationError("SoundFont mapping contains duplicate track ids")
    if set(assignment_ids) != set(track_ids):
        missing = sorted(set(track_ids) - set(assignment_ids))
        unknown = sorted(set(assignment_ids) - set(track_ids))
        raise DomainValidationError(
            "SoundFont mapping must cover exactly the score tracks; "
            f"missing={missing}, unknown={unknown}"
        )
    by_track = {item.track_id: item for item in request.assignments}
    resolved: dict[str, tuple[SoundFontMappingProposal, SoundFontPreset]] = {}
    for track in project.tracks:
        assignment = by_track[track.id]
        try:
            preset = profile.resolve(assignment.preset_id)
        except KeyError as exc:
            raise DomainValidationError(
                f"SoundFont preset is not in the active profile: {assignment.preset_id}"
            ) from exc
        expected_percussion = instrument_definition(track.instrument.id).is_percussion
        if preset.is_percussion != expected_percussion:
            kind = "percussion kit" if preset.is_percussion else "melodic preset"
            raise DomainValidationError(
                f"track {track.id} cannot map {track.instrument.id} to {kind} "
                f"{preset.id}"
            )
        resolved[track.id] = (assignment, preset)
    drum_presets = {
        preset.id for _, preset in resolved.values() if preset.is_percussion
    }
    if len(drum_presets) > 1:
        raise DomainValidationError(
            "all percussion tracks must select one shared SoundFont drum kit"
        )
    return resolved


def resolve_soundfont_mapping(
    project: MusicProject,
    request: SoundFontMappingRequest,
    profile: SoundFontProfile,
) -> SoundFontMapping:
    if request.schema_version != "1.0":
        raise DomainValidationError("unsupported SoundFont mapping schema version")
    validate_soundfont_compatibility(project, profile)
    resolved = _resolve_presets(project, request, profile)
    tracks = []
    for track in project.tracks:
        proposal, preset = resolved[track.id]
        tracks.append(
            SoundFontTrackMapping(
                track_id=track.id,
                preset=SoundFontPresetBinding(
                    id=preset.id,
                    bank=preset.bank,
                    program=preset.program,
                    name=preset.name,
                    is_percussion=preset.is_percussion,
                ),
                reason=proposal.reason,
            )
        )
    return SoundFontMapping(
        schema_version="1.0",
        score_sha256=score_sha256(project),
        soundfont_id=profile.id,
        soundfont_sha256=profile.sha256,
        bank_select=profile.bank_select,
        master_gain=request.master_gain,
        reverb_enabled=request.reverb_enabled,
        tracks=tuple(tracks),
    )


def validate_soundfont_mapping(
    project: MusicProject,
    mapping: SoundFontMapping,
    profile: SoundFontProfile,
) -> None:
    if mapping.score_sha256 != score_sha256(project):
        raise DomainValidationError(
            "SoundFont Mapping IR does not match the current Score IR"
        )
    if mapping.schema_version != "1.0":
        raise DomainValidationError("unsupported SoundFont Mapping IR version")
    if (
        mapping.soundfont_id != profile.id
        or mapping.soundfont_sha256 != profile.sha256
        or mapping.bank_select != profile.bank_select
    ):
        raise DomainValidationError(
            "SoundFont Mapping IR does not match the active SoundFont profile"
        )
    proposals = tuple(
        SoundFontMappingProposal(
            track_id=item.track_id,
            preset_id=item.preset.id,
            reason=item.reason,
        )
        for item in mapping.tracks
    )
    _resolve_presets(
        project,
        SoundFontMappingRequest(
            schema_version="1.0",
            master_gain=mapping.master_gain,
            reverb_enabled=mapping.reverb_enabled,
            assignments=proposals,
        ),
        profile,
    )
