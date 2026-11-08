"""Title-block parsing from positioned words (no PyMuPDF required)."""

from __future__ import annotations

from dataclasses import dataclass

from app.extraction.title_block import group_lines, parse_fields_from_words, select_template


@dataclass
class FakeWord:
    text: str
    bbox: tuple[float, float, float, float]
    block: int = 0
    line: int = 0
    word_no: int = 0
    page_no: int = 1

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def make_line(words: list[str], y: float, x_start: float = 100.0, height: float = 8.0) -> list[FakeWord]:
    result, x = [], x_start
    for index, text in enumerate(words):
        width = 6.0 * len(text)
        result.append(FakeWord(text=text, bbox=(x, y, x + width, y + height), word_no=index))
        x += width + 6
    return result


def test_group_lines_orders_by_row():
    words = make_line(["DOCUMENT", "NO:", "TST-CIV-DR-001"], y=100) + make_line(
        ["REV:", "B"], y=120
    )
    lines = group_lines(words)
    assert len(lines) == 2
    assert lines[0].text.startswith("DOCUMENT NO:")
    assert lines[1].text == "REV: B"


def test_parse_label_value_on_same_line():
    words = make_line(["DOCUMENT", "NO:", "TST-CIV-DR-001"], y=100) + make_line(
        ["REVISION", "B"], y=120
    ) + make_line(["TITLE:", "Plot", "Plan"], y=140)
    fields = parse_fields_from_words(words)
    assert fields["doc_code"].value == "TST-CIV-DR-001"
    assert fields["rev"].value == "B"
    assert fields["title"].value.lower().startswith("plot plan")
    assert fields["doc_code"].confidence > 0.8


def test_parse_value_below_label():
    words = make_line(["DOCUMENT", "NO:"], y=100) + make_line(["TST-MEC-DR-0201"], y=112)
    fields = parse_fields_from_words(words)
    assert fields["doc_code"].value == "TST-MEC-DR-0201"
    assert fields["doc_code"].confidence < 0.9  # lower than same-line confidence


def test_persian_labels_are_understood():
    words = make_line(["کد", "مدرک:", "TST-CIV-DR-0007"], y=100) + make_line(
        ["بازنگری:", "۰۲"], y=120
    )
    fields = parse_fields_from_words(words)
    assert fields["doc_code"].value == "TST-CIV-DR-0007"
    assert fields["rev"].value == "02"


def test_code_pattern_fallback_without_label():
    words = make_line(["SOME", "DRAWING", "TST-ELE-DR-0301", "REV", "A"], y=100)
    fields = parse_fields_from_words(words, template_regex={"doc_code": r"\S+"})
    assert fields["doc_code"].value == "TST-ELE-DR-0301"
    assert fields["doc_code"].confidence < 0.8


def test_empty_words_returns_no_fields():
    assert parse_fields_from_words([]) == {}


def test_select_template_by_aspect_ratio_and_anchor():
    class Template:
        def __init__(self, name, ratios, anchors, priority=100, is_active=True):
            self.id = name
            self.name = name
            self.page_aspect_ratios = ratios
            self.anchor_keywords = anchors
            self.priority = priority
            self.is_active = is_active
            self.regions = {}
            self.field_regex = {}

    wide = Template("wide", [[1.3, 1.5]], ["document"])
    narrow = Template("narrow", [[0.6, 0.8]], ["document"])
    chosen = select_template([wide, narrow], 1.41, "DOCUMENT NO: X")
    assert chosen.name == "wide"
    assert select_template([wide], 1.41, "no anchors here") is None
    assert select_template([wide], 0.7, "document") is None
