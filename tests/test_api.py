from __future__ import annotations

import threading
import time

import pytest
import requests
import uvicorn

from aih_bitrix.api import create_app
from aih_bitrix.config import Settings
from aih_bitrix.ledger import Ledger
from aih_bitrix.pipeline import IngestResult, Pipeline
from aih_bitrix.synthetic import layout_b_pdf
from conftest import WEBHOOK
from fakes import FakeBitrix

KEY = "chave-de-teste-bem-longa"


@pytest.fixture
def start_server(fmap):
    running = []

    def start(poller=None, **settings_kw):
        settings_kw.setdefault("api_key", KEY)
        fake = FakeBitrix()
        settings = Settings(webhook_url=WEBHOOK, **settings_kw)
        pipeline = Pipeline(fake, fmap, Ledger(":memory:"), settings, sleep=lambda s: None)
        server = uvicorn.Server(uvicorn.Config(create_app(pipeline, settings, poller=poller), host="127.0.0.1", port=0, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "servidor de teste não subiu"
        running.append((server, thread))
        port = server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}", fake

    yield start
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=5)


def upload(base, *files, key=KEY, **form):
    headers = {"X-API-Key": key} if key else {}
    payload = [("files", (name, data, "application/pdf")) for name, data in files]
    return requests.post(f"{base}/upload", files=payload, data=form, headers=headers, timeout=60)


# ---------------------------------------------------------------- páginas públicas

def test_health_and_the_form_page_are_public_and_the_docs_are_off(start_server):
    base, _ = start_server()
    health = requests.get(f"{base}/health", timeout=10)
    assert health.status_code == 200 and health.json()["status"] == "ok"
    page = requests.get(f"{base}/", timeout=10)
    assert page.status_code == 200 and "text/html" in page.headers["content-type"] and "AIH" in page.text
    assert page.headers["x-content-type-options"] == "nosniff"
    assert requests.get(f"{base}/docs", timeout=10).status_code == 404  # sem Swagger exposto


# ---------------------------------------------------------------- autenticação

def test_upload_requires_the_api_key(start_server, sample_pdf_bytes):
    base, fake = start_server()
    assert upload(base, ("a.pdf", sample_pdf_bytes), key=None).status_code == 401
    assert upload(base, ("a.pdf", sample_pdf_bytes), key="errada").status_code == 401
    assert fake.writes == []


def test_upload_fails_closed_when_no_key_is_configured(start_server, sample_pdf_bytes):
    base, fake = start_server(api_key="")
    assert upload(base, ("a.pdf", sample_pdf_bytes), key=None).status_code == 503 and fake.writes == []


def test_allow_no_auth_is_an_explicit_opt_in_for_local_use(start_server, sample_pdf_bytes):
    base, _ = start_server(api_key="", allow_no_auth=True)
    assert upload(base, ("CPF12345678909.pdf", sample_pdf_bytes), key=None).status_code == 200


# ---------------------------------------------------------------- upload

def test_model_pdf_upload_runs_end_to_end(start_server, sample_pdf_bytes):
    base, fake = start_server()
    response = upload(base, ("CPF12345678909.pdf", sample_pdf_bytes))
    body = response.json()
    assert response.status_code == 200 and body["summary"] == {"ok": 1}
    result = body["results"][0]
    assert result["status"] == "ok" and result["contact"]["action"] == "created" and result["item"]["action"] == "created"
    assert all(c["ok"] for c in result["checks"]) and len(fake.contacts) == 1 and len(fake.items) == 1


def test_response_never_contains_personal_data_or_the_webhook_token(start_server, sample_pdf_bytes):
    base, _ = start_server()
    text = upload(base, ("CPF12345678909.pdf", sample_pdf_bytes)).text
    for secret in ("MARCOS", "Marcos", "12345678909", "123.456.789", "HELENA", "700000000000012", "abcdef0123456789"):
        assert secret not in text


def test_several_files_in_one_request_are_processed_one_by_one(start_server, sample_pdf_bytes):
    base, fake = start_server()
    body = upload(base, ("CPF12345678909.pdf", sample_pdf_bytes), ("outro.pdf", layout_b_pdf())).json()
    assert body["summary"] == {"ok": 2} and len(fake.contacts) == 2


def test_non_pdf_and_oversize_files_are_rejected_per_file(start_server, sample_pdf_bytes):
    base, fake = start_server(max_pdf_mb=1)
    huge = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10)
    body = upload(base, ("texto.pdf", b"nao sou pdf"), ("grande.pdf", huge), ("ok.pdf", sample_pdf_bytes)).json()
    statuses = [r["status"] for r in body["results"]]
    assert statuses == ["error", "error", "ok"]
    assert "não é um PDF" in body["results"][0]["error"] and "limite" in body["results"][1]["error"]
    assert len(fake.contacts) == 1


def test_cpf_form_field_is_only_accepted_for_a_single_file(start_server, sample_pdf_bytes):
    base, fake = start_server()
    assert upload(base, ("a.pdf", sample_pdf_bytes), ("b.pdf", sample_pdf_bytes), cpf="987.654.321-00").status_code == 400
    one = upload(base, ("sem-cpf-no-nome.pdf", sample_pdf_bytes), cpf="987.654.321-00")
    assert one.status_code == 200 and next(iter(fake.contacts.values()))["UF_CRM_1524762221558"] == "987.654.321-00"


def test_dry_run_flag_writes_nothing(start_server, sample_pdf_bytes):
    base, fake = start_server()
    body = upload(base, ("CPF12345678909.pdf", sample_pdf_bytes), dry_run="true").json()
    assert body["results"][0]["status"] == "dry_run" and fake.writes == []


def test_request_without_files_is_rejected(start_server):
    base, _ = start_server()
    response = requests.post(f"{base}/upload", headers={"X-API-Key": KEY}, timeout=10)
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------- gatilho do poller

def test_poll_endpoint_runs_the_injected_poller_and_returns_the_same_public_shape(start_server):
    calls = []

    def poller(dry_run):
        calls.append(dry_run)
        return [IngestResult(filename="CPF12345678909.pdf", status="ok", sha256="a" * 64)]

    base, _ = start_server(poller=poller)
    assert requests.post(f"{base}/poll", timeout=10).status_code == 401
    body = requests.post(f"{base}/poll", headers={"X-API-Key": KEY}, data={"dry_run": "true"}, timeout=10).json()
    assert calls == [True] and body["summary"] == {"ok": 1} and body["results"][0]["filename"] == "CPF***.***.***-09.pdf"


def test_poll_endpoint_reports_when_no_poller_is_configured(start_server):
    base, _ = start_server()
    assert requests.post(f"{base}/poll", headers={"X-API-Key": KEY}, timeout=10).status_code == 501
