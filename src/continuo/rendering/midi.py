from __future__ import annotations

import struct
from pathlib import Path

from .articulation import realized_duration, realized_velocity
from ..model import (
    AutomationPoint,
    DomainValidationError,
    INSTRUMENT_CATALOG,
    MusicProject,
    NoteEvent,
    Track,
    instrument_definition,
    score_sha256,
)
from .soundfont.mapping import SoundFontMapping, SoundFontTrackMapping


TICKS_PER_BEAT = 480
AUTOMATION_STEP_TICKS = 40
LEGATO_OVERLAP_BEATS = 0.08

CONTROL_CHANGE_BY_PARAMETER = {
    "gain": 7,
    "pan": 10,
    "expression": 11,
    "breath": 2,
    "modulation": 1,
}

GM_PROGRAM_BY_INSTRUMENT = {
    item.id: item.program for item in INSTRUMENT_CATALOG if item.program is not None
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


def realized_midi_end_beat(
    event: NoteEvent,
    following: NoteEvent | None,
) -> float:
    end_beat = event.start_beat + realized_duration(
        event.duration_beats,
        event.articulation,
    )
    if event.connection_to_next == "slur" and following is not None:
        end_beat = max(end_beat, following.start_beat + LEGATO_OVERLAP_BEATS)
    return end_beat


def _automation_value(parameter: str, value: float) -> int:
    if parameter == "pan":
        return max(0, min(127, round((value + 1.0) * 63.5)))
    return max(0, min(127, round(value * 127)))


def _pitch_bend_message(channel: int, value: float) -> bytes:
    bend = max(0, min(16_383, round((value + 1.0) * 8191.5)))
    return bytes([0xE0 | channel, bend & 0x7F, (bend >> 7) & 0x7F])


def _automation_message(channel: int, point: AutomationPoint) -> bytes:
    if point.parameter == "pitch_bend":
        return _pitch_bend_message(channel, point.value)
    controller = CONTROL_CHANGE_BY_PARAMETER[point.parameter]
    return bytes(
        [0xB0 | channel, controller, _automation_value(point.parameter, point.value)]
    )


def _automation_events(
    track: Track,
    channel: int,
    total_ticks: int,
) -> list[tuple[int, int, bytes]]:
    by_parameter: dict[str, list[AutomationPoint]] = {}
    for point in track.automation:
        by_parameter.setdefault(point.parameter, []).append(point)

    messages: dict[tuple[int, str], bytes] = {}
    for parameter, raw_points in by_parameter.items():
        points = sorted(raw_points, key=lambda item: item.beat)
        if len(points) == 1:
            tick = min(total_ticks, round(points[0].beat * TICKS_PER_BEAT))
            messages[(tick, parameter)] = _automation_message(channel, points[0])
            continue
        for left, right in zip(points, points[1:], strict=False):
            start_tick = min(total_ticks, round(left.beat * TICKS_PER_BEAT))
            end_tick = min(total_ticks, round(right.beat * TICKS_PER_BEAT))
            span = max(1, end_tick - start_tick)
            ticks = list(range(start_tick, end_tick, AUTOMATION_STEP_TICKS))
            if not ticks or ticks[-1] != end_tick:
                ticks.append(end_tick)
            for tick in ticks:
                ratio = (tick - start_tick) / span
                value = left.value + (right.value - left.value) * ratio
                messages[(tick, parameter)] = _automation_message(
                    channel,
                    AutomationPoint(beat=tick / TICKS_PER_BEAT, parameter=parameter, value=value),
                )
    return [
        (tick, 1, message)
        for (tick, _), message in sorted(messages.items(), key=lambda item: item[0])
    ]


def _midi_track(
    track: Track,
    total_ticks: int,
    channel: int,
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
            bank_select = b""
        elif 0 <= binding.bank <= 127:
            bank_select = b"\x00" + bytes([0xB0 | channel, 0, binding.bank])
        else:
            raise DomainValidationError(
                f"preset bank cannot be encoded in GS-style MIDI: {binding.bank}"
            )
        preset_messages = bank_select + b"\x00" + bytes(
            [0xC0 | channel, binding.program]
        )
    elif instrument.program is not None:
        preset_messages = b"\x00" + bytes([0xC0 | channel, instrument.program])

    pan = max(0, min(127, round((track.pan + 1.0) * 63.5)))
    volume = max(0, min(127, round(min(track.gain, 1.0) * 127)))
    controllers = (
        b"\x00"
        + bytes([0xB0 | channel, 10, pan])
        + b"\x00"
        + bytes([0xB0 | channel, 7, volume])
    )
    ordered_events = sorted(track.events, key=lambda item: item.start_beat)
    for index, event in enumerate(ordered_events):
        pitch = (
            instrument.percussion_note
            if instrument.percussion_note is not None
            else event.pitch
        )
        start = max(0, round(event.start_beat * TICKS_PER_BEAT))
        following = (
            ordered_events[index + 1]
            if index + 1 < len(ordered_events)
            else None
        )
        end_beat = realized_midi_end_beat(event, following)
        end = min(total_ticks, max(start + 1, round(end_beat * TICKS_PER_BEAT)))
        velocity = max(
            1,
            min(
                127,
                round(
                    realized_velocity(event.velocity, event.articulation) * 127
                ),
            ),
        )
        absolute_events.append((start, 2, bytes([0x90 | channel, pitch, velocity])))
        absolute_events.append((end, 0, bytes([0x80 | channel, pitch, 0])))
    absolute_events.extend(_automation_events(track, channel, total_ticks))
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
    mapping: SoundFontMapping | None = None,
) -> None:
    project.validate()
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
                "MIDI export supports at most 15 simultaneous pitched tracks"
            ) from exc
    tracks = [_meta_track(project)] + [
        _midi_track(
            track,
            total_ticks,
            channel,
            mapping.for_track(track.id) if mapping is not None else None,
        )
        for track, channel in zip(project.tracks, track_channels, strict=True)
    ]
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(tracks), TICKS_PER_BEAT))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + b"".join(tracks))
