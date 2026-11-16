import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { MarkupAnnotation, MarkupSession } from "@/lib/types";
import { MarkupSessionPage } from "./MarkupSession";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  get: vi.fn(),
  save: vi.fn(),
  submit: vi.fn(),
  reply: vi.fn(),
  download: vi.fn(),
  blobData: vi.fn(),
}));

vi.mock("@/lib/markups", () => ({
  createMarkupSession: mocks.create,
  getMarkupSession: mocks.get,
  saveMarkupAnnotations: mocks.save,
  submitMarkupSession: mocks.submit,
  replyToMarkup: mocks.reply,
  downloadMarkupOutput: mocks.download,
  toMarkupAnnotationInputs: (annotations: MarkupAnnotation[], persisted: MarkupAnnotation[]) => {
    const persistedIds = new Set(persisted.map((annotation) => annotation.id));
    return annotations.map((annotation) => ({
      ...(persistedIds.has(annotation.id) ? { id: annotation.id } : {}),
      kind: annotation.kind,
      page_no: annotation.page_no,
      points: annotation.points,
      text: annotation.text,
      color: annotation.color,
    }));
  },
}));

vi.mock("@/lib/api", () => ({
  api: { blobData: mocks.blobData },
  ApiError: class ApiError extends Error {
    detail: string;
    constructor(_status: number, detail: string) {
      super(detail);
      this.detail = detail;
    }
  },
}));

vi.mock("@/components/MarkupEditor", () => ({
  MarkupEditor: ({ annotations, onChange }: { annotations: MarkupAnnotation[]; onChange: (next: MarkupAnnotation[]) => void }) => (
    <button
      type="button"
      aria-label="mock add markup"
      onClick={() => onChange([
        ...annotations,
        {
          id: `local-annotation-${annotations.length + 1}`,
          number: annotations.length + 1,
          kind: "sticky_note",
          page_no: 1,
          points: [{ x: 0.1, y: 0.1 }, { x: 0.2, y: 0.2 }],
          text: "نظر محلی",
          color: "#d64545",
          author_id: null,
          author_name: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          reply_status: "open",
          reply_text: null,
          replied_by_id: null,
          replied_by_name: null,
          replied_at: null,
        },
      ])}
    >
      mock add markup
    </button>
  ),
}));

function session(overrides: Partial<MarkupSession> = {}): MarkupSession {
  return {
    id: "session-1",
    transmittal_id: "transmittal-1",
    project_id: "project-1",
    source_file_id: "source-1",
    revision_id: "revision-1",
    file_id: "file-1",
    document_code: "DOC-1",
    revision_label: "A",
    original_filename: "drawing.pdf",
    original_sha256: "hash",
    page_count: 1,
    status: "draft",
    draft_version: 0,
    last_saved_at: null,
    submitted_at: null,
    author_id: "user-1",
    author_name: "نویسنده",
    can_edit: true,
    can_reply: false,
    submitted_pdf_attachment_id: null,
    comment_sheet_attachment_id: null,
    outputs: [],
    annotations: [],
    ...overrides,
  };
}

function renderPage(entry = "/markups/new?transmittal_id=transmittal-1&file_id=file-1") {
  cleanup();
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/markups/new" element={<MarkupSessionPage />} />
        <Route path="/markups/:sessionId" element={<MarkupSessionPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

beforeEach(() => {
  mocks.create.mockResolvedValue(session());
  mocks.get.mockResolvedValue(session());
  mocks.blobData.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(16) });
  mocks.save.mockImplementation(async (_id: string, annotations: MarkupAnnotation[]) => session({
    draft_version: 1,
    annotations: annotations.map((annotation, index) => ({
      ...annotation,
      id: annotation.id ?? `server-annotation-${index + 1}`,
      number: index + 1,
      text: annotation.text ?? null,
      author_id: null,
      author_name: "نویسنده",
      created_at: annotation.created_at ?? new Date().toISOString(),
      updated_at: new Date().toISOString(),
      reply_status: "open",
      reply_text: null,
      replied_by_id: null,
      replied_by_name: null,
      replied_at: null,
    })),
  }));
  mocks.submit.mockResolvedValue({
    session: session({ status: "submitted", can_edit: false }),
    outputs: [
      { attachment_id: "output-1", filename: "drawing_COMMENT_user_20261024.pdf", kind: "pdf", download_url: "/api/v1/transmittals/transmittal-1/attachments/output-1/download" },
      { attachment_id: "output-2", filename: "drawing_COMMENT_SHEET_user_20261024.pdf", kind: "sheet", download_url: "/api/v1/transmittals/transmittal-1/attachments/output-2/download" },
    ],
    annotation_count: 1,
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("MarkupSessionPage", () => {
  it("keeps a local draft backup and warns before unload", async () => {
    renderPage();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    fireEvent.click(await screen.findByRole("button", { name: "mock add markup" }));
    expect(setItem).toHaveBeenCalledWith("edms-markup-draft:session-1", expect.stringContaining("نظر محلی"));

    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it("reloads the local backup, saves it, and exposes submitted attachments", async () => {
    const first = renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "mock add markup" }));
    await waitFor(() => expect(window.localStorage.getItem("edms-markup-draft:session-1")).not.toBeNull());
    first.unmount();

    renderPage();
    expect(await screen.findByText(/نسخه محلی ذخیره‌نشده بازیابی شد/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "ذخیره دستی" })).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: /ثبت نهایی و تولید Comment/ }).at(-1)!);
    await waitFor(() => expect(mocks.save).toHaveBeenCalled());
    expect(mocks.save.mock.calls[0][1][0]).not.toHaveProperty("id");
    await waitFor(() => expect(mocks.submit).toHaveBeenCalledWith("session-1"));
    expect(await screen.findByRole("button", { name: "دریافت PDF نشانه‌گذاری" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "دریافت Comment Sheet" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("dialog", { name: /Comment Sheet/ })).toBeTruthy();
  });
});
