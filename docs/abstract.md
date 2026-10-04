# JazzBloom Architecture

JazzBloom is a Markdown notebook published with [Quartz](https://quartz.jzhao.xyz/).
The author edits notes in VS Code; Quartz supplies the reading layout, navigation,
search, and backlinks.

The four entry points are Motivation, The Structure of Jazz, The Evolution of
Jazz Styles, and Jazz Musicians. A subnote can be a single `.md` file or a folder
containing several notes. Folder entry pages are optional. The motivation asks
how AI-generated jazz differs from jazz composed and played by people; the
author's Lyria study and Miles Davis's *So What* provide an existing example,
not a completed comparison.

## Implementation boundary

- `content/` contains published notes and their media.
- `quartz.config.yaml` configures the existing Quartz template.
- `quartz/` contains the upstream publishing framework.
- `docs/` contains maintenance documentation and audio provenance, outside the
  published content directory.
- `dist/` is generated static output and can be rebuilt from the sources.

New notes do not require frontend changes or prescribed chapter metadata. Keep
the publishing configuration small; add features only for an explicit author
need.

## Content and evidence

Keep the research question open until evidence supports an answer. Distinguish
original writing, quotations, translations, and personal interpretations. Ted
Gioia's *How to Listen to Jazz* guides original companion notes; the notes are
not the book itself. Reference recordings, generated music, and teaching studies
have separate attribution. Preserve audio originals and associate observations
and timestamps with the specific recording used.
