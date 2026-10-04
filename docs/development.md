# Development

## Run and validate

Use Node.js 22 or newer and npm 10.9.2 or newer. From the repository root:

```powershell
npm ci
npm run dev
```

Open the local URL printed by Quartz. The development server rebuilds when
notes change. Run `npm test` and `npm run build` to validate changes; the static
build writes `dist/`. The `baseUrl` in `quartz.config.yaml` is configured for
`666feiyu666.github.io/JazzBloom`.

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
commits and push to a remote.

## Publish to GitHub Pages

The site is configured for
[666feiyu666/JazzBloom](https://github.com/666feiyu666/JazzBloom), at
<https://666feiyu666.github.io/JazzBloom/>.

The repository's **Settings → Pages** is already set to **GitHub Actions** as
the build and deployment source. For the first publication, commit and push
the configuration and `.github/workflows/deploy.yml` when publishing is
authorized.

The workflow runs on pushes to `main` and can also be started from **Actions →
Deploy JazzBloom to GitHub Pages → Run workflow**. It installs the locked npm
dependencies with Node.js 24, runs `npm test`, builds Quartz with `npm run build`,
and deploys only `dist/`. The build's existing `prebuild` step prepares the
npm-based Quartz plugins automatically.

After setup, edit notes or media under `content/`, then commit and push to `main`
when ready to publish. A local save updates the development preview; the online
site updates after the deployment workflow succeeds. Generated files in `dist/`
remain ignored and do not need to be committed. If the repository name or domain
changes, update `baseUrl` before the next deployment.

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
