from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SoundFontProfileError(RuntimeError):
    """Raised when the selected SoundFont cannot provide a usable preset profile."""


@dataclass(frozen=True, slots=True)
class SoundFontPreset:
    id: str
    bank: int
    program: int
    name: str
    is_percussion: bool

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bank": self.bank,
            "program": self.program,
            "name": self.name,
            "kind": "percussion_kit" if self.is_percussion else "melodic",
        }


@dataclass(frozen=True, slots=True)
class SoundFontProfile:
    id: str
    sha256: str
    presets: tuple[SoundFontPreset, ...]
    bank_select: str = "gs"

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.sha256.strip():
            raise ValueError("SoundFont profile id and sha256 are required")
        if not self.presets:
            raise ValueError("SoundFont profile must contain at least one preset")
        ids = [preset.id for preset in self.presets]
        if len(ids) != len(set(ids)):
            raise ValueError("SoundFont preset ids must be unique")
        if self.bank_select != "gs":
            raise ValueError("Continuo currently supports only GS-style bank selection")

    def resolve(self, preset_id: str) -> SoundFontPreset:
        for preset in self.presets:
            if preset.id == preset_id:
                return preset
        raise KeyError(preset_id)

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sha256": self.sha256,
            "bank_select": self.bank_select,
            "presets": [preset.manifest() for preset in self.presets],
        }


_PRESET_LINE = re.compile(r"^\s*>?\s*(\d{3})-(\d{3})\s+(.+?)\s*$")


def parse_fluidsynth_preset_listing(output: str) -> tuple[SoundFontPreset, ...]:
    """Parse the stable `inst` shell listing emitted by FluidSynth."""

    presets: list[SoundFontPreset] = []
    seen: set[str] = set()
    for line in output.splitlines():
        match = _PRESET_LINE.match(line)
        if match is None:
            continue
        bank = int(match.group(1))
        program = int(match.group(2))
        name = match.group(3).strip()
        preset_id = f"{bank:03d}-{program:03d}"
        if preset_id in seen:
            raise SoundFontProfileError(f"duplicate SoundFont preset: {preset_id}")
        if bank > 128 or program > 127:
            raise SoundFontProfileError(
                f"unsupported SoundFont preset coordinates: {preset_id}"
            )
        seen.add(preset_id)
        presets.append(
            SoundFontPreset(
                id=preset_id,
                bank=bank,
                program=program,
                name=name,
                is_percussion=bank >= 120,
            )
        )
    if not presets:
        raise SoundFontProfileError("FluidSynth returned no SoundFont presets")
    return tuple(presets)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_soundfont(
    *,
    executable: Path,
    soundfont: Path,
    timeout_seconds: float = 60.0,
) -> SoundFontProfile:
    """Ask the selected FluidSynth binary for the selected SoundFont's presets."""

    with tempfile.TemporaryDirectory(prefix="continuo-soundfont-profile-") as directory:
        sink = Path(directory) / "probe.wav"
        command = [
            str(executable),
            "-n",
            "-q",
            "-a",
            "file",
            "-o",
            f"audio.file.name={sink}",
            str(soundfont),
        ]
        try:
            completed = subprocess.run(
                command,
                input="inst 1\nquit\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SoundFontProfileError(
                f"could not inspect SoundFont presets: {exc}"
            ) from exc
    if completed.returncode != 0:
        raise SoundFontProfileError(
            "FluidSynth preset inspection failed with exit code "
            f"{completed.returncode}: {completed.stdout.strip()}"
        )
    return SoundFontProfile(
        id=soundfont.name,
        sha256=_sha256(soundfont),
        presets=parse_fluidsynth_preset_listing(completed.stdout),
    )
