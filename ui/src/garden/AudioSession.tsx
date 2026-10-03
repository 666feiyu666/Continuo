import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../listening/Icons";
import { clampTime, formatTime, seekPosition, validLoop, type LoopRange } from "../listening/transport";
import type { FileIdentity } from "./observations";
import projectAudio from "./project-audio.json";
import teachingAudio from "./teaching-audio.json";

interface AudioSession {
  file: FileIdentity | null;
  source: "project" | "local";
  duration: number;
  time: number;
  playing: boolean;
  ready: boolean;
  error: string | null;
  range: LoopRange;
  looping: boolean;
  rate: number;
  openFile: (file: File | undefined) => void;
  openProject: () => void;
  toggle: () => void;
  pause: () => void;
  seek: (time: number, releaseLoop?: boolean) => void;
  setRange: (range: LoopRange) => void;
  setLooping: (enabled: boolean) => void;
  setRate: (rate: number) => void;
}

const SessionContext = createContext<AudioSession | null>(null);
export function useAudioSession(): AudioSession {
  const session = useContext(SessionContext);
  if (!session) throw new Error("Audio session is unavailable");
  return session;
}

function pauseOtherAudio(active: HTMLAudioElement): void {
  document.querySelectorAll<HTMLAudioElement>("audio").forEach((audio) => {
    if (audio !== active) audio.pause();
  });
}

export function AudioSessionProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const urlRef = useRef<string | null>(null);
  const [url, setUrl] = useState<string>(`${import.meta.env.BASE_URL}${projectAudio.src}`);
  const [file, setFile] = useState<FileIdentity | null>(projectAudio);
  const [source, setSource] = useState<"project" | "local">("project");
  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [range, setRangeState] = useState<LoopRange>({ start: 0, end: 0 });
  const [looping, setLoopingState] = useState(false);
  const [rate, setRateState] = useState(1);

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  useEffect(() => {
    if (!playing || !looping || !validLoop(range, duration)) return;
    let frame = 0;
    const tick = () => {
      const audio = audioRef.current;
      if (audio && !audio.paused && (audio.currentTime >= range.end || audio.currentTime < range.start)) {
        audio.currentTime = range.start;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, looping, range, duration]);

  function openFile(selected: File | undefined) {
    if (!selected) return;
    if (!selected.type.startsWith("audio/") && !/\.(mp3|wav|m4a|ogg|flac|aac|aif|aiff|webm)$/i.test(selected.name)) {
      setError("Choose an audio file, such as an MP3 or WAV."); return;
    }
    audioRef.current?.pause();
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = URL.createObjectURL(selected);
    setReady(false); setDuration(0); setTime(0); setError(null);
    setRangeState({ start: 0, end: 0 }); setLoopingState(false);
    setRateState(1); setPlaying(false);
    setFile({ name: selected.name, size: selected.size, lastModified: selected.lastModified });
    setSource("local");
    setUrl(urlRef.current);
  }

  function openProject() {
    audioRef.current?.pause();
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
    setReady(false); setDuration(0); setTime(0); setError(null);
    setRangeState({ start: 0, end: 0 }); setLoopingState(false); setRateState(1); setPlaying(false);
    setFile(projectAudio); setSource("project");
    setUrl(`${import.meta.env.BASE_URL}${projectAudio.src}`);
  }

  function seek(seconds: number, releaseLoop = false) {
    const audio = audioRef.current;
    if (!audio || !ready) return;
    const target = releaseLoop ? clampTime(seconds, duration) : seekPosition(seconds, duration, looping ? range : null);
    if (releaseLoop) setLoopingState(false);
    audio.currentTime = target; setTime(target);
  }

  function toggle() {
    const audio = audioRef.current;
    if (!audio || !ready) return;
    if (!audio.paused) { audio.pause(); return; }
    if (looping && validLoop(range, duration) && (audio.currentTime < range.start || audio.currentTime >= range.end)) {
      audio.currentTime = range.start;
    }
    setError(null);
    void audio.play().catch(() => setError("Playback could not start. Try playing again or choose another audio file."));
  }

  function setRange(next: LoopRange) {
    setRangeState(next);
    if (!validLoop(next, duration)) setLoopingState(false);
  }
  function setLooping(enabled: boolean) {
    if (enabled && !validLoop(range, duration)) return;
    setLoopingState(enabled);
    const audio = audioRef.current;
    if (enabled && audio && (audio.currentTime < range.start || audio.currentTime >= range.end)) {
      audio.currentTime = range.start; setTime(range.start);
    }
  }
  function setRate(next: number) {
    if (audioRef.current) audioRef.current.playbackRate = next;
    setRateState(next);
  }

  return <SessionContext.Provider value={{ file, source, duration, time, playing, ready, error, range, looping, rate,
    openFile, openProject, seek, toggle, pause: () => audioRef.current?.pause(), setRange, setLooping, setRate }}>
    {children}
    <audio ref={audioRef} src={url} preload="metadata" aria-label="Your recording"
      onLoadedMetadata={(event) => {
        const audio = event.currentTarget;
        const length = Number.isFinite(audio.duration) ? audio.duration : 0;
        setDuration(length); setRangeState({ start: 0, end: length });
        setReady(length > 0); audio.playbackRate = rate;
      }}
      onPlay={(event) => { pauseOtherAudio(event.currentTarget); setPlaying(true); }}
      onPause={() => setPlaying(false)}
      onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
      onEnded={(event) => {
        if (looping && validLoop(range, duration)) {
          event.currentTarget.currentTime = range.start;
          void event.currentTarget.play().catch(() => setError("Press play to resume the loop."));
        } else setPlaying(false);
      }}
      onError={() => { setReady(false); setPlaying(false); setError("This audio could not be decoded. Try another MP3 or WAV file."); }} />
  </SessionContext.Provider>;
}

export function LocalPlayer() {
  const audio = useAudioSession();
  const usableRange = validLoop(audio.range, audio.duration);
  return <section className="local-player" aria-label="Your generated recording">
    <div className="listen-label"><span className="listen-number">B</span><span>YOUR GENERATED PIECE</span><span className="local-tag">{audio.source === "project" ? "LYRIA STUDY" : "LOCAL AUDIO"}</span></div>
    <div className="local-heading"><div><h3>Your Lyria sketch</h3><p>{audio.file?.name ?? "Bring the piece that made you curious."}</p></div>
      <label className="file-picker"><Icon name="upload" />Open local MP3
        <input type="file" accept="audio/*,.mp3,.wav,.m4a" aria-label="Open local audio file"
          onChange={(event) => { audio.openFile(event.target.files?.[0]); event.target.value = ""; }} />
      </label>
    </div>
    <div className="audio-transport">
      <button className="audio-play" disabled={!audio.ready} aria-label={audio.playing ? "Pause your recording" : "Play your recording"} onClick={audio.toggle}><Icon name={audio.playing ? "pause" : "play"} /></button>
      <div className="audio-progress"><input type="range" min="0" max={audio.duration || 1} step="0.01" value={audio.time} disabled={!audio.ready} aria-label="Seek your recording" onChange={(event) => audio.seek(Number(event.target.value))} />
        <div><span>{formatTime(audio.time)}</span><span>{formatTime(audio.duration)}</span></div>
      </div>
      <label className="rate-control"><span>Speed</span><select aria-label="Playback speed" disabled={!audio.ready} value={audio.rate} onChange={(event) => audio.setRate(Number(event.target.value))}><option value="0.75">0.75×</option><option value="1">1×</option><option value="1.25">1.25×</option></select></label>
    </div>
    {audio.file && <div className="loop-tools">
      <span className="loop-caption"><Icon name="loop" />Revisit a moment</span>
      <label>A <input aria-label="Loop start in seconds" type="number" min="0" max={audio.duration} step="0.1" value={Number(audio.range.start.toFixed(2))} disabled={!audio.ready} onChange={(event) => audio.setRange({ ...audio.range, start: Number(event.target.value) })} /></label>
      <label>B <input aria-label="Loop end in seconds" type="number" min="0" max={audio.duration} step="0.1" value={Number(audio.range.end.toFixed(2))} disabled={!audio.ready} onChange={(event) => audio.setRange({ ...audio.range, end: Number(event.target.value) })} /></label>
      <button className={`loop-toggle ${audio.looping ? "active" : ""}`} aria-pressed={audio.looping} disabled={!audio.ready || !usableRange} onClick={() => audio.setLooping(!audio.looping)}>{audio.looping ? "Loop on" : "Loop off"}</button>
      {!usableRange && audio.ready && <small role="status">Choose an end after the start, within the recording.</small>}
    </div>}
    {audio.error && <p className="audio-error" role="alert">{audio.error}</p>}
    <p className="local-footnote">{audio.source === "project" ? "Generated with Lyria · supplied by the project author." : <>Your file plays on this device. Select it again after a refresh. <button onClick={audio.openProject}>Return to the Lyria study ↗</button></>}</p>
  </section>;
}

export function ListeningDock() {
  const audio = useAudioSession();
  if (!audio.file) return null;
  return <aside className="listening-dock" aria-label="Continue listening">
    <button className="audio-play" disabled={!audio.ready} aria-label={audio.playing ? "Pause your recording" : "Play your recording"} onClick={audio.toggle}><Icon name={audio.playing ? "pause" : "play"} /></button>
    <div><small>KEEP LISTENING</small><span>{audio.file.name}</span></div><time>{formatTime(audio.time)} / {formatTime(audio.duration)}</time>
    <a href="#/starting-question">Back to the question <span>↗</span></a>
  </aside>;
}

export function TeachingStudies() {
  const audio = useAudioSession();
  return <div className="teaching-studies">{(["straight", "swing"] as const).map((id, index) => <section key={id}>
    <div className="study-heading"><span>0{index + 1}</span><h3>{index === 0 ? "Evenly spaced" : "Later offbeats"}</h3></div>
    <audio controls preload="metadata" aria-label={index === 0 ? "Straight timing study" : "Swing timing study"}
      src={`${import.meta.env.BASE_URL}${teachingAudio.assets[id].src}`} onPlay={(event) => { audio.pause(); pauseOtherAudio(event.currentTarget); }} />
    <p>{index === 0 ? "Keep a steady tap. Listen for the notes between the beats." : "Keep the same tap. Notice the different spacing of those notes."}</p>
  </section>)}</div>;
}
