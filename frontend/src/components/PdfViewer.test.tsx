import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as pdfjs from "pdfjs-dist";
import { PdfViewer } from "./PdfViewer";

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  PasswordResponses: { NEED_PASSWORD: 1 },
  getDocument: vi.fn(),
}));

const getDocumentMock = pdfjs.getDocument as unknown as ReturnType<typeof vi.fn>;

afterEach(() => {
  vi.clearAllMocks();
});

describe("PdfViewer", () => {
  it("renders a multi-page document, navigates, zooms, and destroys it on unmount", async () => {
    const renderTask = {
      promise: Promise.resolve(),
      cancel: vi.fn(),
    };
    const page = {
      getViewport: vi.fn(({ scale }: { scale: number }) => ({ width: 612 * scale, height: 792 * scale })),
      render: vi.fn(() => renderTask),
      cleanup: vi.fn(),
    };
    const document = {
      numPages: 2,
      getPage: vi.fn(async () => page),
      destroy: vi.fn(async () => undefined),
    };
    const loadingTask = {
      promise: Promise.resolve(document),
      destroy: vi.fn(async () => undefined),
      onPassword: undefined,
    };
    getDocumentMock.mockReturnValue(loadingTask);
    const onPageChange = vi.fn();
    Object.defineProperty(window, "devicePixelRatio", { configurable: true, value: 2 });

    const view = render(<PdfViewer data={new ArrayBuffer(16)} onPageChange={onPageChange} />);

    await waitFor(() => expect(screen.getByText("صفحه ۱ از ۲")).toBeTruthy());
    expect(getDocumentMock).toHaveBeenCalledWith(
      expect.objectContaining({
        cMapPacked: true,
        cMapUrl: expect.stringContaining("/pdfjs/cmaps/"),
        standardFontDataUrl: expect.stringContaining("/pdfjs/standard_fonts/"),
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "بعدی" }));
    expect(onPageChange).toHaveBeenCalledWith(2);
    fireEvent.click(screen.getByRole("button", { name: "+" }));
    await waitFor(() => expect(page.render).toHaveBeenCalledTimes(2));

    const canvas = screen.getByLabelText("PDF page") as HTMLCanvasElement;
    const cssWidth = Number.parseFloat(canvas.style.width);
    const cssHeight = Number.parseFloat(canvas.style.height);
    expect(cssWidth).toBeGreaterThan(0);
    expect(cssHeight / cssWidth).toBeCloseTo(792 / 612, 3);
    expect(canvas.width).toBeGreaterThan(cssWidth);

    view.unmount();
    expect(document.destroy).toHaveBeenCalled();
    expect(loadingTask.destroy).toHaveBeenCalled();
    expect(renderTask.cancel).toHaveBeenCalled();
  });
});
