from __future__ import annotations

import pytest

from aih_bitrix.parser import parse_laudo
from aih_bitrix.pdf_text import read_pages
from aih_bitrix.synthetic import is_sample_name, layout_a_pdf, layout_b_pdf, make_pdf, sample_filename, sample_pdf


def test_make_pdf_roundtrips_text_and_positions():
    page = [(50, 100, "alfa", 10.0), (200, 100, "beta", 10.0)] + [(30, 130 + 10 * i, f"p{i}", 8.0) for i in range(20)]
    words = {w.text: w for w in read_pages(make_pdf([page]))[0].words}
    assert abs(words["alfa"].x0 - 50) < 1 and abs(words["beta"].x0 - 200) < 1
    assert abs(words["alfa"].top - words["beta"].top) < 1 and words["p3"].top > words["alfa"].top


def test_both_synthetic_layouts_are_parseable_aih_documents_with_every_label_recognised():
    for pdf in (layout_a_pdf(), layout_b_pdf()):
        laudo = parse_laudo(read_pages(pdf))
        assert laudo.patient_name and laudo.cns and laudo.birth_date and laudo.request_date
        assert laudo.unmatched == ()


def test_sample_pdf_selects_the_layout_and_rejects_unknown_ones():
    assert sample_pdf("a") == layout_a_pdf() and sample_pdf("b") == layout_b_pdf()
    with pytest.raises(ValueError, match="layout"):
        sample_pdf("c")


def test_the_samples_are_deterministic():
    assert layout_a_pdf() == layout_a_pdf() and layout_b_pdf() == layout_b_pdf()


def test_sample_file_names_are_marked_as_fictional():
    assert sample_filename("a") == "AMOSTRA-FICTICIA-aih-a.pdf" and sample_filename("B") == "AMOSTRA-FICTICIA-aih-b.pdf"
    assert is_sample_name(sample_filename("a")) and is_sample_name("pasta/amostra-ficticia-qualquer.PDF")
    assert not is_sample_name("CPF12345678909.pdf") and not is_sample_name("")
    with pytest.raises(ValueError, match="layout"):
        sample_filename("c")
