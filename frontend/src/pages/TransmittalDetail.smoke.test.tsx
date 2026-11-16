import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TransmittalDetail } from "./TransmittalDetail";

const id = "5eaab996-d13c-45d5-bb4b-a37fa7af694d";
const revisionId = "3a621cca-fe67-4c6c-a304-a4bfe7749123";
const itemId = "76cb219f-9136-4160-aeac-2790b8fefb1f";
const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { id: "f7655b35-a534-4339-838e-23ace13a1634", full_name: "مدیر سیستم", is_superuser: true },
    hasRole: () => true,
  }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: apiGet,
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    postForm: vi.fn(),
    patchForm: vi.fn(),
    download: vi.fn(),
    downloadBlob: vi.fn(),
    blobUrl: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    detail = this.message;
  },
}));

vi.mock("@/lib/markups", () => ({
  createMarkupSession: vi.fn(),
  downloadMarkupOutput: vi.fn(),
  replyToMarkup: vi.fn(),
  saveMarkupAnnotations: vi.fn(),
  submitMarkupSession: vi.fn(),
  toMarkupAnnotationInputs: vi.fn((annotations) => annotations),
}));

vi.mock("@/components/MarkupEditor", () => ({
  MarkupEditor: () => <div data-testid="markup-editor" />,
}));

const detail = {
  id,
  project_id: "9b30e4f3-e5ba-4c00-bdd2-e7a617b8d86a",
  access_circle_id: null,
  access_circle_name: null,
  number: null,
  type: "OT",
  purpose: "IFR",
  status: "draft",
  from_org: "پیمانکار نمونه",
  to_org: "کارفرمای نمونه",
  due_date: "2026-12-06",
  issue_date: "2026-10-24",
  issued_at: null,
  sender_id: "f7655b35-a534-4339-838e-23ace13a1634",
  created_by_id: "f7655b35-a534-4339-838e-23ace13a1634",
  created_by_name: "مدیر سیستم",
  issued_by_id: null,
  issued_by_name: null,
  final_approver_id: "6626d222-2edc-4f7b-9cf3-df5da3b2208b",
  final_approver_name: "مدیر پروژه",
  manual_workflow: true,
  remarks: null,
  meta: { due_date_jalali: "1405/09/15", issued_at_jalali: null },
  stage: "draft",
  created_at: "2026-11-16T05:15:55",
  line_count: 1,
  recipient_count: 1,
  acknowledged_count: 0,
  document_code: "7100-00-EB-EL-SPE-7050",
  document_title: "Specification For Electrical Heat Tracing System",
  document_type: "SPE",
  line_action: "info",
  lines: [{
    id: "b33f2745-d0ac-4c75-9875-adda8132dae8",
    line_no: 1,
    revision_id: revisionId,
    mdl_item_id: itemId,
    action: "info",
    status: "draft",
    doc_status: "No Comment",
    doc_percent: null,
    poi: "IFI",
    due_date: "2026-12-06",
    note: null,
    doc_code: "7100-00-EB-EL-SPE-7050",
    title: "Specification For Electrical Heat Tracing System",
    rev: "A0",
    page_count: 1,
    discipline_code: "ELE",
  }],
  recipients: [],
  referrals: [],
  attachments: [],
  pdf_url: null,
  preview_number: "TM-DEMO-Temp0001",
  stage_label: "پیش‌نویس",
  is_final: false,
  reply_sheet: null,
  flow_history: [],
  os_available: false,
  os_transmittal_id: null,
};

describe("TransmittalDetail smoke render", () => {
  beforeEach(() => {
    apiGet.mockImplementation((path: string) => {
      if (path === `/transmittals/${id}`) return Promise.resolve(detail);
      if (path === "/system/capabilities") return Promise.resolve({ review_markup_enabled: true });
      if (path === `/revisions/${revisionId}/comment-lists`) return Promise.resolve([]);
      if (path === `/mdl-items/${itemId}/document-records`) return Promise.resolve([]);
      if (path === `/transmittals/${id}/reply-sheet`) return Promise.resolve([]);
      if (path === `/transmittals/${id}/reply-sheet/record`) return Promise.resolve(null);
      throw new Error(`unexpected GET ${path}`);
    });
  });

  it("renders a newly created draft instead of crashing", async () => {
    render(
      <MemoryRouter initialEntries={[`/transmittals/${id}`]}>
        <Routes>
          <Route path="/transmittals/:transmittalId" element={<TransmittalDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getAllByText(/Transmittal Workspace/).length).toBeGreaterThan(0));
  });
});
