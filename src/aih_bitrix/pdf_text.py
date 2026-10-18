"""Leitura do PDF: palavras com geometria (pdfplumber). Sem camada de texto => ScannedPdfError."""
from __future__ import annotations

import io
import logging
from typing import Iterable

import pdfplumber

from .models import PageWords, Word

# o pdfminer avisa sobre fontes sem FontBBox a cada página; é ruído sem consequência para nós
logging.getLogger("pdfminer").setLevel(logging.ERROR)

MIN_WORDS = 15


class PdfTextError(Exception):
    pass


class InvalidPdfError(PdfTextError):
    pass


class ScannedPdfError(PdfTextError):
    pass


def read_pages(data: bytes, *, max_pages: int = 3) -> list[PageWords]:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [
                PageWords(
                    width=float(page.width),
                    height=float(page.height),
                    words=tuple(
                        Word(w["text"], float(w["x0"]), float(w["x1"]), float(w["top"]), float(w["bottom"]))
                        for w in page.extract_words(x_tolerance=2, y_tolerance=2)
                    ),
                )
                for page in pdf.pages[:max_pages]
            ]
    except Exception as exc:  # a mensagem das libs pode conter trechos do arquivo: não propagar
        raise InvalidPdfError("PDF inválido, protegido ou corrompido") from exc
    if sum(len(p.words) for p in pages) < MIN_WORDS:
        raise ScannedPdfError("PDF sem camada de texto (provavelmente escaneado); OCR não está habilitado")
    return pages


def group_lines(words: Iterable[Word], tol: float = 3.0) -> list[list[Word]]:
    """Agrupa palavras em linhas (tolerância vertical `tol`), cada linha ordenada por x."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(word.top - lines[-1][0].top) <= tol:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def page_text(page: PageWords) -> str:
    return "\n".join(" ".join(w.text for w in line) for line in group_lines(page.words))
