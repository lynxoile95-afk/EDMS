/**
 * Minimal PDF.js viewer: page navigation + zoom.
 *
 * The PDF is fetched through the authenticated api helper and handed to PDF.js as
 * an object URL, so no signed URL ever needs to be embedded in the page.
 */

import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { Button, Spinner } from "./ui";
import { persianDigits } from "@/lib/format";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfViewer({
  url,
  page,
  onPageChange,
  highlight,
}: {
  url: string;
  page?: number;
  onPageChange?: (page: number) => void;
  highlight?: number[] | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [current, setCurrent] = useState(page ?? 1);
  const [zoom, setZoom] = useState(1.1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const task = pdfjs.getDocument({ url, withCredentials: false });
    task.promise
      .then((document) => {
        if (cancelled) {
          void document.destroy();
          return;
        }
        documentRef.current = document;
        setPageCount(document.numPages);
        setCurrent((previous) => Math.min(Math.max(previous, 1), document.numPages));
      })
      .catch(() => !cancelled && setError("نمایش فایل PDF ممکن نشد."))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
      void documentRef.current?.destroy();
      documentRef.current = null;
    };
  }, [url]);

  useEffect(() => {
    if (page && page !== current) setCurrent(page);
  }, [page]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const document = documentRef.current;
    if (!document || !canvasRef.current) return;
    let cancelled = false;
    void (async () => {
      try {
        const pdfPage = await document.getPage(current);
        if (cancelled || !canvasRef.current) return;
        const viewport = pdfPage.getViewport({ scale: zoom });
        const canvas = canvasRef.current;
        const context = canvas.getContext("2d");
        if (!context) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await pdfPage.render({ canvasContext: context, viewport }).promise;
      } catch {
        if (!cancelled) setError("رسم صفحه ناموفق بود.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [current, zoom, pageCount]);

  const go = (next: number) => {
    const bounded = Math.min(Math.max(next, 1), Math.max(pageCount, 1));
    setCurrent(bounded);
    onPageChange?.(bounded);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <div className="flex items-center gap-1">
          <Button variant="ghost" onClick={() => go(current - 1)} disabled={current <= 1}>
            قبلی
          </Button>
          <span className="min-w-[86px] text-center text-xs text-slate-600">
            صفحه {persianDigits(current)} از {persianDigits(pageCount || 1)}
          </span>
          <Button variant="ghost" onClick={() => go(current + 1)} disabled={current >= pageCount}>
            بعدی
          </Button>
        </div>
        <div className="flex items-center gap-1">
          <Button variant="ghost" onClick={() => setZoom((z) => Math.max(0.5, z - 0.2))}>
            −
          </Button>
          <span className="text-xs text-slate-600">{persianDigits(Math.round(zoom * 100))}٪</span>
          <Button variant="ghost" onClick={() => setZoom((z) => Math.min(3, z + 0.2))}>
            +
          </Button>
        </div>
      </div>

      <div className="relative flex-1 overflow-auto bg-slate-200/60 p-3">
        {loading ? <Spinner label="در حال بارگذاری نقشه…" /> : null}
        {error ? <p className="p-4 text-sm text-rose-700">{error}</p> : null}
        <div className="pdf-page relative mx-auto w-fit">
          <canvas ref={canvasRef} className="rounded bg-white" />
          {highlight && highlight.length === 4 ? (
            <div
              className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/20"
              style={{
                // normalised coordinates (x, y, w, h) -> percentages
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
