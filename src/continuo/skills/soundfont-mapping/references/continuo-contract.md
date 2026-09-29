# Continuo SoundFont Mapping Contract

Source code is authoritative when this reference and runtime behavior differ.

## Ownership

- The composer produces and freezes the Score IR.
- The SoundFont mapper only selects supplied preset ids, master gain, and the
  reverb switch for the selected inspected `.sf2`.
- The MIDI compiler owns channel allocation, bank/program serialization,
  authored articulation realization, and fixed percussion-note binding.
- The FluidSynth renderer owns command construction, output format, and duration
  fitting.

No downstream model may rewrite or reinterpret the frozen score.

## Binding

- Semantic instruments remain renderer-independent.
- Pitched tracks use melodic presets and receive deterministic channels from the
  15 non-percussion MIDI channels.
- Unpitched percussion uses zero-based channel 9 (MIDI channel 10); event pitch
  is replaced with the cataloged drum note during MIDI compilation.
- Multiple percussion tracks must select one shared drum kit.
- Preset inventory proves only that a bank/program/name exists. It does not prove
  sample quality, true legato support, keyswitches, or ideal register.

The current FluidSynth backend uses GS-style bank selection and 16-bit stereo
PCM at 44.1 kHz by default. Technical verification does not establish subjective
musical quality.
