"""Pipeline: PDF -> laudo -> inferências -> contato -> card -> verificação, com idempotência e dry-run.

Entradas (upload, poller, CLI) só chamam `Pipeline.process_pdf`. O resultado público (`to_public`) nunca
contém dado pessoal: só status, IDs do Bitrix e nomes de campos.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .bitrix.client import BitrixError
from .bitrix.contacts import ContactResult, upsert_contact
from .bitrix.fieldmap import FieldMap
from .bitrix.items import ItemResult, upsert_item
from .bitrix.verify import FieldCheck, verify_contact, verify_item
from .config import Settings
from .cpf import cpf_from_filename, is_valid_cpf, mask_cpfs_in_text, only_digits
from .infer import infer_all
from .ledger import Ledger
from .parser import NotAihError, parse_laudo
from .pdf_text import InvalidPdfError, ScannedPdfError, read_pages
from .synthetic import is_sample_name

log =logging.getLogger("aih_bitrix.pipeline")


@dataclass
class IngestResult:
    filename: str
    status: str  # ok | duplicate | needs_review | not_aih | error | dry_run
    contact: ContactResult | None = None
    item: ItemResult | None = None
    checks: list[FieldCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    sha256: str = ""
    retryable: bool = False  # True só para erro transitório do Bitrix: tentar de novo pode funcionar

    def to_public(self) -> dict[str, Any]:
        def part(p: ContactResult | ItemResult | None) -> dict[str, Any] | None:
            if p is None:
                return None
            return {"id": p.id, "action": p.action, "filled": list(p.filled), "skipped": list(p.skipped)}

        return {
            "filename": mask_cpfs_in_text(self.filename),
            "status": self.status,
            "sha256": self.sha256[:12],
            "contact": part(self.contact),
            "item": part(self.item),
            "checks": [{"scope": c.scope, "field": c.field, "ok": c.ok} for c in self.checks],
            "warnings": list(self.warnings),
            "error": self.error,
            "retryable": self.retryable,
        }


class Pipeline:
    def __init__(
        self,
        client: Any,
        fmap: FieldMap,
        ledger: Ledger,
        settings: Settings,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._fmap = fmap
        self._ledger = ledger
        self._settings = settings
        self._sleep = sleep
        # API, poller e CLI podem rodar no mesmo processo: sem exclusão mútua, dois processamentos do mesmo
        # paciente criariam contatos/cards duplicados (os upserts são idempotentes, mas não atômicos)
        self._lock = threading.Lock()

    def _record(self, result: IngestResult, source: str, dry_run: bool) -> None:
        if dry_run or not result.sha256:
            return
        self._ledger.record(
            result.sha256,
            status=result.status,
            source=source,
            contact_id=result.contact.id if result.contact else None,
            item_id=result.item.id if result.item else None,
        )

    def process_pdf(
        self,
        data: bytes,
        filename: str,
        *,
        cpf: str | None = None,
        source: str = "upload",
        dry_run: bool = False,
        display_name: str | None = None,
    ) -> IngestResult:
        """`display_name` substitui o nome do arquivo em TODA saída (o poller usa um nome neutro: no grupo
        real os arquivos se chamam "NOME DO PACIENTE, AIH .PDF")."""
        with self._lock:
            return self._process(data, filename, cpf, source, dry_run, display_name)

    def _process(
        self, data: bytes, filename: str, cpf: str | None, source: str, dry_run: bool, display_name: str | None
    ) -> IngestResult:
        sha = hashlib.sha256(data).hexdigest()
        result = IngestResult(filename=display_name or mask_cpfs_in_text(filename), status="error", sha256=sha)
        if not dry_run and is_sample_name(filename):  # paciente inventado: nunca deve virar contato/card de verdade
            result.error = "arquivo de amostra fictícia (gerado por `sample`): use --dry-run ou extract; nada foi gravado"
            return result

        prior = self._ledger.get(sha)
        if prior and prior["status"] in ("ok", "not_aih"):
            result.status = "duplicate"
            result.warnings.append(
                "arquivo já analisado antes: não é um laudo AIH"
                if prior["status"] == "not_aih"
                else f"arquivo já processado (contato {prior['contact_id']}, card {prior['item_id']})"
            )
            return result

        try:
            pages = read_pages(data)
            laudo = parse_laudo(pages, filename)
        except ScannedPdfError:
            result.status = "not_aih"
            result.warnings.append("PDF sem camada de texto (provavelmente escaneado); OCR não habilitado")
            self._record(result, source, dry_run)
            return result
        except InvalidPdfError as exc:
            result.error = str(exc)
            return result
        except NotAihError as exc:
            result.status = "not_aih"
            result.warnings.append(str(exc))
            self._record(result, source, dry_run)
            return result

        chosen = None
        if cpf:
            if is_valid_cpf(cpf):
                chosen = only_digits(cpf)
            else:
                result.warnings.append("CPF informado é inválido e foi ignorado")
        if chosen is None:
            chosen = cpf_from_filename(filename)
        if not laudo.patient_name:
            result.status = "needs_review"
            result.warnings.append("nome do paciente não encontrado no laudo; nada foi gravado")
            return result
        if laudo.unmatched:
            result.warnings.append(f"{len(laudo.unmatched)} rótulo(s) do PDF não reconhecido(s); veja a timeline do card")

        inferences = infer_all(laudo)
        try:
            result.contact = upsert_contact(self._client, self._fmap, laudo, chosen, dry_run=dry_run)
            result.warnings += result.contact.warnings
            if result.contact.action == "ambiguous":
                result.status = "needs_review"
                self._record(result, source, dry_run)
                return result
            result.item = upsert_item(
                self._client, self._fmap, laudo, inferences, result.contact.id, chosen,
                title_template=self._settings.title_template, dry_run=dry_run,
            )
            result.warnings += result.item.warnings
            if dry_run:
                result.status = "dry_run"
                return result
            if result.contact.expected or result.item.expected:
                self._sleep(self._settings.verify_delay)  # o BP do portal roda de forma assíncrona
            if result.contact.id and result.contact.expected:
                result.checks += verify_contact(self._client, result.contact.id, result.contact.expected, self._fmap)
            if result.item.id and result.item.expected:
                result.checks += verify_item(self._client, result.item.id, result.item.expected, self._fmap)
        except BitrixError as exc:
            result.status, result.error, result.retryable = "error", str(exc), exc.retryable
            self._record(result, source, dry_run)
            return result
        except Exception as exc:  # nunca repetir a mensagem nem o traceback: eles podem conter dado do paciente
            frames = traceback.extract_tb(exc.__traceback__)
            where = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "?"
            log.error("falha inesperada no pipeline: %s em %s", type(exc).__name__, where)
            result.status, result.error = "error", f"erro inesperado ({type(exc).__name__})"
            self._record(result, source, dry_run)
            return result

        failed = [f"{c.scope}.{c.field}" for c in result.checks if not c.ok]
        if failed:
            result.status = "needs_review"
            result.warnings.append("verificação divergente em: " + ", ".join(failed))
        else:
            result.status = "ok"
        self._record(result, source, dry_run)
        return result
