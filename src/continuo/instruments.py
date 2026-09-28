from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    """Semantic instrument plus a development-renderer compatibility profile."""

    id: str
    family: str
    program: int | None
    range_low: int
    range_high: int
    percussion_note: int | None = None

    @property
    def is_percussion(self) -> bool:
        return self.percussion_note is not None


def _pitched(
    instrument_id: str,
    family: str,
    program: int,
    range_low: int,
    range_high: int,
) -> InstrumentDefinition:
    return InstrumentDefinition(
        id=instrument_id,
        family=family,
        program=program,
        range_low=range_low,
        range_high=range_high,
    )


def _percussion(instrument_id: str, note: int) -> InstrumentDefinition:
    return InstrumentDefinition(
        id=instrument_id,
        family="percussion",
        program=None,
        range_low=note,
        range_high=note,
        percussion_note=note,
    )


# Curated semantic vocabulary for composition. `program` is retained only for the
# Python/SuperCollider development paths and recorded-provider test substitution.
# Production SoundFont rendering uses a model-selected preset from the inspected
# SoundFont profile instead of this compatibility value.
INSTRUMENT_CATALOG = (
    _pitched("acoustic_grand_piano", "keyboard", 0, 21, 108),
    _pitched("electric_piano", "keyboard", 4, 28, 103),
    _pitched("harpsichord", "keyboard", 6, 29, 89),
    _pitched("celesta", "keyboard", 8, 60, 108),
    _pitched("church_organ", "keyboard", 19, 24, 96),
    _pitched("accordion", "keyboard", 21, 41, 96),
    _pitched("harmonica", "keyboard", 22, 48, 84),
    _pitched("glockenspiel", "mallet", 9, 79, 108),
    _pitched("vibraphone", "mallet", 11, 53, 89),
    _pitched("marimba", "mallet", 12, 45, 96),
    _pitched("xylophone", "mallet", 13, 65, 108),
    _pitched("tubular_bells", "mallet", 14, 60, 77),
    _pitched("violin", "strings", 40, 55, 103),
    _pitched("viola", "strings", 41, 48, 91),
    _pitched("cello", "strings", 42, 36, 76),
    _pitched("contrabass", "strings", 43, 28, 67),
    _pitched("tremolo_strings", "strings", 44, 36, 96),
    _pitched("pizzicato_strings", "strings", 45, 36, 96),
    _pitched("orchestral_harp", "strings", 46, 24, 103),
    _pitched("string_ensemble", "strings", 48, 36, 96),
    _pitched("piccolo", "woodwind", 72, 74, 108),
    _pitched("flute", "woodwind", 73, 60, 96),
    _pitched("recorder", "woodwind", 74, 60, 96),
    _pitched("pan_flute", "woodwind", 75, 60, 96),
    _pitched("oboe", "woodwind", 68, 58, 91),
    _pitched("english_horn", "woodwind", 69, 52, 81),
    _pitched("bassoon", "woodwind", 70, 34, 75),
    _pitched("clarinet", "woodwind", 71, 50, 94),
    _pitched("bass_clarinet", "woodwind", 71, 34, 77),
    _pitched("soprano_sax", "woodwind", 64, 56, 88),
    _pitched("alto_sax", "woodwind", 65, 49, 80),
    _pitched("tenor_sax", "woodwind", 66, 44, 75),
    _pitched("baritone_sax", "woodwind", 67, 36, 68),
    _pitched("trumpet", "brass", 56, 54, 82),
    _pitched("trombone", "brass", 57, 40, 72),
    _pitched("tuba", "brass", 58, 28, 58),
    _pitched("muted_trumpet", "brass", 59, 54, 82),
    _pitched("french_horn", "brass", 60, 34, 77),
    _pitched("brass_ensemble", "brass", 61, 36, 84),
    _pitched("acoustic_guitar_nylon", "guitar", 24, 40, 88),
    _pitched("acoustic_guitar_steel", "guitar", 25, 40, 88),
    _pitched("electric_guitar_clean", "guitar", 27, 40, 88),
    _pitched("electric_guitar_overdrive", "guitar", 29, 40, 88),
    _pitched("upright_bass", "bass", 32, 28, 67),
    _pitched("electric_bass_finger", "bass", 33, 28, 67),
    _pitched("electric_bass_pick", "bass", 34, 28, 67),
    _pitched("synth_lead_square", "synth", 80, 36, 96),
    _pitched("synth_lead_saw", "synth", 81, 36, 96),
    _pitched("synth_pad_warm", "synth", 89, 36, 96),
    _pitched("synth_pad_sweep", "synth", 95, 36, 96),
    _pitched("timpani", "pitched_percussion", 47, 36, 57),
    _percussion("kick_drum", 36),
    _percussion("side_stick", 37),
    _percussion("snare_drum", 38),
    _percussion("closed_hi_hat", 42),
    _percussion("low_tom", 45),
    _percussion("open_hi_hat", 46),
    _percussion("high_tom", 50),
    _percussion("crash_cymbal", 49),
    _percussion("ride_cymbal", 51),
    _percussion("tambourine", 54),
    _percussion("cowbell", 56),
)

INSTRUMENT_BY_ID = {item.id: item for item in INSTRUMENT_CATALOG}
SUPPORTED_INSTRUMENT_IDS = tuple(item.id for item in INSTRUMENT_CATALOG)


def instrument_definition(instrument_id: str) -> InstrumentDefinition:
    try:
        return INSTRUMENT_BY_ID[instrument_id]
    except KeyError as exc:
        raise ValueError(f"unsupported instrument: {instrument_id}") from exc


def instrument_catalog_for_prompt() -> str:
    """Compact, runtime-derived catalog for trusted SoundFont skill context."""

    families: dict[str, list[str]] = {}
    for item in INSTRUMENT_CATALOG:
        label = item.id
        if item.is_percussion:
            label += f" (fixed drum note {item.percussion_note})"
        else:
            label += f" (recommended MIDI range {item.range_low}-{item.range_high})"
        families.setdefault(item.family, []).append(label)
    lines = ["Runtime instrument catalog (semantic ids):"]
    for family, instruments in families.items():
        lines.append(f"- {family}: {', '.join(instruments)}")
    return "\n".join(lines)
