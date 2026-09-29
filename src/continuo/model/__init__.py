"""Authoritative music-project model and its musical vocabulary."""

from .identity import score_sha256
from .instruments import (
    INSTRUMENT_CATALOG,
    SUPPORTED_INSTRUMENT_IDS,
    InstrumentDefinition,
    instrument_catalog_for_prompt,
    instrument_definition,
)
from .project import (
    SUPPORTED_ARTICULATIONS,
    SUPPORTED_AUTOMATION_PARAMETERS,
    SUPPORTED_NOTE_CONNECTIONS,
    AutomationPoint,
    DomainValidationError,
    InstrumentSpec,
    KeyRegion,
    MasterSpec,
    MusicProject,
    NoteEvent,
    Phrase,
    Section,
    Track,
)

__all__ = [
    "SUPPORTED_ARTICULATIONS",
    "SUPPORTED_AUTOMATION_PARAMETERS",
    "SUPPORTED_INSTRUMENT_IDS",
    "SUPPORTED_NOTE_CONNECTIONS",
    "AutomationPoint",
    "DomainValidationError",
    "INSTRUMENT_CATALOG",
    "InstrumentDefinition",
    "InstrumentSpec",
    "KeyRegion",
    "MasterSpec",
    "MusicProject",
    "NoteEvent",
    "Phrase",
    "Section",
    "Track",
    "instrument_catalog_for_prompt",
    "instrument_definition",
    "score_sha256",
]
