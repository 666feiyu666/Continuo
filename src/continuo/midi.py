from __future__ import annotations

import struct
from pathlib import Path

from .domain import DomainValidationError, MusicProject, Track
from .expressive_performance import (
    ExpressivePerformance,
    TrackPerformance,
    expression_curve,
    validate_expressive_performance,
)
from .instruments import INSTRUMENT_CATALOG, instrument_definition
from .score_identity import score_sha256
from .soundfont_mapping import SoundFontMapping, SoundFontTrackMapping


TICKS_PER_BEAT = 480


GM_PROGRAM_BY_INSTRUMENT = {
    item.id: item.program
    for item in INSTRUMENT_CATALOG
    if item.program is not None
}


def _variable_length(value: int) -> bytes:
    if value < 0:
        raise ValueError("variable-length MIDI integers cannot be negative")
    buffer = value & 0x7F
    output = bytearray([buffer])
    while value >> 7:
        value >>= 7
        buffer = (value & 0x7F) | 0x80
        output.insert(0, buffer)
    return bytes(output)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack(">I", len(payload)) + payload


def _meta_track(project: MusicProject) -> bytes:
    tempo = round(60_000_000 / project.tempo_bpm)
    denominator_power = project.meter_denominator.bit_length() - 1
    events = bytearray()
    events += b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big")
    events += (
        b"\x00\xff\x58\x04"
        + bytes([project.meter_numerator, denominator_power, 24, 8])
    )
    total_ticks = round(project.total_beats * TICKS_PER_BEAT)
    events += _variable_length(total_ticks) + b"\xff\x2f\x00"
    return _chunk(b"MTrk", bytes(events))


_ARTICULATION_LENGTH = {
    "normal": 1.0,
    "legato": 1.08,
    "tenuto": 1.0,
    "staccato": 0.55,
    "accent": 0.92,
    "marcato": 0.72,
}

_ARTICULATION_VELOCITY = {
    "normal": 1.0,
    "legato": 0.98,
    "tenuto": 1.0,
    "staccato": 0.96,
    "accent": 1.12,
    "marcato": 1.18,
}


def _midi_track(
    project: MusicProject,
    track: Track,
    total_ticks: int,
    channel: int,
    performance: TrackPerformance | None = None,
    mapping: SoundFontTrackMapping | None = None,
) -> bytes:
    absolute_events: list[tuple[int, int, bytes]] = []
    instrument = instrument_definition(track.instrument.id)
    name = track.name.encode("utf-8")[:127]
    prefix = b"\x00\xff\x03" + _variable_length(len(name)) + name
    preset_messages = b""
    binding = mapping.preset if mapping is not None else None
    if binding is not None:
        if binding.bank == 128 and binding.is_percussion:
            # FluidSynth's drum channel already addresses its internal bank 128.
            bank_select = b""
        elif 0 <= binding.bank <= 127:
            # The renderer explicitly uses GS bank selection: CC0 is the bank.
            bank_select = b"\x00" + bytes([0xB0 | channel, 0, binding.bank])
        else:
            raise DomainValidationError(
                f"preset bank cannot be encoded in GS-style MIDI: {binding.bank}"
            )
        preset_messages = (
            bank_select
            + b"\x00"
            + bytes([0xC0 | channel, binding.program])
        )
    elif instrument.program is not None:
        # Compatibility binding for the Python and SuperCollider development paths.
        preset_messages = b"\x00" + bytes([0xC0 | channel, instrument.program])
    pan = max(0, min(127, round((track.pan + 1.0) * 63.5)))
    volume = max(0, min(127, round(min(track.gain, 1.0) * 127)))
    controllers = (
        b"\x00" + bytes([0xB0 | channel, 10, pan])
        + b"\x00" + bytes([0xB0 | channel, 7, volume])
    )
    if performance is not None:
        controllers += b"\x00" + bytes(
            [0xB0 | channel, 11, performance.base_expression]
        )
        for beat, value in expression_curve(project, performance):
            tick = max(0, min(total_ticks, round(beat * TICKS_PER_BEAT)))
            absolute_events.append(
                (tick, 1, bytes([0xB0 | channel, 11, value]))
            )

    performed_notes: list[dict[str, object]] = []
    for note_index, event in enumerate(track.events):
        adjustment = performance.note(note_index) if performance is not None else None
        start_beat = event.start_beat + (
            adjustment.onset_offset_beats if adjustment is not None else 0.0
        )
        duration_scale = (
            adjustment.duration_scale if adjustment is not None else 1.0
        )
        velocity_scale = (
            adjustment.velocity_scale if adjustment is not None else 1.0
        )
        performed_notes.append(
            {
                "note_index": note_index,
                "event": event,
                "start_beat": start_beat,
                "end_beat": start_beat
                + event.duration_beats
                * duration_scale
                * _ARTICULATION_LENGTH[event.articulation],
                "velocity_scale": velocity_scale,
            }
        )

    if performance is not None and instrument.monophonic:
        phrase_by_id = {phrase.id: phrase for phrase in project.phrases}
        for phrase_performance in performance.phrases:
            phrase_notes = sorted(
                (
                    item
                    for item in performed_notes
                    if item["event"].phrase_id == phrase_performance.phrase_id
                ),
                key=lambda item: (item["start_beat"], item["note_index"]),
            )
            for current, following in zip(
                phrase_notes,
                phrase_notes[1:],
                strict=False,
            ):
                start = float(current["start_beat"])
                end = float(current["end_beat"])
                following_start = float(following["start_beat"])
                notated_gap = following_start - end
                if notated_gap > 0.125 or following_start <= start:
                    continue
                if phrase_performance.connection == "legato":
                    current["end_beat"] = max(end, following_start + 0.04)
                elif phrase_performance.connection == "connected":
                    current["end_beat"] = max(end, following_start)
                else:
                    current["end_beat"] = min(end, following_start - 0.06)
            if phrase_notes and phrase_performance.breath_after_beats > 0:
                phrase = phrase_by_id[phrase_performance.phrase_id]
                last = phrase_notes[-1]
                last["end_beat"] = min(
                    float(last["end_beat"]),
                    phrase.end_beat - phrase_performance.breath_after_beats,
                )

    for performed_note in performed_notes:
        event = performed_note["event"]
        pitch = (
            instrument.percussion_note
            if instrument.percussion_note is not None
            else event.pitch
        )
        start = max(0, round(float(performed_note["start_beat"]) * TICKS_PER_BEAT))
        end = max(
            start + 1,
            round(float(performed_note["end_beat"]) * TICKS_PER_BEAT),
        )
        end = min(total_ticks, end)
        velocity_scale = float(performed_note["velocity_scale"]) * (
            _ARTICULATION_VELOCITY[event.articulation]
        )
        velocity = max(1, min(127, round(event.velocity * velocity_scale * 127)))
        absolute_events.append((start, 2, bytes([0x90 | channel, pitch, velocity])))
        absolute_events.append((end, 0, bytes([0x80 | channel, pitch, 0])))
    absolute_events.sort(key=lambda item: (item[0], item[1]))
    payload = bytearray(prefix + preset_messages + controllers)
    previous_tick = 0
    for tick, _, message in absolute_events:
        payload += _variable_length(tick - previous_tick)
        payload += message
        previous_tick = tick
    payload += _variable_length(max(0, total_ticks - previous_tick)) + b"\xff\x2f\x00"
    return _chunk(b"MTrk", bytes(payload))


def write_midi(
    project: MusicProject,
    output_path: Path,
    performance: ExpressivePerformance | None = None,
    mapping: SoundFontMapping | None = None,
) -> None:
    project.validate()
    if performance is not None:
        validate_expressive_performance(project, performance)
    if mapping is not None and mapping.score_sha256 != score_sha256(project):
        raise DomainValidationError("SoundFont Mapping IR does not match the Score IR")
    total_ticks = round(project.total_beats * TICKS_PER_BEAT)
    pitched_channels = iter((*range(9), *range(10, 16)))
    track_channels: list[int] = []
    for track in project.tracks:
        instrument = instrument_definition(track.instrument.id)
        if instrument.is_percussion:
            track_channels.append(9)
            continue
        try:
            track_channels.append(next(pitched_channels))
        except StopIteration as exc:
            raise DomainValidationError(
                "SoundFont rendering supports at most 15 pitched instrument tracks"
            ) from exc
    tracks = [_meta_track(project)] + [
        _midi_track(
            project,
            track,
            total_ticks,
            channel,
            performance.for_track(track.id) if performance is not None else None,
            mapping.for_track(track.id) if mapping is not None else None,
        )
        for track, channel in zip(project.tracks, track_channels, strict=True)
    ]
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(tracks), TICKS_PER_BEAT))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + b"".join(tracks))
