"""The customer's document-coding structure, as data.

The attached MDL (`2102-20-ED-GN-MDL-9001-A8.pdf`) numbers every document as

    2102 - 20 - EB - GN - MDL - 9001
    │      │    │    │    │     └─ SERIAL  (four digits, grouped per discipline:
    │      │    │    │    │                 9001+ = general, 1001+ = civil, …)
    │      │    │    │    └─────── DOC. TYPE (MDL, MDR, DWG, SPE, DS H, …)
    │      │    │    └──────────── DISC.     (GN general, CV civil, ME mechanical,
    │      │    │                             EL electrical, IN instrument, PI piping,
    │      │    │                             PR process, HS HSE, QC quality control)
    │      │    └───────────────── PHASE     (EB basic, ED detail)
    │      └────────────────────── UNIT      (two digits, 20 here)
    └───────────────────────────── PROJECT CODE

This module keeps that structure in one place: the regex the decider validates with,
a parser that splits a code back into its fields, a builder, and the vocabulary the
import uses to name lookups instead of creating bare placeholders.

Older projects in this repo (and the demo data) use a four-segment code such as
``PRJ-CIV-DR-0101``. The default pattern accepts **both** shapes; a project that wants
exactly one can set ``doc_code_pattern`` in its settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Default validation pattern. Named groups make the parse and the R02 message precise.
#: The unit+phase pair is optional so four-segment legacy codes stay valid.
DEFAULT_CODE_PATTERN = (
    r"^(?P<project>[A-Z0-9]{2,6})"
    r"(?:-(?P<unit>\d{2})-(?P<phase>[A-Z]{2}))?"
    r"-(?P<discipline>[A-Z]{2,4})-(?P<type>[A-Z]{1,4})-(?P<serial>\d{3,5})$"
)

#: Human-readable pattern, used in error messages and in the UI guide.
CODE_PATTERN_LABEL = "PROJECT-UNIT-PHASE-DISC-TYPE-SERIAL"
CODE_PATTERN_EXAMPLE = "2102-20-EB-GN-MDL-9001"
CODE_PATTERN_LABEL_SHORT = "PROJECT-DISC-TYPE-SERIAL"
CODE_PATTERN_EXAMPLE_SHORT = "PRJ-CIV-DR-0101"


@dataclass(frozen=True)
class CodeSegment:
    key: str
    label_fa: str
    label_en: str
    regex: str
    example: str
    note_fa: str


#: Ordered segments of the six-part structure.
CODE_SEGMENTS: tuple[CodeSegment, ...] = (
    CodeSegment("project", "کد پروژه", "PROJECT CODE", r"[A-Z0-9]{2,6}", "2102",
                "شناسه پروژه/کارفرما؛ در همه مدارک پروژه ثابت است."),
    CodeSegment("unit", "یونیت", "UNIT", r"\d{2}", "20",
                "واحد فرآیندی؛ دو رقم."),
    CodeSegment("phase", "فاز", "PHASE", r"[A-Z]{2}", "EB",
                "EB = Basic، ED = Detail."),
    CodeSegment("discipline", "دیسیپلین", "DISC.", r"[A-Z]{2}", "GN",
                "GN عمومی، CV سیویل، ME مکانیک، EL برق، IN ابزار دقیق، PI پایپینگ، "
                "PR فرآیند، HS ایمنی، QC کنترل کیفیت."),
    CodeSegment("type", "نوع مدرک", "DOC. TYPE", r"[A-Z]{2,4}", "MDL",
                "نوع مدرک: MDL، MDR، DWG، SPE، SCH، RPT، PFD، PID، …"),
    CodeSegment("serial", "شماره سریال", "SER. NO.", r"\d{3,5}", "9001",
                "شماره ترتیبی؛ در هر دیسیپلین یک بازه دارد (مثلاً ۹۰۰۰ برای عمومی)."),
)

#: Discipline codes seen in the customer's MDL, with their names.
DISCIPLINE_VOCAB: dict[str, tuple[str, str]] = {
    "GN": ("General / عمومی", "GENERAL"),
    "CV": ("Civil & Structure / سیویل و سازه", "CIVIL & STRUCTURE"),
    "ME": ("Mechanical / مکانیک", "MECHANICAL"),
    "EL": ("Electrical / برق", "ELECTRICAL"),
    "IN": ("Instrument / ابزار دقیق", "INSTRUMENTATION"),
    "PI": ("Piping / پایپینگ", "PIPING"),
    "PR": ("Process / فرآیند", "PROCESS"),
    "HS": ("HSE / ایمنی و بهداشت", "HSE"),
    "QC": ("Quality Control / کنترل کیفیت", "QUALITY CONTROL"),
    "IS": ("Insulation / عایق‌کاری", "INSULATION"),
}

#: Document types seen in the customer's MDL, with a Persian caption.
DOC_TYPE_VOCAB: dict[str, str] = {
    "MDL": "فهرست مدارک اصلی",
    "MDR": "ثبت مدارک",
    "DWG": "نقشه",
    "STU": "مطالعات سازه",
    "SPE": "مشخصات فنی",
    "SPC": "شرایط فنی",
    "SCH": "جدول/دیاگرام",
    "WBS": "ساختار شکست کار",
    "MNL": "راهنما",
    "PRC": "رویه",
    "RPT": "گزارش",
    "MRP": "گزارش ماهانه پیشرفت",
    "MTO": "برآورد مصالح",
    "MRQ": "درخواست مصالح",
    "CAL": "محاسبات",
    "DSH": "دیتاشیت",
    "DBS": "پایه طراحی",
    "DCR": "مدرک تغییر طراحی",
    "DGR": "دیاگرام",
    "DIG": "دیاگرام",
    "DPH": "طرح‌ها",
    "ISO": "ایزومتریک",
    "ITP": "برنامه بازرسی و آزمون",
    "INX": "فهرست پیوست",
    "FDB": "پیش‌طراحی",
    "FOR": "فرم",
    "LST": "فهرست",
    "PDS": "دیتاشیت فرآیند",
    "PFD": "دیاگرام جریان فرآیند",
    "PID": "دیاگرام لوله‌کشی و ابزار دقیق",
    "PHY": "فیزیکی",
    "SLD": "دیاگرام تک‌خطی",
    "UFD": "دیاگرام جریان کاربردی",
    "VLS": "فهرست فروشندگان",
    "WPS": "دستورالعمل جوشکاری",
    "ORC": "سازمان/چارت",
    "EQL": "فهرست تجهیزات",
    "KPL": "کاپل",
}

#: `CLASS` column values in the sample (A/R) and `BASIC/DETAIL` (B/D).
DOC_CLASS_VALUES: tuple[str, ...] = ("A", "R")
BASIC_DETAIL_VALUES: tuple[str, ...] = ("B", "D")


#: Serial numbers are blocked per discipline in the sample MDL: 1xx process, 1xxx
#: civil, 4xxx mechanical, 5xxx piping, 6xxx electrical, 7xxx instrument, 9xxx the
#: shared general/QC/HSE block. A row outside its discipline's block is reported as a
#: **warning**, never an error — the sheet is the authority, we only flag the odd one.
SERIAL_BLOCKS: dict[str, tuple[int, int]] = {
    "PR": (100, 199),
    "CV": (1000, 1999),
    "CIV": (1000, 1999),
    "ME": (4000, 4999),
    "MEC": (4000, 4999),
    "PI": (5000, 5999),
    "EL": (6000, 6999),
    "ELE": (6000, 6999),
    "IN": (7000, 7999),
    "INS": (7000, 7999),
    "GN": (9000, 9999),
    "QC": (9000, 9999),
    "HS": (9000, 9999),
    "IS": (9000, 9999),
}


def serial_block(discipline: str | None, serial: str | None) -> tuple[int, int] | None:
    """The serial block this discipline uses, if we know it."""
    if not discipline or not serial:
        return None
    return SERIAL_BLOCKS.get(discipline.strip().upper())


def serial_outside_block(discipline: str | None, serial: str | None) -> tuple[int, int] | None:
    """Return the expected block when the serial falls outside it, else ``None``."""
    block = serial_block(discipline, serial)
    if block is None:
        return None
    try:
        value = int(str(serial).strip())
    except (TypeError, ValueError):
        return None
    low, high = block
    return None if low <= value <= high else block


def parse_code(code: str, pattern: str | None = None) -> dict[str, str]:
    """Split a document code into its segments.

    Returns ``{}`` when the code does not match, and only the segments that are
    actually present (a four-segment code has no unit/phase).
    """
    text = (code or "").strip().upper()
    if not text:
        return {}
    try:
        match = re.fullmatch(pattern or DEFAULT_CODE_PATTERN, text)
    except re.error:
        match = re.fullmatch(DEFAULT_CODE_PATTERN, text)
    if match is None:
        return {}
    return {key: value for key, value in match.groupdict().items() if value}


def build_code(
    *,
    project: str,
    discipline: str,
    doc_type: str,
    serial: str,
    unit: str | None = None,
    phase: str | None = None,
) -> str:
    """Compose a document code from its parts (the inverse of :func:`parse_code`)."""
    parts = [project.strip().upper()]
    if unit:
        parts.append(str(unit).strip().upper())
    if phase:
        parts.append(phase.strip().upper())
    parts += [discipline.strip().upper(), doc_type.strip().upper(), str(serial).strip().upper()]
    return "-".join(parts)


def structure_description() -> dict:
    """Everything the UI and the error messages need to explain the coding."""
    return {
        "pattern_label": CODE_PATTERN_LABEL,
        "example": CODE_PATTERN_EXAMPLE,
        "short_pattern_label": CODE_PATTERN_LABEL_SHORT,
        "short_example": CODE_PATTERN_EXAMPLE_SHORT,
        "segments": [
            {
                "key": segment.key,
                "label_fa": segment.label_fa,
                "label_en": segment.label_en,
                "regex": segment.regex,
                "example": segment.example,
                "note_fa": segment.note_fa,
            }
            for segment in CODE_SEGMENTS
        ],
        "disciplines": [
            {"code": code, "name_fa": name_fa, "name_en": name_en}
            for code, (name_fa, name_en) in DISCIPLINE_VOCAB.items()
        ],
        "doc_types": [{"code": code, "name_fa": name_fa} for code, name_fa in DOC_TYPE_VOCAB.items()],
        "doc_classes": list(DOC_CLASS_VALUES),
        "basic_detail_values": list(BASIC_DETAIL_VALUES),
        #: the number every discipline draws from — shown under the legend so a planner
        #: knows why 2102-20-EB-ME-DWG-1005 raises a warning instead of an error.
        "serial_blocks": [
            {
                "discipline": code,
                "name_fa": DISCIPLINE_VOCAB[code][0],
                "from": SERIAL_BLOCKS[code][0],
                "to": SERIAL_BLOCKS[code][1],
            }
            for code in DISCIPLINE_VOCAB
            if code in SERIAL_BLOCKS
        ],
    }


def describe_mismatch(code: str) -> str:
    """The sentence shown when a code fails validation (rule R02)."""
    return (
        f"کد «{code}» با ساختار شماره‌گذاری پروژه سازگار نیست. "
        f"قالب مورد انتظار: {CODE_PATTERN_LABEL} — نمونه: {CODE_PATTERN_EXAMPLE} "
        f"(یا قالب کوتاه {CODE_PATTERN_LABEL_SHORT} مانند {CODE_PATTERN_EXAMPLE_SHORT})."
    )


#: Our own demo/legacy codes vs the customer's two-letter codes. Both resolve to the
#: same project discipline row, so an MDL written with ``CV`` lands on the project's
#: ``CIV`` lookup instead of creating a duplicate.
DISCIPLINE_ALIASES: dict[str, str] = {
    "CV": "CIV",
    "CIV": "CV",
    "ME": "MEC",
    "MEC": "ME",
    "EL": "ELE",
    "ELE": "EL",
    "IN": "INS",
    "INS": "IN",
    "GN": "GEN",
    "GEN": "GN",
}


def resolve_discipline_code(code: str | None, known: set[str]) -> str | None:
    """Pick the code this project actually uses for a discipline.

    ``known`` is the set of codes defined on the project. The code as written wins;
    otherwise its alias is used; otherwise the code is returned unchanged so the caller
    can auto-create or reject it.
    """
    if not code:
        return None
    upper = code.strip().upper()
    if upper in known:
        return upper
    alias = DISCIPLINE_ALIASES.get(upper)
    if alias and alias in known:
        return alias
    return upper


def discipline_name(code: str) -> str:
    return DISCIPLINE_VOCAB.get(code.upper(), (code.upper(), code.upper()))[0]


def doc_type_name(code: str) -> str:
    return DOC_TYPE_VOCAB.get(code.upper(), code.upper())


def group_to_discipline(group: str) -> str | None:
    """Map the sheet's text group ("CIVIL & STRUCTURE") to a discipline code ("CV")."""
    needle = (group or "").strip().upper()
    if not needle:
        return None
    if needle in DISCIPLINE_VOCAB:
        return needle
    for code, (_, name_en) in DISCIPLINE_VOCAB.items():
        if needle == name_en or needle.startswith(name_en.split(" ")[0]):
            return code
    return None
