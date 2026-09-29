from __future__ import annotations

from dataclasses import dataclass

from ..model import instrument_definition


@dataclass(frozen=True, slots=True)
class SynthProfile:
    """Internal approximation used only by non-SoundFont development renderers."""

    voice: str = "oscillator"
    oscillator: str = "sine"
    partials: tuple[float, ...] = (1.0,)
    noise_mix: float = 0.0
    attack_seconds: float = 0.01
    decay_seconds: float = 0.08
    sustain_level: float = 0.7
    release_seconds: float = 0.15
    gain: float = 0.25


def synth_profile_for(instrument_id: str) -> SynthProfile:
    instrument = instrument_definition(instrument_id)
    if instrument_id == "acoustic_grand_piano":
        return SynthProfile(voice="acoustic_piano", release_seconds=0.8, gain=0.22)
    if instrument_id in {"upright_bass", "contrabass"}:
        return SynthProfile(voice="upright_bass", release_seconds=0.35, gain=0.24)
    if instrument.family in {"mallet", "pitched_percussion"}:
        return SynthProfile(voice="vibraphone", release_seconds=1.2, gain=0.20)
    if instrument.family in {"woodwind", "brass"}:
        return SynthProfile(
            voice="tenor_sax",
            oscillator="saw",
            partials=(1.0, 0.35, 0.12),
            noise_mix=0.035,
            attack_seconds=0.04,
            release_seconds=0.18,
            gain=0.16,
        )
    if instrument_id == "kick_drum":
        return SynthProfile(voice="soft_kick", release_seconds=0.2, gain=0.28)
    if instrument_id in {"snare_drum", "side_stick", "tambourine"}:
        return SynthProfile(
            voice="brush_snare",
            noise_mix=0.8,
            release_seconds=0.12,
            gain=0.20,
        )
    if instrument.is_percussion:
        return SynthProfile(
            voice="ride_cymbal",
            noise_mix=0.7,
            release_seconds=0.45,
            gain=0.14,
        )
    if instrument.family == "strings":
        return SynthProfile(
            oscillator="saw",
            partials=(1.0, 0.35, 0.15),
            attack_seconds=0.06,
            release_seconds=0.35,
            gain=0.12,
        )
    if instrument.family == "synth":
        oscillator = "square" if instrument_id == "synth_lead_square" else "saw"
        return SynthProfile(
            oscillator=oscillator,
            partials=(1.0, 0.35, 0.15),
            attack_seconds=0.03,
            release_seconds=0.3,
            gain=0.14,
        )
    return SynthProfile()
