/** API contracts (snake_case mirrors the backend Pydantic schemas). */

export type Role =
  | "submitter"
  | "reviewer"
  | "dcc"
  | "project_manager"
  | "engineering_manager"
  | "system_admin";

/** Where a transmittal sits on the contractor → DCC → management → discipline route. */
export type TransmittalStage = "draft" | "dcc" | "management" | "discipline" | "closed";

export type ReferralStatus = "pending" | "done" | "canceled";

export interface TransmittalReferral {
  id: string;
  transmittal_id: string;
  from_user_id: string | null;
  from_user_name: string | null;
  to_user_id: string;
  to_user_name: string | null;
  discipline_id: string | null;
  discipline_code: string | null;
  action: string;
  note: string | null;
  due_date: string | null;
  status: ReferralStatus;
  responded_at: string | null;
  response_note: string | null;
  created_at: string;
}

/** One project membership, as returned by `/projects/{id}/members`. */
export interface ProjectMember {
  id: string;
  user_id: string;
  full_name: string;
  email: string;
  role: Role;
  discipline_id: string | null;
  discipline_code: string | null;
  discipline_name: string | null;
  is_active: boolean;
}

export interface Membership {
  project_id: string;
  role: Role;
  discipline_id: string | null;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  locale: string;
  auth_provider: string;
  last_login_at: string | null;
  memberships: Membership[];
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface Project {
  id: string;
  code: string;
  name: string;
  client_name: string | null;
  contractor_name: string | null;
  description: string | null;
  is_active: boolean;
  created_at: string;
  settings: Record<string, unknown>;
  document_count?: number;
  member_role?: Role | null;
}

export interface Lookup {
  id: string;
  code: string;
  name: string;
  is_active: boolean;
}

export interface MDLItem {
  id: string;
  project_id: string;
  doc_code: string;
  title: string;
  doc_type: string | null;
  current_rev: string | null;
  rev_seq: number;
  status: string | null;
  weight: number;
  page_count: number | null;
  due_date: string | null;
  planned_issue_date: string | null;
  is_active: boolean;
  discipline_id: string | null;
  unit_id: string | null;
  discipline_code: string | null;
  unit_code: string | null;
  revision_count: number;
  // ---- segments of the six-part code (back-end services/code_structure.py) ----
  phase: string | null;
  serial_no: string | null;
  category: string | null;
  doc_class: string | null;
  basic_detail: string | null;
  poi: string | null;
  remark: string | null;
}

export interface MDLImportEntry {
  row: number;
  severity: "error" | "warning" | "info";
  column: string | null;
  message: string;
}

export interface MDLImportReport {
  status: string;
  batch_id: string | null;
  total_rows: number;
  imported: number;
  updated: number;
  errors: number;
  warnings: number;
  entries: MDLImportEntry[];
}

export interface MDLPreview {
  sheets: string[];
  active_sheet: string | null;
  header_row: number;
  headers: string[];
  suggested_mapping: Record<string, number>;
  sample_rows: (string | number | null)[][];
}

export interface SourceFile {
  id: string;
  original_filename: string;
  size_bytes: number;
  sha256: string;
  page_count: number;
  is_scanned: boolean;
  is_encrypted: boolean;
  av_status: string;
  status: string;
  error_message: string | null;
  created_at: string;
}

export interface UploadSession {
  id: string;
  project_id: string;
  purpose: string;
  mode: string;
  transmittal_type: string;
  status: string;
  note: string | null;
  created_at: string;
  files: SourceFile[];
  results?: Array<{
    id: string;
    doc_code: string | null;
    rev: string | null;
    title: string | null;
    decision: string;
    confidence: number;
    review_status: string;
  }>;
}

export type Decision = "GREEN" | "YELLOW" | "RED" | "PENDING";

export interface Issue {
  id: string;
  rule_code: string;
  severity: "error" | "warning" | "info";
  field_name: string | null;
  message: string;
  message_fa: string | null;
  status: string;
  context: Record<string, unknown>;
}

export interface MatchCandidate {
  mdl_item_id: string;
  doc_code: string;
  title: string;
  discipline_code: string | null;
  score: number;
  method: string;
  reasons: string[];
}

export interface ExtractionResult {
  id: string;
  project_id: string;
  sheet_id: string;
  source_file_id: string;
  session_id: string | null;
  doc_code: string | null;
  rev: string | null;
  title: string | null;
  discipline_code: string | null;
  unit_code: string | null;
  doc_status: string | null;
  doc_date: string | null;
  sheet_no: number | null;
  sheet_count: number | null;
  overall_confidence: number;
  field_confidence: Record<string, number>;
  field_sources: Record<string, string>;
  decision: Decision;
  review_status: string;
  match_score: number;
  match_method: string | null;
  matched_mdl_item_id: string | null;
  human_corrections: Record<string, { model: unknown; human: unknown; by: string }>;
  review_note: string | null;
  created_at: string;
  issues: Issue[];
}

export interface ExtractionResultDetail extends ExtractionResult {
  payload: Record<string, unknown>;
  candidates: MatchCandidate[];
  thumbnail_url: string | null;
  title_block_url: string | null;
  file_name: string | null;
  file_url: string | null;
  summary_fa: string | null;
  mdl_title: string | null;
  mdl_current_rev: string | null;
}

export interface ReviewAction {
  action: "register" | "quarantine" | "reject" | "ignore";
  mdl_item_id?: string | null;
  rev?: string | null;
  title?: string | null;
  notes?: string | null;
  corrections?: Record<string, string>;
  create_transmittal?: boolean;
  purpose?: string;
  transmittal_type?: string;
  recipient_ids?: string[];
  override_issues?: boolean;
}

export interface ReviewOutcome {
  result_id: string;
  action: string;
  decision: string;
  revision_id: string | null;
  transmittal_ids: string[];
  message: string;
}

export type TransmittalStatus =
  | "draft"
  | "pending_approval"
  | "issued"
  | "partially_acknowledged"
  | "acknowledged"
  | "closed"
  | "cancelled";

export interface Transmittal {
  id: string;
  project_id: string;
  number: string | null;
  type: "internal" | "external";
  purpose: string;
  status: TransmittalStatus;
  stage?: TransmittalStage;
  from_org: string | null;
  to_org: string | null;
  due_date: string | null;
  issued_at: string | null;
  remarks: string | null;
  meta: Record<string, unknown>;
  created_at: string;
  line_count: number;
  recipient_count: number;
  acknowledged_count: number;
}

export interface TransmittalLine {
  id: string;
  line_no: number;
  revision_id: string;
  /** Present on rows returned by /revisions/{id}/transmittals. */
  transmittal_id?: string;
  action: string;
  status: string;
  due_date: string | null;
  note: string | null;
  doc_code: string | null;
  title: string | null;
  rev: string | null;
  page_count: number | null;
  discipline_code: string | null;
}

export interface TransmittalRecipient {
  id: string;
  user_id: string;
  action: string;
  ack_status: string;
  acknowledged_at: string | null;
  full_name: string | null;
  email: string | null;
}

export interface TransmittalDetail extends Transmittal {
  lines: TransmittalLine[];
  recipients: TransmittalRecipient[];
  referrals: TransmittalReferral[];
  pdf_url: string | null;
  preview_number: string | null;
  stage: TransmittalStage;
  stage_label: string | null;
}

export interface Comment {
  id: string;
  revision_id: string;
  comment_list_id: string;
  seq_no: number;
  code: string;
  category: string | null;
  text: string;
  page_no: number | null;
  bbox: number[];
  status: "open" | "answered" | "closed";
  disposition: "accepted" | "canceled" | "commented" | "no_comment" | "rejected" | null;
  response_text: string | null;
  responded_at: string | null;
  author_name: string | null;
  created_at: string;
}

export interface CommentList {
  id: string;
  revision_id: string;
  transmittal_id: string | null;
  status: string;
  opened_at: string | null;
  closed_at: string | null;
  comments: Comment[];
}

export interface Notification {
  id: string;
  channel: string;
  template: string;
  subject: string | null;
  body: string | null;
  status: string;
  created_at: string;
  read_at: string | null;
  entity_type: string | null;
  entity_id: string | null;
}

export interface Dashboard {
  project_id: string;
  documents_total: number;
  documents_issued: number;
  revisions_total: number;
  review_queue: number;
  quarantined: number;
  transmittals_total: number;
  transmittals_open: number;
  auto_accept_rate: number;
  issues_by_rule: Record<string, number>;
  uploads_last_7_days: number;
  overdue_documents: number;
}

export interface DDMRule {
  id: string;
  project_id: string;
  name: string;
  discipline_id: string | null;
  unit_id: string | null;
  doc_type: string | null;
  purpose: string | null;
  default_action: string;
  due_days: number;
  priority: number;
  is_active: boolean;
  recipients: TransmittalRecipient[];
}

/* ------------------------------------------------------------------ registration */

export interface RegistrationSettings {
  doc_number_regex: string;
  revision_regex: string;
  confidence_threshold: number;
  discipline_labels: Record<string, string>;
}

export type RegistrationValidationState = "new" | "supersede" | "duplicate" | "blocked";

/**
 * One uploaded PDF on the «ثبت و بارگذاری مدرک» sheet.
 *
 * The first ten editable values are the transmittal columns; `confidence`,
 * `conflicts`, `warnings` and `validation_*` are the audit trail that decides
 * whether the row may be saved without the operator looking at it.
 */
export interface RegistrationRow {
  id: string;
  row_no: number;
  filename: string;
  source_file_id: string | null;
  page_count: number | null;
  doc_no: string | null;
  title: string | null;
  discipline_code: string | null;
  discipline_label: string | null;
  version: string | null;
  poi: string | null;
  doc_status: string | null;
  doc_percent: number;
  mdl_status: string | null;
  confidentiality: string;
  extraction_status: "ok" | "failed" | "manual" | string;
  extraction_error: string | null;
  extraction_model: string | null;
  extraction_attempts: number | null;
  extracted_pages: number[];
  confidence: Record<string, number>;
  conflicts: string[];
  warnings: string[];
  needs_review: boolean;
  review_confirmed: boolean;
  validation_state: RegistrationValidationState;
  validation_messages: string[];
  saved: boolean;
  revision_id: string | null;
  file_url: string | null;
  sha256: string | null;
  page_count_source?: number | null;
}

export interface Registration {
  id: string;
  project_id: string;
  purpose: string;
  status: string;
  stage: string | null;
  number: string | null;
  settings: RegistrationSettings;
  rows: RegistrationRow[];
}
