"""GREEN / YELLOW / RED decision engine.

Core principle of the product: **AI proposes, rules decide.**  This module is a
pure function of (extracted values, confidence, MDL match, project settings) and
contains no I/O — which is what makes it exhaustively testable.

Every rule has a stable code (``R01``…``R22``) that is persisted on
``validation_issues.rule_code`` so that reports and the frontend can explain a
decision without re-running it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import Decision, IssueStatus, Severity
from app.services.code_matching import MatchCandidate
from app.services.normalization import is_valid_code, normalize_code, normalize_rev
from app.services.project_settings import merge_settings
from app.services.revisions import (
    EQUAL,
    INVALID,
    LOWER,
    NEW,
    SKIP,
    compare_revs,
    title_similarity,
)

#: document-level fields that must be extracted, with their weight in the
#: overall confidence.  Weights sum to 1.0.
FIELD_WEIGHTS: dict[str, float] = {
    "doc_code": 0.40,
    "rev": 0.25,
    "title": 0.15,
    "discipline_code": 0.10,
    "doc_status": 0.05,
    "unit_code": 0.05,
}

REQUIRED_FIELDS = ("doc_code", "rev", "title")


@dataclass(slots=True)
class Issue:
    rule_code: str
    severity: Severity
    message: str          # English (machine/log)
    message_fa: str       # Persian (user facing)
    field_name: str | None = None
    context: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "rule_code": self.rule_code,
            "severity": self.severity.value if hasattr(self.severity, "value") else str(self.severity),
            "field_name": self.field_name,
            "message": self.message,
            "message_fa": self.message_fa,
            "status": IssueStatus.OPEN.value,
            "context": self.context,
        }


@dataclass(slots=True)
class DecisionOutcome:
    decision: Decision
    overall_confidence: float
    issues: list[Issue] = field(default_factory=list)
    matched_item_id: str | None = None
    match_score: float = 0.0
    match_method: str | None = None
    requires_dcc: bool = True
    auto_issue_allowed: bool = False
    summary_fa: str = ""

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]


@dataclass(slots=True)
class DocumentFacts:
    """Everything the engine needs to know about one uploaded sheet."""

    doc_code: str | None = None
    rev: str | None = None
    title: str | None = None
    discipline_code: str | None = None
    unit_code: str | None = None
    doc_status: str | None = None
    sheet_no: int | None = None
    sheet_count: int | None = None
    field_confidence: dict[str, float] = field(default_factory=dict)
    methods_used: list[str] = field(default_factory=list)
    filename: str | None = None
    duplicate_of: str | None = None          # existing source_file id with same sha256
    in_session_duplicate: bool = False       # same code twice in one upload session
    title_block_found: bool = True
    extraction_failed: bool = False


def compute_overall_confidence(field_confidence: dict[str, float]) -> float:
    """Weighted mean over fields that were attempted.

    Missing required fields contribute 0, so a document with no code can never
    score high simply because its title was read confidently.
    """
    total_weight = sum(FIELD_WEIGHTS.values())
    score = 0.0
    for name, weight in FIELD_WEIGHTS.items():
        score += weight * max(0.0, min(1.0, field_confidence.get(name, 0.0)))
    return round(score / total_weight, 4)


def decide(
    facts: DocumentFacts,
    *,
    mdl_current_rev: str | None = None,
    mdl_title: str | None = None,
    mdl_discipline_code: str | None = None,
    mdl_unit_code: str | None = None,
    mdl_status: str | None = None,
    mdl_page_count: int | None = None,
    candidates: list[MatchCandidate] | None = None,
    settings: dict | None = None,
) -> DecisionOutcome:
    """Run all rules and return the decision.

    ``candidates`` is the output of :class:`CodeMatcher`; ``candidates[0]`` with a
    score above the configured threshold is treated as a resolved match.
    """
    cfg = merge_settings(settings)
    thresholds = cfg["thresholds"]
    issues: list[Issue] = []

    # ---------------------------------------------------------------- code
    best: MatchCandidate | None = candidates[0] if candidates else None
    code_norm = normalize_code(facts.doc_code)
    # If a high-confidence MDL candidate exists, its canonical code is the one
    # that must satisfy the project pattern (the raw value may contain OCR noise
    # that the matcher has already corrected).
    pattern_target = (
        normalize_code(best.doc_code)
        if best is not None and best.score >= thresholds["code_similarity"]
        else code_norm
    )
    if facts.extraction_failed:
        issues.append(
            Issue("R22", Severity.ERROR, "Extraction failed", "استخراج اطلاعات از فایل ناموفق بود.",
                  context={"methods": facts.methods_used})
        )
    if not code_norm:
        issues.append(
            Issue("R01", Severity.ERROR, "Document code missing",
                  "کد مدرک استخراج نشد. لطفاً کادر مشخصات را بررسی یا فایل را اصلاح کنید.",
                  field_name="doc_code")
        )
    elif not is_valid_code(pattern_target, cfg["doc_code_pattern"]):
        issues.append(
            Issue("R02", Severity.ERROR, "Document code violates project pattern",
                  f"کد مدرک «{facts.doc_code}» با الگوی شماره‌گذاری پروژه سازگار نیست.",
                  field_name="doc_code", context={"pattern": cfg["doc_code_pattern"]})
        )

    match_score = best.score if best else 0.0
    match_method = best.method if best else None
    exact = best is not None and best.method == "exact"

    if code_norm and best is None:
        issues.append(
            Issue("R03", Severity.ERROR, "Document code not found in MDL",
                  "این کد مدرک در فهرست مدارک پروژه (MDL) وجود ندارد. مدرک قرنطینه شد.",
                  field_name="doc_code")
        )
    elif best is not None and not exact:
        if best.score >= thresholds["code_similarity"]:
            issues.append(
                Issue("R04", Severity.WARNING, "Near match with MDL",
                      f"کد مدرک دقیقاً یافت نشد؛ نزدیک‌ترین گزینه «{best.doc_code}» با شباهت "
                      f"{best.score:.0%} پیشنهاد می‌شود.",
                      field_name="doc_code",
                      context={"candidates": [c.as_dict() for c in (candidates or [])[:3]]})
            )
        else:
            issues.append(
                Issue("R03", Severity.ERROR, "Document code not found in MDL (no close match)",
                      "کد مدرک در MDL یافت نشد و گزینه نزدیکی هم وجود ندارد.",
                      field_name="doc_code",
                      context={"best_score": round(best.score, 3)})
            )

    # ------------------------------------------------------------ revision
    rev_token = normalize_rev(facts.rev)
    scheme = cfg["rev_scheme"]
    if not rev_token:
        issues.append(
            Issue("R05", Severity.ERROR, "Revision missing",
                  "شماره بازنگری (Rev) استخراج نشد.", field_name="rev")
        )
    elif mdl_current_rev:
        verdict = compare_revs(mdl_current_rev, rev_token, scheme)
        if verdict == LOWER:
            issues.append(
                Issue("R07", Severity.ERROR, "Revision is older than current",
                      f"بازنگری «{rev_token}» قدیمی‌تر از بازنگری جاری «{mdl_current_rev}» است.",
                      field_name="rev", context={"current": mdl_current_rev})
            )
        elif verdict == EQUAL:
            issues.append(
                Issue("R06", Severity.WARNING, "Revision equals current revision",
                      f"بازنگری «{rev_token}» با بازنگری جاری یکسان است؛ احتمال ارسال تکراری وجود دارد.",
                      field_name="rev", context={"current": mdl_current_rev})
            )
        elif verdict == SKIP and cfg["warn_on_rev_skip"]:
            issues.append(
                Issue("R08", Severity.WARNING, "Revision jump",
                      f"پرش در بازنگری: از «{mdl_current_rev}» به «{rev_token}». لطفاً تأیید کنید.",
                      field_name="rev", context={"current": mdl_current_rev})
            )
        elif verdict == INVALID:
            issues.append(
                Issue("R05", Severity.ERROR, "Revision token is invalid",
                      f"شماره بازنگری «{facts.rev}» معتبر نیست.", field_name="rev")
            )

    # --------------------------------------------------------------- title
    if not (facts.title or "").strip():
        issues.append(
            Issue("R09", Severity.WARNING, "Title missing",
                  "عنوان مدرک استخراج نشد.", field_name="title")
        )
    elif mdl_title:
        similarity = title_similarity(facts.title, mdl_title)
        if similarity < thresholds["title_similarity"]:
            issues.append(
                Issue("R09", Severity.WARNING, "Title mismatch",
                      f"عنوان مدرک با MDL هم‌خوانی ندارد (شباهت {similarity:.0%}).",
                      field_name="title",
                      context={"similarity": round(similarity, 3), "mdl_title": mdl_title})
            )

    # ---------------------------------------------------------- discipline
    if facts.discipline_code and mdl_discipline_code:
        if normalize_code(facts.discipline_code) != normalize_code(mdl_discipline_code):
            severity = Severity.ERROR if cfg["discipline_mismatch_severity"] == "error" else Severity.WARNING
            issues.append(
                Issue("R10", severity, "Discipline mismatch",
                      f"دیسیپلین مدرک «{facts.discipline_code}» با MDL «{mdl_discipline_code}» مغایر است.",
                      field_name="discipline_code",
                      context={"found": facts.discipline_code, "expected": mdl_discipline_code})
            )

    # ----------------------------------------------------------------- unit
    if facts.unit_code and mdl_unit_code:
        if normalize_code(facts.unit_code) != normalize_code(mdl_unit_code):
            severity = Severity.ERROR if cfg["unit_mismatch_severity"] == "error" else Severity.WARNING
            issues.append(
                Issue("R11", severity, "Unit mismatch",
                      f"یونیت مدرک «{facts.unit_code}» با MDL «{mdl_unit_code}» مغایر است.",
                      field_name="unit_code")
            )

    # --------------------------------------------------------------- status
    blocked = {s.upper() for s in cfg["status_blocklist"]}
    if facts.doc_status and facts.doc_status.strip().upper() in blocked:
        issues.append(
            Issue("R12", Severity.ERROR, "Document status blocks resubmission",
                  f"وضعیت مدرک «{facts.doc_status}» اجازه ارسال مجدد نمی‌دهد.",
                  field_name="doc_status")
        )
    if mdl_status and mdl_status.strip().upper() in blocked:
        issues.append(
            Issue("R12", Severity.ERROR, "MDL status blocks new revision",
                  f"وضعیت مدرک در MDL «{mdl_status}» اجازه ثبت بازنگری جدید نمی‌دهد.",
                  field_name="status")
        )

    # ------------------------------------------------------------- filename
    if cfg["warn_on_filename_mismatch"] and facts.filename and code_norm:
        filename_norm = normalize_code(".".join(facts.filename.split(".")[:-1]) or facts.filename)
        if code_norm not in filename_norm and filename_norm and code_norm.replace("-", "") not in filename_norm.replace("-", ""):
            issues.append(
                Issue("R13", Severity.WARNING, "Filename differs from title-block code",
                      f"نام فایل («{facts.filename}») با کد داخل نقشه («{facts.doc_code}») هم‌خوانی ندارد.",
                      field_name="doc_code")
            )

    # ------------------------------------------------------------ duplicate
    if facts.duplicate_of and cfg["reject_duplicate_hash"]:
        issues.append(
            Issue("R14", Severity.ERROR, "Duplicate file (same SHA-256)",
                  "این فایل قبلاً بارگذاری شده است (اثر انگشت یکسان).",
                  context={"duplicate_of": str(facts.duplicate_of)})
        )
    if facts.in_session_duplicate:
        issues.append(
            Issue("R18", Severity.ERROR, "Same document code uploaded twice in one session",
                  "کد این مدرک در همین جلسه بارگذاری تکراری شده است.",
                  field_name="doc_code")
        )

    # ------------------------------------------------------------ quality
    for name in REQUIRED_FIELDS:
        confidence = facts.field_confidence.get(name, 0.0)
        if facts.doc_code and confidence and confidence < cfg["thresholds"]["field_min"]:
            issues.append(
                Issue("R15", Severity.WARNING, f"Low confidence for {name}",
                      f"اطمینان استخراج فیلد «{name}» پایین است ({confidence:.0%}).",
                      field_name=name, context={"confidence": round(confidence, 3)})
            )

    if "ocr" in facts.methods_used:
        issues.append(
            Issue("R16", Severity.INFO, "OCR was used",
                  "این مدرک اسکن‌شده است و اطلاعات با OCR خوانده شده؛ بازبینی چشمی توصیه می‌شود.")
        )
    if "vlm" in facts.methods_used:
        issues.append(
            Issue("R17", Severity.WARNING, "VLM fallback was used",
                  "برای خواندن اطلاعات از مدل بینایی-زبانی استفاده شده است؛ تأیید انسانی الزامی است.")
        )
    if not facts.title_block_found and cfg["title_block_required"]:
        issues.append(
            Issue("R20", Severity.WARNING, "Title block not located",
                  "کادر مشخصات (Title Block) در صفحه پیدا نشد؛ استخراج بر پایه کل صفحه انجام شد.")
        )
    if (
        mdl_page_count
        and facts.sheet_count
        and facts.sheet_count != mdl_page_count
        and facts.sheet_no in (None, 1)
    ):
        issues.append(
            Issue("R19", Severity.INFO, "Sheet count differs from MDL",
                  f"تعداد شیت ({facts.sheet_count}) با MDL ({mdl_page_count}) یکسان نیست.",
                  field_name="sheet_count")
        )

    # ------------------------------------------------------------- decision
    overall = compute_overall_confidence(facts.field_confidence)
    has_error = any(i.severity is Severity.ERROR for i in issues)

    if has_error:
        decision = Decision.RED
    elif overall >= thresholds["green"] and not any(
        i.severity is Severity.WARNING for i in issues
    ):
        decision = Decision.GREEN
    else:
        decision = Decision.YELLOW

    requires_dcc = bool(cfg["always_require_dcc_approval"]) or decision is not Decision.GREEN
    auto_issue = decision is Decision.GREEN and not cfg["always_require_dcc_approval"] and bool(cfg["auto_issue_green"])

    summary = {
        Decision.GREEN: "همه فیلدها با اطمینان بالا خوانده و با MDL تطبیق داده شد.",
        Decision.YELLOW: "اطلاعات خوانده شد اما نیاز به تأیید کارشناس کنترل مدارک دارد.",
        Decision.RED: "مدرک رد یا قرنطینه شد؛ دلیل در فهرست ایرادها آمده است.",
    }[decision]

    return DecisionOutcome(
        decision=decision,
        overall_confidence=overall,
        issues=issues,
        matched_item_id=str(best.mdl_item_id) if best else None,
        match_score=round(match_score, 4),
        match_method=match_method,
        requires_dcc=requires_dcc,
        auto_issue_allowed=auto_issue,
        summary_fa=summary,
    )
