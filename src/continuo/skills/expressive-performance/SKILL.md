---
name: expressive-performance
description: Interpret a frozen Continuo Score IR as connected musical phrases, producing renderer-independent expression, connection, breath, and bounded note-level timing controls.
---

# Expressive Performance

Read the complete frozen Score IR as notation and return the supported
Expressive Performance IR. The task is to make the written music sound like a
continuous performance without recomposing it.

## Musical boundary

- Sections describe form. They never imply a breath, silence, new attack,
  expression reset, or phrase ending by themselves.
- Follow each phrase across any section boundary it crosses. Shape one
  expression arc from its actual start through its peak to its release.
- Choose `legato`, `connected`, or `separated` from the musical context, not as
  a track-wide preset. Use a positive breath only where a player should
  deliberately release before the next phrase; zero means continue.
- Use sparse note adjustments for meaningful local emphasis, anticipation,
  delay, or duration shaping. Do not mechanically perturb every note.

## Invariants

Cover every score track exactly once and every phrase referenced by that track
exactly once. Keep all values inside the supplied schema. Preserve score pitch,
note order, formal structure, phrase identity, and notated rhythm. Bounded onset,
duration, and velocity adjustments are performance realization, not permission
to rewrite the composition.

The host binds the result to the exact Score IR hash, validates phrase and note
references, expands phrase expression arcs into MIDI CC11 curves, and applies
connection and breath only during performance compilation.
