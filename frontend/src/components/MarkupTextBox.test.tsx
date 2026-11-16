// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarkupTextBox, sizeStickyNote } from "./MarkupEditor";

describe("MarkupTextBox", () => {
  afterEach(cleanup);
  it.each(["sticky_note", "free_text", "callout"] as const)("renders %s text in page-sized coordinates and reflows on resize", (kind) => {
    const props = { text: "متن فارسی برای آزمایش کامنت", color: "#123456", fontSize: 16, kind, viewport: { width: 600, height: 800 } };
    const { container, rerender, getByText } = render(<svg viewBox="0 0 1 1"><MarkupTextBox {...props} box={{ x: 0.1, y: 0.2, w: 0.5, h: 0.25 }} /></svg>);
    const nested = container.querySelector("svg svg")!;
    expect(nested.getAttribute("viewBox")).toBe("0 0 300 200");
    expect(container.querySelector("foreignObject")?.getAttribute("width")).toBe("100%");
    expect(getByText(props.text).style.overflow).toBe("hidden");
    expect(getByText(props.text).style.whiteSpace).toBe("pre-wrap");
    rerender(<svg viewBox="0 0 1 1"><MarkupTextBox {...props} box={{ x: 0.1, y: 0.2, w: 0.25, h: 0.125 }} /></svg>);
    expect(nested.getAttribute("viewBox")).toBe("0 0 150 100");
    expect(getByText(props.text)).toBeTruthy();
  });
});

describe("automatic sticky note sizing", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("fits measured text, preserves a larger drawn box and stays within page edges", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ height: 120 } as DOMRect);
    const viewport = { width: 600, height: 800 };
    const points = sizeStickyNote([{ x: 0.98, y: 0.98 }, { x: 0.98, y: 0.98 }], "متن طولانی", 16, viewport);
    expect(points[1].x).toBeCloseTo(1);
    expect(points[1].y).toBeCloseTo(1);
    expect(points[1].x - points[0].x).toBeCloseTo(0.5);
    expect(points[1].y - points[0].y).toBeCloseTo(122 / 800);
    const drawn = sizeStickyNote([{ x: 0.1, y: 0.1 }, { x: 0.8, y: 0.5 }], "متن", 16, viewport);
    expect(drawn[1].x - drawn[0].x).toBeCloseTo(0.7);
    expect(drawn[1].y - drawn[0].y).toBeCloseTo(0.4);
  });

  it("caps very long text at the page boundaries", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ height: 2000 } as DOMRect);
    const points = sizeStickyNote([{ x: 0.5, y: 0.5 }, { x: 0.5, y: 0.5 }], "متن", 16, { width: 600, height: 800 });
    expect(points[0].y).toBe(0);
    expect(points[1].y).toBe(1);
  });
});
