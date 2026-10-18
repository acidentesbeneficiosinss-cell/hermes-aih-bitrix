"""Linha de comando: doctor | sample | extract | discover | ingest | poll | selfcheck | serve.

Códigos de saída: 0 ok · 1 há pendência (needs_review/erro/não-AIH, ou item [FAIL] no doctor) · 2 uso, arquivo ou
configuração · 3 Bitrix inacessível ou campos incompatíveis. Nada impresso contém dado pessoal (exceto `extract --show`).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import __version__
from .api import create_app
from .bitrix.client import BitrixClient, BitrixError
from .bitrix.fieldmap import FieldMap, FieldMapError, load_field_map
from .bitrix.openlines import make_poller, poll_once
from .config import ConfigError, Settings, load_settings
from .doctor import render_text, run_doctor
from .hermes_install import (
    CRON_JOB_NAME,
    CRON_SCRIPT_NAME,
    HermesInstallError,
    default_app_dir,
    default_hermes_home,
    install,
)
from .infer import infer_all
from .ledger import Ledger
from .parser import NotAihError, parse_laudo
from .pdf_text import PdfTextError, read_pages
from .pipeline import IngestResult, Pipeline
from .selfcheck import run_selfcheck
from .synthetic import SAMPLE_LAYOUTS, sample_filename, sample_pdf

ClientFactory = Callable[[Settings], Any]
_TOKEN_IN_URL = re.compile(r"(/rest/\d+/)[A-Za-z0-9]+(/)")
_OK_STATUSES = {"ok", "duplicate", "dry_run"}


def _force_utf8_output() -> None:
    """Saída sempre em UTF-8: com stdout redirecionado o Windows usa cp1252, que perde acentos e não tem '➡'."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


class _RedactFilter(logging.Filter):
    """Garante que o token do webhook nunca vá para o log, mesmo que uma biblioteca o imprima."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TOKEN_IN_URL.sub(r"\1<TOKEN>\2", str(record.msg))
        if record.args:
            record.args = tuple(_TOKEN_IN_URL.sub(r"\1<TOKEN>\2", a) if isinstance(a, str) else a for a in record.args)
        return True


@dataclasses.dataclass
class Runtime:
    settings: Settings
    client: Any
    fmap: FieldMap
    ledger: Ledger
    pipeline: Pipeline


def build_runtime(settings: Settings, client_factory: ClientFactory | None = None) -> Runtime:
    client = client_factory(settings) if client_factory else BitrixClient(settings.webhook_url)
    fmap = load_field_map(client, settings)
    ledger = Ledger(settings.ledger_path)
    return Runtime(settings, client, fmap, ledger, Pipeline(client, fmap, ledger, settings))


def _line(result: IngestResult) -> str:
    public = result.to_public()

    def part(name: str) -> str:
        info = public[name]
        return "-" if info is None else f"{info['action']}{' #' + str(info['id']) if info['id'] else ''}"

    checks = public["checks"]
    bad = sum(1 for c in checks if not c["ok"])
    notes = " | ".join(x for x in [public["error"], *public["warnings"]] if x)
    base = f"[{public['status']}] {public['filename']} | contato: {part('contact')} | card: {part('item')}"
    if checks:
        base += f" | verificação: {len(checks) - bad} ok/{bad} divergente(s)"
    return base + (f" | {notes}" if notes else "")


def _expand(paths: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        files.extend(sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf") if path.is_dir() else [path])
    return files


# ---------------------------------------------------------------------------------------------- comandos

def _extract(args: argparse.Namespace) -> int:
    try:
        data = Path(args.pdf).read_bytes()
    except OSError:
        print("erro: não foi possível ler o arquivo", file=sys.stderr)
        return 2
    try:
        laudo = parse_laudo(read_pages(data), Path(args.pdf).name)
    except (PdfTextError, NotAihError) as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    inferences = infer_all(laudo)
    if args.show:  # contém dado pessoal: só para quem pediu explicitamente
        report: dict[str, Any] = {
            "campos": dict(laudo.fields),
            "inferencias": {k: {"value": v.value, "confidence": v.confidence, "note": v.note} for k, v in inferences.items()},
        }
    else:
        report = {
            "campos": {k: ("ok" if v else "vazio") for k, v in laudo.fields.items()},
            "inferencias": {k: v.confidence for k, v in inferences.items()},
        }
    report["rotulos_nao_reconhecidos"] = list(laudo.unmatched)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _sample(args: argparse.Namespace) -> int:
    target = Path(args.out or sample_filename(args.layout))
    if target.exists() and not args.force:
        print(f"erro: {target.name} já existe; use --force para sobrescrever", file=sys.stderr)
        return 2
    try:
        target.write_bytes(sample_pdf(args.layout))
    except OSError:
        print("erro: não foi possível gravar o arquivo", file=sys.stderr)
        return 2
    print(f"PDF de amostra 100% fictício (layout {args.layout}) gravado em {target}")
    print("Use só com `extract` ou `ingest --dry-run`: o paciente é inventado e não deve virar contato no portal.")
    return 0


def _hermes_install(args: argparse.Namespace) -> int:
    home = Path(args.hermes_home) if args.hermes_home else default_hermes_home()
    app = Path(args.app_dir) if args.app_dir else default_app_dir()
    try:
        written = install(home, app, sys.executable, force=args.force)
    except HermesInstallError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    print("Arquivos gravados:")
    for path in written:
        print(f"  {path}")
    print("\nFalta agendar o poller (o Hermes roda o script de tempos em tempos, sem usar o modelo):")
    print(f'  hermes cron create "every 5m" --no-agent --script {CRON_SCRIPT_NAME} --name {CRON_JOB_NAME} --deliver <destino>')
    print("Destinos de --deliver: origin, local (só grava em arquivo), telegram, discord, signal ou plataforma:chat_id.")
    print("Se uma rodada passar de 120 s (limite padrão dos scripts de cron), aumente cron.script_timeout_seconds")
    print("no config.yaml do Hermes (ou a variável HERMES_CRON_SCRIPT_TIMEOUT).")
    return 0


def _doctor(args: argparse.Namespace, environ: Mapping[str, str] | None, factory: ClientFactory | None) -> int:
    env_file = args.env_file or (".env" if Path(".env").exists() else None)
    report = run_doctor(environ=environ, env_file=env_file, client_factory=factory, offline=args.offline)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) if args.json else render_text(report))
    return 0 if report.ok else 1


def _discover(settings: Settings, args: argparse.Namespace, factory: ClientFactory | None) -> int:
    client = factory(settings) if factory else BitrixClient(settings.webhook_url)
    fmap = load_field_map(client, settings)
    print(f"portal: {client.host}")
    print(f"SPA {fmap.entity_type_id}: categoria {fmap.category_id}, etapa inicial {fmap.stage_id}")
    print(f"campos de contato validados: {len(fmap.contact)} | campos da SPA validados: {len(fmap.item)}")
    print(f"estados mapeados: {len(fmap.state_ids)} (MT -> {fmap.state_ids.get('MT')})")
    if args.out:
        Path(args.out).write_text(json.dumps(dataclasses.asdict(fmap), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"mapa salvo em {args.out}")
    return 0


def _ingest(settings: Settings, args: argparse.Namespace, factory: ClientFactory | None) -> int:
    files = _expand(args.paths)
    if not files:
        print("erro: nenhum PDF encontrado", file=sys.stderr)
        return 2
    runtime = build_runtime(settings, factory)
    worst = 0
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            print(f"[error] {path.name}: não foi possível ler o arquivo")
            worst = 1
            continue
        result = runtime.pipeline.process_pdf(data, path.name, cpf=args.cpf, source="cli", dry_run=args.dry_run)
        print(_line(result))
        if result.status not in _OK_STATUSES:
            worst = 1
    return worst


def _poll(settings: Settings, args: argparse.Namespace, factory: ClientFactory | None) -> int:
    dialogs = tuple(args.dialog or settings.ol_dialog_ids)
    if not dialogs:
        print("erro: nenhum diálogo configurado; defina OL_DIALOG_IDS ou use --dialog", file=sys.stderr)
        return 2
    runtime = build_runtime(settings, factory)

    def cycle() -> int:
        results = poll_once(
            runtime.client, runtime.pipeline, runtime.ledger, dialogs,
            dry_run=args.dry_run, backfill=args.backfill, max_pdf_bytes=settings.max_pdf_bytes,
        )
        if results or not args.quiet:  # --quiet: rodada sem novidade não imprime nada (cron sem agente = tique silencioso)
            for result in results:
                print(_line(result))
            print(f"poll: {len(results)} arquivo(s) analisado(s)")
        return 1 if any(r.status in ("error", "needs_review") for r in results) else 0

    if args.once:
        return cycle()
    interval = args.interval or settings.poll_interval
    try:
        while True:
            try:
                cycle()
            except BitrixError as exc:
                print(f"erro do Bitrix (tentará de novo): {exc}", file=sys.stderr)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _selfcheck(settings: Settings, args: argparse.Namespace, factory: ClientFactory | None) -> int:
    client = factory(settings) if factory else BitrixClient(settings.webhook_url)
    fmap = load_field_map(client, settings)
    report = run_selfcheck(client, fmap, settings)
    for check in report.checks:
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}" + (f"  ({check.detail})" if check.detail else ""))
    print(f"\n{sum(c.ok for c in report.checks)}/{len(report.checks)} verificações passaram")
    if report.contact_id or report.item_id:
        print(f"Registros sintéticos [TESTE HERMES] no portal: contato #{report.contact_id}, card #{report.item_id}.")
        print("Se quiser limpar, apague-os manualmente: o app nunca apaga nada (a lixeira da SPA está desligada).")
    return 0 if report.ok else 1


def _serve(settings: Settings, args: argparse.Namespace, factory: ClientFactory | None) -> int:
    import uvicorn

    runtime = build_runtime(settings, factory)
    poller = make_poller(runtime.client, runtime.pipeline, runtime.ledger, settings)
    if not settings.api_key and not settings.allow_no_auth:
        print("aviso: HERMES_API_KEY não definida; POST /upload e /poll responderão 503", file=sys.stderr)
    if args.with_poller and poller is not None:

        def loop() -> None:
            while True:
                try:
                    poller(False)
                except Exception:  # noqa: BLE001 - o laço não pode morrer; o erro já é logado sem dados pessoais
                    logging.getLogger("aih_bitrix.poller").exception("falha na rodada do poller")
                time.sleep(settings.poll_interval)

        threading.Thread(target=loop, name="aih-poller", daemon=True).start()
    uvicorn.run(create_app(runtime.pipeline, settings, poller=poller), host=args.host, port=args.port, log_level=settings.log_level.lower())
    return 0


# ---------------------------------------------------------------------------------------------- entrada

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aih-bitrix", description="Laudos AIH (PDF) -> Contato + card da SPA 'Leads Auxílio Acidente' no Bitrix24")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--env-file", help="arquivo com BITRIX_WEBHOOK_URL e demais variáveis (padrão: ./.env se existir)")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="diagnóstico da instalação (somente leitura): dependências, extrator, configuração e Bitrix")
    doctor.add_argument("--offline", action="store_true", help="só verificações locais; não acessa o Bitrix")
    doctor.add_argument("--json", action="store_true", help="saída em JSON (para o agente ler)")

    hermes = sub.add_parser("hermes-install", help="grava a skill e o script de cron no agente Hermes (caminhos desta instalação)")
    hermes.add_argument("--hermes-home", help="pasta do Hermes (padrão: HERMES_HOME, ou %%LOCALAPPDATA%%\\hermes / ~/.hermes)")
    hermes.add_argument("--app-dir", help="pasta deste app (padrão: a pasta do projeto)")
    hermes.add_argument("--force", action="store_true", help="sobrescreve arquivos já instalados")

    sample = sub.add_parser("sample", help="gera um PDF de AIH 100%% fictício para testar a instalação")
    sample.add_argument("--layout", choices=SAMPLE_LAYOUTS, default="a", help="a = formulário antigo, b = formulário novo")
    sample.add_argument("--out", help=f"arquivo de saída (padrão: {sample_filename('a')})")
    sample.add_argument("--force", action="store_true", help="sobrescreve o arquivo se já existir")

    extract = sub.add_parser("extract", help="lê um PDF localmente (sem Bitrix); por padrão não mostra dados pessoais")
    extract.add_argument("pdf")
    extract.add_argument("--show", action="store_true", help="mostra os valores (contém dado pessoal)")

    discover = sub.add_parser("discover", help="valida os campos no portal e mostra categoria/etapa")
    discover.add_argument("--out", help="salva o mapa de campos em JSON")

    ingest = sub.add_parser("ingest", help="processa PDFs ou pastas")
    ingest.add_argument("paths", nargs="+")
    ingest.add_argument("--cpf", help="CPF do paciente (vale com um único arquivo)")
    ingest.add_argument("--dry-run", action="store_true", help="não grava nada no Bitrix")

    poll = sub.add_parser("poll", help="lê os grupos dos Canais Abertos e processa os PDFs novos")
    poll.add_argument("--once", action="store_true", help="uma rodada e sai")
    poll.add_argument("--dry-run", action="store_true")
    poll.add_argument("--quiet", action="store_true", help="não imprime nada quando não há arquivo novo (para cron)")
    poll.add_argument("--backfill", type=int, default=0, help="na 1ª execução, processa as últimas N mensagens")
    poll.add_argument("--dialog", action="append", help="ID do diálogo (repita); padrão: OL_DIALOG_IDS")
    poll.add_argument("--interval", type=int, help="segundos entre rodadas (padrão: POLL_INTERVAL_SEC)")

    sub.add_parser("selfcheck", help="teste ao vivo com registros sintéticos [TESTE HERMES] (idempotente; não apaga nada)")

    serve = sub.add_parser("serve", help="sobe a API HTTP (upload manual) e, opcionalmente, o poller")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--with-poller", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, client_factory: ClientFactory | None = None, environ: Mapping[str, str] | None = None) -> int:
    _force_utf8_output()  # antes do parse: --help e mensagens de uso também saem em UTF-8
    args = _parser().parse_args(argv)
    if args.command == "extract":
        return _extract(args)
    if args.command == "sample":
        return _sample(args)
    if args.command == "hermes-install":
        return _hermes_install(args)
    if args.command == "doctor":
        return _doctor(args, environ, client_factory)
    try:
        env_file = args.env_file or (".env" if Path(".env").exists() else None)
        settings = load_settings(environ, env_file=env_file)
    except ConfigError as exc:
        print(f"erro de configuração: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(_RedactFilter())
    handlers = {"discover": _discover, "ingest": _ingest, "poll": _poll, "selfcheck": _selfcheck, "serve": _serve}
    try:
        return handlers[args.command](settings, args, client_factory)
    except FieldMapError as exc:
        print(f"campos do Bitrix incompatíveis: {exc}", file=sys.stderr)
        return 3
    except BitrixError as exc:
        print(f"erro do Bitrix: {exc}", file=sys.stderr)
        return 3
