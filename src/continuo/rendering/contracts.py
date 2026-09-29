from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..model import MusicProject
from .soundfont.mapping import SoundFontMapping
from .soundfont.profile import SoundFontProfile


@dataclass(frozen=True, slots=True)
class RenderReport:
    backend: str
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float
    peak: float
    rms: float
    performance_realization: dict[str, str]


class SoundFontRenderBackend(Protocol):
    name: str

    def soundfont_profile(self) -> SoundFontProfile:
        ...

    def render(
        self,
        project: MusicProject,
        output_path: Path,
        soundfont_mapping: SoundFontMapping,
    ) -> RenderReport:
        ...
