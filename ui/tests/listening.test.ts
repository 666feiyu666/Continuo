import assert from "node:assert/strict";
import { test } from "node:test";
import { clampTime, seekPosition, validLoop } from "../src/listening/transport.ts";
import { readObservations, sameFile, saveObservations } from "../src/garden/observations.ts";

test("seeking stays within playable time, including invalid metadata", () => {
  assert.equal(clampTime(-2, 20), 0);
  assert.equal(clampTime(30, 20), 20);
  assert.equal(clampTime(NaN, 20), 0);
  assert.equal(clampTime(10, Infinity), 0);
});

test("a listening loop needs an ordered, finite interval inside the recording", () => {
  assert.equal(validLoop({ start: 1, end: 2 }, 20), true);
  for (const range of [{ start: 2, end: 1 }, { start: -1, end: 3 },
    { start: 1, end: 21 }, { start: 1, end: 1.1 }, { start: NaN, end: 4 }]) {
    assert.equal(validLoop(range, 20), false);
  }
  assert.equal(seekPosition(8, 20, { start: 1, end: 2 }), 1);
  assert.equal(seekPosition(1.5, 20, { start: 1, end: 2 }), 1.5);
});

test("a timestamp cannot silently switch to another similarly named file", () => {
  const original = { name: "study.mp3", size: 100, lastModified: 20 };
  assert.equal(sameFile(original, { ...original }), true);
  assert.equal(sameFile(original, { ...original, size: 101 }), false);
  assert.equal(sameFile(original, { ...original, lastModified: 21 }), false);
});

test("saved observations round-trip, malformed entries are excluded", () => {
  const entries = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => entries.set(key, value),
  } });
  const note = { id: "one", pageId: "starting-question", text: "An open question",
    kind: "question" as const, createdAt: "2026-10-03T10:00:00Z",
    audio: { file: { name: "study.mp3", size: 100, lastModified: 20 }, seconds: 2.5 } };
  assert.equal(saveObservations([note]), true);
  assert.deepEqual(readObservations(), [note]);
  const key = [...entries.keys()][0];
  entries.set(key, JSON.stringify([note, { ...note, audio: { ...note.audio, seconds: -1 } },
    { ...note, relatedPageId: 42 }, null, "invalid"]));
  assert.deepEqual(readObservations(), [note]);
  entries.set(key, "broken-json");
  assert.deepEqual(readObservations(), []);
});

test("unavailable browser storage is reported without crashing the page", () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: () => { throw new Error("Storage blocked"); },
    setItem: () => { throw new Error("Storage blocked"); },
  } });
  assert.deepEqual(readObservations(), []);
  assert.equal(saveObservations([]), false);
});
