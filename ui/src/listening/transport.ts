export interface LoopRange { start: number; end: number }

export function clampTime(time: number, duration: number): number {
  return Math.max(0, Math.min(Number.isFinite(duration) ? duration : 0, Number.isFinite(time) ? time : 0));
}

export function validLoop(range: LoopRange, duration: number): boolean {
  return Number.isFinite(range.start) && Number.isFinite(range.end)
    && range.start >= 0 && range.end <= duration + 0.05 && range.end - range.start >= 0.25;
}

export function seekPosition(time: number, duration: number, loop: LoopRange | null): number {
  const position = clampTime(time, duration);
  return loop && validLoop(loop, duration) && (position < loop.start || position >= loop.end)
    ? loop.start : position;
}

export function formatTime(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  return `${Math.floor(safe / 60)}:${String(Math.floor(safe % 60)).padStart(2, "0")}`;
}
