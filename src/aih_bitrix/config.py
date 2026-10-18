"""Configuração por variáveis de ambiente (e arquivo .env opcional). Segredos nunca são impressos."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_WEBHOOK_URL = re.compile(r"https://[^/\s]+/rest/\d+/[A-Za-z0-9]+/?")
_KEY_VALUE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
DEFAULT_TITLE_TEMPLATE = "{nome} ➡ AIH {cid} ➡ {data_solicitacao}"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    webhook_url: str
    api_key: str = ""
    ledger_path: str = "data/ledger.sqlite3"
    entity_type_id: int = 1146
    category_id: int | None = None
    stage_id: str | None = None
    title_template: str = DEFAULT_TITLE_TEMPLATE
    verify_delay: float = 8.0
    max_pdf_mb: int = 15
    ol_dialog_ids: tuple[str, ...] = ()
    poll_interval: int = 30
    allow_no_auth: bool = False
    log_level: str = "INFO"

    @property
    def max_pdf_bytes(self) -> int:
        return self.max_pdf_mb * 1024 * 1024


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def load_env_file(path: str | Path) -> dict[str, str]:
    """Lê `CHAVE=VALOR`. Se não houver BITRIX_WEBHOOK_URL, aceita texto livre contendo a URL do webhook."""
    text = _read_text(Path(path))
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _KEY_VALUE.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    if "BITRIX_WEBHOOK_URL" not in values:
        found = _WEBHOOK_URL.search(text)
        if found:
            values["BITRIX_WEBHOOK_URL"] = found.group(0).rstrip("/") + "/"
    return values


def _number(env: Mapping[str, str], name: str, default, cast):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(f"{name} deve ser um número válido") from None


def load_settings(environ: Mapping[str, str] | None = None, env_file: str | Path | None = None) -> Settings:
    env: dict[str, str] = {}
    if env_file is not None and Path(env_file).exists():
        env.update(load_env_file(env_file))
    env.update(os.environ if environ is None else environ)  # o ambiente vence o arquivo

    url = env.get("BITRIX_WEBHOOK_URL", "").strip()
    if not url:
        raise ConfigError("BITRIX_WEBHOOK_URL não definida (veja .env.example)")
    if not re.fullmatch(_WEBHOOK_URL.pattern, url):  # não repete o valor: pode conter o token
        raise ConfigError("BITRIX_WEBHOOK_URL inválida: esperado https://<portal>/rest/<id>/<token>/")

    category = env.get("BITRIX_SPA_CATEGORY_ID", "").strip()
    return Settings(
        webhook_url=url.rstrip("/") + "/",
        api_key=env.get("HERMES_API_KEY", ""),
        ledger_path=env.get("LEDGER_PATH", "data/ledger.sqlite3"),
        entity_type_id=_number(env, "BITRIX_SPA_ENTITY_TYPE_ID", 1146, int),
        category_id=_number(env, "BITRIX_SPA_CATEGORY_ID", None, int) if category else None,
        stage_id=env.get("BITRIX_SPA_STAGE_ID", "").strip() or None,
        title_template=env.get("CARD_TITLE_TEMPLATE", DEFAULT_TITLE_TEMPLATE),
        verify_delay=_number(env, "VERIFY_DELAY_SEC", 8.0, float),
        max_pdf_mb=_number(env, "MAX_PDF_MB", 15, int),
        ol_dialog_ids=tuple(part.strip() for part in env.get("OL_DIALOG_IDS", "").split(",") if part.strip()),
        poll_interval=_number(env, "POLL_INTERVAL_SEC", 30, int),
        allow_no_auth=env.get("ALLOW_NO_AUTH", "").strip().lower() in {"1", "true", "yes", "sim", "on"},
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
    )
