// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MarkupTextBox } from "./MarkupEditor";

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
