# Development

## Run and validate

Use Node.js 24 or newer. From the repository root:

```powershell
Set-Location ui
npm ci
npm run dev -- --host 127.0.0.1 --strictPort
```

Open `http://127.0.0.1:4317/`. Run `npm test` and `npm run build` to validate
changes. The build writes `ui/dist/`; `npm run preview` serves it at port 4173.
Relative asset paths and hash navigation support hosting beneath a project
subdirectory.

## Add a page

Create a Markdown file beneath `ui/src/garden/pages/`. Its path without `.md`
is the stable page ID. Frontmatter requires `title`, `description`, `kind`,
and `stage`; optional fields are `navTitle`, `chapter`, and numeric `order`.
Kinds are `question`, `concept`, `recording`, `reading`, and `about`. Chapter
identifiers are defined in `ui/src/garden/content.ts`.

Use `[[concepts/timbre|the character of a sound]]` to link to another page.
The catalog validates link targets and chapter identifiers and derives
backlinks. Markdown renders without raw HTML.

Interactive blocks are explicit lines in the source:

- `:::comparison` inserts the reference and the author's recording.
- `:::reference` inserts the YouTube reference player.
- `:::rhythm-study` inserts the two timing studies.

## Audio and observations

The Lyria MP3 lives in `ui/public/audio/lyria/v1/` and its metadata in
`ui/src/garden/project-audio.json`. The two teaching WAV files live in
`ui/public/audio/studies/v1/`; their hashes and renderer attribution are in
`ui/src/garden/teaching-audio.json`. Preserve active originals and use a new
version for a later replacement.

Personal observations use `jazzbloom.observations.v1` in local storage. JSON
exports include text, page connections, optional quotations, and recording
identity/timestamps, without audio bytes. Different browser origins have
separate stores. Local file selection is not an upload.

Keep the reference's external YouTube link available when embedding is
restricted. The commercial recording is not a bundled asset.
