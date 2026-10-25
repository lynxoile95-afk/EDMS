"""Project-scoped configuration with defaults and safe merging."""

from __future__ import annotations

import copy
from typing import Any

#: The default now accepts the customer's six-part code
#: (``2102-20-EB-GN-MDL-9001``) *and* the four-part legacy shape
#: (``PRJ-CIV-DR-0101``). See ``app/services/code_structure.py``.
from app.services.code_structure import DEFAULT_CODE_PATTERN as DEFAULT_DOC_CODE_PATTERN  # noqa: E402

DEFAULT_PROJECT_SETTINGS: dict[str, Any] = {
    # ---- document identity ----
    "doc_code_pattern": DEFAULT_DOC_CODE_PATTERN,
    "rev_scheme": "auto",  # auto | alpha | numeric | p_prefix
    "case_sensitive_codes": False,
    # ---- decision thresholds (0..1) ----
    "thresholds": {
        "green": 0.85,            # overall confidence for auto-processing
        "yellow_min": 0.45,       # below this -> RED
        "field_min": 0.60,        # any required field below this -> YELLOW
        "code_similarity": 0.80,  # fuzzy code candidate acceptance
        "title_similarity": 0.62, # title mismatch warning threshold
    },
    # ---- human gates ----
    "always_require_dcc_approval": True,   # phase-1 default: every file passes DCC
    "auto_issue_green": False,             # GREEN may auto-create DRAFT transmittal only
    "require_pm_approval_purposes": ["IFC", "ASB"],
    # ---- validation behaviour ----
    "discipline_mismatch_severity": "warning",  # warning | error
    "unit_mismatch_severity": "warning",
    "status_blocklist": ["FINAL", "AS-BUILT", "ASB"],  # cannot be resubmitted
    "allowed_resubmit_statuses": ["DRAFT", "IFA", "IFR", "REV"],
    "warn_on_filename_mismatch": True,
    "warn_on_rev_skip": True,
    "reject_duplicate_hash": True,
    # ---- transmittal ----
    "grouping_strategy": "by_recipients",  # by_recipients | by_discipline | one_per_session
    "transmittal_number_format": "TRN-{project}-{yy}{mm}-{seq:04d}",
    # Shown to the user before the first save; the permanent number is only
    # allocated on issue (matches the reference system's TM-…-Temp####).
    "transmittal_temp_number_format": "TM-{project}-Temp{seq:04d}",
    "transmittal_sequence_scope": "monthly",  # monthly | yearly | project
    "default_due_days": 14,
    "external_transmittal_requires_approval": True,
    # ---- extraction ----
    "ocr_fallback": True,
    "vlm_fallback": False,
    "vlm_max_calls_per_document": 2,
    "page_split_enabled": True,
    "title_block_required": True,
    # ---- transmittal registration screen ----
    "registration": {
        # validated in code, never by the model
        "doc_number_regex": r"^\d{4}-\d{2}-[A-Z]{2}-[A-Z]{2}-[A-Z]{3}-\d{4}$",
        "revision_regex": r"^[A-Z]\d{1,2}$",
        # any field below this, or a non-empty `conflicts`, marks the cell yellow
        "confidence_threshold": 0.8,
        # the editable lookup table behind the «Discipline» column
        "discipline_labels": {
            "ME": "ME-Mechanical",
            "CV": "CV-Civil",
            "EL": "EL-Electrical",
            "PI": "PI-Piping",
            "IN": "IN-Instrument",
            "PR": "PR-Process",
            "GN": "GN-General",
            "QC": "QC-Quality Control",
            "HS": "HS-HSE",
            "IS": "IS-Insulation",
        },
        "default_confidentiality": "Normal",
        # page 1 first; if it carries no title block, page 2 is tried before giving up
        "extraction_pages": [1, 2],
        "extraction_retries": 2,
    },
    # ---- display ----
    "calendar": "jalali",
    "timezone": "Asia/Tehran",
}

READONLY_SETTING_KEYS = {"timezone"}


def merge_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge project overrides over the defaults (never mutates inputs)."""
    merged = copy.deepcopy(DEFAULT_PROJECT_SETTINGS)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def resolve(settings: dict[str, Any] | None, path: str, fallback: Any = None) -> Any:
    """Read a dotted path such as ``thresholds.green`` with a fallback."""
    node: Any = merge_settings(settings) if settings is not None else copy.deepcopy(DEFAULT_PROJECT_SETTINGS)
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return fallback
        node = node[part]
    return node


def thresholds(settings: dict[str, Any] | None) -> dict[str, float]:
    merged = merge_settings(settings)
    return merged["thresholds"]
