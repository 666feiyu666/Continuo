export type TrackKind = "keys" | "bass" | "drums" | "texture";

export interface StudioNote {
  id: string;
  pitch: number;
  startBeat: number;
  durationBeats: number;
  velocity: number;
}

export interface StudioClip {
  id: string;
  name: string;
  startBeat: number;
  durationBeats: number;
  notes: StudioNote[];
}

export interface StudioTrack {
  id: string;
  name: string;
  kind: TrackKind;
  color: string;
  preset?: {
    soundBankName: string;
    bank: number;
    program: number;
    name: string;
  };
  clips: StudioClip[];
  muted: boolean;
  solo: boolean;
}

export interface StudioProject {
  schema: "continuo.studio-project/v0";
  id: string;
  title: string;
  tempo: number;
  meter: [number, number];
  lengthBeats: number;
  tracks: StudioTrack[];
}

export type GuidanceAction =
  | {
      type: "focus";
      target: { trackId?: string; clipId?: string; beatRange?: [number, number] };
    }
  | {
      type: "preview-notes";
      trackId: string;
      notes: StudioNote[];
    }
  | {
      type: "set-practice-goal";
      title: string;
      completionHint: string;
    };

export interface AgentGuidance {
  schema: "continuo.guidance/v0";
  mode: "teach" | "collaborate" | "diagnose";
  title: string;
  explanation: string;
  listeningPrompt?: string;
  actions: GuidanceAction[];
}

export const initialProject: StudioProject = {
  schema: "continuo.studio-project/v0",
  id: "nocturne-sketch",
  title: "Nocturne Sketch 01",
  tempo: 86,
  meter: [4, 4],
  lengthBeats: 32,
  tracks: [
    {
      id: "felt-piano",
      name: "Felt Piano",
      kind: "keys",
      color: "#df9b72",
      muted: false,
      solo: false,
      preset: {
        soundBankName: "Local SoundFont",
        bank: 0,
        program: 0,
        name: "Warm Grand",
      },
      clips: [
        {
          id: "piano-a",
          name: "Opening voicing",
          startBeat: 0,
          durationBeats: 16,
          notes: [
            { id: "n1", pitch: 60, startBeat: 0, durationBeats: 2, velocity: 78 },
            { id: "n2", pitch: 63, startBeat: 0, durationBeats: 2, velocity: 68 },
            { id: "n3", pitch: 67, startBeat: 0, durationBeats: 3.5, velocity: 72 },
            { id: "n4", pitch: 62, startBeat: 4, durationBeats: 1.5, velocity: 74 },
            { id: "n5", pitch: 65, startBeat: 4, durationBeats: 2, velocity: 67 },
            { id: "n6", pitch: 69, startBeat: 4, durationBeats: 3.5, velocity: 70 },
            { id: "n7", pitch: 58, startBeat: 8, durationBeats: 2, velocity: 77 },
            { id: "n8", pitch: 62, startBeat: 8, durationBeats: 2, velocity: 69 },
            { id: "n9", pitch: 65, startBeat: 8, durationBeats: 3, velocity: 71 },
          ],
        },
      ],
    },
    {
      id: "upright-bass",
      name: "Upright Bass",
      kind: "bass",
      color: "#7cae9e",
      muted: false,
      solo: false,
      clips: [
        {
          id: "bass-a",
          name: "Root motion",
          startBeat: 0,
          durationBeats: 24,
          notes: [
            { id: "b1", pitch: 36, startBeat: 0, durationBeats: 1.5, velocity: 80 },
            { id: "b2", pitch: 38, startBeat: 4, durationBeats: 1.5, velocity: 78 },
            { id: "b3", pitch: 34, startBeat: 8, durationBeats: 1.5, velocity: 76 },
          ],
        },
      ],
    },
    {
      id: "brush-kit",
      name: "Brush Kit",
      kind: "drums",
      color: "#c8b46a",
      muted: false,
      solo: false,
      clips: [
        { id: "drums-a", name: "Brush pulse", startBeat: 8, durationBeats: 8, notes: [] },
        { id: "drums-b", name: "Brush pulse", startBeat: 16, durationBeats: 8, notes: [] },
      ],
    },
    {
      id: "room-texture",
      name: "Room Texture",
      kind: "texture",
      color: "#8d84b8",
      muted: false,
      solo: false,
      clips: [{ id: "texture-a", name: "Air", startBeat: 16, durationBeats: 16, notes: [] }],
    },
  ],
};

export const initialGuidance: AgentGuidance = {
  schema: "continuo.guidance/v0",
  mode: "teach",
  title: "Make the bass answer the piano",
  explanation:
    "Your harmony already establishes a calm downward pull. Instead of adding more notes, give the bass a delayed answer on beat three and compare how the phrase breathes.",
  listeningPrompt:
    "Listen for whether the second half feels like a reply, not another beginning.",
  actions: [
    { type: "focus", target: { trackId: "upright-bass", beatRange: [8, 16] } },
    {
      type: "set-practice-goal",
      title: "Write a two-note answer",
      completionHint: "Use only chord tones and leave at least one beat of silence.",
    },
  ],
};
