export interface FileIdentity { name: string; size: number; lastModified: number }
export interface Observation {
  id: string;
  pageId: string;
  text: string;
  createdAt: string;
  kind: "observation" | "question";
  quote?: string;
  relatedPageId?: string;
  audio?: { file: FileIdentity; seconds: number };
}

const key = "jazzbloom.observations.v1";

export function sameFile(a: FileIdentity, b: FileIdentity): boolean {
  return a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
}

export function readObservations(): Observation[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter((item): item is Observation => {
      if (!item || typeof item !== "object") return false;
      const note = item as Partial<Observation>;
      if (typeof note.id !== "string" || typeof note.pageId !== "string" || typeof note.text !== "string"
        || typeof note.createdAt !== "string" || !["observation", "question"].includes(note.kind ?? "")) return false;
      if (note.quote !== undefined && typeof note.quote !== "string") return false;
      if (note.relatedPageId !== undefined && typeof note.relatedPageId !== "string") return false;
      if (note.audio !== undefined) {
        const audio = note.audio;
        if (!audio || typeof audio.seconds !== "number" || !Number.isFinite(audio.seconds) || audio.seconds < 0
          || !audio.file || typeof audio.file.name !== "string" || typeof audio.file.size !== "number"
          || typeof audio.file.lastModified !== "number") return false;
      }
      return true;
    });
  } catch { return []; }
}

export function saveObservations(notes: Observation[]): boolean {
  try { localStorage.setItem(key, JSON.stringify(notes)); return true; }
  catch { return false; }
}

export function exportObservations(notes: Observation[]): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify({
    schema: "jazzbloom.observations/v1", exportedAt: new Date().toISOString(),
    reference: "https://www.youtube.com/watch?v=KJEzFvXx3Xw", observations: notes,
  }, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url; link.download = `jazzbloom-observations-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
