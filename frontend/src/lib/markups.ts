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

export interface MarkupAnnotationInput {
  id?: string;
  kind: MarkupAnnotation["kind"];
  page_no: number;
  points: MarkupPointInput[];
  text?: string | null;
  color: string;
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
