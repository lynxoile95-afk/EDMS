import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { MarkupAnnotation, MarkupKind, MarkupPoint } from "@/lib/types";
import { persianDigits } from "@/lib/format";
import { Button, Spinner } from "./ui";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Tool = "select" | MarkupKind;
type PdfData = ArrayBuffer | Uint8Array;
type DraftAnnotation = Omit<MarkupAnnotation, "created_at" | "updated_at" | "reply_status" | "reply_text" | "replied_by_id" | "replied_by_name" | "replied_at" | "author_id" | "author_name"> & {
  created_at: string;
  updated_at: string;
  reply_status: MarkupAnnotation["reply_status"];
  reply_text: string | null;
  replied_by_id: string | null;
  replied_by_name: string | null;
  replied_at: string | null;
  author_id: string | null;
  author_name: string | null;
};

const TOOL_LABELS: Record<Tool, string> = {
  select: "انتخاب",
  sticky_note: "یادداشت",
  highlight: "هایلایت",
  rectangle: "مستطیل",
  revision_cloud: "ابر بازنگری",
  arrow: "پیکان",
  free_text: "متن",
  freehand: "قلم",
};

const TOOL_ORDER: Tool[] = ["select", "sticky_note", "highlight", "rectangle", "revision_cloud", "arrow", "free_text", "freehand"];
const DEFAULT_COLOR = "#d64545";
const MIN_ZOOM = 0.55;
const MAX_ZOOM = 2.5;

function clamp(value: number, minimum = 0, maximum = 1): number {
  return Math.min(Math.max(value, minimum), maximum);
}

function pointFromEvent(event: PointerEvent | React.PointerEvent, element: HTMLElement): MarkupPoint {
  const rect = element.getBoundingClientRect();
  return {
    x: clamp((event.clientX - rect.left) / Math.max(rect.width, 1)),
    y: clamp((event.clientY - rect.top) / Math.max(rect.height, 1)),
  };
}

function rectanglePoints(start: MarkupPoint, end: MarkupPoint): MarkupPoint[] {
  return [
    { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y) },
    { x: Math.max(start.x, end.x), y: Math.max(start.y, end.y) },
  ];
}

function bounds(annotation: Pick<MarkupAnnotation, "points">): { x: number; y: number; w: number; h: number } {
  const points = annotation.points.length ? annotation.points : [{ x: 0.05, y: 0.05 }, { x: 0.2, y: 0.12 }];
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return {
    x,
    y,
    w: Math.max(0.012, Math.max(...xs) - x),
    h: Math.max(0.012, Math.max(...ys) - y),
  };
}

function colorFor(annotation: Pick<MarkupAnnotation, "color">): string {
  return /^#[0-9a-f]{6}$/i.test(annotation.color) ? annotation.color : DEFAULT_COLOR;
}

export interface MarkupEditorProps {
  data: PdfData;
  annotations: MarkupAnnotation[];
  onChange: (annotations: MarkupAnnotation[]) => void;
  readOnly?: boolean;
  initialPage?: number;
  onPageChange?: (page: number) => void;
  onPageCount?: (count: number) => void;
  selectedId?: string | null;
  onSelectedIdChange?: (id: string | null) => void;
}

/**
 * Local PDF.js canvas with a normalized SVG editing layer. The canvas is never
 * written back to the server; only the small JSON annotation model is saved.
 */
export function MarkupEditor({
  data,
  annotations,
  onChange,
  readOnly = false,
  initialPage = 1,
  onPageChange,
  onPageCount,
  selectedId,
  onSelectedIdChange,
}: MarkupEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const loadingTaskRef = useRef<ReturnType<typeof pdfjs.getDocument> | null>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const drawingRef = useRef<{ start: MarkupPoint; points: MarkupPoint[]; sourceId?: string; mode: "draw" | "move" | "resize" } | null>(null);
  const [documentPageCount, setDocumentPageCount] = useState(0);
  const [page, setPage] = useState(initialPage);
  const [tool, setTool] = useState<Tool>(readOnly ? "select" : "select");
  const [color, setColor] = useState(DEFAULT_COLOR);
  const [zoom, setZoom] = useState(1);
  const [fitScale, setFitScale] = useState(1);
  const [viewport, setViewport] = useState({ width: 612, height: 792 });
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localSelectedId, setLocalSelectedId] = useState<string | null>(selectedId ?? null);

  const effectiveSelectedId = selectedId === undefined ? localSelectedId : selectedId;
  const scale = Math.min(Math.max(fitScale * zoom, 0.25), 5);
  const pageAnnotations = useMemo(
    () => annotations.filter((annotation) => annotation.page_no === page),
    [annotations, page],
  );

  const select = useCallback((id: string | null) => {
    setLocalSelectedId(id);
    onSelectedIdChange?.(id);
  }, [onSelectedIdChange]);

  useEffect(() => {
    setPage(Math.max(1, initialPage));
  }, [initialPage]);

  useEffect(() => {
    let disposed = false;
    renderTaskRef.current?.cancel();
    renderTaskRef.current = null;
    if (documentRef.current) void documentRef.current.destroy();
    documentRef.current = null;
    if (loadingTaskRef.current) void loadingTaskRef.current.destroy();
    loadingTaskRef.current = null;
    setLoading(true);
    setError(null);
    setDocumentPageCount(0);

    const task = pdfjs.getDocument({
      data,
      cMapUrl: `${import.meta.env.BASE_URL.replace(/\/$/, "")}/pdfjs/cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${import.meta.env.BASE_URL.replace(/\/$/, "")}/pdfjs/standard_fonts/`,
      useWorkerFetch: true,
      isEvalSupported: true,
    } as Parameters<typeof pdfjs.getDocument>[0]);
    loadingTaskRef.current = task;
    void task.promise
      .then((document) => {
        if (disposed) {
          void document.destroy();
          return;
        }
        documentRef.current = document;
        setDocumentPageCount(document.numPages);
        setPage((current) => Math.min(Math.max(current, 1), document.numPages));
        onPageCount?.(document.numPages);
      })
      .catch(() => {
        if (!disposed) setError("نمایش PDF ممکن نشد؛ فایل ممکن است خراب یا رمزگذاری شده باشد.");
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      if (documentRef.current) void documentRef.current.destroy();
      documentRef.current = null;
      if (loadingTaskRef.current) void loadingTaskRef.current.destroy();
      loadingTaskRef.current = null;
    };
  }, [data, onPageCount]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const update = () => {
      const available = Math.max(260, host.clientWidth - 32);
      setFitScale(Math.min(Math.max(available / Math.max(viewport.width, 1), 0.25), 3));
    };
    update();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(host);
    return () => observer?.disconnect();
  }, [viewport.width]);

  useEffect(() => {
    const document = documentRef.current;
    const canvas = canvasRef.current;
    if (!document || !canvas || !documentPageCount) return;
    let cancelled = false;
    renderTaskRef.current?.cancel();
    setRendering(true);
    void (async () => {
      let pdfPage: Awaited<ReturnType<PDFDocumentProxy["getPage"]>> | null = null;
      let renderTask: RenderTask | null = null;
      try {
        pdfPage = await document.getPage(Math.min(page, document.numPages));
        if (cancelled) return;
        const baseViewport = pdfPage.getViewport({ scale: 1 });
        setViewport({ width: baseViewport.width, height: baseViewport.height });
        const renderedViewport = pdfPage.getViewport({ scale });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.ceil(renderedViewport.width * ratio);
        canvas.height = Math.ceil(renderedViewport.height * ratio);
        canvas.style.width = `${renderedViewport.width}px`;
        canvas.style.height = `${renderedViewport.height}px`;
        const context = canvas.getContext("2d", { alpha: false });
        if (!context) throw new Error("canvas context unavailable");
        context.setTransform(1, 0, 0, 1, 0, 0);
        context.fillStyle = "white";
        context.fillRect(0, 0, canvas.width, canvas.height);
        renderTask = pdfPage.render({
          canvasContext: context,
          viewport: renderedViewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        });
        renderTaskRef.current = renderTask;
        await renderTask.promise;
      } catch (reason: unknown) {
        if (!cancelled && !(reason instanceof Error && reason.name === "RenderingCancelledException")) {
          setError("رسم صفحه PDF ناموفق بود.");
        }
      } finally {
        if (renderTaskRef.current === renderTask) renderTaskRef.current = null;
        pdfPage?.cleanup();
        if (!cancelled) setRendering(false);
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
    };
  }, [documentPageCount, page, scale]);

  const pageHostStyle = {
    width: `${viewport.width * scale}px`,
    height: `${viewport.height * scale}px`,
  };

  const updateAnnotation = (id: string, nextPoints: MarkupPoint[]) => {
    onChange(annotations.map((annotation) => annotation.id === id ? { ...annotation, points: nextPoints } : annotation));
  };

  const pointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (readOnly || !hostRef.current) return;
    const target = event.target as HTMLElement;
    const svg = target.closest("svg");
    const point = pointFromEvent(event, event.currentTarget);
    event.currentTarget.setPointerCapture(event.pointerId);

    if (tool === "select") {
      const annotationId = target.closest("[data-markup-id]")?.getAttribute("data-markup-id") ?? null;
      const current = annotationId ? annotations.find((annotation) => annotation.id === annotationId) : null;
      select(annotationId);
      if (current && svg) {
        drawingRef.current = {
          start: point,
          points: current.points,
          sourceId: current.id,
          mode: target.closest("[data-resize-handle]") ? "resize" : "move",
        };
      }
      return;
    }
    drawingRef.current = { start: point, points: [point], mode: "draw" };
  };

  const pointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const draft = drawingRef.current;
    if (!draft || !hostRef.current) return;
    const point = pointFromEvent(event, event.currentTarget);
    if (draft.mode === "draw") {
      draft.points = tool === "freehand" ? [...draft.points, point] : [draft.start, point];
      return;
    }
    const source = annotations.find((annotation) => annotation.id === draft.sourceId);
    if (!source) return;
    const delta = { x: point.x - draft.start.x, y: point.y - draft.start.y };
    if (draft.mode === "move") {
      updateAnnotation(source.id, draft.points.map((item) => ({ x: clamp(item.x + delta.x), y: clamp(item.y + delta.y) })));
    } else {
      const original = bounds({ points: draft.points });
      updateAnnotation(source.id, rectanglePoints({ x: original.x, y: original.y }, point));
    }
  };

  const pointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    const draft = drawingRef.current;
    drawingRef.current = null;
    if (!draft || readOnly) return;
    event.currentTarget.releasePointerCapture(event.pointerId);
    if (draft.mode !== "draw") return;
    const points = tool === "freehand" ? draft.points : rectanglePoints(draft.start, draft.points[draft.points.length - 1] ?? draft.start);
    const created: DraftAnnotation = {
      id: typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      number: annotations.length ? Math.max(...annotations.map((annotation) => annotation.number)) + 1 : 1,
      kind: tool as MarkupKind,
      page_no: page,
      points: points.length > 1 ? points : [draft.start, { x: clamp(draft.start.x + 0.08), y: clamp(draft.start.y + 0.05) }],
      text: null,
      color,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      reply_status: "open",
      reply_text: null,
      replied_by_id: null,
      replied_by_name: null,
      replied_at: null,
      author_id: null,
      author_name: null,
    };
    if (tool === "sticky_note" || tool === "free_text") {
      created.text = window.prompt("متن نظر را وارد کنید:")?.trim() || "نظر";
    }
    onChange([...annotations, created]);
    select(created.id);
    setTool("select");
  };

  const removeSelected = useCallback(() => {
    if (readOnly || !effectiveSelectedId) return;
    onChange(annotations.filter((annotation) => annotation.id !== effectiveSelectedId));
    select(null);
  }, [annotations, effectiveSelectedId, onChange, readOnly, select]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
      if (event.key === "Delete" || event.key === "Backspace") removeSelected();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [removeSelected]);

  const go = (next: number) => {
    const bounded = Math.min(Math.max(next, 1), Math.max(documentPageCount, 1));
    setPage(bounded);
    onPageChange?.(bounded);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#e9edf0]" dir="rtl">
      <div className="flex flex-wrap items-center gap-1 border-b border-[#b9c5cf] bg-[#f7f8f2] px-2 py-1 text-[10px]">
        <div className="flex items-center gap-1" dir="ltr">
          <Button className="btn-sm" onClick={() => go(page - 1)} disabled={loading || page <= 1}>‹</Button>
          <span className="min-w-[92px] text-center" dir="rtl">صفحه {persianDigits(page)} از {persianDigits(documentPageCount || 1)}</span>
          <Button className="btn-sm" onClick={() => go(page + 1)} disabled={loading || page >= documentPageCount}>›</Button>
        </div>
        <span className="mx-1 h-4 border-r border-[#c3ccd6]" />
        {!readOnly ? TOOL_ORDER.map((item) => (
          <button
            key={item}
            type="button"
            className={`border px-1.5 py-1 ${tool === item ? "border-[#1f4e79] bg-[#dbe8f3] font-bold text-[#1f3864]" : "border-[#c3ccd6] bg-white text-[#33475b]"}`}
            onClick={() => setTool(item)}
            title={TOOL_LABELS[item]}
          >
            {TOOL_LABELS[item]}
          </button>
        )) : <span className="font-bold text-[#5c6b78]">جلسه ثبت نهایی‌شده — فقط خواندنی</span>}
        {!readOnly ? (
          <label className="mr-auto flex items-center gap-1" title="رنگ نشانه‌گذاری">
            رنگ
            <input type="color" value={color} onChange={(event) => setColor(event.target.value)} className="h-6 w-7 cursor-pointer border-0 bg-transparent p-0" />
          </label>
        ) : <span className="mr-auto" />}
        <Button className="btn-sm" onClick={() => setZoom(1)} disabled={loading}>عرض متناسب</Button>
        <Button className="btn-sm" onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - 0.15))} disabled={loading}>−</Button>
        <span className="w-10 text-center text-[9px]">{Math.round(zoom * 100)}%</span>
        <Button className="btn-sm" onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + 0.15))} disabled={loading}>＋</Button>
        {!readOnly ? <Button className="btn-sm" disabled={!effectiveSelectedId} onClick={removeSelected}>حذف</Button> : null}
      </div>
      {error ? <div className="m-2 border border-[#c98a86] bg-[#fdeeee] p-2 text-[10px] text-[#8a2b25]">{error}</div> : null}
      <div ref={hostRef} className="min-h-0 flex-1 overflow-auto p-4" dir="ltr">
        <div
          className="relative mx-auto bg-white shadow-md"
          style={pageHostStyle}
          onPointerDown={pointerDown}
          onPointerMove={pointerMove}
          onPointerUp={pointerUp}
          onDoubleClick={(event) => {
            if (readOnly || tool !== "select") return;
            const id = (event.target as HTMLElement).closest("[data-markup-id]")?.getAttribute("data-markup-id");
            if (id) select(id);
          }}
        >
          <canvas ref={canvasRef} className="absolute inset-0 block" aria-label="PDF page" />
          <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none" role="application" aria-label="لایه نشانه‌گذاری">
            <defs>
              <marker id="markup-arrow-head" markerWidth="0.04" markerHeight="0.04" refX="0.035" refY="0.02" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M0,0 L0.04,0.02 L0,0.04 z" fill="context-stroke" />
              </marker>
            </defs>
            {pageAnnotations.map((annotation) => {
              const box = bounds(annotation);
              const selected = annotation.id === effectiveSelectedId;
              const stroke = colorFor(annotation);
              const common = { "data-markup-id": annotation.id, stroke, strokeWidth: selected ? 0.004 : 0.0025, vectorEffect: "non-scaling-stroke" as const };
              const p = annotation.points;
              if (annotation.kind === "freehand") {
                return <polyline key={annotation.id} {...common} points={p.map((item) => `${item.x},${item.y}`).join(" ")} fill="none" />;
              }
              if (annotation.kind === "arrow") {
                const first = p[0] ?? { x: box.x, y: box.y };
                const last = p[p.length - 1] ?? { x: box.x + box.w, y: box.y + box.h };
                return <line key={annotation.id} {...common} x1={first.x} y1={first.y} x2={last.x} y2={last.y} markerEnd="url(#markup-arrow-head)" />;
              }
              return (
                <g key={annotation.id} data-markup-id={annotation.id}>
                  {annotation.kind === "highlight" ? <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill={stroke} fillOpacity={0.25} /> : null}
                  {annotation.kind === "rectangle" ? <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill="none" /> : null}
                  {annotation.kind === "revision_cloud" ? <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill="none" strokeDasharray="0.012 0.006" /> : null}
                  {annotation.kind === "sticky_note" ? <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill="#fff2a8" fillOpacity={0.9} /> : null}
                  {annotation.kind === "free_text" ? <text {...common} x={box.x} y={box.y + box.h * 0.8} fontSize={Math.max(0.014, box.h * 0.65)} fill={stroke} stroke="none">{annotation.text || "نظر"}</text> : null}
                  {annotation.kind === "sticky_note" ? <text x={box.x + 0.006} y={box.y + Math.min(0.03, box.h * 0.55)} fontSize={Math.max(0.01, box.h * 0.24)} fill="#482c00" stroke="none">{annotation.text || "نظر"}</text> : null}
                  {selected && !readOnly ? <rect data-resize-handle="true" x={box.x + box.w - 0.012} y={box.y + box.h - 0.012} width="0.024" height="0.024" fill="white" stroke={stroke} strokeWidth="0.003" vectorEffect="non-scaling-stroke" /> : null}
                </g>
              );
            })}
          </svg>
          {loading ? <div className="absolute inset-0 flex items-center justify-center bg-white/80"><Spinner label="در حال بارگذاری PDF…" /></div> : null}
          {rendering ? <div className="pointer-events-none absolute left-2 top-2 bg-white/80 px-1 text-[9px] text-slate-500">در حال رسم…</div> : null}
        </div>
      </div>
    </div>
  );
}
