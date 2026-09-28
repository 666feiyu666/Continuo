from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from .domain import MusicProject
from .rendering import RenderReport, inspect_wav
from .timbre import SynthProfile, synth_profile_for


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


def _oscillator_signal_expression(spec: SynthProfile) -> str:
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


def _voice_signal_expression(spec: SynthProfile) -> str:
    """Return a deterministic SC signal graph for one validated Sound Spec voice."""
    if spec.voice == "oscillator":
        return _oscillator_signal_expression(spec)
    if spec.voice == "acoustic_piano":
        return (
            "LPF.ar(Mix.ar(Ringz.ar("
            "HPF.ar(PinkNoise.ar(0.55), 650) * "
            "EnvGen.ar(Env.perc(0.001, 0.035, curve: -7)), "
            "freq * [1, 2.006, 3.992, 6.015], "
            "[3.8, 2.7, 1.65, 0.9], "
            "[0.72, 0.22, 0.075, 0.025]"
            ")) + (SinOsc.ar(freq * [1, 1.003], 0, [0.13, 0.09]).sum * "
            "EnvGen.ar(Env.perc(0.002, 1.3, curve: -5))), "
            "(4200 + (vel * 5200)).clip(4200, 9000))"
        )
    if spec.voice == "upright_bass":
        return (
            "LPF.ar("
            "CombL.ar("
            "LPF.ar(PinkNoise.ar(0.8), 2400) * "
            "EnvGen.ar(Env.perc(0.001, 0.045, curve: -8)), "
            "0.1, freq.reciprocal.clip(0.001, 0.1), 3.8, 0.9"
            ") + (SinOsc.ar(freq, 0, 0.09) * EnvGen.ar(Env.perc(0.002, 0.7))), "
            "(900 + (vel * 1700)).clip(900, 2500))"
        )
    if spec.voice == "vibraphone":
        return (
            "(Mix.ar(SinOsc.ar("
            "freq * [1, 3.984, 10.02, 14.37], 0, [0.72, 0.16, 0.055, 0.025]"
            ")) * EnvGen.ar(Env.perc(0.001, 4.2, curve: -4)) * "
            "SinOsc.kr(5.3, 0, 0.08, 0.92))"
        )
    if spec.voice == "tenor_sax":
        return (
            "RLPF.ar("
            "(Saw.ar(freq * SinOsc.kr(5.2, 0, 0.0025, 1), 0.34) + "
            "Pulse.ar(freq * 2.002, 0.46, 0.12) + PinkNoise.ar(0.028)), "
            "(freq * (4.2 + (vel * 1.8))).clip(850, 5200), 0.32"
            ").tanh"
        )
    if spec.voice == "soft_kick":
        return (
            "(SinOsc.ar(XLine.kr(92, 47, 0.085), 0, 0.9) * "
            "EnvGen.ar(Env.perc(0.001, 0.28, curve: -7))) + "
            "(LPF.ar(WhiteNoise.ar(0.16), 1800) * "
            "EnvGen.ar(Env.perc(0.001, 0.018, curve: -8)))"
        )
    if spec.voice == "brush_snare":
        return (
            "(BPF.ar(PinkNoise.ar(0.9), 2600, 0.85) * "
            "EnvGen.ar(Env.perc(0.004, 0.19, curve: -3))) + "
            "(HPF.ar(WhiteNoise.ar(0.22), 5200) * "
            "EnvGen.ar(Env.perc(0.001, 0.055, curve: -6)))"
        )
    if spec.voice == "ride_cymbal":
        return (
            "(HPF.ar(PinkNoise.ar(0.22), 6200) * "
            "EnvGen.ar(Env.perc(0.001, 0.75, curve: -5))) + "
            "(Mix.ar(Ringz.ar("
            "WhiteNoise.ar(0.12) * EnvGen.ar(Env.perc(0.001, 0.012)), "
            "[2471, 3319, 4591, 6787, 9137], "
            "[0.7, 0.55, 0.42, 0.3, 0.22], "
            "[0.12, 0.09, 0.065, 0.04, 0.025]"
            ")) )"
        )
    raise ValueError(f"unsupported synth voice: {spec.voice}")


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
            spec = synth_profile_for(track.instrument.id)
            signal = _voice_signal_expression(spec)
            synthdefs.append(
                "\n".join(
                    [
                        f"trackDefs[{index}] = SynthDef(\\ct{index}, {{ |out=16, freq=440, amp=0.1, vel=0.7, gate=1, pan=0, seed=0|",
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
                f"\\roomDelay, {_number(project.master.room_delay_seconds)}, "
                f"\\targetPeak, {_number(project.master.target_peak)}, "
                f"\\duration, {_number(project.duration_seconds)}, "
                f"\\fadeTime, {_number(project.master.fade_out_seconds)}]",
            )
        )
        node_id = 10_000
        seconds_per_beat = 60.0 / project.tempo_bpm
        for track_index, track in enumerate(project.tracks):
            spec = synth_profile_for(track.instrument.id)
            for event in sorted(track.events, key=lambda item: item.start_beat):
                start_seconds = event.start_beat * seconds_per_beat
                end_seconds = min(
                    project.duration_seconds,
                    (event.start_beat + event.duration_beats) * seconds_per_beat,
                )
                frequency = 440.0 * (2.0 ** ((event.pitch - 69) / 12.0))
                amplitude = event.velocity * track.gain * spec.gain
                event_seed = (project.seed + node_id) % 2_147_483_647
                messages.append(
                    (
                        start_seconds,
                        f"[\\s_new, \\ct{track_index}, {node_id}, 0, 1, "
                        f"\\out, 16, \\freq, {_number(frequency)}, "
                        f"\\amp, {_number(amplitude)}, \\vel, {_number(event.velocity)}, "
                        f"\\pan, {_number(track.pan)}, "
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
                "masterDef = SynthDef(\\continuoMaster, { |inBus=16, roomMix=0.1, roomDelay=0.03, targetPeak=0.9, duration=1, fadeTime=0.1|",
                "    var dry = LeakDC.ar(In.ar(inBus, 2));",
                "    var early = [DelayC.ar(dry[1], 0.2, roomDelay), DelayC.ar(dry[0], 0.2, roomDelay)];",
                "    var room = FreeVerb2.ar(dry[0] + (early[0] * 0.18), dry[1] + (early[1] * 0.18), roomMix, 0.52, 0.72);",
                "    var fade = EnvGen.kr(Env([1, 1, 0], ["
                f"{_number(hold)}, {_number(fade)}], \\lin));",
                "    Out.ar(0, Limiter.ar(room * 2.4 * fade, targetPeak, 0.01));",
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
