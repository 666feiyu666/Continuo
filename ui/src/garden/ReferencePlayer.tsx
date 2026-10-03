import { useEffect, useRef, useState } from "react";
import { Icon } from "../listening/Icons";
import { useAudioSession } from "./AudioSession";
import { loadYouTubeAPI, type YouTubePlayer } from "./youtube";

export function ReferencePlayer() {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const audio = useAudioSession();
  const containerRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<YouTubePlayer | null>(null);
  const pauseRef = useRef(audio.pause);
  pauseRef.current = audio.pause;

  useEffect(() => {
    if (!loaded || !containerRef.current) return;
    let disposed = false;
    const iframe = document.createElement("iframe");
    iframe.title = "So What by Miles Davis on YouTube";
    iframe.src = `https://www.youtube-nocookie.com/embed/KJEzFvXx3Xw?playsinline=1&enablejsapi=1&origin=${encodeURIComponent(window.location.origin)}`;
    iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
    iframe.referrerPolicy = "strict-origin-when-cross-origin";
    iframe.allowFullscreen = true;
    containerRef.current.replaceChildren(iframe);
    void loadYouTubeAPI().then((api) => {
      if (disposed) return;
      playerRef.current = new api.Player(iframe, { events: {
        onReady: () => {},
        onStateChange: (event) => { if (event.data === 1) pauseRef.current(); },
        onError: (event) => {
          if (!disposed) {
            console.warn(`YouTube embedded player returned error ${event.data}`);
            setError("This recording is unavailable in the embedded player here.");
          }
        },
      } });
    }).catch(() => { if (!disposed) setError("The embedded player could not load here."); });
    return () => { disposed = true; playerRef.current?.destroy(); playerRef.current = null; };
  }, [loaded]);
  useEffect(() => { if (audio.playing && typeof playerRef.current?.pauseVideo === "function") playerRef.current.pauseVideo(); }, [audio.playing]);
  return <section className="reference-player" aria-label="So What reference recording">
    <div className="listen-label"><span className="listen-number">A</span><span>THE STARTING RECORDING</span><a href="https://www.youtube.com/watch?v=KJEzFvXx3Xw&list=PLCpBhVdBoDT9WC5BmBPKypiNfWr000JEy&index=61" target="_blank" rel="noreferrer">Open on YouTube <Icon name="arrow" /></a></div>
    <div className="video-area">{loaded
      ? <><div className={`youtube-player ${error ? "player-unavailable" : ""}`} ref={containerRef} />{error && <div className="video-fallback"><span>MILES DAVIS · SO WHAT</span><p>{error}</p><a href="https://www.youtube.com/watch?v=KJEzFvXx3Xw" target="_blank" rel="noreferrer">Listen on YouTube <Icon name="arrow" /></a></div>}</>
      : <button className="reference-cover" aria-label="Load So What YouTube player" onClick={() => { audio.pause(); setLoaded(true); }}>
        <div className="recording-title"><span>MILES DAVIS</span><strong>So What</strong><small>A recording to return to.</small></div>
        <svg className="record-ring" viewBox="0 0 240 240" aria-hidden="true"><circle cx="120" cy="120" r="116" /><circle cx="120" cy="120" r="98" /><circle cx="120" cy="120" r="80" /><circle cx="120" cy="120" r="62" /><circle cx="120" cy="120" r="44" /><circle cx="120" cy="120" r="10" /></svg>
        <span className="load-video"><span><Icon name="play" /></span>Load YouTube player</span>
      </button>}
    </div>
    <p className="reference-credit">Miles Davis · Official Artist Channel · Columbia/Legacy</p>
    {loaded && <p className="embed-help">If the player is unavailable, use “Open on YouTube” above.</p>}
  </section>;
}
