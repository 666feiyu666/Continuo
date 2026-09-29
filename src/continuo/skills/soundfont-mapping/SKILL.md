---
name: soundfont-mapping
description: Bind frozen Continuo score tracks to presets from an inspected SoundFont without changing the authored score.
---

# SoundFont Mapping

Use this skill only for the `fluidsynth-soundfont` renderer. Read
[references/continuo-contract.md](references/continuo-contract.md) before
mapping.

The complete Score IR is already frozen. Select one supplied `preset_id` for
every score track, plus bounded master gain and the SoundFont reverb switch. Use
semantic instrument, register, role, density, dynamics, and written
articulation to judge the closest available preset.

Do not change phrasing, timing, dynamics, articulation, pitch, rhythm, form,
instruments, bank/program coordinates, paths, or commands.
A pitched instrument must select a melodic preset. Percussion tracks must select
one shared percussion kit because they share MIDI channel 10. The inspected
profile and host validation are authoritative.
