# JazzBloom Architecture

JazzBloom is a digital humanities garden for learning to listen to jazz. Its
opening question asks how a Lyria-generated piece inspired by Miles Davis's
*So What* might resemble or differ from the reference recording. This is a
learning goal rather than a completed analysis. Ted Gioia's *How to Listen to
Jazz* guides the original companion reading.

The core unit is a page: a question, concept, recording, or reading note.
Chapters offer a reading path, while links and backlinks allow inquiry to
branch and return. Pages grow independently as the author learns.

## Implementation boundary

- The React application in `ui/` presents Markdown pages with YAML frontmatter.
- Wiki links connect pages and generate backlinks.
- A shared browser audio session continues across page navigation.
- The author's Lyria MP3 and two original timing studies are project assets.
- Local file selection uses browser object URLs without uploading audio.
- Personal observations attach to pages, selected text, and audio moments;
  they persist in the browser and can be exported as JSON.
- Reference recordings use external players or attributed listening links.

The initial garden needs no backend. Shared annotations, cloud persistence,
audio analysis, or spatial presentation can be added when they serve a
concrete reading or listening action.

## Content and evidence

Keep the opening question open until evidence supports an answer. Distinguish
original writing, quotations, translations, and personal interpretations.
Reference recordings, generated music, and teaching studies have separate
provenance. A timestamp belongs to a specific recording; it must not silently
retarget another file. Personal observations do not edit the public article.
