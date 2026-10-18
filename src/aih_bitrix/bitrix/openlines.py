"""Poller dos Canais Abertos: lê as mensagens novas de um grupo, baixa os PDFs e entrega ao pipeline.

Padrão observado no grupo "Docs protocolo" (conector 24Zap, linha 31): arquivos -> mensagem "cpf NNN.NNN.NNN-NN"
-> separador "-----" -> resposta do bot. Só lotes FECHADOS (separador, resposta de bot, intervalo longo ou
silêncio do chat) são processados: assim o CPF que chega DEPOIS dos arquivos não se perde.

A captura usa só o webhook: `im.dialog.messages.get` (paginação por FIRST_ID, medida no portal real),
`params.FILE_ID` -> `disk.file.get` -> `DOWNLOAD_URL`. Webhook não consegue registrar eventos (event.* é só OAuth).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ..config import Settings
from ..cpf import cpf_from_filename, cpf_from_text
from ..ledger import Ledger
from ..pipeline import IngestResult, Pipeline
from .client import BitrixError

log = logging.getLogger("aih_bitrix.openlines")

PAGE = 50
MAX_MESSAGES_PER_POLL = 2000
_SEPARATOR = re.compile(r"[\W_]{5,}")  # "-----", "​-----", "━━━━━"


@dataclass(frozen=True)
class Attachment:
    file_id: int
    name: str
    size: int | None
    message_id: int
    cpf_hint: str | None


@dataclass
class Batch:
    message_ids: list[int]
    attachments: list[Attachment]
    cpf_hint: str | None = None


@dataclass
class _Draft:
    message_ids: list[int] = field(default_factory=list)
    files: list[tuple[int, str, int | None, int]] = field(default_factory=list)
    cpf: str | None = None

    def finish(self) -> Batch:
        attachments = [Attachment(fid, name, size, mid, self.cpf) for fid, name, size, mid in self.files]
        return Batch(self.message_ids, attachments, self.cpf)


def _when(message: Mapping[str, Any]) -> datetime:
    moment = datetime.fromisoformat(str(message["date"]))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _is_pdf(info: Mapping[str, Any]) -> bool:
    return str(info.get("extension") or "").lower() == "pdf" or str(info.get("name") or "").lower().endswith(".pdf")


def group_batches(
    messages: Sequence[Mapping[str, Any]],
    files: Mapping[int, Mapping[str, Any]],
    users: Mapping[int, Mapping[str, Any]],
    *,
    now: datetime,
    quiet_sec: float = 60.0,
    gap_sec: float = 300.0,
) -> tuple[list[Batch], int | None]:
    """Devolve (lotes fechados, id da 1ª mensagem do lote ainda aberto ou None)."""
    closed: list[Batch] = []
    current: _Draft | None = None
    last_time: datetime | None = None
    for message in sorted(messages, key=lambda m: int(m["id"])):
        moment = _when(message)
        if current is not None and last_time is not None and (moment - last_time).total_seconds() > gap_sec:
            closed.append(current.finish())  # silêncio longo fecha o lote anterior
            current = None
        last_time = moment
        if bool((users.get(int(message["author_id"])) or {}).get("bot")):
            if current is not None:  # a resposta do bot ("Recebido — ...") fecha o lote
                closed.append(current.finish())
                current = None
            continue
        current = current or _Draft()
        current.message_ids.append(int(message["id"]))
        text = message.get("text") or ""
        current.cpf = current.cpf or cpf_from_text(text)
        for file_id in (message.get("params") or {}).get("FILE_ID") or []:
            info = files.get(int(file_id))
            if info is not None and _is_pdf(info):
                current.files.append((int(file_id), str(info.get("name") or ""), info.get("size"), int(message["id"])))
        if _SEPARATOR.fullmatch(text.strip()):
            closed.append(current.finish())
            current = None
    open_first: int | None = None
    if current is not None and last_time is not None:
        if (now - last_time).total_seconds() >= quiet_sec:
            closed.append(current.finish())
        else:
            open_first = current.message_ids[0]
    return closed, open_first


def fetch_messages(client: Any, dialog_id: str, after_id: int) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Mensagens mais novas que `after_id`, em ordem crescente (FIRST_ID pagina para frente)."""
    messages: list[dict[str, Any]] = []
    files: dict[int, dict[str, Any]] = {}
    users: dict[int, dict[str, Any]] = {}
    cursor = after_id
    while len(messages) < MAX_MESSAGES_PER_POLL:
        result = client.result("im.dialog.messages.get", {"DIALOG_ID": dialog_id, "FIRST_ID": cursor, "LIMIT": PAGE}) or {}
        page = result.get("messages") or []
        for info in result.get("files") or []:
            files[int(info["id"])] = info
        for user in result.get("users") or []:
            users[int(user["id"])] = user
        messages.extend(page)
        newest = max((int(m["id"]) for m in page), default=cursor)
        if len(page) < PAGE or newest <= cursor:
            break
        cursor = newest
    return sorted(messages, key=lambda m: int(m["id"])), files, users


def latest_message_id(client: Any, dialog_id: str) -> int:
    result = client.result("im.dialog.messages.get", {"DIALOG_ID": dialog_id, "LIMIT": PAGE}) or {}
    return max((int(m["id"]) for m in result.get("messages") or []), default=0)


def backfill_cursor(client: Any, dialog_id: str, count: int) -> int:
    """Cursor que faz o próximo `fetch_messages` devolver as últimas `count` mensagens."""
    ids: list[int] = []
    params: dict[str, Any] = {"DIALOG_ID": dialog_id, "LIMIT": PAGE}
    while len(ids) < count:
        page = (client.result("im.dialog.messages.get", params) or {}).get("messages") or []
        if not page:
            break
        ids.extend(int(m["id"]) for m in page)
        params = {"DIALOG_ID": dialog_id, "LIMIT": PAGE, "LAST_ID": min(ids)}
    newest = sorted(ids, reverse=True)[:count]
    return (min(newest) - 1) if newest else 0


def _process_attachment(
    client: Any, pipeline: Pipeline, attachment: Attachment, dialog: str, dry_run: bool, max_pdf_bytes: int
) -> IngestResult:
    shown = f"anexo {dialog}#{attachment.file_id}"  # nunca o nome real: pode ser o nome do paciente
    if attachment.size is not None and int(attachment.size) > max_pdf_bytes:
        return IngestResult(filename=shown, status="error", error=f"arquivo maior que o limite de {max_pdf_bytes / 1048576:.1f} MB")
    try:
        info = client.result("disk.file.get", {"id": attachment.file_id}) or {}
        url = info.get("DOWNLOAD_URL")
        if not url:
            return IngestResult(filename=shown, status="error", error="o Bitrix não devolveu URL de download")
        data = client.download(url, max_pdf_bytes)
    except BitrixError as exc:
        return IngestResult(filename=shown, status="error", error=str(exc), retryable=exc.retryable)
    cpf = None if cpf_from_filename(attachment.name) else attachment.cpf_hint  # o nome do arquivo é mais específico
    return pipeline.process_pdf(data, attachment.name, cpf=cpf, source=f"ol:{dialog}", dry_run=dry_run, display_name=shown)


def poll_once(
    client: Any,
    pipeline: Pipeline,
    ledger: Ledger,
    dialog_ids: Sequence[str],
    *,
    dry_run: bool = False,
    backfill: int = 0,
    max_pdf_bytes: int = 15 * 1024 * 1024,
    now: datetime | None = None,
    quiet_sec: float = 60.0,
    gap_sec: float = 300.0,
) -> list[IngestResult]:
    now = now or datetime.now(timezone.utc)
    results: list[IngestResult] = []
    for dialog in dialog_ids:
        cursor = ledger.get_cursor(dialog)
        if cursor == 0:
            if backfill <= 0:  # primeira execução: não importa o histórico do grupo, só marca o ponto de partida
                if not dry_run:
                    latest = latest_message_id(client, dialog)
                    if latest:
                        ledger.set_cursor(dialog, latest)
                log.info("poller: cursor do diálogo %s inicializado sem processar o histórico", dialog)
                continue
            cursor = backfill_cursor(client, dialog, backfill)
        messages, files, users = fetch_messages(client, dialog, cursor)
        if not messages:
            continue
        batches, open_first = group_batches(messages, files, users, now=now, quiet_sec=quiet_sec, gap_sec=gap_sec)
        new_cursor = (open_first - 1) if open_first is not None else max(int(m["id"]) for m in messages)
        skipped_images = sum(
            1 for m in messages for f in (m.get("params") or {}).get("FILE_ID") or [] if int(f) in files and not _is_pdf(files[int(f)])
        )
        halted = False
        for batch in batches:
            for attachment in batch.attachments:
                result = _process_attachment(client, pipeline, attachment, dialog, dry_run, max_pdf_bytes)
                results.append(result)
                if result.status == "error" and result.retryable:  # transitório: o cursor fica antes deste lote
                    new_cursor = min(new_cursor, batch.message_ids[0] - 1)
                    halted = True
                    break
            if halted:
                break
        log.info("poller: diálogo %s: %d lote(s), %d resultado(s), %d imagem(ns) ignorada(s)", dialog, len(batches), len(results), skipped_images)
        if not dry_run:
            ledger.set_cursor(dialog, new_cursor)
    return results


def make_poller(
    client: Any, pipeline: Pipeline, ledger: Ledger, settings: Settings, *, backfill: int = 0
) -> Callable[[bool], list[IngestResult]] | None:
    """Função `run(dry_run)` pronta para a API/CLI; None se nenhum diálogo foi configurado (OL_DIALOG_IDS)."""
    if not settings.ol_dialog_ids:
        return None

    def run(dry_run: bool = False) -> list[IngestResult]:
        return poll_once(
            client, pipeline, ledger, settings.ol_dialog_ids,
            dry_run=dry_run, backfill=backfill, max_pdf_bytes=settings.max_pdf_bytes,
        )

    return run
