import { describe, expect, it } from "vitest";
import type { MarkupAnnotation } from "./types";
import { normalizeMarkupColor, toMarkupAnnotationInputs } from "./markups";

function annotation(id: string, number: number): MarkupAnnotation {
  return {
    id,
    number,
    kind: "sticky_note",
    page_no: 1,
    points: [{ x: 0.1, y: 0.2 }, { x: 0.3, y: 0.4 }],
    text: `نظر ${number}`,
    color: "#d64545",
    author_id: null,
    author_name: null,
    created_at: "2026-10-24T00:00:00.000Z",
    updated_at: "2026-10-24T00:00:00.000Z",
    reply_status: "open",
    reply_text: null,
    replied_by_id: null,
    replied_by_name: null,
    replied_at: null,
  };
}

describe("toMarkupAnnotationInputs", () => {
  it("keeps IDs returned by the session and omits IDs created only in the browser", () => {
    const persisted = annotation("server-annotation-1", 1);
    const local = annotation("local-annotation-2", 2);
    const [persistedInput, localInput] = toMarkupAnnotationInputs([persisted, local], [persisted]);

    expect(persistedInput).toMatchObject({ id: "server-annotation-1", text: "نظر 1", stroke_width: 2.5 });
    expect(localInput).not.toHaveProperty("id");
    expect(localInput).toMatchObject({ kind: "sticky_note", page_no: 1, text: "نظر 2", stroke_width: 2.5 });
  });

  it("replaces legacy white strokes with a visible default", () => {
    expect(normalizeMarkupColor("#ffffff")).toBe("#d64545");
    expect(normalizeMarkupColor("#1f4e79")).toBe("#1f4e79");
  });
});
