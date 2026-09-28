from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from .domain import MusicProject, SynthSpec
from .rendering import RenderReport, inspect_wav


class SuperColliderUnavailableError(RuntimeError):
    pass


def _number(value: float | int) -> str:
    if not math.isfinite(float(value)):
        raise ValueError("SuperCollider parameters must be finite")
    return format(float(value), ".10g")


def _sc_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace('"', '\\"')


def _oscillator_expression(oscillator: str, frequency: str) -> str:
    if oscillator == "sine":
        return f"SinOsc.ar({frequency})"
    if oscillator == "triangle":
        return f"LFTri.ar({frequency})"
    if oscillator == "square":
        return f"Pulse.ar({frequency}, 0.5)"
    if oscillator == "saw":
        return f"Saw.ar({frequency})"
    raise ValueError(f"unsupported oscillator: {oscillator}")


def _signal_expression(spec: SynthSpec) -> str:
    pieces = []
    total = sum(spec.partials) or 1.0
    for index, weight in enumerate(spec.partials, start=1):
        oscillator = _oscillator_expression(spec.oscillator, f"freq * {index}")
        pieces.append(f"({oscillator} * {_number(weight / total)})")
    harmonic = " + ".join(pieces)
    if spec.noise_mix <= 0:
        return harmonic
    return (
        f"(({harmonic}) * {_number(1.0 - spec.noise_mix)}"
        f" + WhiteNoise.ar({_number(spec.noise_mix)}))"
    )


class SuperColliderNrtRenderer:
    """Compile validated Music IR into a controlled SuperCollider NRT score."""

    name = "supercollider-nrt"

    def __init__(self, executable: Path | None = None, sample_rate: int = 44_100):
        self.executable = executable or self.discover_executable()
        self.sample_rate = sample_rate

    @staticmethod
    def discover_executable() -> Path:
        discovered = shutil.which("sclang")
        if discovered:
            return Path(discovered)
        candidates = sorted(Path("C:/Program Files").glob("SuperCollider-*/sclang.exe"), reverse=True)
        if candidates:
            return candidates[0]
        raise SuperColliderUnavailableError("sclang was not found")

    def render(self, project: MusicProject, output_path: Path) -> RenderReport:
        project.validate()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        script_path = output_path.parent / "render.generated.scd"
        score_path = output_path.parent / "score.osc"
        script_path.write_text(
            self.compile_script(project, output_path, score_path),
            encoding="utf-8",
        )
        output_path.unlink(missing_ok=True)
        score_path.unlink(missing_ok=True)
        log_path = output_path.parent / "supercollider.log"
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [
                    str(self.executable),
                    "-m",
                    "64m",
                    "-g",
                    "4m",
                    "-D",
                    str(script_path.resolve()),
                ],
                cwd=output_path.parent,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300,
                check=False,
            )
        if completed.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"SuperCollider NRT render failed with exit code {completed.returncode}; "
                "see supercollider.log"
            )
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

    def compile_script(
        self,
        project: MusicProject,
        output_path: Path,
        score_path: Path,
    ) -> str:
        synthdefs: list[str] = []
        for index, track in enumerate(project.tracks):
            spec = track.synth
            signal = _signal_expression(spec)
            synthdefs.append(
                "\n".join(
                    [
                        f"trackDefs[{index}] = SynthDef(\\ct{index}, {{ |out=16, freq=440, amp=0.1, gate=1, pan=0, seed=0|",
                        "    var sig, env;",
                        "    RandSeed.ir(1, seed);",
                        f"    sig = {signal};",
                        "    env = EnvGen.ar(",
                        "        Env.adsr("
                        f"{_number(spec.attack_seconds)}, {_number(spec.decay_seconds)}, "
                        f"{_number(spec.sustain_level)}, {_number(spec.release_seconds)}),",
                        "        gate, doneAction: 2",
                        "    );",
                        "    Out.ar(out, Pan2.ar(sig * env * amp, pan));",
                        "});",
                    ]
                )
            )

        messages: list[tuple[float, str]] = []
        messages.append((0.0, "[\\g_new, 1, 0, 0]"))
        messages.append(
            (
                0.0,
                "[\\s_new, \\continuoMaster, 1000, 1, 0, "
                f"\\inBus, 16, \\roomMix, {_number(project.master.room_mix)}, "
                f"\\duration, {_number(project.duration_seconds)}, "
                f"\\fadeTime, {_number(project.master.fade_out_seconds)}]",
            )
        )
        node_id = 10_000
        seconds_per_beat = 60.0 / project.tempo_bpm
        for track_index, track in enumerate(project.tracks):
            for event in sorted(track.events, key=lambda item: item.start_beat):
                start_seconds = event.start_beat * seconds_per_beat
                end_seconds = min(
                    project.duration_seconds,
                    (event.start_beat + event.duration_beats) * seconds_per_beat,
                )
                frequency = 440.0 * (2.0 ** ((event.pitch - 69) / 12.0))
                amplitude = event.velocity * track.gain * track.synth.gain
                event_seed = (project.seed + node_id) % 2_147_483_647
                messages.append(
                    (
                        start_seconds,
                        f"[\\s_new, \\ct{track_index}, {node_id}, 0, 1, "
                        f"\\out, 16, \\freq, {_number(frequency)}, "
                        f"\\amp, {_number(amplitude)}, \\pan, {_number(track.pan)}, "
                        f"\\seed, {event_seed}]",
                    )
                )
                messages.append((end_seconds, f"[\\n_set, {node_id}, \\gate, 0]"))
                node_id += 1
        messages.append((project.duration_seconds, "[\\c_set, 0, 0]"))
        messages.sort(key=lambda item: item[0])
        score_lines = [f"    [{_number(time)}, {message}]," for time, message in messages]

        duration = project.duration_seconds
        fade = min(project.master.fade_out_seconds, duration)
        hold = max(0.0, duration - fade)
        return "\n".join(
            [
                "(",
                "var trackDefs = Array.newClear(%d);" % len(project.tracks),
                "var masterDef, score, options;",
                *synthdefs,
                "masterDef = SynthDef(\\continuoMaster, { |inBus=16, roomMix=0.1, duration=1, fadeTime=0.1|",
                "    var dry = In.ar(inBus, 2);",
                "    var wet = [DelayC.ar(dry[1], 0.2, 0.085), DelayC.ar(dry[0], 0.2, 0.085)];",
                "    var fade = EnvGen.kr(Env([1, 1, 0], ["
                f"{_number(hold)}, {_number(fade)}], \\lin));",
                "    Out.ar(0, Limiter.ar((dry + (wet * roomMix)) * fade, 0.95));",
                "});",
                "score = [",
                "    [0.0, [\\d_recv, masterDef.asBytes]],",
                *[f"    [0.0, [\\d_recv, trackDefs[{index}].asBytes]]," for index in range(len(project.tracks))],
                *score_lines,
                "];",
                "options = ServerOptions.new;",
                "options.numOutputBusChannels = 2;",
                "options.numInputBusChannels = 0;",
                "options.numAudioBusChannels = 128;",
                "Score.recordNRT(",
                "    score,",
                f"    \"{_sc_path(score_path)}\",",
                f"    \"{_sc_path(output_path)}\",",
                f"    sampleRate: {self.sample_rate},",
                "    headerFormat: \"WAV\",",
                "    sampleFormat: \"int16\",",
                "    options: options,",
                f"    duration: {_number(project.duration_seconds)},",
                "    action: { \"CONTINUO_RENDER_COMPLETE\".postln; 0.exit; }",
                ");",
                ")",
            ]
        )
