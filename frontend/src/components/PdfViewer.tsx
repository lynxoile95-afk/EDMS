/**
 * Authenticated, display-only PDF viewer.
 *
 * PDF.js receives the original bytes as ArrayBuffer/Blob data. It never writes
 * back to the attachment. The browser-facing canvas is deliberately isolated
 * from the application's RTL/global typography rules; only this component is
 * LTR because PDF page coordinates and canvas sizing are LTR concerns.
 */

import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Button, Spinner } from "./ui";
import { persianDigits } from "@/lib/format";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const PDF_ASSET_BASE = `${import.meta.env.BASE_URL.endsWith("/") ? import.meta.env.BASE_URL : `${import.meta.env.BASE_URL}/`}pdfjs/`;
const MIN_ZOOM_MULTIPLIER = 0.5;
const MAX_ZOOM_MULTIPLIER = 3;
const MAX_DEVICE_PIXEL_RATIO = 2;

type PdfData = ArrayBuffer | Uint8Array;

function isRenderCancellation(error: unknown): boolean {
  return error instanceof Error && error.name === "RenderingCancelledException";
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}

export function PdfViewer({
  url,
  data,
  page,
  onPageChange,
  highlight,
  onOpenOriginal,
}: {
  /** Blob URL fallback for older callers and for the original-file fallback. */
  url?: string;
  /** Authenticated original bytes. PDF.js uses this instead of issuing a URL request. */
  data?: PdfData;
  page?: number;
  onPageChange?: (page: number) => void;
  highlight?: number[] | null;
  onOpenOriginal?: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const loadingTaskRef = useRef<ReturnType<typeof pdfjs.getDocument> | null>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const pageWidthRef = useRef(612);
  const renderSequenceRef = useRef(0);
  const [pageCount, setPageCount] = useState(0);
  const [current, setCurrent] = useState(page ?? 1);
  const [fitScale, setFitScale] = useState(1);
  const [zoomMultiplier, setZoomMultiplier] = useState(1);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sourceIsAvailable = Boolean(data || url);
  const scale = clamp(fitScale * zoomMultiplier, 0.25, 6);

  useEffect(() => {
    let disposed = false;
    const previousRender = renderTaskRef.current;
    previousRender?.cancel();
    renderTaskRef.current = null;
    const previousDocument = documentRef.current;
    documentRef.current = null;
    if (previousDocument) void previousDocument.destroy();
    const previousLoadingTask = loadingTaskRef.current;
    loadingTaskRef.current = null;
    if (previousLoadingTask) void previousLoadingTask.destroy();

    setLoading(true);
    setRendering(false);
    setError(null);
    setPageCount(0);
    setCurrent(page ?? 1);
    setZoomMultiplier(1);

    if (!sourceIsAvailable) {
      setLoading(false);
      setError("داده فایل PDF برای نمایش آماده نیست.");
      return () => {
        disposed = true;
      };
    }

    const loadingTask = pdfjs.getDocument({
      ...(data ? { data } : { url: url as string }),
      cMapUrl: `${PDF_ASSET_BASE}cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${PDF_ASSET_BASE}standard_fonts/`,
      useWorkerFetch: true,
      isEvalSupported: true,
    } as Parameters<typeof pdfjs.getDocument>[0]);
    loadingTask.onPassword = (callback: (password: string) => void, reason: number) => {
      if (disposed) return;
      setError(
        reason === pdfjs.PasswordResponses.NEED_PASSWORD
          ? "این فایل PDF با رمز محافظت شده است و بدون رمز قابل نمایش نیست."
          : "رمز فایل PDF صحیح نیست یا فایل قابل بازکردن نیست.",
      );
      callback("");
    };
    loadingTaskRef.current = loadingTask;

    void loadingTask.promise
      .then(async (document) => {
        if (disposed) {
          void document.destroy();
          return;
        }
        documentRef.current = document;
        const firstPage = await document.getPage(1);
        if (disposed) {
          void document.destroy();
          return;
        }
        pageWidthRef.current = firstPage.getViewport({ scale: 1 }).width;
        setPageCount(document.numPages);
        setCurrent((previous) => clamp(page ?? previous, 1, document.numPages));
      })
      .catch((reason: unknown) => {
        if (!disposed && !isRenderCancellation(reason)) {
          setError("نمایش PDF ممکن نشد؛ فایل ممکن است خراب یا رمزگذاری شده باشد.");
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });

    return () => {
      disposed = true;
      renderSequenceRef.current += 1;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      const document = documentRef.current;
      documentRef.current = null;
      if (document) void document.destroy();
      const task = loadingTaskRef.current;
      loadingTaskRef.current = null;
      if (task) void task.destroy();
    };
  }, [data, page, sourceIsAvailable, url]);

  useEffect(() => {
    if (page && page !== current && pageCount > 0) {
      setCurrent(clamp(page, 1, pageCount));
    }
  }, [current, page, pageCount]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const updateFitScale = () => {
      const availableWidth = Math.max(container.clientWidth - 32, 240);
      setFitScale(clamp(availableWidth / pageWidthRef.current, 0.25, 3));
    };
    updateFitScale();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateFitScale);
    observer?.observe(container);
    return () => observer?.disconnect();
  }, [pageCount]);

  useEffect(() => {
    const document = documentRef.current;
    const canvas = canvasRef.current;
    if (!document || !canvas || pageCount === 0) return;

    const sequence = renderSequenceRef.current + 1;
    renderSequenceRef.current = sequence;
    renderTaskRef.current?.cancel();
    renderTaskRef.current = null;
    setRendering(true);
    setError(null);
    let cancelled = false;
    let pdfPage: PDFPageProxy | null = null;
    let renderTask: RenderTask | null = null;

    void (async () => {
      try {
        pdfPage = await document.getPage(current);
        if (cancelled || renderSequenceRef.current !== sequence) return;

        const viewport = pdfPage.getViewport({ scale });
        const devicePixelRatio = clamp(window.devicePixelRatio || 1, 1, MAX_DEVICE_PIXEL_RATIO);
        canvas.width = Math.max(1, Math.ceil(viewport.width * devicePixelRatio));
        canvas.height = Math.max(1, Math.ceil(viewport.height * devicePixelRatio));
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        canvas.style.maxWidth = "none";
        canvas.style.display = "block";
        canvas.setAttribute("dir", "ltr");

        const context = canvas.getContext("2d", { alpha: false });
        if (!context) throw new Error("canvas context unavailable");
        context.setTransform(1, 0, 0, 1, 0, 0);
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, canvas.width, canvas.height);
        renderTask = pdfPage.render({
          canvasContext: context,
          viewport,
          transform: devicePixelRatio === 1 ? undefined : [devicePixelRatio, 0, 0, devicePixelRatio, 0, 0],
        });
        renderTaskRef.current = renderTask;
        await renderTask.promise;
      } catch (reason: unknown) {
        if (!cancelled && renderSequenceRef.current === sequence && !isRenderCancellation(reason)) {
          setError("رسم صفحه PDF ناموفق بود؛ از بازکردن نسخه اصلی استفاده کنید.");
        }
      } finally {
        if (renderTaskRef.current === renderTask) renderTaskRef.current = null;
        if (pdfPage) pdfPage.cleanup();
        if (!cancelled && renderSequenceRef.current === sequence) setRendering(false);
      }
    })();

    return () => {
      cancelled = true;
      renderTask?.cancel();
      if (renderTaskRef.current === renderTask) renderTaskRef.current = null;
    };
  }, [current, pageCount, scale]);

  const go = (next: number) => {
    const bounded = clamp(next, 1, Math.max(pageCount, 1));
    setCurrent(bounded);
    onPageChange?.(bounded);
  };

  const openOriginal = () => {
    if (onOpenOriginal) onOpenOriginal();
    else if (url) window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <div
      className="flex h-full min-h-0 flex-col bg-[#e9edf0] text-[11px]"
      dir="ltr"
      style={{ direction: "ltr", unicodeBidi: "isolate", letterSpacing: "normal", fontFeatureSettings: "normal" }}
    >
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2" dir="ltr">
        <div className="flex items-center gap-1">
          <Button variant="ghost" onClick={() => go(current - 1)} disabled={loading || current <= 1}>
            قبلی
          </Button>
          <span className="min-w-[86px] text-center text-xs text-slate-600" dir="rtl">
            صفحه {persianDigits(current)} از {persianDigits(pageCount || 1)}
          </span>
          <Button variant="ghost" onClick={() => go(current + 1)} disabled={loading || current >= pageCount}>
            بعدی
          </Button>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" onClick={() => setZoomMultiplier(1)} disabled={loading}>
            عرض کامل
          </Button>
          <Button variant="ghost" onClick={() => setZoomMultiplier((value) => clamp(value - 0.2, MIN_ZOOM_MULTIPLIER, MAX_ZOOM_MULTIPLIER))} disabled={loading}>
            −
          </Button>
          <span className="min-w-[45px] text-center text-xs text-slate-600" dir="ltr">
            {persianDigits(Math.round(scale * 100))}٪
          </span>
          <Button variant="ghost" onClick={() => setZoomMultiplier((value) => clamp(value + 0.2, MIN_ZOOM_MULTIPLIER, MAX_ZOOM_MULTIPLIER))} disabled={loading}>
            +
          </Button>
        </div>
      </div>

      <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-auto bg-slate-200/60 p-4" dir="ltr">
        {loading ? <Spinner label="در حال بارگذاری PDF…" /> : null}
        {rendering && !loading ? <Spinner label="در حال رسم صفحه…" /> : null}
        {error ? (
          <div className="mx-auto max-w-xl rounded border border-rose-200 bg-rose-50 p-3 text-center" dir="rtl">
            <p className="mb-2 text-sm text-rose-700">{error}</p>
            <Button variant="primary" onClick={openOriginal}>
              باز کردن نسخه اصلی
            </Button>
          </div>
        ) : null}
        <div
          className={error ? "hidden" : "relative mx-auto w-max bg-white shadow-sm"}
          dir="ltr"
          style={{ direction: "ltr", unicodeBidi: "isolate" }}
        >
          <canvas ref={canvasRef} aria-label="PDF page" />
          {highlight && highlight.length === 4 ? (
            <div
              className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/20"
              style={{
                right: `${(1 - highlight[0] - highlight[2]) * 100}%`,
                top: `${highlight[1] * 100}%`,
                width: `${highlight[2] * 100}%`,
                height: `${highlight[3] * 100}%`,
              }}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
