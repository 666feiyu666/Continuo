from __future__ import annotations

import math
import sys
import wave
from array import array
from pathlib import Path


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
