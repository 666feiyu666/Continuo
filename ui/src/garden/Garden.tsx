import { useEffect, useRef, useState, type RefObject } from "react";
import Markdown from "react-markdown";
import { Bloom, Icon } from "../listening/Icons";
import { formatTime } from "../listening/transport";
import { AudioSessionProvider, ListeningDock, LocalPlayer, TeachingStudies, useAudioSession } from "./AudioSession";
import { ReferencePlayer } from "./ReferencePlayer";
import { chapters, linkedMarkdown, pageById, pageHref, pages, startingPageId, type GardenPage } from "./content";
import { exportObservations, readObservations, sameFile, saveObservations, type Observation } from "./observations";

interface Draft {
  text: string;
  kind: Observation["kind"];
  relatedPageId: string;
  quote?: string;
  audio?: Observation["audio"];
}
const emptyDraft: Draft = { text: "", kind: "observation", relatedPageId: "" };

function currentPageId(): string {
  try { return decodeURIComponent(window.location.hash.replace(/^#\/?/, "")) || startingPageId; }
  catch { return "missing-page"; }
}

function Navigation({ current, close }: { current?: GardenPage; close: () => void }) {
  return <nav aria-label="Chapters" className="garden-nav" id="chapter-navigation">
    <a className="garden-brand" href={pageHref(startingPageId)} onClick={close}><Bloom /><span>JazzBloom<small>ADD JAZZ TO YOUR LIFE.</small></span></a>
    <div className="nav-section-label">Chapters</div>
    <a className={`chapter-start ${current?.id === startingPageId ? "active" : ""}`} href={pageHref(startingPageId)} aria-current={current?.id === startingPageId ? "page" : undefined} onClick={close}>Get started</a>
    <div className="chapter-groups">{chapters.map((chapter) => {
      const children = pages.filter((page) => page.chapter === chapter.id);
      const active = current?.chapter === chapter.id;
      return <details key={chapter.id} className={`chapter-group ${active ? "active" : ""}`} open={active || (current?.id === startingPageId && chapter.id === "rhythm")}>
        <summary><span className="chapter-number">{chapter.number}</span>{chapter.label}<span className="chapter-chevron">›</span></summary>
        <div className="chapter-children">{children.length ? children.map((page) => <a key={page.id} href={pageHref(page.id)} aria-current={page.id === current?.id ? "page" : undefined} onClick={close}>{page.navTitle}</a>) : <p>A thread to grow as we read.</p>}</div>
      </details>;
    })}</div>
    <div className="nav-section-label pages-label">Elsewhere in the garden</div>
    {pages.filter((page) => ["recording", "about", "reading"].includes(page.kind) && !page.chapter).map((page) => <a className="nav-other-page" key={page.id} href={pageHref(page.id)} aria-current={page.id === current?.id ? "page" : undefined} onClick={close}>{page.navTitle}<span>↗</span></a>)}
    <div className="nav-book"><span>READING ALONGSIDE</span><p>How to Listen to Jazz</p><small>Ted Gioia · Basic Books, 2016</small></div>
    <p className="nav-garden-status"><i /> {pages.length} pages. Room to grow.</p>
  </nav>;
}

function PageBody({ page }: { page: GardenPage }) {
  const blocks = page.body.split(/^:::(comparison|reference|rhythm-study)\s*$/m);
  return <>{blocks.map((block, index) => {
    if (block === "comparison") return <div className="listening-comparison" key={index}><div className="section-overline">TWO RECORDINGS · ONE OPEN QUESTION</div><ReferencePlayer /><LocalPlayer /><p className="listening-invitation">Listen in turn. Pause one recording before starting the other.</p></div>;
    if (block === "reference") return <ReferencePlayer key={index} />;
    if (block === "rhythm-study") return <TeachingStudies key={index} />;
    return <div className="prose" key={index}><Markdown components={{
      a: ({ node: _node, href, children, ...props }) => {
        const target = href?.startsWith("#/") ? pageById.get(href.slice(2)) : undefined;
        return <a {...props} href={href} className={target ? "garden-link" : undefined} title={target?.description} target={target ? undefined : "_blank"} rel={target ? undefined : "noreferrer"}>{children}</a>;
      },
    }}>{linkedMarkdown(block)}</Markdown></div>;
  })}</>;
}

function Connections({ page, notes }: { page: GardenPage; notes: Observation[] }) {
  const incoming = pages.filter((entry) => entry.id !== page.id && entry.links.includes(page.id));
  const personalIncoming = notes.filter((note) => note.relatedPageId === page.id && note.pageId !== page.id);
  return <section className="connections" aria-label="Connected pages">
    <div className="section-overline">THE GARDEN GROWS THROUGH CONNECTIONS</div>
    <div className="connections-grid"><div><h2>Follow a thread <span>↗</span></h2><ul>{page.links.map((id) => {
      const target = pageById.get(id)!;
      return <li key={id}><a href={pageHref(id)}><span>{target.navTitle}</span><small>{target.kind}</small></a></li>;
    })}</ul></div><div><h2>Linked from <span>↙</span></h2><ul>{incoming.map((entry) => <li key={entry.id}><a href={pageHref(entry.id)}><span>{entry.navTitle}</span><small>{entry.kind}</small></a></li>)}</ul>{incoming.length === 0 && <p className="quiet">Connections will grow as new pages refer here.</p>}</div></div>
    {personalIncoming.length > 0 && <div className="personal-connections"><h3>Connected by you</h3>{personalIncoming.map((note) => <p key={note.id}><a href={pageHref(note.pageId)}>{pageById.get(note.pageId)?.navTitle ?? note.pageId}</a><span> — {note.text}</span></p>)}</div>}
  </section>;
}

function Observations({ page, notes, add, saved, draft, setDraft, articleRef, sectionRef }: {
  page: GardenPage;
  notes: Observation[];
  add: (note: Observation) => void;
  saved: boolean;
  draft: Draft;
  setDraft: (draft: Draft) => void;
  articleRef: RefObject<HTMLElement | null>;
  sectionRef: RefObject<HTMLElement | null>;
}) {
  const audio = useAudioSession();
  const [message, setMessage] = useState("");
  const pageNotes = notes.filter((note) => note.pageId === page.id);
  function useSelection() {
    const selection = window.getSelection();
    const quote = selection?.toString().trim();
    if (!quote || !selection?.anchorNode || !articleRef.current?.contains(selection.anchorNode)) {
      setMessage("Select a passage in this article first, then use this button."); return;
    }
    setDraft({ ...draft, quote }); setMessage("Selected passage attached.");
  }
  function captureMoment() {
    if (!audio.file || !audio.ready) return;
    setDraft({ ...draft, audio: { file: audio.file, seconds: audio.time } });
    setMessage(`Attached ${formatTime(audio.time)} from ${audio.file.name}.`);
  }
  function submit() {
    if (!draft.text.trim()) return;
    add({ id: crypto.randomUUID(), pageId: page.id, text: draft.text.trim(), kind: draft.kind,
      createdAt: new Date().toISOString(), quote: draft.quote, audio: draft.audio,
      relatedPageId: draft.relatedPageId || undefined });
    setDraft({ ...emptyDraft }); setMessage("Observation added to this page.");
  }
  return <section className="observations" ref={sectionRef} aria-label="Your page observations">
    <div className="observations-heading"><div><span className="section-overline">YOUR TRACES ON THIS PAGE</span><h2>What caught your ear?</h2></div><span className="notes-count">{pageNotes.length} {pageNotes.length === 1 ? "trace" : "traces"}</span></div>
    <p className="observation-prompt">An observation, an uncertainty, a question to return to. Your interpretation can change as you learn.</p>
    <div className="observation-editor"><div className="editor-tools"><label>Leave an <select aria-label="Observation type" value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value as Draft["kind"] })}><option value="observation">observation</option><option value="question">open question</option></select></label><button onMouseDown={(event) => event.preventDefault()} onClick={useSelection}>Use selected passage</button></div>
      {draft.quote && <div className="draft-attachment"><blockquote>{draft.quote}</blockquote><button aria-label="Remove selected passage from draft" onClick={() => setDraft({ ...draft, quote: undefined })}>×</button></div>}
      <textarea aria-label="Your observation" placeholder="I notice… / I wonder…" value={draft.text} onChange={(event) => setDraft({ ...draft, text: event.target.value })} />
      {draft.audio && <div className="draft-audio"><Icon name="note" /><span>{formatTime(draft.audio.seconds)} · {draft.audio.file.name}</span><button aria-label="Remove audio moment from draft" onClick={() => setDraft({ ...draft, audio: undefined })}>×</button></div>}
      <div className="editor-attachments"><button disabled={!audio.ready} onClick={captureMoment}><Icon name="note" />Attach current audio time</button><label>Connect to <select aria-label="Connect observation to page" value={draft.relatedPageId} onChange={(event) => setDraft({ ...draft, relatedPageId: event.target.value })}><option value="">Choose a page…</option>{pages.filter((entry) => entry.id !== page.id).map((entry) => <option key={entry.id} value={entry.id}>{entry.navTitle}</option>)}</select></label></div>
      <div className="editor-footer"><span>{saved ? "Saved in this browser when you add a trace." : "Browser storage is unavailable. Export your observations to keep them."}</span><button className="primary-button" disabled={!draft.text.trim()} onClick={submit}>Add {draft.kind === "question" ? "question" : "observation"}<span>↗</span></button></div>
    </div>
    <p className="note-feedback" role="status">{message}</p>
    {pageNotes.length ? <ol className="observation-list">{pageNotes.map((note) => {
      const canSeek = !!(note.audio && audio.file && sameFile(note.audio.file, audio.file) && audio.ready && note.audio.seconds <= audio.duration);
      return <li key={note.id}><div className="observation-meta"><span>{note.kind === "question" ? "OPEN QUESTION" : "OBSERVATION"}</span><time dateTime={note.createdAt}>{new Date(note.createdAt).toLocaleDateString("en", { month: "short", day: "numeric" })}</time></div>
        {note.quote && <blockquote>{note.quote}</blockquote>}<p>{note.text}</p>
        <div className="observation-links">{note.audio && <button disabled={!canSeek} title={canSeek ? "Return to this moment in your local recording" : `Open ${note.audio.file.name} again to revisit this moment`} onClick={() => audio.seek(note.audio!.seconds, true)}><Icon name="play" />{formatTime(note.audio.seconds)} · {note.audio.file.name}</button>}{note.relatedPageId && <a href={pageHref(note.relatedPageId)}>Connected to {pageById.get(note.relatedPageId)?.navTitle ?? note.relatedPageId} ↗</a>}</div>
      </li>;
    })}</ol> : <p className="empty-observations">A first listen is enough to leave a first trace.</p>}
    <button className="export-notes" disabled={notes.length === 0} onClick={() => exportObservations(notes)}>Export all observations <Icon name="arrow" /></button>
  </section>;
}

function GardenShell() {
  const [id, setId] = useState(currentPageId);
  const [menuOpen, setMenuOpen] = useState(false);
  const [notes, setNotes] = useState(readObservations);
  const [saved, setSaved] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const articleRef = useRef<HTMLElement>(null);
  const observationsRef = useRef<HTMLElement>(null);
  const page = pageById.get(id);

  useEffect(() => {
    const onHashChange = () => { setId(currentPageId()); setMenuOpen(false); };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    document.title = `${page?.title ?? "Page not found"} · JazzBloom`;
    window.scrollTo({ top: 0, behavior: "instant" });
    articleRef.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true });
  }, [page]);

  function add(note: Observation) {
    const next = [...notes, note];
    setNotes(next); setSaved(saveObservations(next));
  }
  function goToObservations() {
    observationsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    observationsRef.current?.querySelector("textarea")?.focus({ preventScroll: true });
  }

  return <div className="garden-shell">
    <a className="skip-link" href="#main-reading" onClick={(event) => { event.preventDefault(); articleRef.current?.querySelector<HTMLHeadingElement>("h1")?.focus(); }}>Skip to reading</a>
    <aside className={`garden-sidebar ${menuOpen ? "mobile-open" : ""}`}><Navigation current={page} close={() => setMenuOpen(false)} /></aside>
    {menuOpen && <button className="menu-scrim" aria-label="Close chapter menu" onClick={() => setMenuOpen(false)} />}
    <div className="reading-column"><header className="reading-topbar"><button className="mobile-menu-button" aria-label={menuOpen ? "Close chapters" : "Open chapters"} aria-expanded={menuOpen} aria-controls="chapter-navigation" onClick={() => setMenuOpen(!menuOpen)}><Icon name={menuOpen ? "close" : "menu"} /></button><span>Garden <span className="breadcrumb-slash">/</span> {page?.kind === "question" ? "Starting question" : page?.kind ?? "Unknown page"}</span><button className="your-traces-button" onClick={goToObservations}><Icon name="note" />Your traces <span>{notes.filter((note) => note.pageId === id).length}</span></button></header>
      {page ? <>
        <article ref={articleRef} id="main-reading" className="garden-article"><div className="reading-kicker"><span>FIELD NOTE {String(pages.indexOf(page) + 1).padStart(3, "0")}</span><span className="stage-label"><i />{page.stage}</span></div>
          <h1 tabIndex={-1}>{page.title}</h1><p className="reading-deck">{page.description}</p>
          <div className="article-divider"><span>A DIGITAL LISTENING GARDEN</span><span>Read · Listen · Connect</span></div>
          <PageBody page={page} />
        </article>
        <Connections page={page} notes={notes} />
        <Observations key={page.id} page={page} notes={notes} add={add} saved={saved} draft={drafts[page.id] ?? emptyDraft} setDraft={(draft) => setDrafts((current) => ({ ...current, [page.id]: draft }))} articleRef={articleRef} sectionRef={observationsRef} />
      </> : <article ref={articleRef} className="garden-article not-found"><h1 tabIndex={-1}>A path still to grow.</h1><p>This page does not exist yet.</p><a href={pageHref(startingPageId)}>Return to the starting question ↗</a></article>}
      <footer className="garden-footer"><span>JazzBloom <i>— a little closer to the music.</i></span><a href={pageHref("about")}>Sources & approach ↗</a></footer>
    </div>
    {page?.id !== startingPageId && <ListeningDock />}
  </div>;
}

export function Garden() { return <AudioSessionProvider><GardenShell /></AudioSessionProvider>; }
