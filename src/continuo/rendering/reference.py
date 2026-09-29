from __future__ import annotations

import math
import random
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .articulation import realized_duration, realized_velocity
from ..model import MasterSpec, MusicProject, NoteEvent, Track
from .timbre import SynthProfile, synth_profile_for


@dataclass(frozen=True, slots=True)
class RenderReport:
    backend: str
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    peak: float
    rms: float


class RenderBackend(Protocol):
    name: str

    def render(
        self,
        project: MusicProject,
        output_path: Path,
        soundfont_mapping: Any | None = None,
    ) -> RenderReport:
        ...


def _midi_frequency(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _base_wave(oscillator: str, phase: float) -> float:
    cycle = (phase / math.tau) % 1.0
    if oscillator == "sine":
        return math.sin(phase)
    if oscillator == "triangle":
        return 4.0 * abs(cycle - 0.5) - 1.0
    if oscillator == "square":
        return 1.0 if cycle < 0.5 else -1.0
    if oscillator == "saw":
        return 2.0 * cycle - 1.0
    raise ValueError(f"unsupported oscillator: {oscillator}")


def _envelope(t: float, gate_seconds: float, spec: SynthProfile) -> float:
    if t < spec.attack_seconds and spec.attack_seconds > 0:
        return t / spec.attack_seconds
    decay_end = spec.attack_seconds + spec.decay_seconds
    if t < decay_end and spec.decay_seconds > 0:
        fraction = (t - spec.attack_seconds) / spec.decay_seconds
        return 1.0 + (spec.sustain_level - 1.0) * max(0.0, fraction)
    if t < gate_seconds:
        return spec.sustain_level
    if spec.release_seconds <= 0:
        return 0.0
    release_fraction = (t - gate_seconds) / spec.release_seconds
    return spec.sustain_level * max(0.0, 1.0 - release_fraction)


class ReferenceWavRenderer:
    """Portable reference renderer used until a production DSP backend is available."""

    name = "python-reference"

    def __init__(self, sample_rate: int = 44_100):
        self.sample_rate = sample_rate

    def render(
        self,
        project: MusicProject,
        output_path: Path,
        soundfont_mapping: Any | None = None,
    ) -> RenderReport:
        del soundfont_mapping
        project.validate()
        master = MasterSpec()
        frame_count = round(project.duration_seconds * self.sample_rate)
        left = array("f", [0.0]) * frame_count
        right = array("f", [0.0]) * frame_count
        seconds_per_beat = 60.0 / project.tempo_bpm
        for track_index, track in enumerate(project.tracks):
            for event_index, event in enumerate(sorted(track.events, key=lambda item: item.start_beat)):
                self._render_event(
                    left,
                    right,
                    track,
                    event,
                    seconds_per_beat,
                    project.seed + track_index * 100_003 + event_index,
                )
        self._apply_room(left, right, master)
        peak, rms = self._finalize(left, right, master)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pcm = array("h")
        append = pcm.append
        for index in range(frame_count):
            append(int(max(-1.0, min(1.0, left[index])) * 32767.0))
            append(int(max(-1.0, min(1.0, right[index])) * 32767.0))
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes(pcm.tobytes())
        return RenderReport(
            backend=self.name,
            sample_rate=self.sample_rate,
            channels=2,
            frames=frame_count,
            duration_seconds=frame_count / self.sample_rate,
            peak=peak,
            rms=rms,
        )

    def _render_event(
        self,
        left: array,
        right: array,
        track: Track,
        event: NoteEvent,
        seconds_per_beat: float,
        noise_seed: int,
    ) -> None:
        spec = synth_profile_for(track.instrument.id)
        start_frame = round(event.start_beat * seconds_per_beat * self.sample_rate)
        gate_seconds = realized_duration(
            event.duration_beats,
            event.articulation,
        ) * seconds_per_beat
        total_seconds = gate_seconds + spec.release_seconds
        end_frame = min(len(left), start_frame + round(total_seconds * self.sample_rate))
        if end_frame <= start_frame:
            return
        frequency = _midi_frequency(event.pitch)
        partial_total = sum(spec.partials) or 1.0
        left_gain = math.sqrt((1.0 - track.pan) * 0.5)
        right_gain = math.sqrt((1.0 + track.pan) * 0.5)
        amplitude = (
            realized_velocity(event.velocity, event.articulation)
            * track.gain
            * spec.gain
        )
        rng = random.Random(noise_seed)
        for frame in range(start_frame, end_frame):
            t = (frame - start_frame) / self.sample_rate
            phase = math.tau * frequency * t
            noise = rng.uniform(-1.0, 1.0)
            if spec.voice == "tenor_sax":
                vibrato_phase = phase + 0.32 * math.sin(math.tau * 5.2 * t)
                harmonic = (
                    0.62 * math.sin(vibrato_phase)
                    + 0.23 * math.sin(vibrato_phase * 2.0 + 0.12)
                    + 0.10 * math.sin(vibrato_phase * 3.0 + 0.23)
                    + 0.05 * math.sin(vibrato_phase * 4.0 + 0.31)
                )
                breath_mix = max(0.035, spec.noise_mix)
                signal = math.tanh(harmonic * 1.55) * (1.0 - breath_mix)
                signal += noise * breath_mix
            else:
                harmonic = 0.0
                for partial_index, weight in enumerate(
                    spec.partials,
                    start=1,
                ):
                    harmonic += weight * _base_wave(
                        spec.oscillator,
                        phase * partial_index,
                    )
                harmonic /= partial_total
                signal = (
                    harmonic * (1.0 - spec.noise_mix)
                    + noise * spec.noise_mix
                )
            signal *= _envelope(t, gate_seconds, spec) * amplitude
            left[frame] += signal * left_gain
            right[frame] += signal * right_gain

    def _apply_room(self, left: array, right: array, master: MasterSpec) -> None:
        mix = master.room_mix
        delay_frames = round(master.room_delay_seconds * self.sample_rate)
        if mix <= 0 or delay_frames <= 0:
            return
        for index in range(delay_frames, len(left)):
            delayed_left = left[index - delay_frames]
            delayed_right = right[index - delay_frames]
            left[index] += delayed_right * mix
            right[index] += delayed_left * mix

    def _finalize(
        self,
        left: array,
        right: array,
        master: MasterSpec,
    ) -> tuple[float, float]:
        fade_frames = round(master.fade_out_seconds * self.sample_rate)
        if fade_frames > 0:
            start = max(0, len(left) - fade_frames)
            for index in range(start, len(left)):
                factor = (len(left) - index - 1) / max(1, fade_frames)
                left[index] *= factor
                right[index] *= factor
        peak = 0.0
        for value in left:
            peak = max(peak, abs(value))
        for value in right:
            peak = max(peak, abs(value))
        scale = master.target_peak / peak if peak > master.target_peak else 1.0
        square_sum = 0.0
        for index in range(len(left)):
            left[index] *= scale
            right[index] *= scale
            square_sum += left[index] * left[index] + right[index] * right[index]
        final_peak = peak * scale
        rms = math.sqrt(square_sum / max(1, len(left) * 2))
        return final_peak, rms


def inspect_wav(path: Path) -> dict[str, float | int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_rate = source.getframerate()
        frames = source.getnframes()
        sample_width = source.getsampwidth()
        raw = source.readframes(frames)
    if sample_width != 2:
        raise ValueError("only 16-bit PCM WAV inspection is supported")
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    peak_sample = max((abs(item) for item in samples), default=0)
    square_sum = sum(item * item for item in samples)
    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "frames": frames,
        "duration_seconds": frames / sample_rate,
        "peak": peak_sample / 32767.0,
        "rms": math.sqrt(square_sum / max(1, len(samples))) / 32767.0,
    }
