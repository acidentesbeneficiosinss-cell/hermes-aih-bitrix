"""Diagnóstico da instalação, SOMENTE LEITURA: Python, dependências, extrator de PDF, configuração e acesso ao Bitrix.

Pensado para ser a primeira coisa que o Hermes (ou uma pessoa) roda depois de instalar. Nada aqui grava no portal,
e o relatório nunca contém o token do webhook, nomes ou documentos: só nomes de verificação, status e IDs técnicos.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from .bitrix.client import BitrixClient, BitrixError, redact
from .bitrix.fieldmap import FieldMapError, load_field_map
from .config import ConfigError, Settings, load_settings
from .parser import parse_laudo
from .pdf_text import read_pages
from .synthetic import SAMPLE_LAYOUTS, sample_pdf

MIN_PYTHON = (3, 10)
REQUIRED_PACKAGES: Mapping[str, str] = {  # distribuição -> módulo importável
    "pdfplumber": "pdfplumber", "requests": "requests", "fastapi": "fastapi", "uvicorn": "uvicorn",
    "python-multipart": "multipart",
}
CRITICAL_SCOPES = ("crm",)
POLLER_SCOPES = ("im", "disk")
STATUSES = ("PASS", "WARN", "FAIL", "SKIP")
SHORT_API_KEY = 16


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS | WARN | FAIL | SKIP
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> Check:
        check = Check(name, status, detail)
        self.checks.append(check)
        return check

    @property
    def ok(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    def counts(self) -> dict[str, int]:
        return {status: sum(1 for c in self.checks if c.status == status) for status in STATUSES}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": self.counts(),
            "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks],
        }


def render_text(report: Report) -> str:
    lines = [f"[{c.status}] {c.name}" + (f" — {c.detail}" if c.detail else "") for c in report.checks]
    counts = report.counts()
    summary = ", ".join(f"{counts[s]} {s}" for s in STATUSES)
    lines += ["", f"Resultado: {summary} — " + ("instalação em ordem" if report.ok else "corrija os itens [FAIL] antes de usar")]
    return "\n".join(lines)


# ---------------------------------------------------------------- verificações locais

def check_python(version: Sequence[int] | None = None) -> Check:
    parts = tuple(version) if version is not None else tuple(sys.version_info[:3])
    text = ".".join(str(n) for n in parts[:3])
    if tuple(parts[:2]) >= MIN_PYTHON:
        return Check("Python", "PASS", text)
    return Check("Python", "FAIL", f"{text}: é preciso Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} ou mais novo")


def check_dependencies(required: Mapping[str, str] = REQUIRED_PACKAGES) -> Check:
    found: list[str] = []
    missing: list[str] = []
    for dist, module in required.items():
        if importlib.util.find_spec(module) is None:
            missing.append(dist)
            continue
        try:
            found.append(f"{dist} {importlib.metadata.version(dist)}")
        except importlib.metadata.PackageNotFoundError:
            found.append(dist)
    if missing:
        return Check("Dependências", "FAIL", f"faltando: {', '.join(missing)} — rode: pip install -r requirements.txt")
    return Check("Dependências", "PASS", ", ".join(found))


def check_extractor() -> Check:
    """Lê as amostras fictícias embutidas e exige que todos os rótulos sejam reconhecidos."""
    summary: list[str] = []
    for layout in SAMPLE_LAYOUTS:
        try:
            laudo = parse_laudo(read_pages(sample_pdf(layout)), "doctor")
        except Exception as exc:  # noqa: BLE001 - qualquer falha aqui é um problema de instalação, não de dado
            return Check("Extrator de PDF", "FAIL", f"layout {layout}: {type(exc).__name__}")
        if laudo.unmatched:
            return Check("Extrator de PDF", "FAIL", f"layout {layout}: {len(laudo.unmatched)} rótulo(s) não reconhecido(s)")
        missing = [key for key in ("patient_name", "cns", "birth_date", "request_date") if not laudo.get(key)]
        if missing:
            return Check("Extrator de PDF", "FAIL", f"layout {layout}: campos não lidos: {', '.join(missing)}")
        summary.append(f"layout {layout}: {sum(1 for v in laudo.fields.values() if v)} campos lidos")
    return Check("Extrator de PDF", "PASS", "; ".join(summary))


def check_ledger_location(path: str) -> Check:
    if path == ":memory:":
        return Check("Ledger (SQLite)", "PASS", "em memória")
    target = Path(path).expanduser().resolve()
    probe = target.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if probe.is_dir() and os.access(probe, os.W_OK):
        return Check("Ledger (SQLite)", "PASS", f"{target}" + ("" if target.parent.exists() else " (a pasta será criada)"))
    return Check("Ledger (SQLite)", "FAIL", f"sem permissão de escrita em {target.parent}")


# ---------------------------------------------------------------- configuração e portal

def _config_checks(report: Report, settings: Settings) -> None:
    host = urlparse(settings.webhook_url).netloc
    report.add("Configuração", "PASS", f"portal {host}; SPA {settings.entity_type_id}")
    if settings.api_key:
        if len(settings.api_key) >= SHORT_API_KEY:
            report.add("Chave da API", "PASS", "definida")
        else:
            report.add("Chave da API", "WARN", f"definida, mas curta (use {SHORT_API_KEY}+ caracteres)")
    elif settings.allow_no_auth:
        report.add("Chave da API", "WARN", "sem autenticação (ALLOW_NO_AUTH): quem alcançar a API poderá enviar laudos")
    else:
        report.add("Chave da API", "WARN", "HERMES_API_KEY não definida: POST /upload e /poll responderão 503 (CLI e poller funcionam)")
    if settings.ol_dialog_ids:
        report.add("Diálogos monitorados", "PASS", f"{len(settings.ol_dialog_ids)}: {', '.join(settings.ol_dialog_ids)}")
    else:
        report.add("Diálogos monitorados", "WARN", "OL_DIALOG_IDS vazio: o poller não tem o que monitorar (upload e CLI funcionam)")
    report.checks.append(check_ledger_location(settings.ledger_path))


def _skip_portal(report: Report, settings: Settings | None, reason: str, *, after_auth: bool = False) -> None:
    names = ["Portal: permissões", "Portal: campos da SPA"] if after_auth else ["Portal: acesso"]
    names += [f"Portal: diálogo {d}" for d in (settings.ol_dialog_ids if settings and after_auth else ())]
    for name in names:
        report.add(name, "SKIP", reason)


def _portal_checks(report: Report, settings: Settings, client_factory: Callable[[Settings], Any] | None) -> None:
    def safe(exc: Exception) -> str:
        return redact(str(exc), settings.webhook_url) if isinstance(exc, BitrixError) else type(exc).__name__

    try:
        client = client_factory(settings) if client_factory else BitrixClient(settings.webhook_url)
        profile = client.result("profile") or {}
    except Exception as exc:  # noqa: BLE001
        report.add("Portal: autenticação", "FAIL", safe(exc))
        _skip_portal(report, settings, "sem acesso ao portal", after_auth=True)
        return
    if profile.get("ADMIN"):
        report.add("Portal: autenticação", "PASS", f"usuário #{profile.get('ID')}, administrador")
    else:
        report.add("Portal: autenticação", "WARN", f"usuário #{profile.get('ID')} NÃO é administrador: alguns métodos podem ser negados")

    try:
        scopes = {str(s) for s in (client.result("scope") or [])}
    except BitrixError as exc:
        report.add("Portal: permissões", "WARN", f"não foi possível listar as permissões do webhook ({safe(exc)})")
    else:
        lacking_critical = [s for s in CRITICAL_SCOPES if s not in scopes]
        lacking_poller = [s for s in POLLER_SCOPES if s not in scopes]
        if lacking_critical:
            report.add("Portal: permissões", "FAIL", f"o webhook não tem permissão para: {', '.join(lacking_critical)}")
        elif lacking_poller:
            report.add("Portal: permissões", "WARN", f"o poller precisa também de: {', '.join(lacking_poller)}")
        else:
            report.add("Portal: permissões", "PASS", ", ".join((*CRITICAL_SCOPES, *POLLER_SCOPES)) + " disponíveis")

    try:
        fmap = load_field_map(client, settings)
    except (FieldMapError, BitrixError) as exc:
        report.add("Portal: campos da SPA", "FAIL", safe(exc) if isinstance(exc, BitrixError) else str(exc))
    else:
        report.add(
            "Portal: campos da SPA", "PASS",
            f"SPA {fmap.entity_type_id}, categoria {fmap.category_id}, etapa inicial {fmap.stage_id}; "
            f"{len(fmap.contact)} campos de contato e {len(fmap.item)} da SPA validados",
        )

    for dialog in settings.ol_dialog_ids:
        name = f"Portal: diálogo {dialog}"
        try:
            client.call("im.dialog.messages.get", {"DIALOG_ID": dialog, "LIMIT": 1})
        except BitrixError as exc:
            report.add(name, "FAIL", safe(exc))
        else:
            report.add(name, "PASS", "legível")


def run_doctor(
    *,
    environ: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    client_factory: Callable[[Settings], Any] | None = None,
    offline: bool = False,
) -> Report:
    report = Report()
    report.checks += [check_python(), check_dependencies(), check_extractor()]
    if offline:
        report.add("Configuração", "SKIP", "modo offline")
        _skip_portal(report, None, "modo offline: nada foi enviado ao Bitrix")
        return report
    try:
        settings = load_settings(environ, env_file=env_file)
    except ConfigError as exc:
        report.add("Configuração", "FAIL", str(exc))
        _skip_portal(report, None, "sem configuração válida")
        return report
    _config_checks(report, settings)
    _portal_checks(report, settings, client_factory)
    return report
