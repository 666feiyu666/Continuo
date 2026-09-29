from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path

from ...model import MusicProject
from ..midi import write_midi
from ..reference import RenderReport, inspect_wav
from .mapping import (
    SoundFontMapping,
    validate_soundfont_mapping,
)
from .profile import SoundFontProfile, inspect_soundfont


class SoundFontUnavailableError(RuntimeError):
    pass


class FluidSynthRenderer:
    """Render a validated Score IR with a mapped local SoundFont."""

    name = "fluidsynth-soundfont"

    def __init__(
        self,
        *,
        executable: Path | None = None,
        soundfont: Path | None = None,
        sample_rate: int = 44_100,
    ) -> None:
        self.executable = executable or self.discover_executable()
        self.soundfont = soundfont or self.discover_soundfont()
        self.sample_rate = sample_rate
        self._profile: SoundFontProfile | None = None
        if not self.executable.is_file():
            raise SoundFontUnavailableError(
                f"FluidSynth executable does not exist: {self.executable}"
            )
        if not self.soundfont.is_file():
            raise SoundFontUnavailableError(
                f"SoundFont does not exist: {self.soundfont}"
            )

    def soundfont_profile(self) -> SoundFontProfile:
        if self._profile is None:
            self._profile = inspect_soundfont(
                executable=self.executable,
                soundfont=self.soundfont,
            )
        return self._profile

    @staticmethod
    def discover_executable() -> Path:
        configured = os.environ.get("CONTINUO_FLUIDSYNTH")
        if configured:
            return Path(configured)
        discovered = shutil.which("fluidsynth")
        if discovered:
            return Path(discovered)
        candidates = sorted(
            (Path.cwd() / ".continuo" / "tools").glob(
                "fluidsynth-*/**/bin/fluidsynth.exe"
            ),
            reverse=True,
        )
        if candidates:
            return candidates[0]
        raise SoundFontUnavailableError(
            "FluidSynth was not found; pass --fluidsynth-executable or set "
            "CONTINUO_FLUIDSYNTH"
        )

    @staticmethod
    def discover_soundfont() -> Path:
        configured = os.environ.get("CONTINUO_SOUNDFONT")
        if configured:
            return Path(configured)
        candidates = sorted(
            (Path.cwd() / ".continuo" / "soundfonts").glob("*.sf[23]"),
            reverse=True,
        )
        if candidates:
            return candidates[0]
        raise SoundFontUnavailableError(
            "No SoundFont was found; pass --soundfont or set CONTINUO_SOUNDFONT"
        )

    def render(
        self,
        project: MusicProject,
        output_path: Path,
        soundfont_mapping: SoundFontMapping | None = None,
    ) -> RenderReport:
        project.validate()
        if soundfont_mapping is None:
            raise ValueError("FluidSynth rendering requires a SoundFont Mapping IR")
        validate_soundfont_mapping(
            project,
            soundfont_mapping,
            self.soundfont_profile(),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        midi_path = output_path.parent / "render.soundfont.mid"
        log_path = output_path.parent / "fluidsynth.log"
        write_midi(project, midi_path, soundfont_mapping)
        output_path.unlink(missing_ok=True)
        command = [
            str(self.executable),
            "-ni",
            "-q",
            "-r",
            str(self.sample_rate),
            "-g",
            str(soundfont_mapping.master_gain),
            "-R",
            "1" if soundfont_mapping.reverb_enabled else "0",
            "-C",
            "0",
            "-T",
            "wav",
            "-O",
            "s16",
            "-o",
            "synth.midi-bank-select=gs",
            "-F",
            str(output_path.resolve()),
            str(self.soundfont.resolve()),
            str(midi_path.resolve()),
        ]
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=output_path.parent,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300,
                check=False,
            )
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"FluidSynth render failed with exit code {completed.returncode}; "
                "see fluidsynth.log"
            )
        self._fit_duration(output_path, project.duration_seconds)
        inspection = inspect_wav(output_path)
        return RenderReport(
            backend=self.name,
            sample_rate=int(inspection["sample_rate"]),
            channels=int(inspection["channels"]),
            frames=int(inspection["frames"]),
            duration_seconds=float(inspection["duration_seconds"]),
            peak=float(inspection["peak"]),
            rms=float(inspection["rms"]),
        )

    def _fit_duration(self, path: Path, duration_seconds: float) -> None:
        target_frames = round(duration_seconds * self.sample_rate)
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            compression = source.getcomptype()
            frames = source.readframes(source.getnframes())
        if channels != 2 or sample_width != 2 or sample_rate != self.sample_rate:
            raise RuntimeError(
                "FluidSynth must produce 16-bit stereo PCM at the configured sample rate"
            )
        if compression != "NONE":
            raise RuntimeError("FluidSynth output must be uncompressed PCM")
        frame_width = channels * sample_width
        target_bytes = target_frames * frame_width
        fitted = frames[:target_bytes]
        if len(fitted) < target_bytes:
            fitted += bytes(target_bytes - len(fitted))
        with wave.open(str(path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(sample_width)
            output.setframerate(sample_rate)
            output.writeframes(fitted)
