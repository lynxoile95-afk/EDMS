import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { MarkupAnnotation, MarkupKind, MarkupPoint } from "@/lib/types";
import { persianDigits } from "@/lib/format";
import {
  DEFAULT_MARKUP_COLOR,
  DEFAULT_MARKUP_STROKE_WIDTH,
  MARKUP_COLOR_PALETTE,
  MAX_MARKUP_STROKE_WIDTH,
  MIN_MARKUP_STROKE_WIDTH,
  normalizeMarkupColor,
  normalizeMarkupStrokeWidth,
} from "@/lib/markups";
import {
  IconArrow,
  IconCallout,
  IconCloud,
  IconCursor,
  IconDelete,
  IconEllipse,
  IconEraser,
  IconFit,
  IconHand,
  IconHighlight,
  IconPen,
  IconRedo,
  IconRectangle,
  IconStickyNote,
  IconStrikethrough,
  IconText,
  IconUnderline,
  IconUndo,
  IconZoomIn,
  IconZoomOut,
} from "./icons";
import { Spinner } from "./ui";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type Tool = "select" | "pan" | "eraser" | MarkupKind;
type PdfData = ArrayBuffer | Uint8Array;
type TextTool = "highlight" | "underline" | "strikethrough";
type TextLayerItem = { text: string; x: number; y: number; width: number; height: number };
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
  pan: "دست",
  highlight: "هایلایت",
  underline: "خط‌زیر",
  strikethrough: "خط‌خورده",
  sticky_note: "یادداشت",
  free_text: "متن",
  callout: "کالوت",
  rectangle: "مستطیل",
  ellipse: "بیضی",
  revision_cloud: "ابر بازبینی",
  arrow: "پیکان",
  freehand: "قلم",
  eraser: "پاک‌کن",
};

const TOOL_GROUPS: Tool[][] = [
  ["select", "pan"],
  ["highlight"],
  ["sticky_note", "free_text", "callout"],
  ["rectangle", "ellipse", "revision_cloud", "arrow", "freehand", "eraser"],
];
const TEXT_TOOLS: TextTool[] = ["highlight", "underline", "strikethrough"];

function ToolIcon({ tool }: { tool: Tool }) {
  const props = { className: "h-3.5 w-3.5" };
  switch (tool) {
    case "select": return <IconCursor {...props} />;
    case "pan": return <IconHand {...props} />;
    case "sticky_note": return <IconStickyNote {...props} />;
    case "highlight": return <IconHighlight {...props} />;
    case "underline": return <IconUnderline {...props} />;
    case "strikethrough": return <IconStrikethrough {...props} />;
    case "rectangle": return <IconRectangle {...props} />;
    case "ellipse": return <IconEllipse {...props} />;
    case "revision_cloud": return <IconCloud {...props} />;
    case "arrow": return <IconArrow {...props} />;
    case "free_text": return <IconText {...props} />;
    case "callout": return <IconCallout {...props} />;
    case "freehand": return <IconPen {...props} />;
    case "eraser": return <IconEraser {...props} />;
  }
}

function RibbonButton({
  icon,
  label,
  onClick,
  active = false,
  disabled = false,
}: {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`group flex min-w-[2.1rem] flex-col items-center justify-center gap-0 rounded-sm border px-0.5 py-0.5 transition-colors ${
        active
          ? "border-[#1f4e79] bg-[#dbe8f3] font-bold text-[#1f3864]"
          : "border-transparent text-[#33475b] hover:border-[#b9c5cf] hover:bg-white"
      } disabled:cursor-not-allowed disabled:opacity-45`}
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
    >
      <span className="flex h-4 items-center justify-center">{icon}</span>
      <span className="whitespace-nowrap text-[7px] leading-3">{label}</span>
    </button>
  );
}

const MIN_ZOOM = 0.55;
const MAX_ZOOM = 2.5;
const MIN_FONT_SIZE = 8;
const MAX_FONT_SIZE = 36;

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

// Measure with the same font, wrapping and padding as the text displayed on the page.
export function sizeMarkupText(points: MarkupPoint[], text: string, fontSize: number, viewport: { width: number; height: number }, kind: "sticky_note" | "free_text" | "callout"): MarkupPoint[] {
  const pageWidth = Math.max(1, viewport.width);
  const pageHeight = Math.max(1, viewport.height);
  const [start, end] = rectanglePoints(points[0], points[1]);
  const width = Math.min(pageWidth, Math.max((end.x - start.x) * pageWidth, Math.min(300, pageWidth * 0.6)));
  const measure = document.createElement("div");
  Object.assign(measure.style, {
    position: "fixed", left: "-10000px", top: "0", visibility: "hidden", pointerEvents: "none",
    boxSizing: "border-box", width: `${width}px`, height: "auto", padding: kind === "sticky_note" ? "3px 4px" : "2px 4px",
    fontFamily: "Arial, sans-serif", fontSize: `${Math.max(8, Math.min(36, fontSize * (kind === "sticky_note" ? 0.75 : 1)))}px`,
    lineHeight: "1.15", direction: "rtl", textAlign: "right", whiteSpace: "pre-wrap",
    overflowWrap: "anywhere", wordBreak: "break-word",
  });
  measure.textContent = text;
  document.body.appendChild(measure);
  const measuredHeight = measure.getBoundingClientRect().height;
  measure.remove();
  const height = Math.min(pageHeight, Math.max((end.y - start.y) * pageHeight, measuredHeight + 2, 24));
  const w = width / pageWidth;
  const h = height / pageHeight;
  const x = Math.min(start.x, 1 - w);
  const y = Math.min(start.y, 1 - h);
  return [{ x, y }, { x: x + w, y: y + h }];
}

// A click has no selection rectangle; give text-marking tools a visible default.
export function textMarkGesturePoints(start: MarkupPoint, end: MarkupPoint): MarkupPoint[] {
  if (Math.abs(end.x - start.x) >= 0.002 || Math.abs(end.y - start.y) >= 0.002) return rectanglePoints(start, end);
  const w = 0.08;
  const h = 0.03;
  const x = Math.min(start.x, 1 - w);
  const y = Math.min(start.y, 1 - h);
  return [{ x, y }, { x: x + w, y: y + h }];
}

function pointRectangles(points: MarkupPoint[]): MarkupPoint[][] {
  if (points.length >= 4 && points.length % 2 === 0) {
    return Array.from({ length: points.length / 2 }, (_, index) => points.slice(index * 2, index * 2 + 2));
  }
  return [points.length ? points : [{ x: 0.05, y: 0.05 }, { x: 0.2, y: 0.12 }]];
}

type NormalizedMarkupBox = { x: number; y: number; w: number; h: number };

function bounds(annotation: Pick<MarkupAnnotation, "points" | "kind">): NormalizedMarkupBox {
  const sourcePoints = annotation.kind === "callout" && annotation.points.length >= 3 ? annotation.points.slice(1) : annotation.points;
  const points = sourcePoints.length ? sourcePoints : [{ x: 0.05, y: 0.05 }, { x: 0.2, y: 0.12 }];
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

export function MarkupTextBox({
  box,
  text,
  color,
  fontSize,
  kind,
  viewport,
}: {
  box: NormalizedMarkupBox;
  text: string | null | undefined;
  color: string;
  fontSize: number;
  kind: "sticky_note" | "free_text" | "callout";
  viewport: { width: number; height: number };
}) {
  const sticky = kind === "sticky_note";
  // The outer SVG uses normalized coordinates; HTML needs page-sized units for CSS pixels.
  return (
    <svg x={box.x} y={box.y} width={box.w} height={box.h} viewBox={`0 0 ${box.w * viewport.width} ${box.h * viewport.height}`} preserveAspectRatio="none" pointerEvents="none" overflow="hidden">
      <foreignObject x="0" y="0" width="100%" height="100%" overflow="hidden">
        <div
          style={{
            boxSizing: "border-box",
            width: "100%",
            height: "100%",
            overflow: "hidden",
            padding: sticky ? "3px 4px" : "2px 4px",
            color: sticky ? "#482c00" : color,
            fontFamily: "Arial, sans-serif",
            fontSize: `${Math.max(8, Math.min(36, fontSize * (sticky ? 0.75 : 1)))}px`,
            lineHeight: 1.15,
            direction: "rtl",
            textAlign: "right",
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            wordBreak: "break-word",
          }}
        >
          {text || "نظر"}
        </div>
      </foreignObject>
    </svg>
  );
}

function colorFor(annotation: Pick<MarkupAnnotation, "color">): string {
  return normalizeMarkupColor(annotation.color);
}

function annotationsEqual(left: MarkupAnnotation[], right: MarkupAnnotation[]): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function annotationContent(annotation: MarkupAnnotation) {
  return {
    kind: annotation.kind,
    page_no: annotation.page_no,
    points: annotation.points,
    text: annotation.text,
    color: annotation.color,
    stroke_width: annotation.stroke_width,
  };
}

function annotationContentEqual(left: MarkupAnnotation[], right: MarkupAnnotation[]): boolean {
  return left.length === right.length && left.every((annotation, index) => JSON.stringify(annotationContent(annotation)) === JSON.stringify(annotationContent(right[index])));
}

function isTextTool(tool: Tool): tool is TextTool {
  return TEXT_TOOLS.includes(tool as TextTool);
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
  const pageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const loadingTaskRef = useRef<ReturnType<typeof pdfjs.getDocument> | null>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const drawingRef = useRef<{ start: MarkupPoint; points: MarkupPoint[]; sourceId?: string; mode: "draw" | "move" | "resize" } | null>(null);
  const textGestureRef = useRef<{ start: MarkupPoint; end: MarkupPoint } | null>(null);
  const panRef = useRef<{ x: number; y: number; scrollLeft: number; scrollTop: number } | null>(null);
  const erasedIdsRef = useRef(new Set<string>());
  const annotationsRef = useRef<MarkupAnnotation[]>(annotations);
  const historyRef = useRef<MarkupAnnotation[][]>([]);
  const futureRef = useRef<MarkupAnnotation[][]>([]);
  const suppressExternalHistoryRef = useRef(false);
  const gestureHistoryRecordedRef = useRef(false);
  const [textLayerItems, setTextLayerItems] = useState<TextLayerItem[]>([]);
  const [, setHistoryVersion] = useState(0);
  const [documentPageCount, setDocumentPageCount] = useState(0);
  const [page, setPage] = useState(initialPage);
  const [tool, setTool] = useState<Tool>(readOnly ? "select" : "select");
  const [stylesOpen, setStylesOpen] = useState(false);
  const [color, setColor] = useState(DEFAULT_MARKUP_COLOR);
  const [strokeWidth, setStrokeWidth] = useState(DEFAULT_MARKUP_STROKE_WIDTH);
  const [fontSize, setFontSize] = useState(16);
  const [lastCreatedId, setLastCreatedId] = useState<string | null>(null);
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

  useEffect(() => {
    if (annotationsEqual(annotationsRef.current, annotations)) {
      // publish/undo/redo update the ref before notifying the parent. Clear
      // this guard on the next render so a later external edit is undoable.
      suppressExternalHistoryRef.current = false;
      return;
    }
    if (suppressExternalHistoryRef.current) {
      suppressExternalHistoryRef.current = false;
    } else if (!annotationContentEqual(annotationsRef.current, annotations)) {
      historyRef.current = [...historyRef.current, annotationsRef.current.map((annotation) => ({ ...annotation, points: annotation.points.map((point) => ({ ...point })) }))];
      futureRef.current = [];
    }
    annotationsRef.current = annotations;
    setHistoryVersion((version) => version + 1);
  }, [annotations]);

  const recordHistory = () => {
    historyRef.current = [...historyRef.current, annotationsRef.current.map((annotation) => ({ ...annotation, points: annotation.points.map((point) => ({ ...point })) }))];
    futureRef.current = [];
    setHistoryVersion((version) => version + 1);
  };

  const publish = (next: MarkupAnnotation[], record = true) => {
    if (record) recordHistory();
    suppressExternalHistoryRef.current = true;
    annotationsRef.current = next;
    onChange(next);
  };

  const select = useCallback((id: string | null) => {
    setLocalSelectedId(id);
    onSelectedIdChange?.(id);
  }, [onSelectedIdChange]);

  useEffect(() => {
    const selected = effectiveSelectedId ? annotations.find((annotation) => annotation.id === effectiveSelectedId) : null;
    if (!selected) return;
    setColor(colorFor(selected));
    setStrokeWidth(normalizeMarkupStrokeWidth(selected.stroke_width));
    setFontSize(selected.font_size ? Math.min(Math.max(selected.font_size, MIN_FONT_SIZE), MAX_FONT_SIZE) : 16);
  }, [annotations, effectiveSelectedId]);

  useEffect(() => {
    setPage(Math.max(1, initialPage));
  }, [initialPage]);

  useEffect(() => {
    if (!lastCreatedId) return;
    const timer = window.setTimeout(() => setLastCreatedId(null), 850);
    return () => window.clearTimeout(timer);
  }, [lastCreatedId]);

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
    const document = documentRef.current;
    if (!document || !documentPageCount) {
      setTextLayerItems([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const pdfPage = await document.getPage(Math.min(page, document.numPages));
        const baseViewport = pdfPage.getViewport({ scale: 1 });
        const content = await pdfPage.getTextContent();
        const items: TextLayerItem[] = [];
        for (const raw of content.items) {
          if (!("str" in raw) || !raw.str.trim()) continue;
          const item = raw as { str: string; transform: number[]; width: number; height: number };
          const transform = pdfjs.Util.transform(baseViewport.transform, item.transform);
          const height = Math.max(4, Math.hypot(transform[2], transform[3]));
          const x = transform[4];
          const y = transform[5] - height;
          items.push({
            text: item.str,
            x: clamp(x / Math.max(baseViewport.width, 1)),
            y: clamp(y / Math.max(baseViewport.height, 1)),
            width: clamp(item.width / Math.max(baseViewport.width, 1), 0, 1 - clamp(x / Math.max(baseViewport.width, 1))),
            height: clamp(height / Math.max(baseViewport.height, 1)),
          });
        }
        if (!cancelled) setTextLayerItems(items);
        pdfPage.cleanup();
      } catch {
        if (!cancelled) setTextLayerItems([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [documentPageCount, page]);

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
    if (!gestureHistoryRecordedRef.current) {
      recordHistory();
      gestureHistoryRecordedRef.current = true;
    }
    publish(annotationsRef.current.map((annotation) => annotation.id === id ? { ...annotation, points: nextPoints } : annotation), false);
  };

  const changeColor = (value: string) => {
    const nextColor = normalizeMarkupColor(value);
    setColor(nextColor);
    if (readOnly || !effectiveSelectedId) return;
    publish(annotationsRef.current.map((annotation) => annotation.id === effectiveSelectedId
      ? { ...annotation, color: nextColor, updated_at: new Date().toISOString() }
      : annotation));
  };

  const changeStrokeWidth = (value: number) => {
    const nextWidth = normalizeMarkupStrokeWidth(value);
    setStrokeWidth(nextWidth);
    if (readOnly || !effectiveSelectedId) return;
    publish(annotationsRef.current.map((annotation) => annotation.id === effectiveSelectedId
      ? { ...annotation, stroke_width: nextWidth, updated_at: new Date().toISOString() }
      : annotation));
  };

  const changeFontSize = (value: number) => {
    const nextSize = Math.min(Math.max(value, MIN_FONT_SIZE), MAX_FONT_SIZE);
    setFontSize(nextSize);
    if (readOnly || !effectiveSelectedId) return;
    publish(annotationsRef.current.map((annotation) => annotation.id === effectiveSelectedId
      ? { ...annotation, font_size: nextSize, updated_at: new Date().toISOString() }
      : annotation));
  };

  const undo = useCallback(() => {
    if (readOnly || !historyRef.current.length) return;
    const previous = historyRef.current.pop();
    if (!previous) return;
    futureRef.current.push(annotationsRef.current);
    suppressExternalHistoryRef.current = true;
    annotationsRef.current = previous;
    onChange(previous);
    setHistoryVersion((version) => version + 1);
    if (effectiveSelectedId && !previous.some((annotation) => annotation.id === effectiveSelectedId)) select(null);
  }, [effectiveSelectedId, onChange, readOnly, select]);

  const redo = useCallback(() => {
    if (readOnly || !futureRef.current.length) return;
    const next = futureRef.current.pop();
    if (!next) return;
    historyRef.current.push(annotationsRef.current);
    suppressExternalHistoryRef.current = true;
    annotationsRef.current = next;
    onChange(next);
    setHistoryVersion((version) => version + 1);
  }, [onChange, readOnly]);

  const removeAnnotation = (id: string) => {
    if (readOnly || !annotationsRef.current.some((annotation) => annotation.id === id)) return;
    publish(annotationsRef.current.filter((annotation) => annotation.id !== id));
    if (effectiveSelectedId === id) select(null);
  };

  const eraseAt = (event: React.PointerEvent<HTMLDivElement>) => {
    const target = (event.target as HTMLElement).closest("[data-markup-id]")
      ?? (document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null)?.closest("[data-markup-id]");
    const id = target?.getAttribute("data-markup-id");
    if (!id || erasedIdsRef.current.has(id)) return;
    erasedIdsRef.current.add(id);
    removeAnnotation(id);
  };

  const selectedTextGeometry = (): { points: MarkupPoint[]; text: string } | null => {
    const selection = window.getSelection();
    const pageElement = pageRef.current;
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed || !pageElement) return null;
    const range = selection.getRangeAt(0);
    if (!textLayerRef.current?.contains(range.commonAncestorContainer)) return null;
    const pageRect = pageElement.getBoundingClientRect();
    const rects = Array.from(range.getClientRects())
      .filter((rect) => rect.width > 1 && rect.height > 1)
      .map((rect) => ({
        left: clamp((rect.left - pageRect.left) / Math.max(pageRect.width, 1)),
        top: clamp((rect.top - pageRect.top) / Math.max(pageRect.height, 1)),
        right: clamp((rect.right - pageRect.left) / Math.max(pageRect.width, 1)),
        bottom: clamp((rect.bottom - pageRect.top) / Math.max(pageRect.height, 1)),
      }))
      .filter((rect) => rect.right > rect.left && rect.bottom > rect.top);
    if (!rects.length) return null;
    return {
      points: rects.flatMap((rect) => [
        { x: rect.left, y: rect.top },
        { x: rect.right, y: rect.bottom },
      ]),
      text: selection.toString().trim(),
    };
  };

  const pointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (readOnly || !hostRef.current) return;
    const target = event.target as HTMLElement;
    const point = pointFromEvent(event, event.currentTarget);
    gestureHistoryRecordedRef.current = false;

    if (tool === "pan") {
      panRef.current = {
        x: event.clientX,
        y: event.clientY,
        scrollLeft: hostRef.current.scrollLeft,
        scrollTop: hostRef.current.scrollTop,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
      return;
    }
    if (tool === "eraser") {
      erasedIdsRef.current = new Set();
      event.currentTarget.setPointerCapture(event.pointerId);
      eraseAt(event);
      return;
    }
    if (isTextTool(tool)) {
      // Do not capture the pointer here: the browser must keep native text
      // selection working in the PDF.js text layer. Pointer-up uses the
      // selection rectangles and falls back to a manually dragged line/box.
      textGestureRef.current = { start: point, end: point };
      return;
    }

    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === "select") {
      const annotationId = target.closest("[data-markup-id]")?.getAttribute("data-markup-id") ?? null;
      const current = annotationId ? annotationsRef.current.find((annotation) => annotation.id === annotationId) : null;
      select(annotationId);
      if (current) {
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
    if (panRef.current && hostRef.current) {
      const pan = panRef.current;
      hostRef.current.scrollLeft = pan.scrollLeft - (event.clientX - pan.x);
      hostRef.current.scrollTop = pan.scrollTop - (event.clientY - pan.y);
      return;
    }
    if (tool === "eraser") {
      eraseAt(event);
      return;
    }
    if (textGestureRef.current) {
      textGestureRef.current.end = pointFromEvent(event, event.currentTarget);
      return;
    }
    const draft = drawingRef.current;
    if (!draft || !hostRef.current) return;
    const point = pointFromEvent(event, event.currentTarget);
    if (draft.mode === "draw") {
      if (tool === "freehand") {
        const nextPoints = [...draft.points, point];
        draft.points = nextPoints.length > 1000 ? nextPoints.filter((_item, index) => index % 2 === 0).slice(-1000) : nextPoints;
      } else {
        draft.points = [draft.start, point];
      }
      return;
    }
    const source = annotationsRef.current.find((annotation) => annotation.id === draft.sourceId);
    if (!source) return;
    const delta = { x: point.x - draft.start.x, y: point.y - draft.start.y };
    if (draft.mode === "move") {
      updateAnnotation(source.id, draft.points.map((item) => ({ x: clamp(item.x + delta.x), y: clamp(item.y + delta.y) })));
    } else {
      const original = bounds({ points: draft.points, kind: source.kind });
      const resizedBox = rectanglePoints({ x: original.x, y: original.y }, point);
      const resizedPoints = source.kind === "callout" && draft.points.length >= 3
        ? [draft.points[0], ...resizedBox]
        : resizedBox;
      updateAnnotation(source.id, resizedPoints);
    }
  };

  const pointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (panRef.current) {
      panRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      return;
    }
    if (tool === "eraser") {
      erasedIdsRef.current = new Set();
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      return;
    }
    if (isTextTool(tool)) {
      const gesture = textGestureRef.current;
      textGestureRef.current = null;
      if (!gesture || readOnly) return;
      const snapped = selectedTextGeometry();
      const points = snapped?.points ?? textMarkGesturePoints(gesture.start, gesture.end);
      const created: DraftAnnotation = {
        id: typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
        number: annotationsRef.current.length ? Math.max(...annotationsRef.current.map((annotation) => annotation.number)) + 1 : 1,
        kind: tool,
        page_no: page,
        points: points.length > 1 ? points : [gesture.start, { x: clamp(gesture.start.x + 0.08), y: clamp(gesture.start.y + 0.03) }],
        text: snapped?.text || null,
        color: normalizeMarkupColor(color),
        stroke_width: normalizeMarkupStrokeWidth(strokeWidth),
        font_size: fontSize,
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
      publish([...annotationsRef.current, created]);
      setLastCreatedId(created.id);
      select(created.id);
      window.getSelection()?.removeAllRanges();
      setTool("select");
      return;
    }

    const draft = drawingRef.current;
    drawingRef.current = null;
    if (!draft || readOnly) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (draft.mode !== "draw") {
      gestureHistoryRecordedRef.current = false;
      return;
    }
    const end = draft.points[draft.points.length - 1] ?? draft.start;
    const points = tool === "freehand" ? draft.points : tool === "callout"
      ? (() => {
          const halfWidth = Math.max(Math.abs(end.x - draft.start.x) * 0.45, 0.05);
          const halfHeight = Math.max(Math.abs(end.y - draft.start.y) * 0.35, 0.04);
          return [draft.start, ...rectanglePoints(
            { x: clamp(end.x - halfWidth), y: clamp(end.y - halfHeight) },
            { x: clamp(end.x + halfWidth), y: clamp(end.y + halfHeight) },
          )];
        })()
      : rectanglePoints(draft.start, end);
    const created: DraftAnnotation = {
      id: typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
      number: annotationsRef.current.length ? Math.max(...annotationsRef.current.map((annotation) => annotation.number)) + 1 : 1,
      kind: tool as MarkupKind,
      page_no: page,
      points: points.length > 1 ? points : [draft.start, { x: clamp(draft.start.x + 0.08), y: clamp(draft.start.y + 0.05) }],
      text: null,
      color: normalizeMarkupColor(color),
      stroke_width: normalizeMarkupStrokeWidth(strokeWidth),
      font_size: fontSize,
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
    if (tool === "sticky_note" || tool === "free_text" || tool === "callout") {
      const entered = window.prompt("متن نظر را وارد کنید:");
      if (entered === null) {
        setTool("select");
        return;
      }
      created.text = entered.trim() || "نظر";
      const anchor = tool === "callout" ? created.points[0] : null;
      const boxPoints = anchor ? created.points.slice(1) : created.points;
      const fitted = sizeMarkupText(boxPoints, created.text, fontSize, viewport, tool);
      created.points = anchor ? [anchor, ...fitted] : fitted;
    }
    publish([...annotationsRef.current, created]);
    setLastCreatedId(created.id);
    select(created.id);
    setTool("select");
  };

  const removeSelected = useCallback(() => {
    if (readOnly || !effectiveSelectedId) return;
    removeAnnotation(effectiveSelectedId);
  }, [effectiveSelectedId, readOnly]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        redo();
        return;
      }
      if (event.key === "Escape") {
        setStylesOpen(false);
        drawingRef.current = null;
        textGestureRef.current = null;
        panRef.current = null;
        window.getSelection()?.removeAllRanges();
        if (!readOnly) setTool("select");
        return;
      }
      if (event.key === "Delete" || event.key === "Backspace") removeSelected();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readOnly, redo, removeSelected, tool, undo]);

  const go = (next: number) => {
    const bounded = Math.min(Math.max(next, 1), Math.max(documentPageCount, 1));
    setPage(bounded);
    onPageChange?.(bounded);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-[#e9edf0]" dir="rtl">
      <div className="sticky top-0 z-40 shrink-0 bg-[#e9edf0]/95 shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-stretch gap-1 border-b border-[#b9c5cf] bg-[#f7f8f2] px-2 py-1 text-[10px] shadow-sm">
        {!readOnly ? TOOL_GROUPS.map((group, groupIndex) => (
          <div key={groupIndex} className={`flex flex-nowrap items-stretch gap-0.5 rounded border border-[#d3d9de] bg-white/70 px-1 ${groupIndex ? "border-r-2 border-r-[#c3ccd6]" : ""}`}>
            {group.map((item) => (
              <RibbonButton
                key={item}
                icon={<ToolIcon tool={item} />}
                label={TOOL_LABELS[item]}
                active={tool === item}
                onClick={() => {
                  setTool(item);
                  setStylesOpen(false);
                }}
              />
            ))}
          </div>
        )) : <span className="flex items-center px-2 font-bold text-[#5c6b78]">جلسه ثبت نهایی‌شده — فقط خواندنی</span>}
        {!readOnly ? (
          <div className="relative flex items-stretch gap-0.5 rounded border border-[#d3d9de] bg-white/70 px-1">
            <RibbonButton
              icon={<IconStickyNote className="h-3.5 w-3.5" style={{ color }} />}
              label="سبک کامنت"
              active={stylesOpen}
              onClick={() => setStylesOpen((open) => !open)}
            />
            {stylesOpen ? (
              <div
                className="absolute right-0 top-full z-50 mt-1 w-[17rem] rounded border border-[#9eabb7] bg-white p-1.5 text-[#33475b] shadow-xl"
                role="dialog"
                aria-label="سبک‌های کامنت"
                dir="rtl"
              >
                <div className="flex items-center justify-between border-b border-[#d3d9de] px-1 pb-1 text-[9px] font-bold">
                  <span>سبک‌های کامنت</span>
                  <span className="flex items-center gap-1 font-normal text-[#687887]">
                    <IconStickyNote className="h-4 w-4" style={{ color }} />
                    <span>یادداشت</span>
                  </span>
                </div>
                <div className="grid grid-cols-4 gap-1 p-1" role="menu" aria-label="انتخاب رنگ یادداشت">
                  {MARKUP_COLOR_PALETTE.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      role="menuitemradio"
                      aria-checked={color.toLowerCase() === item.value}
                      className={`flex min-h-[3.35rem] flex-col items-center justify-center gap-0.5 rounded border px-1 py-0.5 text-[8px] transition-colors hover:border-[#1f4e79] hover:bg-[#eef5fa] focus:outline-none focus:ring-2 focus:ring-[#1f4e79] ${color.toLowerCase() === item.value ? "border-[#1f4e79] bg-[#dbe8f3] font-bold" : "border-[#d3d9de]"}`}
                      title={`سبک یادداشت: ${item.label}`}
                      onClick={() => {
                        changeColor(item.value);
                        setTool("sticky_note");
                        setStylesOpen(false);
                      }}
                    >
                      <IconStickyNote className="h-6 w-6" style={{ color: item.value }} />
                      <span className="whitespace-nowrap">{item.label}</span>
                    </button>
                  ))}
                </div>
                <p className="border-t border-[#e4e8eb] px-1 pt-1 text-[8px] text-[#687887]">انتخاب سبک، ابزار یادداشت را فعال می‌کند.</p>
              </div>
            ) : null}
          </div>
        ) : null}
        {!readOnly ? (
          <div className="flex items-stretch gap-0.5 rounded border border-[#d3d9de] bg-white/70 px-1">
            <RibbonButton icon={<IconUndo className="h-3.5 w-3.5" />} label="برگرداندن" disabled={!historyRef.current.length} onClick={undo} />
            <RibbonButton icon={<IconRedo className="h-3.5 w-3.5" />} label="جلو" disabled={!futureRef.current.length} onClick={redo} />
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1 border-b border-[#c3ccd6] bg-white px-2 py-0.5 text-[9px]" dir="rtl">
        <div className="flex items-center gap-0.5 rounded border border-[#d3d9de] bg-[#f7f8f2] px-1" dir="ltr">
          <RibbonButton
            icon={<IconArrow className="h-3.5 w-3.5 rotate-180" />}
            label="صفحه قبل"
            onClick={() => go(page - 1)}
            disabled={loading || page <= 1}
          />
          <span className="min-w-[5.2rem] text-center text-[9px]" dir="rtl">صفحه {persianDigits(page)} از {persianDigits(documentPageCount || 1)}</span>
          <RibbonButton
            icon={<IconArrow className="h-3.5 w-3.5" />}
            label="صفحه بعد"
            onClick={() => go(page + 1)}
            disabled={loading || page >= documentPageCount}
          />
        </div>
        {!readOnly ? (
          <div className="flex min-h-[2.35rem] flex-wrap items-center gap-1 rounded border border-[#d3d9de] bg-[#f7f8f2] px-1.5 py-0.5 text-[8px] text-[#33475b]" title="پالت رنگ خط">
            <span className="font-bold">رنگ</span>
            <div className="flex max-w-[14rem] flex-wrap items-center gap-0.5" role="group" aria-label="پالت رنگ خطوط">
              {MARKUP_COLOR_PALETTE.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={`h-3.5 w-3.5 rounded-full border border-[#697783] shadow-sm transition-transform hover:scale-110 focus:outline-none focus:ring-2 focus:ring-[#1f4e79] focus:ring-offset-1 ${color.toLowerCase() === item.value ? "ring-2 ring-[#1f4e79] ring-offset-1" : ""}`}
                  style={{ backgroundColor: item.value }}
                  aria-label={`رنگ خط ${item.label}`}
                  title={`رنگ خط: ${item.label}`}
                  onClick={() => changeColor(item.value)}
                />
              ))}
            </div>
            <label className="flex cursor-pointer items-center gap-0.5 border-r border-[#d3d9de] pr-1" title="رنگ سفارشی خط">
              <span className="sr-only">رنگ سفارشی خط</span>
              <input type="color" aria-label="رنگ سفارشی خط" value={color} onChange={(event) => changeColor(event.target.value)} className="h-4 w-5 cursor-pointer border-0 bg-transparent p-0" />
            </label>
          </div>
        ) : null}
        {!readOnly ? (
          <label className="flex min-h-[2.35rem] min-w-[9rem] items-center gap-1 rounded border border-[#d3d9de] bg-[#f7f8f2] px-1.5 py-0.5 text-[8px] text-[#33475b]" title="ضخامت خط">
            <span className="whitespace-nowrap font-bold">ضخامت</span>
            <input type="range" min={MIN_MARKUP_STROKE_WIDTH} max={MAX_MARKUP_STROKE_WIDTH} step={0.5} value={strokeWidth} onChange={(event) => changeStrokeWidth(Number(event.target.value))} aria-label="ضخامت خط" className="w-16 accent-[#1f4e79]" />
            <output className="min-w-[2.1rem] text-center" dir="ltr">{strokeWidth.toFixed(1)}</output>
          </label>
        ) : null}
        {!readOnly ? (
          <label className="flex min-h-[2.35rem] min-w-[8.5rem] items-center gap-1 rounded border border-[#d3d9de] bg-[#f7f8f2] px-1.5 py-0.5 text-[8px] text-[#33475b]" title="اندازه متن">
            <span className="whitespace-nowrap font-bold">اندازه متن</span>
            <input type="range" min={MIN_FONT_SIZE} max={MAX_FONT_SIZE} step={1} value={fontSize} onChange={(event) => changeFontSize(Number(event.target.value))} aria-label="اندازه متن" className="w-14 accent-[#1f4e79]" />
            <output className="min-w-[1.8rem] text-center" dir="ltr">{fontSize}</output>
          </label>
        ) : null}
        <div className="flex items-stretch gap-0.5 rounded border border-[#d3d9de] bg-[#f7f8f2] px-1">
          <RibbonButton icon={<IconFit className="h-3.5 w-3.5" />} label="عرض متناسب" onClick={() => setZoom(1)} disabled={loading} />
          <RibbonButton icon={<IconZoomOut className="h-3.5 w-3.5" />} label="کوچک‌نمایی" onClick={() => setZoom((value) => Math.max(MIN_ZOOM, value - 0.15))} disabled={loading} />
          <span className="flex min-w-[2.5rem] items-center justify-center text-[9px]" dir="ltr">{Math.round(zoom * 100)}%</span>
          <RibbonButton icon={<IconZoomIn className="h-3.5 w-3.5" />} label="بزرگ‌نمایی" onClick={() => setZoom((value) => Math.min(MAX_ZOOM, value + 0.15))} disabled={loading} />
        </div>
        {!readOnly ? <RibbonButton icon={<IconDelete className="h-3.5 w-3.5" />} label="حذف" disabled={!effectiveSelectedId} onClick={removeSelected} /> : null}
        </div>
      </div>
      {error ? <div className="m-2 border border-[#c98a86] bg-[#fdeeee] p-2 text-[10px] text-[#8a2b25]">{error}</div> : null}
      <div ref={hostRef} className="min-h-0 flex-1 overflow-auto p-4" dir="ltr">
        <div
          ref={pageRef}
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
          <canvas ref={canvasRef} className="absolute inset-0 z-0 block" aria-label="PDF page" />
          <div
            ref={textLayerRef}
            className="absolute inset-0 z-10 select-text overflow-hidden"
            style={{ pointerEvents: !readOnly && isTextTool(tool) ? "auto" : "none" }}
            aria-label="لایه متن PDF برای انتخاب"
          >
            {textLayerItems.map((item, index) => (
              <span
                key={`${item.x}-${item.y}-${index}`}
                className="absolute block whitespace-pre text-transparent"
                style={{
                  left: `${item.x * 100}%`,
                  top: `${item.y * 100}%`,
                  width: `${item.width * 100}%`,
                  height: `${Math.max(item.height, 0.012) * 100}%`,
                  fontSize: `${Math.max(8, item.height * viewport.height * scale * 0.82)}px`,
                  lineHeight: 1,
                  userSelect: "text",
                }}
              >
                {item.text}
              </span>
            ))}
          </div>
          <svg className="pointer-events-none absolute inset-0 z-20 h-full w-full" viewBox="0 0 1 1" preserveAspectRatio="none" role="application" aria-label="لایه نشانه‌گذاری">
            <defs>
              <marker id="markup-arrow-head" markerWidth="0.04" markerHeight="0.04" refX="0.035" refY="0.02" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M0,0 L0.04,0.02 L0,0.04 z" fill="context-stroke" />
              </marker>
            </defs>
            {pageAnnotations.map((annotation) => {
              const box = bounds(annotation);
              const selected = annotation.id === effectiveSelectedId;
              const stroke = colorFor(annotation);
              const width = normalizeMarkupStrokeWidth(annotation.stroke_width) / 1000;
              const entering = annotation.id === lastCreatedId;
              const animationClass = entering
                ? annotation.kind === "highlight" ? "markup-highlight-pulse" : "markup-annotation-enter"
                : undefined;
              const common = {
                "data-markup-id": annotation.id,
                stroke,
                strokeWidth: selected ? width + 0.001 : width,
                pointerEvents: isTextTool(tool) ? "none" as const : "auto" as const,
              };
              const p = annotation.points;
              if (annotation.kind === "freehand") {
                return <polyline key={annotation.id} {...common} className={animationClass} points={p.map((item) => `${item.x},${item.y}`).join(" ")} fill="none" />;
              }
              if (annotation.kind === "arrow") {
                const first = p[0] ?? { x: box.x, y: box.y };
                const last = p[p.length - 1] ?? { x: box.x + box.w, y: box.y + box.h };
                return <line key={annotation.id} {...common} className={animationClass} x1={first.x} y1={first.y} x2={last.x} y2={last.y} markerEnd="url(#markup-arrow-head)" />;
              }
              return (
                <g key={annotation.id} data-markup-id={annotation.id} className={animationClass} pointerEvents={isTextTool(tool) ? "none" : "auto"}>
                  {annotation.kind === "highlight" ? pointRectangles(p).map((item, index) => {
                    const itemBox = bounds({ points: item, kind: annotation.kind });
                    return <rect key={index} {...common} x={itemBox.x} y={itemBox.y} width={itemBox.w} height={itemBox.h} fill={stroke} fillOpacity={0.25} />;
                  }) : null}
                  {annotation.kind === "underline" || annotation.kind === "strikethrough" ? pointRectangles(p).map((item, index) => {
                    const itemBox = bounds({ points: item, kind: annotation.kind });
                    const y = annotation.kind === "underline" ? itemBox.y + itemBox.h : itemBox.y + itemBox.h * 0.52;
                    return <line key={index} {...common} x1={itemBox.x} y1={y} x2={itemBox.x + itemBox.w} y2={y} />;
                  }) : null}
                  {annotation.kind === "rectangle" ? <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill="none" /> : null}
                  {annotation.kind === "ellipse" ? <ellipse {...common} cx={box.x + box.w / 2} cy={box.y + box.h / 2} rx={box.w / 2} ry={box.h / 2} fill="none" /> : null}
                  {annotation.kind === "revision_cloud" ? <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill="none" strokeDasharray="0.012 0.006" /> : null}
                  {annotation.kind === "sticky_note" ? <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill="#fff2a8" fillOpacity={0.9} /> : null}
                  {annotation.kind === "free_text" ? <MarkupTextBox viewport={viewport} box={box} text={annotation.text} color={stroke} fontSize={annotation.font_size ?? fontSize} kind="free_text" /> : null}
                  {annotation.kind === "sticky_note" ? <MarkupTextBox viewport={viewport} box={box} text={annotation.text} color={stroke} fontSize={annotation.font_size ?? fontSize} kind="sticky_note" /> : null}
                  {annotation.kind === "callout" ? (
                    <>
                      {(() => {
                        const anchor = p[0] ?? { x: box.x, y: box.y };
                        const target = { x: clamp(anchor.x, box.x, box.x + box.w), y: clamp(anchor.y, box.y, box.y + box.h) };
                        return <line {...common} x1={anchor.x} y1={anchor.y} x2={target.x} y2={target.y} markerEnd="url(#markup-arrow-head)" />;
                      })()}
                      <rect {...common} x={box.x} y={box.y} width={box.w} height={box.h} fill="white" fillOpacity={0.92} />
                      <MarkupTextBox viewport={viewport} box={box} text={annotation.text} color={stroke} fontSize={annotation.font_size ?? fontSize} kind="callout" />
                    </>
                  ) : null}
                  {selected && !readOnly ? (
                    <rect
                      data-resize-handle="true"
                      x={box.x + box.w - 0.012}
                      y={box.y + box.h - 0.012}
                      width="0.024"
                      height="0.024"
                      fill="white"
                      stroke={stroke}
                      strokeWidth={width}
                      pointerEvents={isTextTool(tool) ? "none" : "auto"}
                      style={{ cursor: "nwse-resize" }}
                    >
                      <title>برای تغییر اندازه، دستگیره گوشه را بکشید</title>
                    </rect>
                  ) : null}
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
