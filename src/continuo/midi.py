from __future__ import annotations

import struct
from pathlib import Path

from .domain import DomainValidationError, MusicProject, Track
from .instruments import INSTRUMENT_CATALOG, instrument_definition


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


def _midi_track(track: Track, total_ticks: int, channel: int) -> bytes:
    absolute_events: list[tuple[int, int, bytes]] = []
    instrument = instrument_definition(track.instrument.id)
    name = track.name.encode("utf-8")[:127]
    prefix = b"\x00\xff\x03" + _variable_length(len(name)) + name
    preset_messages = b""
    binding = track.instrument.soundfont_preset
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
    for event in track.events:
        pitch = (
            instrument.percussion_note
            if instrument.percussion_note is not None
            else event.pitch
        )
        start = max(0, round(event.start_beat * TICKS_PER_BEAT))
        end = max(start + 1, round((event.start_beat + event.duration_beats) * TICKS_PER_BEAT))
        velocity = max(1, min(127, round(event.velocity * 127)))
        absolute_events.append((start, 1, bytes([0x90 | channel, pitch, velocity])))
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


def write_midi(project: MusicProject, output_path: Path) -> None:
    project.validate()
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
        _midi_track(track, total_ticks, channel)
        for track, channel in zip(project.tracks, track_channels, strict=True)
    ]
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(tracks), TICKS_PER_BEAT))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + b"".join(tracks))
