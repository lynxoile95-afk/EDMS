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
export const MAX_MARKUP_STROKE_WIDTH = 6;

/** The palette deliberately excludes white and near-white strokes on PDF pages. */
export const MARKUP_COLOR_PALETTE = [
  { value: "#d64545", label: "قرمز" },
  { value: "#1f4e79", label: "آبی" },
  { value: "#1f8a70", label: "سبز" },
  { value: "#d97706", label: "نارنجی" },
  { value: "#7c3aed", label: "بنفش" },
  { value: "#111827", label: "مشکی" },
  { value: "#0f766e", label: "سبزآبی" },
  { value: "#b91c1c", label: "قرمز تیره" },
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
