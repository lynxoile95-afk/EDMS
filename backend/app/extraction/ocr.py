"""OCR layer with a pluggable engine (Tesseract default, PaddleOCR optional)."""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from functools import lru_cache

from app.core.config import settings
from app.extraction.base import OCREngine


@dataclass(slots=True)
class OCRWord:
    """Word geometry normalised to the `Word` interface used by the parser."""

    text: str
    bbox: tuple[float, float, float, float]
    block: int
    line: int
    word_no: int
    page_no: int

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


class TesseractEngine:
    name = "tesseract"

    def __init__(self) -> None:
        self._configured: bool | None = None

    def is_available(self) -> bool:
        if self._configured is not None:
            return self._configured
        try:
            import pytesseract  # type: ignore

            if settings.tesseract_cmd and shutil.which(settings.tesseract_cmd):
                pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
            version = pytesseract.get_tesseract_version()
            self._configured = version is not None
        except Exception:
            self._configured = False
        return self._configured

    def read_image(self, image_bytes: bytes, langs: str) -> tuple[str, float]:
        words = self.read_words(image_bytes, langs)
        if not words:
            return "", 0.0
        text = " ".join(w.text for w in words)
        # heuristic quality: mean word length and share of alphanumeric tokens
        mean_len = sum(len(w.text) for w in words) / len(words)
        quality = min(1.0, max(0.0, (mean_len - 1.5) / 6.0)) * 0.7 + 0.3
        return text, round(quality, 3)

    def read_words(self, image_bytes: bytes, langs: str, page_no: int = 1) -> list[OCRWord]:
        if not self.is_available():
            return []
        import pytesseract  # type: ignore
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        config = "--oem 1 --psm 6"  # LSTM engine, assume a uniform block of text
        data = pytesseract.image_to_data(
            image, lang=langs, config=config, output_type=pytesseract.Output.DICT
        )
        words: list[OCRWord] = []
        for i, text in enumerate(data.get("text", [])):
            token = (text or "").strip()
            if not token:
                continue
            try:
                confidence = float(data["conf"][i])
            except (ValueError, KeyError):
                confidence = -1.0
            if confidence < 40:  # drop very low-confidence noise
                continue
            left, top = int(data["left"][i]), int(data["top"][i])
            width, height = int(data["width"][i]), int(data["height"][i])
            words.append(
                OCRWord(
                    text=token,
                    bbox=(float(left), float(top), float(left + width), float(top + height)),
                    block=int(data["block_num"][i]),
                    line=int(data["line_num"][i]),
                    word_no=int(data["word_num"][i]),
                    page_no=page_no,
                )
            )
        return words


class PaddleOCREngine:
    """Optional engine — install with ``pip install paddleocr paddlepaddle``."""

    name = "paddle"

    def is_available(self) -> bool:
        try:
            import paddleocr  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    @lru_cache(maxsize=1)
    def _engine(self):
        from paddleocr import PaddleOCR  # type: ignore

        lang = "fa" if "fas" in settings.ocr_langs or "fa" in settings.ocr_langs else "en"
        return PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

    def read_words(self, image_bytes: bytes, langs: str, page_no: int = 1) -> list[OCRWord]:
        if not self.is_available():
            return []
        import numpy as np
        from PIL import Image

        image = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        result = self._engine().ocr(image, cls=True)
        words: list[OCRWord] = []
        for block_index, block in enumerate(result or []):
            for line_index, line in enumerate(block or []):
                box, (text, score) = line[0], line[1]
                if not text or score < 0.5:
                    continue
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                words.append(
                    OCRWord(
                        text=text.strip(),
                        bbox=(float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))),
                        block=block_index,
                        line=line_index,
                        word_no=len(words),
                        page_no=page_no,
                    )
                )
        return words

    def read_image(self, image_bytes: bytes, langs: str) -> tuple[str, float]:
        words = self.read_words(image_bytes, langs)
        if not words:
            return "", 0.0
        return " ".join(w.text for w in words), 0.85


@lru_cache(maxsize=1)
def get_ocr_engine() -> OCREngine:
    if settings.ocr_engine == "paddle":
        engine: OCREngine = PaddleOCREngine()
        if engine.is_available():
            return engine
    return TesseractEngine()
