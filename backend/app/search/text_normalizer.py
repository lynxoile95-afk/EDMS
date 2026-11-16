"""Persian/Arabic text normalisation shared by the file-QA index.

The existing content-search normaliser is intentionally left in place for
backward compatibility.  This module is the exact, versioned normalisation
contract for the additive Persian RAG index: source text is kept separately and
this representation is used only for matching and embeddings.
"""

from __future__ import annotations

import re
import unicodedata

# Arabic vowel marks, Quranic marks and superscript alef.  Keep punctuation,
# ASCII letters, digits and engineering drawing codes untouched.
_ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
_WHITESPACE = re.compile(r"\s+")

_TRANSLATION = str.maketrans(
    {
        "ك": "ک",
        "ي": "ی",
        "ى": "ی",
        "ئ": "ی",
        "ة": "ه",
        "ۀ": "ه",
        "ؤ": "و",
        "أ": "ا",
        "إ": "ا",
        "ٱ": "ا",
        "آ": "آ",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
        "۰": "0",
        "۱": "1",
        "۲": "2",
        "۳": "3",
        "۴": "4",
        "۵": "5",
        "۶": "6",
        "۷": "7",
        "۸": "8",
        "۹": "9",
        "ـ": "",
        # A zero-width joiner is a layout/control mark for this index.  Replacing
        # it with a space prevents two source words from being glued together.
        "\u200c": " ",
        "\u200f": " ",
        "\u200e": " ",
        "\u202a": " ",
        "\u202b": " ",
        "\u202c": " ",
        "\u202d": " ",
        "\u202e": " ",
        "\u2066": " ",
        "\u2067": " ",
        "\u2068": " ",
        "\u2069": " ",
    }
)


def normalize_farsi(text: str) -> str:
    """Return the canonical retrieval form used by Persian RAG.

    This deliberately does *not* transliterate or strip punctuation.  For
    example, ``7100-00-EB-EL-SPE-7052`` remains byte-for-byte identical except
    for any surrounding/duplicated whitespace.
    """

    value = unicodedata.normalize("NFKC", text or "")
    value = value.translate(_TRANSLATION)
    value = _ARABIC_MARKS.sub("", value)
    return _WHITESPACE.sub(" ", value).strip()


def normalize_visible_text(text: str) -> str:
    """Clean extraction layout noise while preserving the display spelling."""

    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ")
    return _WHITESPACE.sub(" ", value).strip()


__all__ = ["normalize_farsi", "normalize_visible_text"]
