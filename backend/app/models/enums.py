"""Domain enumerations (stored as strings for portability & migrations)."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class ProjectRole(StrEnum):
    """Role inside a project.

    The review route this system implements is:

    ``SUBMITTER`` (contractor) creates the sheet → ``DCC`` (document control)
    controls and forwards it → ``ENGINEERING_MANAGER`` refers it to the right
    discipline → the discipline ``REVIEWER`` answers → back to management.
    ``PROJECT_MANAGER`` shares the management step.
    """

    SUBMITTER = "submitter"
    REVIEWER = "reviewer"
    DCC = "dcc"
    PROJECT_MANAGER = "project_manager"
    ENGINEERING_MANAGER = "engineering_manager"
    SYSTEM_ADMIN = "system_admin"


class PurposeOfIssue(StrEnum):
    """Purpose of Issue (P.O.I).

    The vocabulary of the organisation's existing EDMS is IPC / IFE / IFP / IFC
    (see ``docs/03-reference-system-gap-analysis.md``); IFR / IFA / IFI / RTC /
    ASB are kept because they are used by the upload wizard and the DDM rules.
    """

    IFR = "IFR"  # Issued for Review
    IFA = "IFA"  # Issued for Approval
    IFC = "IFC"  # Issued for Construction
    IFI = "IFI"  # Issued for Information
    IFE = "IFE"  # Issued for Engineering
    IFP = "IFP"  # Issued for Purchase
    IPC = "IPC"  # Issued for Procurement / Purchase (reference-system wording)
    RESPONSE_TO_COMMENT = "RTC"  # Response to Comment
    AS_BUILT = "ASB"  # As-Built


#: Single source of truth for validating a P.O.I in request schemas.
PURPOSE_VALUES: tuple[str, ...] = tuple(member.value for member in PurposeOfIssue)
PURPOSE_PATTERN = r"^(" + "|".join(PURPOSE_VALUES) + r")$"


class SubmissionMode(StrEnum):
    NEW_DOCUMENT = "new_document"
    REVISION = "revision"
    RESPONSE_TO_COMMENT = "response_to_comment"


class Decision(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    PENDING = "PENDING"


class ExtractionSource(StrEnum):
    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    VLM = "vlm"
    RULE = "rule"
    MANUAL = "manual"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueStatus(StrEnum):
    OPEN = "open"
    OVERRIDDEN = "overridden"
    RESOLVED = "resolved"


class ExtractionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class RevisionStatus(StrEnum):
    REGISTERED = "registered"
    ISSUED = "issued"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class UploadStatus(StrEnum):
    CREATED = "created"
    UPLOADING = "uploading"
    COMMITTED = "committed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FileStatus(StrEnum):
    UPLOADED = "uploaded"
    SCANNING = "scanning"
    QUARANTINED = "quarantined"
    PROCESSING = "processing"
    PROCESSED = "processed"
    REJECTED = "rejected"
    FAILED = "failed"


class AVStatus(StrEnum):
    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"
    SKIPPED = "skipped"


class TransmittalType(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class TransmittalStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    ISSUED = "issued"
    PARTIALLY_ACKNOWLEDGED = "partially_acknowledged"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TransmittalStage(StrEnum):
    """Where the sheet sits in the review route (kept apart from its status)."""

    DRAFT = "draft"            # contractor is still assembling it
    DCC = "dcc"                # document control is checking it
    MANAGEMENT = "management"  # project / engineering manager is directing it
    DISCIPLINE = "discipline"  # a discipline engineer is reviewing it
    CLOSED = "closed"          # route finished


class ReferralStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    CANCELED = "canceled"


class TransmittalLineStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    ACKNOWLEDGED = "acknowledged"
    SUPERSEDED = "superseded"
    CLOSED = "closed"


class ActionRequired(StrEnum):
    REVIEW = "review"
    APPROVE = "approve"
    INFO = "info"
    FOR_CONSTRUCTION = "for_construction"


class AcknowledgementStatus(StrEnum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    DECLINED = "declined"


class CommentCode(StrEnum):
    CODE1 = "1"  # reject / major
    CODE2 = "2"  # revise and resubmit
    CODE3 = "3"  # information / minor


class CommentDisposition(StrEnum):
    """Disposition of a comment, as printed by the reference system.

    A separate axis from the 1/2/3 comment code: the code says how serious the
    remark is, the disposition says what the coordinator decided about it once
    the contractor answered.
    """

    ACCEPTED = "accepted"
    CANCELED = "canceled"
    COMMENTED = "commented"
    NO_COMMENT = "no_comment"
    REJECTED = "rejected"


DISPOSITION_VALUES: tuple[str, ...] = tuple(member.value for member in CommentDisposition)


class CommentStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    READ = "read"


class MDLImportStatus(StrEnum):
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class GroupingStrategy(StrEnum):
    BY_RECIPIENTS = "by_recipients"      # one transmittal per recipient set + purpose
    BY_DISCIPLINE = "by_discipline"      # split further per discipline
    ONE_PER_SESSION = "one_per_session"  # everything in a single transmittal
