from __future__ import annotations

import json

import pytest

from aih_bitrix import doctor as doctor_module
from aih_bitrix.bitrix.client import BitrixError
from aih_bitrix.doctor import check_dependencies, check_python, render_text, run_doctor
from conftest import WEBHOOK
from fakes import FakeBitrix

DIALOG = "chat360293"


@pytest.fixture
def env(tmp_path):
    return {
        "BITRIX_WEBHOOK_URL": WEBHOOK, "LEDGER_PATH": str(tmp_path / "data" / "ledger.sqlite3"),
        "HERMES_API_KEY": "chave-de-teste-com-mais-de-16", "OL_DIALOG_IDS": DIALOG,
    }


def run(env, fake=None, **kw):
    fake = fake if fake is not None else FakeBitrix()
    fake.add_message(DIALOG, text="início")
    return run_doctor(environ=env, client_factory=lambda settings: fake, **kw), fake


def status(report, name):
    return next(c.status for c in report.checks if c.name == name)


# ---------------------------------------------------------------- verificações locais

def test_python_must_be_3_10_or_newer():
    assert check_python((3, 10, 0)).status == "PASS" and check_python((3, 14, 3)).status == "PASS"
    old = check_python((3, 9, 18))
    assert old.status == "FAIL" and "3.10" in old.detail


def test_a_missing_dependency_is_a_failure_that_names_it_and_the_fix():
    ok = check_dependencies({"pytest": "pytest"})
    assert ok.status == "PASS" and "pytest" in ok.detail
    bad = check_dependencies({"pytest": "pytest", "pacote-que-nao-existe": "modulo_que_nao_existe"})
    assert bad.status == "FAIL" and "pacote-que-nao-existe" in bad.detail and "pip install" in bad.detail


def test_offline_doctor_checks_the_install_and_never_touches_the_portal():
    report = run_doctor(environ={}, offline=True)
    assert report.ok
    assert [c.name for c in report.checks][:3] == ["Python", "Dependências", "Extrator de PDF"]
    assert status(report, "Extrator de PDF") == "PASS"
    portal = [c for c in report.checks if c.name.startswith("Portal") or c.name == "Configuração"]
    assert portal and all(c.status == "SKIP" for c in portal)


def test_the_pdf_extractor_check_fails_when_a_sample_label_is_not_recognised(monkeypatch):
    from aih_bitrix.synthetic import layout_b_page, make_pdf

    def broken(layout="a"):
        return make_pdf([layout_b_page() + [(33, 340.0, "53 - Campo Inventado Pelo Hospital", 7.0)]])

    monkeypatch.setattr(doctor_module, "sample_pdf", broken)
    report = run_doctor(environ={}, offline=True)
    extractor = next(c for c in report.checks if c.name == "Extrator de PDF")
    assert not report.ok and extractor.status == "FAIL" and "rótulo" in extractor.detail


# ---------------------------------------------------------------- configuração

def test_missing_or_malformed_webhook_is_a_failure_and_the_portal_checks_are_skipped(tmp_path):
    for env in ({}, {"BITRIX_WEBHOOK_URL": "https://x.bitrix24.com.br/nao-e-webhook"}):
        report = run_doctor(environ=env)
        assert not report.ok and status(report, "Configuração") == "FAIL"
        assert all(c.status == "SKIP" for c in report.checks if c.name.startswith("Portal"))


def test_api_key_and_dialogs_are_only_warnings(env):
    weak = {k: v for k, v in env.items() if k not in ("HERMES_API_KEY", "OL_DIALOG_IDS")}
    report, _ = run(weak)
    assert report.ok and status(report, "Chave da API") == "WARN" and status(report, "Diálogos monitorados") == "WARN"
    open_api = {**weak, "ALLOW_NO_AUTH": "true"}
    report, _ = run(open_api)
    assert status(report, "Chave da API") == "WARN" and "sem autenticação" in next(c.detail for c in report.checks if c.name == "Chave da API")


def test_unwritable_ledger_location_is_a_failure(env, tmp_path):
    blocker = tmp_path / "arquivo"
    blocker.write_text("x")
    report, _ = run({**env, "LEDGER_PATH": str(blocker / "sub" / "ledger.sqlite3")})
    assert status(report, "Ledger (SQLite)") == "FAIL"


# ---------------------------------------------------------------- portal (somente leitura)

def test_healthy_portal_passes_every_check_without_writing_anything(env):
    report, fake = run(env)
    assert report.ok and not any(c.status in ("FAIL", "WARN") for c in report.checks)
    names = [c.name for c in report.checks]
    for expected in ("Configuração", "Chave da API", "Diálogos monitorados", "Ledger (SQLite)", "Portal: autenticação",
                     "Portal: permissões", "Portal: campos da SPA", f"Portal: diálogo {DIALOG}"):
        assert expected in names
    assert fake.writes == []
    assert {m for m, _ in fake.calls} <= {"profile", "scope", "crm.contact.fields", "crm.item.fields", "crm.category.list",
                                          "crm.status.list", "im.dialog.messages.get"}


def test_the_report_never_contains_the_webhook_token(env):
    report, _ = run(env)
    assert "abcdef0123456789" not in render_text(report) and "abcdef0123456789" not in json.dumps(report.to_dict())


def test_scopes_missing_for_crm_fail_and_missing_for_the_poller_only_warn(env):
    fake = FakeBitrix()
    fake.scopes = ["im", "disk"]
    report, _ = run(env, fake)
    assert not report.ok and status(report, "Portal: permissões") == "FAIL"
    fake = FakeBitrix()
    fake.scopes = ["crm", "user"]
    report, _ = run(env, fake)
    assert report.ok and status(report, "Portal: permissões") == "WARN"


def test_a_non_admin_webhook_is_a_warning(env):
    fake = FakeBitrix()
    fake.profile = {"ID": "7", "ADMIN": False}
    report, _ = run(env, fake)
    assert report.ok and status(report, "Portal: autenticação") == "WARN"


def test_unreachable_portal_fails_authentication_and_skips_the_rest(env):
    class Down(FakeBitrix):
        def _m_profile(self, p):
            raise BitrixError("NETWORK", "falha de rede (ConnectionError)")

    report, _ = run(env, Down())
    assert not report.ok and status(report, "Portal: autenticação") == "FAIL"
    assert status(report, "Portal: permissões") == "SKIP" and status(report, "Portal: campos da SPA") == "SKIP"


def test_incompatible_spa_fields_fail_with_the_reason(env):
    fake = FakeBitrix()
    del fake.item_fields["ufCrm53_1792335030647"]  # campo "CNS" some do portal
    report, _ = run(env, fake)
    fields = next(c for c in report.checks if c.name == "Portal: campos da SPA")
    assert not report.ok and fields.status == "FAIL" and fields.detail


def test_an_unreadable_dialog_is_a_failure_for_that_dialog_only(env):
    class NoChat(FakeBitrix):
        def _m_im_dialog_messages_get(self, p):
            raise BitrixError("ACCESS_DENIED", "Access denied")

    report, _ = run(env, NoChat())
    assert not report.ok and status(report, f"Portal: diálogo {DIALOG}") == "FAIL"
    assert status(report, "Portal: campos da SPA") == "PASS"


# ---------------------------------------------------------------- saída

def test_json_report_is_machine_readable(env):
    report, _ = run(env)
    data = json.loads(json.dumps(report.to_dict()))
    assert data["ok"] is True and data["counts"]["FAIL"] == 0
    assert {"name", "status", "detail"} <= set(data["checks"][0])


def test_text_report_ends_with_a_summary_line(env):
    report, _ = run(env)
    text = render_text(report)
    assert "[PASS] Python" in text and text.strip().splitlines()[-1].startswith("Resultado:")
