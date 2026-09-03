"""OCR contracts; real OCR can be added without changing the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .models import OcrText


@dataclass(slots=True)
class OcrResult:
    texts: list[OcrText] = field(default_factory=list)

    @property
    def joined_text(self) -> str:
        return " ".join(x.text for x in self.texts if x.text).strip()


class OcrEngine(Protocol):
    def recognize(self, image: np.ndarray) -> OcrResult: ...


class NullOcrEngine:
    def recognize(self, image: np.ndarray) -> OcrResult:
        del image
        return OcrResult()
