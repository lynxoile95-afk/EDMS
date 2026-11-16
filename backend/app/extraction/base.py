"""Shared contracts for the extraction layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models.enums import ExtractionSource


@dataclass(slots=True)
class FieldCandidate:
    """A proposed value for one field, with its provenance."""

    name: str
    value: str
    confidence: float
    source: ExtractionSource
    bbox: list[float] | None = None
    page_no: int = 1
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.name,
            "value": self.value,
            "confidence": round(self.confidence, 4),
            "source": self.source.value,
            "bbox": self.bbox,
            "page_no": self.page_no,
            "note": self.note,
        }


@dataclass(slots=True)
class LayerOutput:
    """What one extraction layer produced."""

    layer: str                       # text_layer | ocr | vlm
    fields: dict[str, FieldCandidate] = field(default_factory=dict)
    text: str = ""
    confidence: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def add(self, candidate: FieldCandidate) -> None:
        existing = self.fields.get(candidate.name)
        if existing is None or candidate.confidence > existing.confidence:
            self.fields[candidate.name] = candidate


class OCREngine(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def read_image(self, image_bytes: bytes, langs: str) -> tuple[str, float]:
        """Return recognised text and a 0..1 quality estimate."""
        ...


class VLMClient(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def extract(self, image_bytes: bytes, prompt: str, schema: dict) -> dict: ...


class ExtractionError(RuntimeError):
    pass
