from __future__ import annotations

import pytest

from aih_bitrix.pdf_text import InvalidPdfError, ScannedPdfError, page_text, read_pages
from aih_bitrix.synthetic import make_pdf


def test_model_pdf_exposes_words_with_geometry(sample_pdf_bytes):
    pages = read_pages(sample_pdf_bytes)
    assert len(pages) == 1
    words = {w.text: w for w in pages[0].words}
    assert "5-NOME" in words and "MARCOS" in words
    marcos = words["MARCOS"]
    assert marcos.x1 > marcos.x0 and marcos.bottom > marcos.top
    assert 590 < pages[0].width < 600 and 840 < pages[0].height < 845


def test_page_text_is_in_reading_order(sample_pdf_bytes):
    text = page_text(read_pages(sample_pdf_bytes)[0])
    assert text.index("5-NOME DO PACIENTE") < text.index("MARCOS DE OLIVEIRA")


def test_pdf_without_text_layer_is_reported_as_scanned():
    with pytest.raises(ScannedPdfError):
        read_pages(make_pdf([[]]))


def test_garbage_bytes_are_an_invalid_pdf():
    with pytest.raises(InvalidPdfError):
        read_pages(b"isto nao e um pdf")


def test_only_the_first_pages_are_read():
    page = [(30, 20 + 10 * i, f"palavra{i}", 8.0) for i in range(20)]
    pdf = make_pdf([page] * 5)
    assert len(read_pages(pdf, max_pages=3)) == 3
