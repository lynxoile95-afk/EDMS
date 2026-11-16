import { api } from "./api";
import type {
  MarkupAnnotation,
  MarkupSession,
  MarkupSubmitResult,
  MarkupReplyStatus,
} from "./types";

export interface MarkupPointInput {
  x: number;
  y: number;
}

export const DEFAULT_MARKUP_COLOR = "#d64545";
export const DEFAULT_MARKUP_STROKE_WIDTH = 2.5;
export const MIN_MARKUP_STROKE_WIDTH = 1;
export const MAX_MARKUP_STROKE_WIDTH = 10;
export const MARKUP_FINAL_SUBMIT_LABEL = "ثبت نهایی و ثبت در Comment";

export const MARKUP_KIND_LABELS: Record<MarkupAnnotation["kind"], string> = {
  sticky_note: "یادداشت",
  highlight: "هایلایت",
  underline: "خط‌زیر",
  strikethrough: "خط‌خورده",
  rectangle: "مستطیل",
  ellipse: "بیضی",
  revision_cloud: "ابر بازبینی",
  arrow: "پیکان",
  free_text: "متن",
  callout: "کالوت",
  freehand: "قلم",
};

export function markupKindLabel(kind: MarkupAnnotation["kind"]): string {
  return MARKUP_KIND_LABELS[kind] ?? kind;
}

/** The palette deliberately excludes white and near-white strokes on PDF pages. */
export const MARKUP_COLOR_PALETTE = [
  { value: "#d64545", label: "قرمز" },
  { value: "#1d4ed8", label: "آبی" },
  { value: "#15803d", label: "سبز" },
  { value: "#d97706", label: "کهربایی" },
  { value: "#7c3aed", label: "بنفش" },
  { value: "#111827", label: "مشکی" },
  { value: "#0891b2", label: "فیروزه‌ای" },
  { value: "#be123c", label: "زرشکی" },
  { value: "#db2777", label: "صورتی" },
  { value: "#65a30d", label: "لیمویی" },
  { value: "#0f766e", label: "سبزآبی" },
  { value: "#92400e", label: "قهوه‌ای" },
  { value: "#4f46e5", label: "نیلی" },
  { value: "#c2410c", label: "نارنجی تیره" },
] as const;

function isLightMarkupColor(color: string): boolean {
  const channels = [1, 3, 5].map((index) => Number.parseInt(color.slice(index, index + 2), 16));
  return channels.every((channel) => channel >= 238);
}

/** Normalize legacy/invalid white strokes to a visible offline-safe color. */
export function normalizeMarkupColor(value: string | null | undefined): string {
  const color = value ?? DEFAULT_MARKUP_COLOR;
  if (!/^#[0-9a-f]{6}$/i.test(color) || isLightMarkupColor(color)) return DEFAULT_MARKUP_COLOR;
  return color;
}

export function normalizeMarkupStrokeWidth(value: number | null | undefined): number {
  const raw = Number(value ?? DEFAULT_MARKUP_STROKE_WIDTH);
  if (!Number.isFinite(raw)) return DEFAULT_MARKUP_STROKE_WIDTH;
  return Math.min(Math.max(raw, MIN_MARKUP_STROKE_WIDTH), MAX_MARKUP_STROKE_WIDTH);
}

export interface MarkupAnnotationInput {
  id?: string;
  kind: MarkupAnnotation["kind"];
  page_no: number;
  points: MarkupPointInput[];
  text?: string | null;
  color: string;
  stroke_width: number;
}

/**
 * The editor needs a local id before the first save so selection and dragging
 * remain stable. The API id is assigned by the database, so only ids already
 * returned by the current session may be sent back on a later save.
 */
export function toMarkupAnnotationInputs(
  annotations: MarkupAnnotation[],
  persistedAnnotations: MarkupAnnotation[],
): MarkupAnnotationInput[] {
  const persistedIds = new Set(persistedAnnotations.map((annotation) => annotation.id));
  return annotations.map((annotation) => ({
    ...(persistedIds.has(annotation.id) ? { id: annotation.id } : {}),
    kind: annotation.kind,
    page_no: annotation.page_no,
    points: annotation.points,
    text: annotation.text,
    color: normalizeMarkupColor(annotation.color),
    stroke_width: normalizeMarkupStrokeWidth(annotation.stroke_width),
  }));
}

export async function createMarkupSession(transmittalId: string, fileId: string): Promise<MarkupSession> {
  return api.post<MarkupSession>("/markups", { transmittal_id: transmittalId, file_id: fileId });
}

export async function getMarkupSession(sessionId: string): Promise<MarkupSession> {
  return api.get<MarkupSession>(`/markups/${sessionId}`);
}

export async function listMarkupSessions(fileId: string, transmittalId?: string): Promise<MarkupSession[]> {
  const query = new URLSearchParams({ file_id: fileId });
  if (transmittalId) query.set("transmittal_id", transmittalId);
  return api.get<MarkupSession[]>(`/markups?${query.toString()}`);
}

export async function saveMarkupAnnotations(
  sessionId: string,
  annotations: MarkupAnnotationInput[],
  expectedVersion?: number,
): Promise<MarkupSession> {
  return api.put<MarkupSession>(`/markups/${sessionId}/annotations`, {
    annotations,
    expected_version: expectedVersion ?? null,
  });
}

export async function submitMarkupSession(sessionId: string): Promise<MarkupSubmitResult> {
  return api.post<MarkupSubmitResult>(`/markups/${sessionId}/submit`);
}

export async function replyToMarkup(
  sessionId: string,
  number: number,
  text: string,
  status: MarkupReplyStatus,
): Promise<MarkupSession> {
  return api.post<MarkupSession>(`/markups/${sessionId}/annotations/${number}/reply`, { text, status });
}

export async function downloadMarkupOutput(transmittalId: string, attachmentId: string, filename: string): Promise<void> {
  return api.downloadBlob(`/transmittals/${transmittalId}/attachments/${attachmentId}/download`, filename);
}
