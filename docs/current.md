# Current Implementation

JazzBloom uses Quartz 5.0.0 at the repository root. Its source was taken from
upstream commit
[`97a2d05f80c4c50534959b1d0d41cc4b3895625e`](https://github.com/jackyzha0/quartz/tree/97a2d05f80c4c50534959b1d0d41cc4b3895625e).
Project configuration is in `quartz.config.yaml`; the framework is in `quartz/`.

GitHub Pages configuration is in `.github/workflows/deploy.yml`. It tests and
builds pushes to `main`, then publishes `dist/` to
<https://666feiyu666.github.io/JazzBloom/>. Pages is enabled with GitHub Actions
as its source; the first publication requires these changes to be committed
and pushed. See [Development](development.md) for setup and publishing.

## Notes

- `content/index.md` links to the four author-chosen entry points.
- `content/Motivation/index.md` presents the open research question.
- `content/Motivation/So What.md` identifies the reference recording and the
  author's Lyria study.
- `content/The Structure of Jazz/` contains the existing pulse and swing,
  timbre, and form notes.
- `content/The Evolution of Jazz Styles/` and `content/Jazz Musicians/` provide
  entry pages for future author-written notes.

Quartz derives navigation, search, and backlinks from the Markdown files.
Notes use ordinary Markdown and wiki links. YAML frontmatter is optional;
`title` can override the filename. There are no required growth stages,
chapter identifiers, or page-kind fields.

## Listening and provenance

The supplied Lyria MP3 and both original timing-study WAV files are preserved
in `content/audio/`. Notes embed them with native browser audio controls.
Metadata and hashes remain in `docs/audio/project-audio.json` and
`docs/audio/teaching-audio.json`. The commercial reference recording is linked
externally; its listening link remains available if YouTube restricts embedding.

The former React application and its custom local-file player, A/B loop,
continuous playback dock, and browser observation editor are removed. Existing
browser observations are not erased or automatically converted; see
[Development](development.md) for recovery. New observations belong in the
author's Markdown notes.
