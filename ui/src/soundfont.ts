import { MIDIControllers, SoundBankLoader } from "spessasynth_core";
import { WorkletSynthesizer } from "spessasynth_lib";
import processorUrl from "spessasynth_lib/dist/spessasynth_processor.min.js?url";
import type { StudioProject, StudioTrack, TrackKind } from "./domain";

export interface SoundFontPreset {
  name: string;
  bankMSB: number;
  bankLSB: number;
  program: number;
}

export interface SoundFontSummary {
  fileName: string;
  byteLength: number;
  presets: SoundFontPreset[];
}

export interface LoadedSoundFont {
  summary: SoundFontSummary;
  buffer: ArrayBuffer;
}

export async function inspectSoundFont(file: File): Promise<LoadedSoundFont> {
  const buffer = await file.arrayBuffer();
  const soundBank = SoundBankLoader.fromArrayBuffer(buffer);
  return {
    buffer,
    summary: {
      fileName: file.name,
      byteLength: buffer.byteLength,
      presets: soundBank.presets.map((preset) => ({
        name: preset.name,
        bankMSB: preset.bankMSB,
        bankLSB: preset.bankLSB,
        program: preset.program,
      })),
    },
  };
}

export class SoundFontAuditioner {
  private context: AudioContext | null = null;
  private synth: WorkletSynthesizer | null = null;
  private analyser: AnalyserNode | null = null;
  private previewTimers = new Map<number, number>();
  private transportTimers: number[] = [];

  get contextState(): AudioContextState | "unloaded" {
    return this.context?.state ?? "unloaded";
  }

  async load(buffer: ArrayBuffer) {
    this.dispose();
    this.context = new AudioContext({ sampleRate: 44_100 });
    await this.context.audioWorklet.addModule(processorUrl);
    this.synth = new WorkletSynthesizer(this.context);
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 256;
    this.synth.connect(this.analyser);
    this.analyser.connect(this.context.destination);
    await this.synth.isReady;
    await this.synth.soundBankManager.addSoundBank(buffer.slice(0), "continuo-user-bank");
  }

  async play(preset: SoundFontPreset, midiNote: number, durationMs = 900) {
    const synth = await this.requireRunningSynth();
    const previewChannel = 15;
    synth.controllerChange(previewChannel, MIDIControllers.bankSelect, preset.bankMSB);
    synth.controllerChange(previewChannel, MIDIControllers.bankSelectLSB, preset.bankLSB);
    synth.programChange(previewChannel, preset.program);
    synth.noteOn(previewChannel, midiNote, 108);
    const existingTimer = this.previewTimers.get(midiNote);
    if (existingTimer !== undefined) window.clearTimeout(existingTimer);
    const releaseTimer = window.setTimeout(() => this.stop(midiNote), durationMs);
    this.previewTimers.set(midiNote, releaseTimer);
  }

  stop(midiNote: number) {
    this.synth?.noteOff(15, midiNote);
    const timer = this.previewTimers.get(midiNote);
    if (timer !== undefined) window.clearTimeout(timer);
    this.previewTimers.delete(midiNote);
  }

  async playProject(project: StudioProject, fromBeat: number) {
    const synth = await this.requireRunningSynth();
    this.stopTransport();

    const soloed = project.tracks.filter((track) => track.solo);
    const audibleTracks = project.tracks.filter(
      (track) => !track.muted && (soloed.length === 0 || track.solo),
    );
    const pitchedChannels = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14];
    const millisecondsPerBeat = 60_000 / project.tempo;

    audibleTracks.forEach((track, index) => {
      const channel = track.kind === "drums" ? 9 : pitchedChannels[index % pitchedChannels.length];
      const preset = projectPreset(track);
      synth.controllerChange(channel, MIDIControllers.bankSelect, preset.bankMSB);
      synth.controllerChange(channel, MIDIControllers.bankSelectLSB, preset.bankLSB);
      synth.programChange(channel, preset.program);
      if (track.kind === "drums") synth.midiChannels[channel]?.setDrums(true);

      track.clips.flatMap((clip) => clip.notes).forEach((note) => {
        const noteEnd = note.startBeat + note.durationBeats;
        if (noteEnd <= fromBeat) return;
        const audibleStart = Math.max(note.startBeat, fromBeat);
        const delay = Math.max(0, audibleStart - fromBeat) * millisecondsPerBeat;
        const duration = Math.max(0.05, noteEnd - audibleStart) * millisecondsPerBeat;
        this.transportTimers.push(window.setTimeout(() => {
          synth.noteOn(channel, note.pitch, note.velocity);
          this.transportTimers.push(window.setTimeout(() => {
            synth.noteOff(channel, note.pitch);
          }, duration));
        }, delay));
      });
    });
  }

  stopTransport() {
    this.transportTimers.forEach((timer) => window.clearTimeout(timer));
    this.transportTimers = [];
    this.synth?.stopAll(true);
  }

  sampleOutputLevel() {
    if (!this.analyser) return 0;
    const samples = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(samples);
    const meanSquare = samples.reduce((sum, value) => {
      const centered = (value - 128) / 128;
      return sum + centered * centered;
    }, 0) / samples.length;
    return Math.sqrt(meanSquare);
  }

  private async requireRunningSynth() {
    if (!this.context || !this.synth) {
      throw new Error("Load a SoundFont before playing audio.");
    }
    await this.context.resume();
    if (this.context.state !== "running") {
      throw new Error(`The browser audio engine is ${this.context.state}. Click a key to enable it.`);
    }
    return this.synth;
  }

  dispose() {
    this.previewTimers.forEach((timer) => window.clearTimeout(timer));
    this.previewTimers.clear();
    this.stopTransport();
    this.synth?.destroy();
    this.synth = null;
    this.analyser?.disconnect();
    this.analyser = null;
    if (this.context) void this.context.close();
    this.context = null;
  }
}

const defaultPrograms: Record<TrackKind, number> = {
  keys: 0,
  bass: 32,
  drums: 0,
  texture: 88,
};

function projectPreset(track: StudioTrack): SoundFontPreset {
  return {
    name: track.preset?.name ?? track.kind,
    bankMSB: track.kind === "drums" ? 120 : (track.preset?.bank ?? 0),
    bankLSB: 0,
    program: track.preset?.program ?? defaultPrograms[track.kind],
  };
}
