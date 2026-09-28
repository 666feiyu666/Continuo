from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .domain import (
    DomainValidationError,
    MusicProject,
    SoundFontPresetBinding,
)
from .instruments import instrument_definition
from .soundfont_profile import SoundFontProfile, SoundFontPreset


@dataclass(frozen=True, slots=True)
class SoundFontAssignment:
    track_id: str
    preset_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SoundFontMapping:
    schema_version: str
    assignments: tuple[SoundFontAssignment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "assignments": [asdict(item) for item in self.assignments],
        }


def parse_soundfont_mapping(raw_response: str) -> SoundFontMapping:
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise DomainValidationError(
            f"SoundFont mapping response is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "assignments",
    }:
        raise DomainValidationError(
            "SoundFont mapping response must contain only schema_version and assignments"
        )
    if payload["schema_version"] != "1.0":
        raise DomainValidationError("unsupported SoundFont mapping schema version")
    raw_assignments = payload["assignments"]
    if not isinstance(raw_assignments, list) or not raw_assignments:
        raise DomainValidationError("SoundFont assignments must be a non-empty list")
    assignments: list[SoundFontAssignment] = []
    for index, item in enumerate(raw_assignments):
        if not isinstance(item, dict) or set(item) != {
            "track_id",
            "preset_id",
            "reason",
        }:
            raise DomainValidationError(
                f"invalid SoundFont assignment at index {index}"
            )
        if not all(isinstance(item[key], str) for key in item):
            raise DomainValidationError(
                f"SoundFont assignment fields must be strings at index {index}"
            )
        if not item["track_id"].strip() or not item["preset_id"].strip():
            raise DomainValidationError(
                f"SoundFont assignment ids cannot be empty at index {index}"
            )
        assignments.append(SoundFontAssignment(**item))
    return SoundFontMapping(schema_version="1.0", assignments=tuple(assignments))


def _resolve_assignments(
    project: MusicProject,
    mapping: SoundFontMapping,
    profile: SoundFontProfile,
) -> dict[str, SoundFontPreset]:
    track_ids = [track.id for track in project.tracks]
    assignment_ids = [assignment.track_id for assignment in mapping.assignments]
    if len(assignment_ids) != len(set(assignment_ids)):
        raise DomainValidationError("SoundFont mapping contains duplicate track ids")
    if set(assignment_ids) != set(track_ids):
        missing = sorted(set(track_ids) - set(assignment_ids))
        unknown = sorted(set(assignment_ids) - set(track_ids))
        raise DomainValidationError(
            f"SoundFont mapping must cover exactly the project tracks; "
            f"missing={missing}, unknown={unknown}"
        )
    by_track = {item.track_id: item for item in mapping.assignments}
    resolved: dict[str, SoundFontPreset] = {}
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
        resolved[track.id] = preset
    drum_presets = {
        preset.id for preset in resolved.values() if preset.is_percussion
    }
    if len(drum_presets) > 1:
        raise DomainValidationError(
            "all percussion tracks must select one shared SoundFont drum kit"
        )
    return resolved


def apply_soundfont_mapping(
    project: MusicProject,
    mapping: SoundFontMapping,
    profile: SoundFontProfile,
) -> None:
    """Validate the complete model proposal before attaching any preset bindings."""

    resolved = _resolve_assignments(project, mapping, profile)
    for track in project.tracks:
        preset = resolved[track.id]
        track.instrument.soundfont_preset = SoundFontPresetBinding(
            id=preset.id,
            bank=preset.bank,
            program=preset.program,
            name=preset.name,
            is_percussion=preset.is_percussion,
        )
    project.validate()


def validate_soundfont_bindings(
    project: MusicProject,
    profile: SoundFontProfile,
) -> None:
    assignments = []
    for track in project.tracks:
        binding = track.instrument.soundfont_preset
        if binding is None:
            raise DomainValidationError(
                f"track {track.id} has no model-selected SoundFont preset"
            )
        assignments.append(
            SoundFontAssignment(
                track_id=track.id,
                preset_id=binding.id,
                reason="validated persisted binding",
            )
        )
    _resolve_assignments(
        project,
        SoundFontMapping(schema_version="1.0", assignments=tuple(assignments)),
        profile,
    )
