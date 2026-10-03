export function Icon({ name }: { name: "play" | "pause" | "back" | "next" | "menu" | "close" | "loop" | "volume" | "upload" | "arrow" | "note" }) {
  const paths = {
    play: <path d="m8 5 11 7-11 7z" fill="currentColor" stroke="none" />,
    pause: <><path d="M8 5v14M16 5v14" strokeWidth="4" /></>,
    back: <><path d="m13 5-7 7 7 7" /><path d="M6 12h15" /></>,
    next: <><path d="m11 5 7 7-7 7" /><path d="M3 12h15" /></>,
    menu: <><path d="M4 6h16M4 12h11M4 18h7" /></>,
    close: <path d="m5 5 14 14M19 5 5 19" />,
    loop: <><path d="M5 7h12a4 4 0 0 1 4 4v2M19 17H7a4 4 0 0 1-4-4v-2" /><path d="m14 4 3 3-3 3m-4 4-3 3 3 3" /></>,
    volume: <><path d="M4 9h4l5-4v14l-5-4H4z" /><path d="M17 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></>,
    upload: <><path d="M12 16V4m-5 5 5-5 5 5M4 16v4h16v-4" /></>,
    arrow: <path d="M5 19 19 5M5 5h14v14" />,
    note: <><path d="M9 17V5l11-2v12M9 7l11-2" /><ellipse cx="6" cy="18" rx="3" ry="2" /><ellipse cx="17" cy="16" rx="3" ry="2" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

export function Bloom() {
  return <svg className="bloom" viewBox="0 0 48 48" aria-hidden="true">
    {[0, 60, 120, 180, 240, 300].map((angle) => <ellipse key={angle} cx="24" cy="13" rx="6" ry="10" transform={`rotate(${angle} 24 24)`} fill="currentColor" />)}
    <circle cx="24" cy="24" r="4" fill="var(--paper)" />
  </svg>;
}
