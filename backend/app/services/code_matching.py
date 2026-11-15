"""Document-code matching against the MDL.

Strategy (cheapest and most deterministic first):

1. **exact**       – normalised code equals ``mdl_items.doc_code_norm``
2. **alias**       – code equals a registered contractor/legacy alias
3. **ocr_variant** – deterministic single-glyph OCR-confusion variants
4. **fuzzy**       – rapidfuzz similarity over the project's codes, ranked

The result is always a list of candidates with a 0..1 score and the method that
produced them.  Nothing here decides acceptance — the decision engine does.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MDLItem
from app.services.normalization import normalize_code, normalize_title, ocr_confusion_variants

ACCEPT_SCORE = 0.80  # candidate is "close enough" to be pre-selected in the UI


@dataclass(slots=True)
class MatchCandidate:
    mdl_item_id: uuid.UUID
    doc_code: str
    title: str
    discipline_code: str | None
    score: float
    method: str  # exact | alias | ocr_variant | fuzzy
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "mdl_item_id": str(self.mdl_item_id),
            "doc_code": self.doc_code,
            "title": self.title,
            "discipline_code": self.discipline_code,
            "score": round(self.score, 4),
            "method": self.method,
            "reasons": self.reasons,
        }


def _discipline_code(mdl: MDLItem, cache: dict) -> str | None:
    if mdl.discipline_id is None:
        return None
    if mdl.discipline_id not in cache:
        cache[mdl.discipline_id] = getattr(mdl, "discipline_code", None)
    return cache[mdl.discipline_id]


class CodeMatcher:
    """Project-scoped matcher. Cheap to construct; load the index once per call."""

    def __init__(self, db: Session, project_id: uuid.UUID, threshold: float = ACCEPT_SCORE) -> None:
        self.db = db
        self.project_id = project_id
        self.threshold = threshold
        self._items: list[MDLItem] | None = None

    # ------------------------------------------------------------------ index
    def _load(self) -> list[MDLItem]:
        if self._items is None:
            stmt = select(MDLItem).where(
                MDLItem.project_id == self.project_id, MDLItem.is_active.is_(True)
            )
            self._items = list(self.db.execute(stmt).scalars())
        return self._items

    def invalidate(self) -> None:
        self._items = None

    # -------------------------------------------------------------- matching
    def match(self, code: str | None, title: str | None = None, limit: int = 5) -> list[MatchCandidate]:
        norm = normalize_code(code)
        if not norm:
            return []
        items = self._load()
        by_norm = {item.doc_code_norm: item for item in items}

        exact = by_norm.get(norm)
        if exact is not None:
            return [
                MatchCandidate(
                    mdl_item_id=exact.id,
                    doc_code=exact.doc_code,
                    title=exact.title,
                    discipline_code=self._discipline_of(exact),
                    score=1.0,
                    method="exact",
                    reasons=["کد مدرک به‌صورت دقیق در MDL موجود است"],
                )
            ]

        results: dict[uuid.UUID, MatchCandidate] = {}

        # --- alias ---------------------------------------------------------
        for item in items:
            for alias in item.aliases or []:
                if normalize_code(alias) == norm:
                    results[item.id] = MatchCandidate(
                        mdl_item_id=item.id,
                        doc_code=item.doc_code,
                        title=item.title,
                        discipline_code=self._discipline_of(item),
                        score=0.99,
                        method="alias",
                        reasons=[f"کد با نام مستعار ثبت‌شده مطابقت دارد: {alias}"],
                    )

        # --- OCR variants --------------------------------------------------
        if not results:
            for variant in ocr_confusion_variants(norm):
                item = by_norm.get(variant)
                if item is not None:
                    results[item.id] = MatchCandidate(
                        mdl_item_id=item.id,
                        doc_code=item.doc_code,
                        title=item.title,
                        discipline_code=self._discipline_of(item),
                        score=0.93,
                        method="ocr_variant",
                        reasons=[f"اختلاف تک‌کاراکتری (خطای OCR) اصلاح شد: {variant}"],
                    )

        # --- fuzzy ---------------------------------------------------------
        title_norm = normalize_title(title)
        for item in items:
            base = fuzz.ratio(norm, item.doc_code_norm) / 100.0
            if base < 0.55:
                continue
            reasons: list[str] = [f"شباهت متنی کد: {base:.0%}"]

            # structural agreement boosts the score (same tail sequence number)
            tail_a, tail_b = norm.rsplit("-", 1)[-1], item.doc_code_norm.rsplit("-", 1)[-1]
            if tail_a and tail_a == tail_b:
                base = min(1.0, base + 0.04)
                reasons.append("شماره ترتیبی یکسان")

            if title_norm and item.title_norm:
                title_score = fuzz.token_set_ratio(title_norm, item.title_norm) / 100.0
                if title_score > 0.8:
                    base = min(1.0, base + 0.05)
                    reasons.append(f"عنوان نزدیک: {title_score:.0%}")

            existing = results.get(item.id)
            candidate = MatchCandidate(
                mdl_item_id=item.id,
                doc_code=item.doc_code,
                title=item.title,
                discipline_code=self._discipline_of(item),
                score=base,
                method="fuzzy",
                reasons=reasons,
            )
            if existing is None or candidate.score > existing.score:
                results[item.id] = candidate

        ordered = sorted(results.values(), key=lambda c: (-c.score, c.doc_code))
        return ordered[:limit]

    def _discipline_of(self, item: MDLItem) -> str | None:
        try:
            discipline = item.__dict__.get("discipline")
            if discipline is not None:
                return discipline.code
        except Exception:  # pragma: no cover - defensive
            pass
        return item.attributes.get("discipline_code") if item.attributes else None

    # -------------------------------------------------------------- helpers
    def best(self, code: str | None, title: str | None = None) -> MatchCandidate | None:
        candidates = self.match(code, title, limit=1)
        return candidates[0] if candidates else None

    def is_confident(self, candidate: MatchCandidate | None) -> bool:
        return candidate is not None and candidate.score >= self.threshold
