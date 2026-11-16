// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MarkupAnnotation } from "@/lib/types";
import { MarkupEditor } from "./MarkupEditor";

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: vi.fn(() => ({ promise: new Promise(() => {}), destroy: vi.fn() })),
}));

const annotation = {
  id: "text-1", number: 1, kind: "free_text", page_no: 1,
  points: [{ x: 0.1, y: 0.2 }, { x: 0.4, y: 0.4 }], text: "نظر فارسی",
  color: "#123456", stroke_width: 2, font_size: 16,
} as MarkupAnnotation;

beforeEach(() => {
  vi.stubGlobal("PointerEvent", MouseEvent);
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => false);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("free text movement", () => {
  it.each(["انتخاب", "دست"])("moves text using %s and keeps its dimensions", (tool) => {
    const onChange = vi.fn();
    const { container } = render(<MarkupEditor data={new ArrayBuffer(1)} annotations={[annotation]} onChange={onChange} />);
    if (tool === "دست") fireEvent.click(screen.getByRole("button", { name: "دست" }));
    const rect = container.querySelector('rect[data-markup-id="text-1"]')!;
    expect(rect.getAttribute("fill")).toBe("transparent");
    const page = screen.getByLabelText("PDF page").parentElement!;
    vi.spyOn(page, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1000, height: 1000 } as DOMRect);
    fireEvent.pointerDown(rect, { clientX: 200, clientY: 300 });
    fireEvent.pointerMove(page, { clientX: 300, clientY: 400 });
    fireEvent.pointerUp(page, { clientX: 300, clientY: 400 });
    expect(onChange).toHaveBeenCalled();
    const moved = onChange.mock.calls.at(-1)![0][0].points;
    expect(moved[0].x).toBeCloseTo(0.2);
    expect(moved[0].y).toBeCloseTo(0.3);
    expect(moved[1].x).toBeCloseTo(0.5);
    expect(moved[1].y).toBeCloseTo(0.5);
  });

  it("still pans the page when the hand starts on the background", () => {
    const onChange = vi.fn();
    render(<MarkupEditor data={new ArrayBuffer(1)} annotations={[annotation]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "دست" }));
    const page = screen.getByLabelText("PDF page").parentElement!;
    const host = page.parentElement!;
    host.scrollLeft = 100;
    host.scrollTop = 100;
    fireEvent.pointerDown(page, { clientX: 200, clientY: 300 });
    fireEvent.pointerMove(page, { clientX: 220, clientY: 330 });
    fireEvent.pointerUp(page, { clientX: 220, clientY: 330 });
    expect(host.scrollLeft).toBe(80);
    expect(host.scrollTop).toBe(70);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("does not move annotations in read-only mode", () => {
    const onChange = vi.fn();
    const { container } = render(<MarkupEditor data={new ArrayBuffer(1)} annotations={[annotation]} onChange={onChange} readOnly />);
    const rect = container.querySelector('rect[data-markup-id="text-1"]')!;
    fireEvent.pointerDown(rect, { clientX: 200, clientY: 300 });
    fireEvent.pointerMove(rect, { clientX: 300, clientY: 400 });
    expect(onChange).not.toHaveBeenCalled();
  });
});
