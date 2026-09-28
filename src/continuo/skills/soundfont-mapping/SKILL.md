---
name: soundfont-mapping
description: Bind frozen Continuo score tracks to presets from an inspected SoundFont after expressive interpretation, without changing notes or performance.
---

# SoundFont Mapping

Use this skill only for the `fluidsynth-soundfont` renderer. Read
[references/continuo-contract.md](references/continuo-contract.md) before
mapping.

The complete Score IR and Expressive Performance IR are already frozen. Select
one supplied `preset_id` for every score track, plus bounded master gain and the
SoundFont reverb switch. Use semantic instrument, register, role, density, and
the intended articulation to judge the closest available preset.

Do not change phrasing, timing, dynamics, connection, breath, articulation,
pitch, rhythm, form, instruments, bank/program coordinates, paths, or commands.
A pitched instrument must select a melodic preset. Percussion tracks must select
one shared percussion kit because they share MIDI channel 10. The inspected
profile and host validation are authoritative.
