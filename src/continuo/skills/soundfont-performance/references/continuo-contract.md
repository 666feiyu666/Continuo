# Continuo SoundFont Contract

Use this reference for the currently implemented FluidSynth planning boundary.
Source code remains authoritative when this document and runtime behavior differ.

## Ownership boundary

- The user or runtime selects the SoundFont and FluidSynth executable.
- The composition model selects schema-supported semantic instruments and musical
  parameters without choosing renderer coordinates.
- A bounded SoundFont mapping model receives the inspected preset inventory and
  selects one supplied `preset_id` for every track.
- Deterministic validation resolves those ids, checks coverage and instrument type,
  and rejects invented or incompatible selections.
- The MIDI compiler owns channel allocation, validated bank/program serialization,
  and fixed percussion-note binding.
- The FluidSynth renderer owns command construction, output format, and duration
  fitting.
- Neither model may emit filesystem paths, shell commands, or keyswitches. The
  mapping model emits only a supplied preset id, not arbitrary bank/program values.

## Semantic instrument and preset binding

The authoritative catalog is generated from runtime code and appended to the
skill instructions. Do not maintain a duplicate instrument list here.

- Semantic instruments remain stable across renderers; they do not determine the
  production SoundFont preset.
- The selected `.sf2` is inspected through FluidSynth for its real bank, program,
  and preset names before mapping.
- Pitched tracks use model-selected melodic presets from that inspected inventory.
- Pitched tracks receive deterministic channels from the 15 non-percussion MIDI
  channels. A project may therefore contain at most 15 pitched tracks.
- Unpitched percussion maps to fixed General MIDI drum notes on zero-based channel
  9 (MIDI channel 10). The model selects one shared drum kit; event pitch is
  replaced during MIDI compilation.
- Recommended ranges guide composition but are not hard validation boundaries;
  individual SoundFonts may vary.

Multiple semantic percussion tracks share the same kit channel. Treat per-track
pan and gain differences there as best-effort MIDI metadata rather than isolated
audio routing.

## Planning constraints

- MIDI pitches must remain in `0..127`.
- Velocities are normalized values in `0.0..1.0`, not MIDI `0..127` integers.
- Track pan is `-1.0..1.0`; track gain is `0.0..2.0`.
- Use only the semantic instrument ids present in the schema supplied for the run.
- Treat preset names and coordinates as capability evidence, not proof of sample
  quality, articulation behavior, or ideal register.
- The current inspection exposes preset inventory only; it does not extract sample
  audio features, keyswitches, generators, modulators, or articulation metadata.

## Renderer assumptions

The current FluidSynth backend requests GS-style bank selection and renders
16-bit stereo PCM at 44.1 kHz by default. SoundFont is the product rendering path;
the Python and SuperCollider renderers remain development compatibility tools,
not A/B evaluation branches. Technical verification checks duration, channel
count, sample rate, signal presence, and clipping; it does not establish musical
quality.
