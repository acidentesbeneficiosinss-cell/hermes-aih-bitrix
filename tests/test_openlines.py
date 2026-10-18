from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aih_bitrix.bitrix.client import BitrixError
from aih_bitrix.bitrix.openlines import Attachment, group_batches, poll_once
from aih_bitrix.config import Settings
from aih_bitrix.ledger import Ledger
from aih_bitrix.pipeline import Pipeline
from aih_bitrix.synthetic import layout_b_pdf, make_pdf
from conftest import WEBHOOK
from fakes import FakeBitrix

DIALOG = "chat360293"
T0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
CPF_KEY = "UF_CRM_1524762221558"
FILES = {
    101: {"id": 101, "name": "a.pdf", "extension": "pdf", "size": 10, "type": "file"},
    102: {"id": 102, "name": "foto.jpg", "extension": "jpg", "size": 10, "type": "image"},
}
USERS = {152247: {"id": 152247, "bot": False}, 151851: {"id": 151851, "bot": True}}


def msg(message_id, seconds, *, author=152247, text="", files=()):
    return {"id": message_id, "author_id": author, "date": (T0 + timedelta(seconds=seconds)).isoformat(timespec="seconds"),
            "text": text, "params": {"FILE_ID": list(files)} if files else {}}


def batches_of(messages, now_seconds, **kw):
    return group_batches(messages, FILES, USERS, now=T0 + timedelta(seconds=now_seconds), **kw)


# ---------------------------------------------------------------- agrupamento (padrão observado no "Docs protocolo")

def test_batch_follows_the_observed_pattern_files_then_cpf_then_separator_then_bot():
    messages = [
        msg(1, 0, text="[b]Cliente:[/b] FULANO"),
        msg(2, 5, files=[101]),
        msg(3, 8, text="cpf 123.456.789-09"),
        msg(4, 9, text="​-------------"),
        msg(5, 20, author=151851, text="✅ Recebido — FULANO · 1 arq."),
    ]
    batches, open_first = batches_of(messages, 30)
    assert open_first is None and len(batches) == 1
    assert batches[0].attachments == [Attachment(file_id=101, name="a.pdf", size=10, message_id=2, cpf_hint="12345678909")]
    assert batches[0].message_ids == [1, 2, 3, 4]


def test_a_batch_without_terminator_stays_open_until_the_chat_goes_quiet():
    messages = [msg(1, 0, files=[101])]
    batches, open_first = batches_of(messages, 10, quiet_sec=60)
    assert batches == [] and open_first == 1
    batches, open_first = batches_of(messages, 120, quiet_sec=60)
    assert len(batches) == 1 and open_first is None


def test_a_long_gap_closes_the_previous_batch_and_the_cpf_does_not_leak_to_the_next_one():
    messages = [
        msg(1, 0, files=[101], text="cpf 123.456.789-09"),
        msg(2, 900, files=[101]),
    ]
    batches, open_first = batches_of(messages, 2000, gap_sec=300, quiet_sec=60)
    assert open_first is None and [b.attachments[0].cpf_hint for b in batches] == ["12345678909", None]


def test_only_pdfs_become_attachments():
    batches, _ = batches_of([msg(1, 0, files=[101, 102]), msg(2, 1, text="-----")], 100)
    assert [a.name for a in batches[0].attachments] == ["a.pdf"]


# ---------------------------------------------------------------- poll_once

@pytest.fixture
def env(fmap):
    fake = FakeBitrix()
    ledger = Ledger(":memory:")
    return fake, ledger, Pipeline(fake, fmap, ledger, Settings(webhook_url=WEBHOOK), sleep=lambda s: None)


def poll(env, **kw):
    fake, ledger, pipeline = env
    kw.setdefault("now", T0 + timedelta(hours=1))
    return poll_once(fake, pipeline, ledger, [DIALOG], **kw)


def other_aih(n: int) -> bytes:
    return layout_b_pdf(values={"patient_name": f"PACIENTE NUMERO {n}", "cns": f"70000000000010{n}"})


def test_first_run_only_initialises_the_cursor_without_importing_the_history(env, sample_pdf_bytes):
    fake, ledger, _ = env
    last = fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0)
    assert poll(env) == [] and ledger.get_cursor(DIALOG) == last and fake.writes == []


def test_backfill_processes_only_the_last_messages(env, sample_pdf_bytes):
    fake, _, _ = env
    fake.add_message(DIALOG, files=(("antigo.pdf", sample_pdf_bytes),), at=T0)
    fake.add_message(DIALOG, files=(("um.pdf", other_aih(1)),), at=T0 + timedelta(minutes=10))
    fake.add_message(DIALOG, files=(("dois.pdf", other_aih(2)),), at=T0 + timedelta(minutes=20))
    results = poll(env, backfill=2)
    assert [r.status for r in results] == ["ok", "ok"] and len(fake.contacts) == 2


def test_new_pdfs_are_processed_images_are_ignored_and_the_cursor_advances(env, sample_pdf_bytes):
    fake, ledger, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)  # inicializa o cursor
    contract = make_pdf([[(30 + 40 * (i % 10), 40 + 12 * (i // 10), w, 9.0) for i, w in enumerate(
        "Contrato de prestação de serviços advocatícios entre as partes abaixo qualificadas firmam o presente instrumento mediante cláusulas".split())]])
    fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes), ("foto.jpg", b"\xff\xd8\xff"), ("contrato.pdf", contract)),
                     at=T0 + timedelta(minutes=5))
    last = fake.add_message(DIALOG, text="-----", at=T0 + timedelta(minutes=5, seconds=3))
    results = poll(env)
    assert sorted(r.status for r in results) == ["not_aih", "ok"]
    assert ledger.get_cursor(DIALOG) == last
    assert poll(env) == [] and len(fake.contacts) == 1


def test_poller_results_never_carry_the_original_file_name(env, sample_pdf_bytes):
    # no grupo real os arquivos se chamam "NOME DO PACIENTE, AIH .PDF": o nome não pode ir para saída/log do poller
    fake, _, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("FULANO DE TAL DA SILVA, AIH .PDF", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    fake.add_message(DIALOG, files=(("BELTRANO GRANDE, AIH .PDF", sample_pdf_bytes),), at=T0 + timedelta(minutes=30))
    fake.download_error = BitrixError("NETWORK", "falha")
    errors = poll(env)
    fake.download_error = None
    ok = poll(env)
    for result in errors + ok:
        assert result.filename.startswith("anexo ") and "FULANO" not in result.filename and "BELTRANO" not in result.filename
        assert "FULANO" not in str(result.to_public()) and "BELTRANO" not in str(result.to_public())


def test_cpf_from_the_file_name_wins_over_the_batch_message(env, sample_pdf_bytes):
    fake, _, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    fake.add_message(DIALOG, text="cpf 987.654.321-00", at=T0 + timedelta(minutes=5, seconds=2))
    poll(env)
    assert next(iter(fake.contacts.values()))[CPF_KEY] == "123.456.789-09"


def test_batch_message_cpf_is_the_fallback_when_the_file_name_has_none(env, sample_pdf_bytes):
    fake, _, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("laudo-sem-cpf.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    fake.add_message(DIALOG, text="cpf 987.654.321-00", at=T0 + timedelta(minutes=5, seconds=2))
    poll(env)
    assert next(iter(fake.contacts.values()))[CPF_KEY] == "987.654.321-00"


def test_a_download_error_keeps_the_cursor_before_the_batch_so_it_is_retried(env, sample_pdf_bytes):
    fake, ledger, _ = env
    start = fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    fake.download_error = BitrixError("NETWORK", "falha de rede")
    results = poll(env)
    assert [r.status for r in results] == ["error"] and ledger.get_cursor(DIALOG) == start
    fake.download_error = None
    assert [r.status for r in poll(env)] == ["ok"] and ledger.get_cursor(DIALOG) > start


def test_a_download_url_outside_the_portal_is_refused_and_does_not_block_the_queue(env, sample_pdf_bytes):
    fake, ledger, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    last = fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    next(iter(fake.disk.values()))["DOWNLOAD_URL"] = "https://evil.example.com/x.pdf"
    results = poll(env)
    assert results[0].status == "error" and "DOWNLOAD_HOST" in results[0].error and fake.writes == []
    assert results[0].retryable is False and ledger.get_cursor(DIALOG) == last  # falha permanente: não trava o poller


def test_an_oversize_attachment_is_reported_without_blocking_the_files_behind_it(env, sample_pdf_bytes):
    # caso real: um PDF escaneado de 15 MB virava erro e, segurando o cursor, travava o grupo inteiro
    fake, ledger, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("grande.pdf", sample_pdf_bytes + b"0" * 10_000),), at=T0 + timedelta(minutes=5))
    last = fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=20))
    results = poll(env, max_pdf_bytes=len(sample_pdf_bytes) + 5_000)  # o normal cabe; o "grande" (+10 KB) estoura
    assert [r.status for r in results] == ["error", "ok"] and results[0].retryable is False
    assert ledger.get_cursor(DIALOG) == last and len(fake.contacts) == 1


def test_a_transient_error_is_flagged_retryable(env, sample_pdf_bytes):
    fake, _, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    fake.download_error = BitrixError("HTTP_503", "indisponível")
    assert poll(env)[0].retryable is True


def test_a_batch_still_being_filled_is_left_for_the_next_poll(env, sample_pdf_bytes):
    fake, ledger, _ = env
    start = fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    assert poll(env, now=T0 + timedelta(minutes=5, seconds=10)) == [] and ledger.get_cursor(DIALOG) == start
    assert [r.status for r in poll(env, now=T0 + timedelta(minutes=8))] == ["ok"]


def test_dry_run_neither_writes_nor_moves_the_cursor(env, sample_pdf_bytes):
    fake, ledger, _ = env
    start = fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    results = poll(env, dry_run=True)
    assert [r.status for r in results] == ["dry_run"] and fake.writes == [] and ledger.get_cursor(DIALOG) == start
    assert [r.status for r in poll(env)] == ["ok"]


def test_oversize_attachment_is_reported_without_downloading_it(env, sample_pdf_bytes):
    fake, _, _ = env
    fake.add_message(DIALOG, text="início", at=T0)
    poll(env)
    fake.add_message(DIALOG, files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=T0 + timedelta(minutes=5))
    results = poll(env, max_pdf_bytes=10)
    assert results[0].status == "error" and "limite" in results[0].error
    assert not any(method == "disk.file.get" for method, _ in fake.calls)
