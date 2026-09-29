"""Score-to-MIDI realization of authored articulations."""

from __future__ import annotations


LENGTH_SCALE = {
    "normal": 1.0,
    "legato": 1.08,
    "tenuto": 1.0,
    "staccato": 0.55,
    "accent": 0.92,
    "marcato": 0.72,
}

VELOCITY_SCALE = {
    "normal": 1.0,
    "legato": 0.98,
    "tenuto": 1.0,
    "staccato": 0.96,
    "accent": 1.12,
    "marcato": 1.18,
}


def realized_duration(duration_beats: float, articulation: str) -> float:
    return duration_beats * LENGTH_SCALE[articulation]


def realized_velocity(velocity: float, articulation: str) -> float:
    return max(0.0, min(1.0, velocity * VELOCITY_SCALE[articulation]))
