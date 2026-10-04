# Development

## Run and validate

Use Node.js 22 or newer and npm 10.9.2 or newer. From the repository root:

```powershell
npm ci
npm run dev
```

Open the local URL printed by Quartz. The development server rebuilds when
notes change. Run `npm test` and `npm run build` to validate changes; the static
build writes `dist/`. Configure `baseUrl` in `quartz.config.yaml` for the actual
hosting domain and optional project subpath before deployment.

## Write notes in VS Code

Create or edit `.md` files under `content/`. A single file is a complete note;
a topic can also be a folder containing several notes. Add `index.md` only
when the folder needs its own introduction. A title can be set with optional
frontmatter:

```markdown
---
title: My note
---

Write the note here.
```

Use `[[The Structure of Jazz/timbre|Timbre]]` for an internal wiki link, or a
relative Markdown link such as `[Timbre](./timbre.md)` from a note in the same
folder. Place media in `content/audio/` and embed it with, for example,
`![[audio/lyria/v1/so-what-minimal-modal-trio.mp3]]`. Keep attribution with the
note and technical provenance in `docs/audio/`.

## Maintenance

Keep changes in authored content and `quartz.config.yaml` where possible.
The upstream version and commit are recorded in [Current implementation](current.md).
Consult [Quartz configuration](https://quartz.jzhao.xyz/configuration) and
[content authoring](https://quartz.jzhao.xyz/getting-started/authoring-content)
for native options. Do not run `quartz sync` for routine editing: it can create
commits and push to a remote. Publish the generated `dist/` directory through
the chosen static host when deployment is authorized.

## Recover earlier browser observations

The former observation editor stored JSON under `jazzbloom.observations.v1`.
To recover it, open a page at the exact origin previously used (same scheme,
hostname, and port), then run this in Chrome or Edge DevTools:

```javascript
copy(localStorage.getItem("jazzbloom.observations.v1"))
```

Paste the copied value into a `.json` file. A `null` value means that origin
has no stored observations. This does not alter the saved data. The new site
does not import these records; meaningful observations can be transferred into
notes with their recording identity and timestamps preserved.
