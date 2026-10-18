"""Fixtures compartilhadas dos testes."""
from __future__ import annotations

import pytest

from aih_bitrix.synthetic import layout_a_pdf

WEBHOOK = "https://fake.bitrix24.com.br/rest/1/abcdef0123456789/"


@pytest.fixture(scope="session")
def sample_pdf_bytes() -> bytes:
    """Laudo AIH modelo (formulário antigo, 49 campos): 100 % fictício, gerado em memória."""
    return layout_a_pdf()


@pytest.fixture
def fake():
    from fakes import FakeBitrix

    return FakeBitrix()


@pytest.fixture
def fmap(fake):
    from aih_bitrix.bitrix.fieldmap import load_field_map
    from aih_bitrix.config import Settings

    return load_field_map(fake, Settings(webhook_url=WEBHOOK))


@pytest.fixture
def laudo(sample_pdf_bytes):
    from aih_bitrix.parser import parse_laudo
    from aih_bitrix.pdf_text import read_pages

    return parse_laudo(read_pages(sample_pdf_bytes), "CPF12345678909.pdf")


@pytest.fixture
def laudo_real_phone(laudo):
    """O laudo modelo, mas com um telefone de verdade (o do modelo é placeholder)."""
    from aih_bitrix.models import Laudo

    return Laudo(fields={**laudo.fields, "phone": "(65) 99123-4567"}, source_name=laudo.source_name)
