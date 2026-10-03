import { parse } from "yaml";

export type PageKind = "question" | "concept" | "recording" | "reading" | "about";
export interface GardenPage {
  id: string;
  title: string;
  navTitle: string;
  description: string;
  kind: PageKind;
  stage: string;
  chapter?: string;
  order: number;
  body: string;
  links: string[];
}

export const chapters = [
  { id: "rhythm", label: "Rhythm", number: "01" },
  { id: "inside", label: "Inside the music", number: "02" },
  { id: "form", label: "Jazz forms", number: "03" },
  { id: "origins", label: "Origins", number: "04" },
  { id: "styles", label: "Styles", number: "05" },
  { id: "innovators", label: "Innovators", number: "06" },
  { id: "today", label: "Listening today", number: "07" },
];

const sources = import.meta.glob<string>("./pages/**/*.md", {
  query: "?raw", import: "default", eager: true,
});
const linkPattern = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
const kinds: PageKind[] = ["question", "concept", "recording", "reading", "about"];

export const pages: GardenPage[] = Object.entries(sources).map(([path, raw]) => {
  const id = path.replace(/^\.\/pages\//, "").replace(/\.md$/, "");
  const source = raw.replace(/\r\n/g, "\n");
  const match = source.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) throw new Error(`Missing frontmatter in ${path}`);
  const meta: unknown = parse(match[1]);
  if (!meta || typeof meta !== "object") throw new Error(`Invalid frontmatter in ${path}`);
  const value = meta as Record<string, unknown>;
  for (const field of ["title", "description", "kind", "stage"]) {
    if (typeof value[field] !== "string") throw new Error(`Missing ${field} in ${path}`);
  }
  if (!kinds.includes(value.kind as PageKind)) throw new Error(`Invalid page kind in ${path}`);
  const links = [...match[2].matchAll(linkPattern)].map((entry) => entry[1]);
  return {
    id, title: value.title as string,
    navTitle: typeof value.navTitle === "string" ? value.navTitle : value.title as string,
    description: value.description as string,
    kind: value.kind as PageKind, stage: value.stage as string,
    chapter: typeof value.chapter === "string" ? value.chapter : undefined,
    order: typeof value.order === "number" ? value.order : 0,
    body: match[2], links: [...new Set(links)],
  };
}).sort((a, b) => a.order - b.order || a.title.localeCompare(b.title));

export const pageById = new Map(pages.map((page) => [page.id, page]));
export const startingPageId = "starting-question";
for (const page of pages) {
  for (const target of page.links) {
    if (!pageById.has(target)) throw new Error(`Unknown page link [[${target}]] in ${page.id}`);
  }
  if (page.chapter && !chapters.some((chapter) => chapter.id === page.chapter)) {
    throw new Error(`Unknown chapter ${page.chapter} in ${page.id}`);
  }
}

export function pageHref(id: string): string { return `#/${id}`; }
export function linkedMarkdown(body: string): string {
  return body.replace(linkPattern, (_match, id: string, label?: string) =>
    `[${label ?? pageById.get(id)?.navTitle ?? id}](${pageHref(id)})`);
}
