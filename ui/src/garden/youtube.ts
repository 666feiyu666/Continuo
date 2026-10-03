interface YouTubePlayer {
  pauseVideo: () => void;
  destroy: () => void;
}
interface YouTubeAPI {
  Player: new (iframe: HTMLIFrameElement, options: {
    events: {
      onReady: () => void;
      onStateChange: (event: { data: number }) => void;
      onError: (event: { data: number }) => void;
    };
  }) => YouTubePlayer;
}
declare global {
  interface Window {
    YT?: YouTubeAPI;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let pending: Promise<YouTubeAPI> | undefined;
export function loadYouTubeAPI(): Promise<YouTubeAPI> {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (pending) return pending;
  pending = new Promise((resolve, reject) => {
    const previous = window.onYouTubeIframeAPIReady;
    const timeout = window.setTimeout(() => reject(new Error("The YouTube player could not load here.")), 12000);
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      window.clearTimeout(timeout);
      if (window.YT?.Player) resolve(window.YT);
      else reject(new Error("The YouTube player is unavailable."));
    };
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.async = true;
    script.referrerPolicy = "strict-origin-when-cross-origin";
    script.onerror = () => { window.clearTimeout(timeout); reject(new Error("The YouTube player could not load here.")); };
    document.head.append(script);
  });
  return pending;
}
export type { YouTubePlayer };
