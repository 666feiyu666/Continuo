---
name: soundfont-performance
description: Plan playable Continuo instrumentation and select concrete presets from the inspected SoundFont profile before validated MIDI compilation.
---

# SoundFont Performance

Use this skill only when the selected renderer is `fluidsynth-soundfont`.

# Scope

This is a Continuo integration skill, not an exhaustive SoundFont-format or
FluidSynth-library manual. It covers the subset that the current Music IR, MIDI
compiler, and offline FluidSynth renderer can actually execute.

Do not imply support for SoundFont authoring, preset inventory extraction,
multiple-SoundFont stacks, bank offsets, arbitrary generators or modulators,
microtuning, live MIDI routing, sequencing, per-channel legato or portamento,
custom loaders, LADSPA, or multi-channel audio unless the runtime later exposes
and validates those capabilities.

Read [references/continuo-contract.md](references/continuo-contract.md) before
planning SoundFont-targeted material. The composition stage selects semantic
instrument ids according to musical role, register, balance, and density. After
the composition is validated, the SoundFont mapping stage receives the actual
preset inventory inspected from the selected `.sf2` and uses musical judgment to
choose one concrete preset for every track.

The mapping model may select only a supplied `preset_id`; it must not invent a
preset or alter notes, form, semantic instruments, paths, or commands. The host
resolves the chosen id to bank/program data, checks melodic-versus-percussion
compatibility and complete track coverage, and only then compiles MIDI.

Shape notes so the resulting MIDI performance is plausible: respect the catalog's
recommended ranges when practical, use normalized velocity intentionally, avoid
accidental note congestion, distinguish pitched and percussion roles, and do not
assume unsupported keyswitches, articulations, or controllers. For an unpitched
percussion track, event pitch is not a drum-key request; the MIDI compiler
replaces it with the cataloged drum note. Because all percussion tracks share
MIDI channel 10, choose one common kit for them.

The inspected SoundFont profile and runtime contract are authoritative. This
skill cannot add tools, expand either candidate set, or override validation.
