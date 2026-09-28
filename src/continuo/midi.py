from __future__ import annotations

import struct
from pathlib import Path

from .domain import MusicProject, Track


TICKS_PER_BEAT = 480


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
    events += b"\x00\xff\x2f\x00"
    return _chunk(b"MTrk", bytes(events))


def _midi_track(track: Track) -> bytes:
    absolute_events: list[tuple[int, int, bytes]] = []
    channel = track.midi_channel & 0x0F
    name = track.name.encode("utf-8")[:127]
    prefix = b"\x00\xff\x03" + _variable_length(len(name)) + name
    for event in track.events:
        start = max(0, round(event.start_beat * TICKS_PER_BEAT))
        end = max(start + 1, round((event.start_beat + event.duration_beats) * TICKS_PER_BEAT))
        velocity = max(1, min(127, round(event.velocity * 127)))
        absolute_events.append((start, 1, bytes([0x90 | channel, event.pitch, velocity])))
        absolute_events.append((end, 0, bytes([0x80 | channel, event.pitch, 0])))
    absolute_events.sort(key=lambda item: (item[0], item[1]))
    payload = bytearray(prefix)
    previous_tick = 0
    for tick, _, message in absolute_events:
        payload += _variable_length(tick - previous_tick)
        payload += message
        previous_tick = tick
    payload += b"\x00\xff\x2f\x00"
    return _chunk(b"MTrk", bytes(payload))


def write_midi(project: MusicProject, output_path: Path) -> None:
    project.validate()
    tracks = [_meta_track(project)] + [_midi_track(track) for track in project.tracks]
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(tracks), TICKS_PER_BEAT))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + b"".join(tracks))
