# Project Documentation

- [Architecture](abstract.md): the notebook model and content boundaries.
- [Current implementation](current.md): Quartz version, note locations, and media.
- [Development](development.md): run Quartz, write notes in VS Code, and maintain
  the publishing configuration.

Published notes and audio are in `content/`. Publishing configuration is in
`quartz.config.yaml`; audio provenance is in `docs/audio/`. The framework in
`quartz/` comes from upstream Quartz. Generated output is written to `dist/`.
