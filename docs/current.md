# Current Implementation

The active application is the React and TypeScript garden in `ui/`. Six
Markdown pages cover the starting question, the reference recording, pulse
and swing, timbre, form, and the project's approach. They are original
companion writing, not book excerpts.

`ui/src/garden/pages/` is loaded through a Vite glob import. Frontmatter
provides titles, page kind, growth stage, order, and optional chapter
membership. Wiki links resolve to hash routes and generate backlinks.
Chapter navigation is derived from the page catalog.

## Reading and listening

- The supplied Lyria MP3 plays directly, with file identity and SHA-256 in
  `project-audio.json`.
- Local audio selection supports playback, seeking, speed, and bounded A/B
  loops. A shared session and playback dock continue across pages.
- Two original timing studies illustrate even and delayed offbeat placement.
  `teaching-audio.json` records their asset paths, hashes, and provenance.
- YouTube loads on demand and provides an external link when embedding fails.
- Observations attach to a page with optional selected text, a timestamp and
  recording identity, and a connection to another page.
- Observations persist in browser local storage, generate personal incoming
  connections, and export as JSON.
- Responsive navigation and keyboard-accessible controls support reading on
  desktop and mobile.

Refresh restores the project recording; a different local file must be
selected again. Saved timestamps work only when the matching file is open.
Draft observations survive page navigation but not a refresh.

## Boundaries

The supplied reference video reports YouTube error 150 because embedding is
restricted; its external listening link remains available. Personal
observations belong to the browser and origin. There is no account system,
cloud synchronization, collaborative editing, automatic similarity analysis,
or device-level XR presentation. The exact Lyria prompt has not been supplied.

The old DAW, Python generation pipeline, synth dependencies, and unused audio
have been removed. The current app requires only Node.js and npm.
