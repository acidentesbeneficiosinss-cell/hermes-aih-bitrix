from __future__ import annotations

import json
import logging

import pytest

from aih_bitrix.bitrix.client import BitrixError
from aih_bitrix.config import Settings
from aih_bitrix.ledger import Ledger
from aih_bitrix.pipeline import Pipeline
from aih_bitrix.synthetic import make_pdf
from conftest import WEBHOOK
from fakes import FakeBitrix

CPF_KEY = "UF_CRM_1524762221558"
CEP_KEY = "UF_CRM_1725391645"


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def make_pipeline(fmap, sleeps):
    def build(fake=None, **settings):
        fake = fake or FakeBitrix()
        settings.setdefault("verify_delay", 8.0)
        return Pipeline(fake, fmap, Ledger(":memory:"), Settings(webhook_url=WEBHOOK, **settings), sleep=sleeps.append), fake

    return build


# ---------------------------------------------------------------- fluxo feliz

def test_model_pdf_goes_end_to_end_and_every_check_passes(make_pipeline, sleeps, sample_pdf_bytes):
    pipeline, fake = make_pipeline()
    result = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    assert result.status == "ok" and result.error is None
    assert result.contact.action == "created" and result.item.action == "created"
    assert result.checks and all(c.ok for c in result.checks)
    assert fake.contacts[result.contact.id][CPF_KEY] == "123.456.789-09"  # CPF veio do nome do arquivo
    assert fake.items[result.item.id]["xmlId"] == "AIH-700000000000012-20260914-S821"
    assert sleeps == [8.0]  # espera o BP assíncrono do portal antes de reler
    assert pipeline._ledger.seen(result.sha256)


def test_same_file_again_is_a_duplicate_without_any_new_write(make_pipeline, sample_pdf_bytes):
    pipeline, fake = make_pipeline()
    first = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    writes = len(fake.writes)
    again = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    renamed = pipeline.process_pdf(sample_pdf_bytes, "outro nome.pdf")
    assert again.status == "duplicate" and renamed.status == "duplicate" and len(fake.writes) == writes
    assert str(first.contact.id) in again.warnings[0] and str(first.item.id) in again.warnings[0]


def test_dry_run_writes_nothing_and_leaves_the_ledger_alone(make_pipeline, sample_pdf_bytes):
    pipeline, fake = make_pipeline()
    dry = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf", dry_run=True)
    assert dry.status == "dry_run" and fake.writes == [] and dry.contact.action == "dry_run_create"
    assert dry.item.action == "dry_run_create"
    assert pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf").status == "ok"


def test_the_fictional_sample_file_is_never_written_to_the_portal(make_pipeline, sample_pdf_bytes):
    pipeline, fake = make_pipeline()
    refused = pipeline.process_pdf(sample_pdf_bytes, "AMOSTRA-FICTICIA-aih-a.pdf")
    assert refused.status == "error" and "fictícia" in refused.error and refused.retryable is False
    assert fake.writes == [] and not pipeline._ledger.seen(refused.sha256)
    dry = pipeline.process_pdf(sample_pdf_bytes, "AMOSTRA-FICTICIA-aih-a.pdf", dry_run=True)  # ver o que faria é permitido
    assert dry.status == "dry_run" and fake.writes == []


def test_explicit_cpf_wins_over_the_filename_and_an_invalid_one_is_ignored(make_pipeline, sample_pdf_bytes):
    pipeline, fake = make_pipeline()
    result = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf", cpf="987.654.321-00")
    assert fake.contacts[result.contact.id][CPF_KEY] == "987.654.321-00"
    other, fake2 = make_pipeline()
    bad = other.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf", cpf="123")
    assert fake2.contacts[bad.contact.id][CPF_KEY] == "123.456.789-09"
    assert any("CPF informado" in w for w in bad.warnings)


def test_display_name_replaces_the_file_name_in_every_outcome(make_pipeline, sample_pdf_bytes):
    pipeline, _ = make_pipeline()
    first = pipeline.process_pdf(sample_pdf_bytes, "NOME DO PACIENTE.pdf", display_name="anexo 7")
    again = pipeline.process_pdf(sample_pdf_bytes, "NOME DO PACIENTE.pdf", display_name="anexo 8")
    bad = pipeline.process_pdf(b"lixo", "NOME DO PACIENTE.pdf", display_name="anexo 9")
    assert [r.filename for r in (first, again, bad)] == ["anexo 7", "anexo 8", "anexo 9"]
    assert "NOME DO PACIENTE" not in str([r.to_public() for r in (first, again, bad)])


# ---------------------------------------------------------------- entradas que não são laudo

def test_garbage_bytes_are_an_error_that_never_echoes_the_content(make_pipeline):
    pipeline, fake = make_pipeline()
    result = pipeline.process_pdf(b"conteudo-secreto-123", "x.pdf")
    assert result.status == "error" and "secreto" not in json.dumps(result.to_public()) and fake.writes == []


def test_pdf_without_text_layer_is_reported_as_not_an_aih_with_a_hint(make_pipeline):
    pipeline, _ = make_pipeline()
    result = pipeline.process_pdf(make_pdf([[]]), "scan.pdf")
    assert result.status == "not_aih" and any("sem camada de texto" in w for w in result.warnings)


def test_another_document_is_not_an_aih(make_pipeline):
    words = ("Contrato de prestação de serviços advocatícios entre as partes abaixo qualificadas "
             "firmam o presente instrumento mediante as cláusulas e condições a seguir").split()
    pdf = make_pdf([[(30 + 40 * (i % 10), 40 + 12 * (i // 10), w, 9.0) for i, w in enumerate(words)]])
    pipeline, fake = make_pipeline()
    result = pipeline.process_pdf(pdf, "contrato.pdf")
    assert result.status == "not_aih" and fake.writes == []
    assert pipeline.process_pdf(pdf, "contrato.pdf").status == "duplicate"  # não-AIH também não é reprocessado


# ---------------------------------------------------------------- revisão humana e falhas

def test_ambiguous_contact_needs_review_and_creates_no_card(make_pipeline, sample_pdf_bytes):
    pipeline, fake = make_pipeline()
    for _ in range(2):
        fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", **{CPF_KEY: "123.456.789-09"})
    result = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    assert result.status == "needs_review" and result.contact.action == "ambiguous" and result.item is None
    assert fake.items == {} and fake.writes == []
    assert not pipeline._ledger.seen(result.sha256)  # reprocessável depois que alguém resolver as duplicatas


def test_a_failed_verification_needs_review_and_names_the_field(fmap, sample_pdf_bytes):
    fake = FakeBitrix()

    def tamper(_seconds):  # simula uma automação do portal que mexe no CEP depois da gravação
        for contact in fake.contacts.values():
            contact[CEP_KEY] = "00000000"

    pipeline = Pipeline(fake, fmap, Ledger(":memory:"), Settings(webhook_url=WEBHOOK), sleep=tamper)
    result = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    assert result.status == "needs_review" and any(not c.ok for c in result.checks)
    assert any("cep" in w for w in result.warnings)
    assert not pipeline._ledger.seen(result.sha256)


def test_bitrix_failure_is_an_error_with_the_partial_ids_and_stays_retryable(fmap, sample_pdf_bytes):
    class ItemFails(FakeBitrix):
        def _m_crm_item_add(self, p):
            raise BitrixError("ERROR_CORE", "falha simulada")

    fake = ItemFails()
    pipeline = Pipeline(fake, fmap, Ledger(":memory:"), Settings(webhook_url=WEBHOOK), sleep=lambda s: None)
    result = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    assert result.status == "error" and "ERROR_CORE" in result.error
    assert result.contact is not None and result.contact.id and result.item is None
    assert not pipeline._ledger.seen(result.sha256)


def test_only_transient_bitrix_errors_are_flagged_retryable(fmap, sample_pdf_bytes):
    class Down(FakeBitrix):
        def _m_crm_contact_list(self, p):
            raise BitrixError("HTTP_503", "indisponível")

    class Rejects(FakeBitrix):
        def _m_crm_contact_list(self, p):
            raise BitrixError("ERROR_CORE", "campo obrigatório")

    def run(fake):
        return Pipeline(fake, fmap, Ledger(":memory:"), Settings(webhook_url=WEBHOOK), sleep=lambda s: None).process_pdf(
            sample_pdf_bytes, "CPF12345678909.pdf")

    assert run(Down()).retryable is True and run(Rejects()).retryable is False
    assert run(Down()).to_public()["retryable"] is True


def test_unexpected_exception_reports_only_the_exception_type(fmap, sample_pdf_bytes, caplog):
    class Broken(FakeBitrix):
        def _m_crm_contact_list(self, p):
            raise RuntimeError("Marcos 123.456.789-09")

    pipeline = Pipeline(Broken(), fmap, Ledger(":memory:"), Settings(webhook_url=WEBHOOK), sleep=lambda s: None)
    with caplog.at_level(logging.DEBUG):
        result = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    assert result.status == "error" and result.error == "erro inesperado (RuntimeError)"
    # o log (que um agente pode ler pelo terminal) diz o tipo e onde, mas nunca a mensagem nem o traceback com ela
    assert "RuntimeError" in caplog.text and "Marcos" not in caplog.text and "123.456" not in caplog.text


# ---------------------------------------------------------------- concorrência (API + poller no mesmo processo)

def test_concurrent_processing_of_the_same_file_never_creates_duplicates(fmap, sample_pdf_bytes):
    import threading
    import time

    class Slow(FakeBitrix):  # alarga a janela da corrida: sem exclusão mútua os dois enxergam "não existe" e criam
        def _m_crm_contact_list(self, p):
            time.sleep(0.05)
            return super()._m_crm_contact_list(p)

    fake = Slow()
    pipeline = Pipeline(fake, fmap, Ledger(":memory:"), Settings(webhook_url=WEBHOOK), sleep=lambda s: None)
    results = []
    threads = [threading.Thread(target=lambda: results.append(pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(r.status for r in results) == ["duplicate", "ok"]
    assert len(fake.contacts) == 1 and len(fake.items) == 1


# ---------------------------------------------------------------- privacidade da resposta

def test_public_result_contains_no_personal_data(make_pipeline, sample_pdf_bytes):
    pipeline, _ = make_pipeline()
    result = pipeline.process_pdf(sample_pdf_bytes, "CPF12345678909.pdf")
    text = json.dumps(result.to_public(), ensure_ascii=False)
    for secret in ("MARCOS", "Marcos", "HELENA", "12345678909", "123.456.789", "700000000000012", "CUIAB", "TIBIA", "tíbia"):
        assert secret not in text
    public = result.to_public()
    assert public["status"] == "ok" and public["filename"] == "CPF***.***.***-09.pdf"
    assert {"scope": "contact", "field": "cpf", "ok": True} in public["checks"]
    assert public["contact"]["id"] == result.contact.id and public["item"]["id"] == result.item.id
