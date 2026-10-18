from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aih_bitrix.cli import main
from aih_bitrix.synthetic import make_pdf
from conftest import WEBHOOK
from fakes import FakeBitrix


@pytest.fixture
def pdf_file(tmp_path, sample_pdf_bytes):
    path = tmp_path / "CPF12345678909.pdf"
    path.write_bytes(sample_pdf_bytes)
    return path


@pytest.fixture
def env(tmp_path):
    return {"BITRIX_WEBHOOK_URL": WEBHOOK, "LEDGER_PATH": str(tmp_path / "ledger.sqlite3"), "VERIFY_DELAY_SEC": "0"}


def run(args, env, fake=None, capsys=None):
    code = main(args, client_factory=(lambda settings: fake) if fake is not None else None, environ=env)
    out = capsys.readouterr() if capsys else None
    return code, out


# ---------------------------------------------------------------- saída em UTF-8

def test_output_is_forced_to_utf8_even_when_the_console_encoding_cannot_represent_the_text(monkeypatch):
    import io
    import sys

    from aih_bitrix.cli import _force_utf8_output

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))  # redirecionado no Windows
    _force_utf8_output()
    print("título ➡ ok")  # "➡" não existe em cp1252: sem a correção isto levantaria UnicodeEncodeError
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8").splitlines() == ["título ➡ ok"]  # splitlines: \n ou \r\n conforme o SO


def test_help_text_is_utf8_even_on_a_cp1252_stdout(monkeypatch):
    import io
    import sys

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    with pytest.raises(SystemExit) as info:
        main(["--help"], environ={})
    sys.stdout.flush()
    assert info.value.code == 0 and "Auxílio Acidente" in raw.getvalue().decode("utf-8")


# ---------------------------------------------------------------- extract (offline)

def test_extract_masks_personal_data_by_default(pdf_file, env, capsys):
    code, out = run(["extract", str(pdf_file)], env, capsys=capsys)
    report = json.loads(out.out)
    assert code == 0 and report["campos"]["patient_name"] == "ok" and report["campos"]["cns"] == "ok"
    assert "MARCOS" not in out.out and "700000000000012" not in out.out and "TIBIAL" not in out.out
    assert set(report["inferencias"]) == {"accident_date", "limb", "surgery"} and report["rotulos_nao_reconhecidos"] == []


def test_extract_show_reveals_the_values(pdf_file, env, capsys):
    code, out = run(["extract", "--show", str(pdf_file)], env, capsys=capsys)
    shown = json.loads(out.out)
    assert code == 0 and shown["campos"]["patient_name"] == "MARCOS DE OLIVEIRA"
    assert shown["inferencias"]["surgery"]["value"] == "Proposta cirúrgica"


def test_extract_rejects_files_that_are_not_an_aih_without_echoing_content(tmp_path, env, capsys):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"conteudo-secreto")
    code, out = run(["extract", str(bad)], env, capsys=capsys)
    assert code == 2 and "secreto" not in out.out + out.err and out.err.strip()


# ---------------------------------------------------------------- sample (offline)

def test_sample_writes_a_fictional_pdf_that_extract_reads(tmp_path, env, capsys):
    target = tmp_path / "amostra.pdf"
    code, out = run(["sample", "--out", str(target)], env, capsys=capsys)
    assert code == 0 and target.read_bytes().startswith(b"%PDF") and "FICTÍCIO" in out.out.upper()
    code, shown = run(["extract", str(target)], env, capsys=capsys)
    assert code == 0 and json.loads(shown.out)["rotulos_nao_reconhecidos"] == []


def test_sample_default_name_is_marked_fictional_and_both_layouts_exist(tmp_path, env, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for layout in ("a", "b"):
        assert run(["sample", "--layout", layout], env, capsys=capsys)[0] == 0
        assert (tmp_path / f"AMOSTRA-FICTICIA-aih-{layout}.pdf").exists()


def test_sample_does_not_overwrite_an_existing_file_without_force(tmp_path, env, capsys):
    target = tmp_path / "meu.pdf"
    target.write_bytes(b"importante")
    code, out = run(["sample", "--out", str(target)], env, capsys=capsys)
    assert code == 2 and target.read_bytes() == b"importante" and out.err.strip()
    assert run(["sample", "--out", str(target), "--force"], env, capsys=capsys)[0] == 0
    assert target.read_bytes().startswith(b"%PDF")


def test_ingest_of_the_sample_is_refused_but_dry_run_is_fine(tmp_path, env, capsys):
    target = tmp_path / "AMOSTRA-FICTICIA-aih-a.pdf"
    run(["sample", "--out", str(target)], env, capsys=capsys)
    fake = FakeBitrix()
    code, out = run(["ingest", str(target)], env, fake, capsys)
    assert code == 1 and "fictícia" in out.out and fake.writes == []
    code, out = run(["ingest", "--dry-run", str(target)], env, fake, capsys)
    assert code == 0 and "dry_run" in out.out and fake.writes == []


# ---------------------------------------------------------------- doctor

def test_doctor_offline_needs_no_configuration(capsys):
    code, out = run(["doctor", "--offline"], {}, capsys=capsys)
    assert code == 0 and "[PASS] Extrator de PDF" in out.out and "Resultado:" in out.out


def test_doctor_checks_the_portal_read_only_and_exits_zero_when_healthy(env, capsys):
    fake = FakeBitrix()
    fake.add_message("chat360293", text="início")
    code, out = run(["doctor"], {**env, "OL_DIALOG_IDS": "chat360293", "HERMES_API_KEY": "k" * 20}, fake, capsys)
    assert code == 0 and "[FAIL]" not in out.out and "Portal: campos da SPA" in out.out and fake.writes == []


def test_doctor_exits_one_and_says_what_is_missing_when_the_webhook_is_not_configured(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # sem .env por perto
    code, out = run(["doctor"], {}, capsys=capsys)
    assert code == 1 and "BITRIX_WEBHOOK_URL" in out.out


def test_doctor_json_output_is_one_parseable_document(env, capsys):
    code, out = run(["doctor", "--json"], env, FakeBitrix(), capsys)
    data = json.loads(out.out)
    assert code == (0 if data["ok"] else 1) and any(c["name"] == "Python" for c in data["checks"])


# ---------------------------------------------------------------- ingest

def test_ingest_dry_run_writes_nothing(pdf_file, env, capsys):
    fake = FakeBitrix()
    code, out = run(["ingest", "--dry-run", str(pdf_file)], env, fake, capsys)
    assert code == 0 and "dry_run" in out.out and fake.writes == []


def test_ingest_then_ingest_again_is_idempotent_and_prints_no_personal_data(pdf_file, env, capsys):
    fake = FakeBitrix()
    code, first = run(["ingest", str(pdf_file)], env, fake, capsys)
    writes = len(fake.writes)
    code2, second = run(["ingest", str(pdf_file)], env, fake, capsys)
    assert code == 0 and code2 == 0 and "ok" in first.out and "duplicate" in second.out and len(fake.writes) == writes
    for text in (first.out, second.out):
        assert "MARCOS" not in text and "12345678909" not in text and "123.456.789" not in text


def test_ingest_directory_expands_pdfs_and_a_non_aih_file_makes_the_exit_code_nonzero(tmp_path, sample_pdf_bytes, env, capsys):
    folder = tmp_path / "laudos"
    folder.mkdir()
    (folder / "CPF12345678909.pdf").write_bytes(sample_pdf_bytes)
    words = "Contrato de prestação de serviços advocatícios entre as partes abaixo qualificadas firmam o presente instrumento mediante cláusulas".split()
    (folder / "contrato.pdf").write_bytes(make_pdf([[(30 + 40 * (i % 10), 40 + 12 * (i // 10), w, 9.0) for i, w in enumerate(words)]]))
    (folder / "ignorar.txt").write_text("não é pdf")
    code, out = run(["ingest", str(folder)], env, FakeBitrix(), capsys)
    assert code == 1 and "not_aih" in out.out and "ok" in out.out and "ignorar" not in out.out


def test_ingest_needing_review_returns_exit_code_one(pdf_file, env, capsys):
    fake = FakeBitrix()
    for _ in range(2):
        fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", UF_CRM_1524762221558="123.456.789-09")
    code, out = run(["ingest", str(pdf_file)], env, fake, capsys)
    assert code == 1 and "needs_review" in out.out


# ---------------------------------------------------------------- discover

def test_discover_summarises_the_field_map_and_can_save_it(env, tmp_path, capsys):
    target = tmp_path / "field_map.json"
    code, out = run(["discover", "--out", str(target)], env, FakeBitrix(), capsys)
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert code == 0 and "101" in out.out and "DT1146_101:NEW" in out.out
    assert saved["category_id"] == 101 and saved["state_ids"]["MT"] == "377"


def test_discover_reports_a_broken_field_map_with_exit_code_three(env, capsys):
    fake = FakeBitrix()
    del fake.contact_fields["UF_CRM_1725391645"]
    code, out = run(["discover"], env, fake, capsys)
    assert code == 3 and "UF_CRM_1725391645" in out.err


# ---------------------------------------------------------------- selfcheck

def test_selfcheck_command_prints_pass_lines_and_exits_zero_without_personal_data(env, capsys):
    fake = FakeBitrix()
    code, out = run(["selfcheck"], env, fake, capsys)
    assert code == 0 and "PASS" in out.out and "FAIL" not in out.out
    assert "TESTE HERMES" in out.out and "apague" in out.out  # diz ao usuário o que limpar; o app nunca apaga
    assert len(fake.contacts) == 1 and not any(m.endswith(".delete") for m, _ in fake.calls)


def test_selfcheck_command_exits_one_when_a_check_fails(env, capsys):
    class WrongCns(FakeBitrix):
        def _m_crm_item_add(self, p):
            p["fields"]["ufCrm53_1792335030647"] = "000000000000000"
            return super()._m_crm_item_add(p)

    code, out = run(["selfcheck"], env, WrongCns(), capsys)
    assert code == 1 and "FAIL" in out.out


# ---------------------------------------------------------------- configuração

def test_missing_or_malformed_webhook_is_a_clear_exit_code_two_without_echoing_secrets(pdf_file, capsys):
    code, out = run(["ingest", str(pdf_file)], {}, FakeBitrix(), capsys)
    assert code == 2 and "BITRIX_WEBHOOK_URL" in out.err
    code, out = run(["ingest", str(pdf_file)], {"BITRIX_WEBHOOK_URL": "https://x.bitrix24.com.br/segredo"}, FakeBitrix(), capsys)
    assert code == 2 and "segredo" not in out.err


def test_env_file_option_is_honoured(pdf_file, tmp_path, capsys):
    env_file = tmp_path / "bitrix.env.txt"
    env_file.write_text(f"Seu webhook\n{WEBHOOK}\n", encoding="utf-8")
    code, out = run(["--env-file", str(env_file), "ingest", "--dry-run", str(pdf_file)], {"VERIFY_DELAY_SEC": "0", "LEDGER_PATH": str(tmp_path / "l.db")}, FakeBitrix(), capsys)
    assert code == 0 and "dry_run" in out.out


def test_an_explicit_environment_never_reads_a_dotenv_from_the_working_folder(pdf_file, tmp_path, monkeypatch, capsys):
    # regressão real: rodar a suíte numa instalação que já tinha .env (webhook e LEDGER_PATH de verdade) gravou no ledger REAL
    real_ledger = tmp_path / "data" / "ledger.sqlite3"
    (tmp_path / ".env").write_text(
        f"BITRIX_WEBHOOK_URL={WEBHOOK}\nLEDGER_PATH={real_ledger}\nOL_DIALOG_IDS=chat360293\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fake = FakeBitrix()
    code, out = run(["ingest", str(pdf_file)], {}, fake, capsys)  # ambiente explícito e vazio: vale sozinho
    assert code == 2 and "BITRIX_WEBHOOK_URL" in out.err
    assert fake.writes == [] and not real_ledger.exists()
    code, out = run(["doctor"], {}, fake, capsys)
    assert code == 1 and "BITRIX_WEBHOOK_URL" in out.out


def test_without_an_explicit_environment_the_dotenv_of_the_working_folder_is_used(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text(f"BITRIX_WEBHOOK_URL={WEBHOOK}\nLEDGER_PATH={tmp_path / 'l.sqlite3'}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for name in ("BITRIX_WEBHOOK_URL", "LEDGER_PATH", "OL_DIALOG_IDS"):
        monkeypatch.delenv(name, raising=False)
    code = main(["discover"], client_factory=lambda settings: FakeBitrix())  # environ=None: ambiente real do processo + .env
    assert code == 0 and "DT1146_101:NEW" in capsys.readouterr().out


# ---------------------------------------------------------------- poll

def test_poll_once_processes_new_messages_and_requires_a_dialog(pdf_file, env, sample_pdf_bytes, capsys):
    fake = FakeBitrix()
    env = {**env, "OL_DIALOG_IDS": "chat360293"}
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    fake.add_message("chat360293", text="início", at=old)
    assert run(["poll", "--once"], env, fake, capsys)[0] == 0  # 1ª execução só inicializa o cursor
    fake.add_message("chat360293", files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=old + timedelta(minutes=30))
    code, out = run(["poll", "--once"], env, fake, capsys)
    assert code == 0 and "ok" in out.out and len(fake.contacts) == 1 and "MARCOS" not in out.out
    code, out = run(["poll", "--once"], {k: v for k, v in env.items() if k != "OL_DIALOG_IDS"}, fake, capsys)
    assert code == 2 and "OL_DIALOG_IDS" in out.err


def test_poll_init_only_marks_the_starting_point_and_never_processes_even_when_a_cursor_exists(env, sample_pdf_bytes, capsys):
    fake = FakeBitrix()
    env = {**env, "OL_DIALOG_IDS": "chat360293"}
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    first = fake.add_message("chat360293", text="início", at=old)
    code, out = run(["poll", "--init-only"], env, fake, capsys)
    assert code == 0 and "marcado" in out.out and f"#{first}" in out.out and fake.contacts == {}
    fake.add_message("chat360293", files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=old + timedelta(minutes=30))
    code, out = run(["poll", "--init-only"], env, fake, capsys)  # já existe cursor: NÃO processa o PDF novo
    assert code == 0 and "já" in out.out and fake.contacts == {} and fake.writes == []
    assert run(["poll", "--init-only", "--dry-run"], env, fake, capsys)[0] == 2  # combinação sem sentido: recusada
    assert run(["poll", "--init-only"], {k: v for k, v in env.items() if k != "OL_DIALOG_IDS"}, fake, capsys)[0] == 2


def test_poll_quiet_prints_nothing_when_there_is_nothing_new_and_reports_when_there_is(pdf_file, env, sample_pdf_bytes, capsys):
    # o cron do Hermes sem agente entrega a saída tal como está: saída vazia = rodada silenciosa
    fake = FakeBitrix()
    env = {**env, "OL_DIALOG_IDS": "chat360293"}
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    fake.add_message("chat360293", text="início", at=old)
    code, out = run(["poll", "--once", "--quiet"], env, fake, capsys)
    assert code == 0 and out.out == ""
    fake.add_message("chat360293", files=(("CPF12345678909.pdf", sample_pdf_bytes),), at=old + timedelta(minutes=30))
    code, out = run(["poll", "--once", "--quiet"], env, fake, capsys)
    assert code == 0 and "[ok]" in out.out and "MARCOS" not in out.out
    code, out = run(["poll", "--once", "--quiet"], env, fake, capsys)  # nada novo de novo
    assert code == 0 and out.out == ""
