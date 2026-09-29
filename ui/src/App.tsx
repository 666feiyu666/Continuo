import { useEffect, useMemo, useRef, useState } from "react";
import { initialGuidance, initialProject, type StudioNote, type StudioProject } from "./domain";
import { inspectSoundFont, SoundFontAuditioner, type SoundFontSummary } from "./soundfont";

const pixelsPerBeat = 42;
const pianoPitches = Array.from({ length: 24 }, (_, index) => 72 - index);

function Icon({ name }: { name: "play" | "pause" | "stop" | "rewind" | "upload" | "spark" | "save" }) {
  const paths = {
    play: <path d="m8 5 11 7-11 7z" />,
    pause: <><path d="M7 5h4v14H7z" /><path d="M14 5h4v14h-4z" /></>,
    stop: <path d="M7 7h10v10H7z" />,
    rewind: <><path d="m11 6-7 6 7 6z" /><path d="m20 6-7 6 7 6z" /></>,
    upload: <><path d="M12 16V4" /><path d="m7 9 5-5 5 5" /><path d="M5 20h14" /></>,
    spark: <><path d="m12 3 1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6z" /><path d="m19 16 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7z" /></>,
    save: <><path d="M5 4h12l2 2v14H5z" /><path d="M8 4v6h8V4" /><path d="M8 20v-6h8v6" /></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

function formatTime(beats: number, tempo: number) {
  const seconds = Math.round((beats / tempo) * 60);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function App() {
  const [project, setProject] = useState<StudioProject>(initialProject);
  const [selectedTrackId, setSelectedTrackId] = useState(project.tracks[0].id);
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [activePanel, setActivePanel] = useState<"soundfont" | "teacher">("soundfont");
  const [soundFont, setSoundFont] = useState<SoundFontSummary | null>(null);
  const [soundFontError, setSoundFontError] = useState<string | null>(null);
  const [isInspecting, setIsInspecting] = useState(false);
  const [audioReady, setAudioReady] = useState(false);
  const [audioContextState, setAudioContextState] = useState<AudioContextState | "unloaded">("unloaded");
  const [audioSignalLevel, setAudioSignalLevel] = useState<number | null>(null);
  const [selectedPreset, setSelectedPreset] = useState(0);
  const frameRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number | null>(null);
  const auditionerRef = useRef<SoundFontAuditioner | null>(null);

  const selectedTrack = project.tracks.find((track) => track.id === selectedTrackId) ?? project.tracks[0];
  const selectedClip = selectedTrack.clips[0];
  const visibleNotes = selectedClip?.notes ?? [];

  useEffect(() => {
    if (!playing) {
      lastTimeRef.current = null;
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
      return;
    }
    const tick = (time: number) => {
      if (lastTimeRef.current !== null) {
        const deltaBeats = ((time - lastTimeRef.current) / 1000) * (project.tempo / 60);
        setPlayhead((current) => {
          if (current + deltaBeats < project.lengthBeats) return current + deltaBeats;
          setPlaying(false);
          auditionerRef.current?.stopTransport();
          return 0;
        });
      }
      lastTimeRef.current = time;
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [playing, project.lengthBeats, project.tempo]);

  useEffect(() => () => auditionerRef.current?.dispose(), []);

  const bars = useMemo(
    () => Array.from({ length: project.lengthBeats / project.meter[0] }, (_, index) => index + 1),
    [project.lengthBeats, project.meter],
  );

  function toggleTrackFlag(trackId: string, flag: "muted" | "solo") {
    setProject((current) => ({
      ...current,
      tracks: current.tracks.map((track) =>
        track.id === trackId ? { ...track, [flag]: !track[flag] } : track,
      ),
    }));
  }

  function addNote(event: React.MouseEvent<HTMLDivElement>) {
    if (!selectedClip) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const localX = event.clientX - bounds.left;
    const localY = event.clientY - bounds.top;
    const startBeat = Math.max(0, Math.round((localX / pixelsPerBeat) * 2) / 2);
    const pitchIndex = Math.min(pianoPitches.length - 1, Math.floor(localY / 18));
    const note: StudioNote = {
      id: crypto.randomUUID(),
      pitch: pianoPitches[pitchIndex],
      startBeat,
      durationBeats: 1,
      velocity: 76,
    };
    setProject((current) => ({
      ...current,
      tracks: current.tracks.map((track) =>
        track.id !== selectedTrack.id
          ? track
          : {
              ...track,
              clips: track.clips.map((clip) =>
                clip.id === selectedClip.id ? { ...clip, notes: [...clip.notes, note] } : clip,
              ),
            },
      ),
    }));
  }

  function removeNote(noteId: string) {
    if (!selectedClip) return;
    setProject((current) => ({
      ...current,
      tracks: current.tracks.map((track) =>
        track.id !== selectedTrack.id
          ? track
          : {
              ...track,
              clips: track.clips.map((clip) =>
                clip.id === selectedClip.id
                  ? { ...clip, notes: clip.notes.filter((note) => note.id !== noteId) }
                  : clip,
              ),
            },
      ),
    }));
  }

  async function loadSoundFont(file: File | undefined) {
    if (!file) return;
    setIsInspecting(true);
    setAudioReady(false);
    setSoundFontError(null);
    try {
      const loaded = await inspectSoundFont(file);
      const auditioner = new SoundFontAuditioner();
      await auditioner.load(loaded.buffer);
      auditionerRef.current?.dispose();
      auditionerRef.current = auditioner;
      setSoundFont(loaded.summary);
      setSelectedPreset(0);
      setAudioReady(true);
      setAudioContextState(auditioner.contextState);
    } catch (error) {
      setSoundFontError(error instanceof Error ? error.message : "Unable to read this SoundFont.");
    } finally {
      setIsInspecting(false);
    }
  }

  function auditionNote(midiNote: number) {
    const preset = soundFont?.presets[selectedPreset];
    if (!preset || !auditionerRef.current) return;
    setAudioSignalLevel(null);
    void auditionerRef.current.play(preset, midiNote).then(() => {
      setAudioContextState(auditionerRef.current?.contextState ?? "unloaded");
      window.setTimeout(() => {
        setAudioSignalLevel(auditionerRef.current?.sampleOutputLevel() ?? 0);
      }, 120);
    }).catch((error: unknown) => {
      setSoundFontError(error instanceof Error ? error.message : "Unable to audition this preset.");
    });
  }

  async function togglePlayback() {
    if (playing) {
      auditionerRef.current?.stopTransport();
      setPlaying(false);
      setAudioSignalLevel(null);
      return;
    }
    if (!auditionerRef.current || !audioReady) {
      setSoundFontError("Load a SoundFont before playing the project.");
      setActivePanel("soundfont");
      return;
    }
    setSoundFontError(null);
    setAudioSignalLevel(null);
    try {
      await auditionerRef.current.playProject(project, playhead);
      setAudioContextState(auditionerRef.current.contextState);
      window.setTimeout(() => {
        setAudioSignalLevel(auditionerRef.current?.sampleOutputLevel() ?? 0);
      }, 120);
      setPlaying(true);
    } catch (error) {
      setSoundFontError(error instanceof Error ? error.message : "Unable to start project playback.");
      setActivePanel("soundfont");
    }
  }

  function stopPlayback() {
    auditionerRef.current?.stopTransport();
    setPlaying(false);
    setPlayhead(0);
    setAudioSignalLevel(null);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark">C</div>
          <div>
            <div className="brand-name">CONTINUO</div>
            <div className="project-title">{project.title}</div>
          </div>
          <span className="draft-pill">LOCAL DRAFT</span>
        </div>
        <div className="transport">
          <button className="icon-button" aria-label="Return to start" onClick={() => setPlayhead(0)}><Icon name="rewind" /></button>
          <button className="icon-button" aria-label="Stop" onClick={stopPlayback}><Icon name="stop" /></button>
          <button className="play-button" aria-label={playing ? "Pause" : "Play"} onClick={() => void togglePlayback()}>
            <Icon name={playing ? "pause" : "play"} />
          </button>
          <div className="transport-readout">
            <strong>{formatTime(playhead, project.tempo)}</strong>
            <span>{Math.floor(playhead / 4) + 1}.{Math.floor(playhead % 4) + 1}.1</span>
          </div>
          <div className="transport-stat"><span>TEMPO</span><strong>{project.tempo}</strong></div>
          <div className="transport-stat"><span>METER</span><strong>{project.meter.join(" / ")}</strong></div>
        </div>
        <button className="save-button"><Icon name="save" /> Save project</button>
      </header>

      <main className="workspace">
        <section className="arrangement panel">
          <div className="section-heading">
            <div>
              <span className="eyebrow">ARRANGEMENT</span>
              <h1>Shape the performance</h1>
            </div>
            <div className="view-switch"><button className="active">Timeline</button><button>Session</button></div>
          </div>
          <div className="timeline-shell">
            <div className="track-header-label">TRACKS</div>
            <div className="ruler" style={{ width: project.lengthBeats * pixelsPerBeat }}>
              {bars.map((bar) => <span key={bar} style={{ left: (bar - 1) * project.meter[0] * pixelsPerBeat }}>{bar}</span>)}
            </div>
            <div className="track-list">
              {project.tracks.map((track) => (
                <button key={track.id} className={`track-label ${track.id === selectedTrackId ? "selected" : ""}`} onClick={() => setSelectedTrackId(track.id)}>
                  <i style={{ background: track.color }} />
                  <span><strong>{track.name}</strong><small>{track.preset?.name ?? track.kind}</small></span>
                  <span className="track-toggles">
                    <b className={track.muted ? "enabled" : ""} onClick={(event) => { event.stopPropagation(); toggleTrackFlag(track.id, "muted"); }}>M</b>
                    <b className={track.solo ? "enabled" : ""} onClick={(event) => { event.stopPropagation(); toggleTrackFlag(track.id, "solo"); }}>S</b>
                  </span>
                </button>
              ))}
            </div>
            <div className="lanes" style={{ width: project.lengthBeats * pixelsPerBeat }}>
              <div className="playhead" style={{ left: playhead * pixelsPerBeat }}><i /></div>
              {project.tracks.map((track) => (
                <div className={`lane ${track.id === selectedTrackId ? "selected" : ""}`} key={track.id} onClick={() => setSelectedTrackId(track.id)}>
                  {track.clips.map((clip) => (
                    <div className="clip" key={clip.id} style={{ left: clip.startBeat * pixelsPerBeat + 4, width: clip.durationBeats * pixelsPerBeat - 8, borderColor: track.color, background: `color-mix(in srgb, ${track.color} 22%, #1c1a18)` }}>
                      <span>{clip.name}</span>
                      <div className="clip-notes">
                        {clip.notes.slice(0, 20).map((note) => <i key={note.id} style={{ left: `${((note.startBeat - clip.startBeat) / clip.durationBeats) * 100}%`, width: `${Math.max(2, (note.durationBeats / clip.durationBeats) * 100)}%`, top: `${82 - ((note.pitch % 24) / 24) * 70}%`, background: track.color }} />)}
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="editor panel">
          <div className="editor-toolbar">
            <div><span className="eyebrow">NOTE EDITOR</span><strong>{selectedTrack.name} · {selectedClip?.name ?? "No clip"}</strong></div>
            <span className="editor-hint">Click to add · Double-click a note to remove</span>
          </div>
          <div className="piano-roll-shell">
            <div className="piano-keys">
              {pianoPitches.map((pitch) => <div key={pitch} className={([1,3,6,8,10].includes(pitch % 12)) ? "black" : "white"}>{pitch % 12 === 0 ? `C${Math.floor(pitch / 12) - 1}` : ""}</div>)}
            </div>
            <div className="piano-grid" style={{ width: project.lengthBeats * pixelsPerBeat }} onClick={addNote}>
              {visibleNotes.map((note) => (
                <button key={note.id} className="note-event" title={`MIDI ${note.pitch} · velocity ${note.velocity}`} onDoubleClick={(event) => { event.stopPropagation(); removeNote(note.id); }} style={{ left: note.startBeat * pixelsPerBeat + 1, top: pianoPitches.indexOf(note.pitch) * 18 + 2, width: Math.max(12, note.durationBeats * pixelsPerBeat - 2), background: selectedTrack.color }} />
              ))}
              <div className="playhead editor-playhead" style={{ left: playhead * pixelsPerBeat }} />
            </div>
          </div>
        </section>

        <aside className="right-rail panel">
          <div className="rail-tabs">
            <button className={activePanel === "soundfont" ? "active" : ""} onClick={() => setActivePanel("soundfont")}>SoundFont</button>
            <button className={activePanel === "teacher" ? "active" : ""} onClick={() => setActivePanel("teacher")}><Icon name="spark" /> Guide</button>
          </div>

          {activePanel === "soundfont" ? (
            <div className="rail-content">
              <div className="rail-intro"><span className="eyebrow">INSTRUMENT MATERIAL</span><h2>SoundFont library</h2><p>Open a local bank, inspect its presets, then assign one to the selected track.</p></div>
              <label className="drop-zone">
                <Icon name="upload" />
                <strong>{isInspecting ? "Reading bank…" : "Open .sf2 / .sf3"}</strong>
                <span>The file stays in this browser session.</span>
                <input type="file" accept=".sf2,.sf3,.dls" onChange={(event) => void loadSoundFont(event.target.files?.[0])} />
              </label>
              {soundFontError && <div className="error-card">{soundFontError}</div>}
              {soundFont ? (
                <div className="soundfont-result">
                  <div className="bank-summary"><span>ACTIVE BANK</span><strong>{soundFont.fileName}</strong><small>{(soundFont.byteLength / 1024 / 1024).toFixed(1)} MB · {soundFont.presets.length} presets</small></div>
                  <div className="preset-list">
                    {(soundFont.presets.length ? soundFont.presets : [{ name: "Bank loaded — preset metadata unavailable", bankMSB: 0, bankLSB: 0, program: 0 }]).slice(0, 12).map((preset, index) => (
                      <button key={`${preset.bankMSB}-${preset.bankLSB}-${preset.program}-${index}`} className={selectedPreset === index ? "selected" : ""} onClick={() => setSelectedPreset(index)}>
                        <span>{String(preset.program).padStart(3, "0")}</span><strong>{preset.name}</strong><small>{preset.bankMSB}:{preset.bankLSB}</small>
                      </button>
                    ))}
                  </div>
                  <div className="keyboard-preview">
                    <span>PREVIEW</span>
                    <div className="mini-keys">{Array.from({ length: 14 }, (_, index) => <button key={index} className={[1,3,6,8,10,13].includes(index) ? "sharp" : "natural"} aria-label={`Preview MIDI note ${60 + index}`} onPointerDown={() => auditionNote(60 + index)} />)}</div>
                    <small>{audioReady ? `Audio ${audioContextState}${audioSignalLevel !== null ? ` · signal ${Math.round(audioSignalLevel * 100)}%` : ""} — press a key to hear the selected preset.` : "Preparing the synthesis engine…"}</small>
                  </div>
                </div>
              ) : (
                <div className="empty-library"><span>01</span><p>Load your first SoundFont to turn a binary bank into understandable musical material.</p></div>
              )}
            </div>
          ) : (
            <div className="rail-content teacher-panel">
              <div className="teacher-badge"><Icon name="spark" /><span>TEACH MODE</span></div>
              <h2>{initialGuidance.title}</h2>
              <p>{initialGuidance.explanation}</p>
              <div className="listening-card"><span>LISTEN FOR</span><p>{initialGuidance.listeningPrompt}</p></div>
              <div className="practice-card"><span>TRY IT YOURSELF</span><strong>Write a two-note answer</strong><p>Use only chord tones and leave at least one beat of silence.</p><button onClick={() => { setSelectedTrackId("upright-bass"); setPlayhead(8); }}>Focus bars 3–4</button></div>
              <div className="guidance-boundary"><strong>You remain the author.</strong><p>Suggestions focus the workspace or offer a preview. They never alter the project until you choose an action.</p></div>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}
