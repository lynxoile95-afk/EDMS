import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { TransmittalDetail } from "@/lib/types";
import { ManualTransmittalPage } from "./ManualTransmittal";

const { mocks, projectFixture } = vi.hoisted(() => ({
  mocks: {
    get: vi.fn(),
    post: vi.fn(),
    postForm: vi.fn(),
  },
  projectFixture: {
    id: "project-1",
    code: "PRJ-1",
    name: "پروژه آزمون",
    client_name: "کارفرما",
    contractor_name: "پیمانکار",
    contractor_code: "PC",
    parent_company_code: "MC",
    description: null,
    is_active: true,
    created_at: "2026-10-24T00:00:00.000Z",
    settings: {},
    member_role: "dcc",
  },
}));

vi.mock("@/lib/project", () => ({
  useProject: () => ({ project: projectFixture, isDcc: true }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: mocks.get,
    post: mocks.post,
    postForm: mocks.postForm,
  },
  ApiError: class ApiError extends Error {
    detail = this.message;
  },
}));

const item = {
  id: "mdl-1",
  project_id: "project-1",
  doc_code: "DOC-1",
  title: "مدرک آزمون",
  doc_type: "DR",
  current_rev: "A",
  rev_seq: 1,
  status: "active",
  weight: 1,
  page_count: 2,
  due_date: null,
  planned_issue_date: null,
  is_active: true,
  discipline_id: null,
  unit_id: null,
  discipline_code: "CIV",
  unit_code: null,
  revision_count: 1,
  phase: null,
  serial_no: null,
  category: null,
  doc_class: null,
  basic_detail: null,
  poi: null,
  remark: null,
};

const member = {
  id: "membership-1",
  user_id: "user-approver",
  full_name: "تأییدکننده آزمون",
  email: "approver@example.test",
  national_id: null,
  mobile: null,
  role: "project_manager",
  discipline_id: null,
  discipline_code: null,
  discipline_name: null,
  is_active: true,
};

function createdDetail(): TransmittalDetail {
  return {
    id: "transmittal-1",
    lines: [{ id: "line-1", mdl_item_id: item.id }],
  } as unknown as TransmittalDetail;
}

function renderPage() {
  cleanup();
  return render(
    <MemoryRouter initialEntries={["/transmittals/new"]}>
      <Routes>
        <Route path="/transmittals/new" element={<ManualTransmittalPage />} />
        <Route path="/transmittals/:transmittalId" element={<div>صفحه پیش‌نویس</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.get.mockImplementation((path: string) => {
    if (path === "/projects/project-1/mdl/items?size=200") return Promise.resolve({ items: [item], total: 1 });
    if (path === "/projects/project-1/members") return Promise.resolve([member]);
    throw new Error(`unexpected GET ${path}`);
  });
  mocks.post.mockResolvedValue(createdDetail());
  mocks.postForm.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ManualTransmittalPage", () => {
  async function selectOne() {
    fireEvent.click(await screen.findByRole("checkbox", { name: "انتخاب DOC-1" }));
    fireEvent.change(screen.getByLabelText("تأیید کننده نهایی"), { target: { value: member.user_id } });
  }

  it("creates and navigates a draft when the selected row has zero attachments", async () => {
    renderPage();
    await selectOne();

    fireEvent.click(screen.getByRole("button", { name: "ترانسمیتال جدید" }));

    expect(await screen.findByText("صفحه پیش‌نویس")).toBeTruthy();
    expect(mocks.postForm).not.toHaveBeenCalled();
    expect(mocks.post).toHaveBeenCalledWith(
      "/projects/project-1/transmittals/manual",
      expect.objectContaining({ rows: [expect.objectContaining({ mdl_item_id: item.id })] }),
    );
  });

  it("uploads multiple files in parallel before navigating", async () => {
    renderPage();
    await selectOne();
    const files = [
      new File(["pdf-one"], "one.pdf", { type: "application/pdf" }),
      new File(["pdf-two"], "two.pdf", { type: "application/pdf" }),
    ];
    const fileInput = document.querySelector<HTMLInputElement>("input[type=\"file\"][multiple]");
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, { target: { files } });

    fireEvent.click(screen.getByRole("button", { name: "ترانسمیتال جدید" }));

    await waitFor(() => expect(mocks.postForm).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("صفحه پیش‌نویس")).toBeTruthy();
    expect(mocks.postForm.mock.calls.map((call) => call[0])).toEqual([
      "/transmittals/transmittal-1/attachments",
      "/transmittals/transmittal-1/attachments",
    ]);
  });

  it("keeps the created draft visible and reports a partial upload failure", async () => {
    mocks.postForm
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error("پیوست دوم معتبر نیست."));
    renderPage();
    await selectOne();
    const fileInput = document.querySelector<HTMLInputElement>("input[type=\"file\"][multiple]");
    expect(fileInput).not.toBeNull();
    fireEvent.change(fileInput!, {
      target: {
        files: [
          new File(["pdf-one"], "one.pdf", { type: "application/pdf" }),
          new File(["bad"], "two.pdf", { type: "application/pdf" }),
        ],
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "ترانسمیتال جدید" }));

    await waitFor(() => expect(mocks.postForm).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/پیش‌نویس transmittal-1 ایجاد شد، اما/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "ترانسمیتال جدید" })).toBeTruthy();
    expect(screen.getByText("مشاهده پیش‌نویس")).toBeTruthy();
    expect(screen.queryByText("صفحه پیش‌نویس")).toBeNull();
  });
});
