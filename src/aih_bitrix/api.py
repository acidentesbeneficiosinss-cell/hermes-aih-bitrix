"""API HTTP do Hermes: upload manual de laudos AIH e gatilho do poller.

Segurança: `POST /upload` e `POST /poll` exigem `X-API-Key` (falha FECHADA se a chave não estiver
configurada); a documentação interativa fica desligada; nenhuma resposta traz dado pessoal.
"""
from __future__ import annotations

import hmac
import threading
from collections import Counter
from typing import Callable

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from . import __version__
from .config import Settings
from .cpf import mask_cpfs_in_text
from .pipeline import IngestResult, Pipeline

Poller = Callable[[bool], list[IngestResult]]

_PAGE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes - Laudos AIH para o Bitrix24</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#222}
 label{display:block;margin:.9rem 0 .2rem;font-weight:600}
 input[type=text],input[type=password]{width:100%;padding:.5rem;box-sizing:border-box}
 button{margin-top:1rem;padding:.6rem 1.3rem}
 table{border-collapse:collapse;width:100%;margin-top:1rem}
 th,td{border-bottom:1px solid #ddd;padding:.4rem;text-align:left;font-size:.9rem;vertical-align:top}
 .ok{color:#0a7a2f}.warn{color:#b26a00}.err{color:#b00020}
</style>
</head>
<body>
<h1>Laudos AIH para o Bitrix24</h1>
<p>Envie um ou mais PDFs de <em>Laudo para Solicitação de Autorização de Internação Hospitalar</em> (AIH - SUS).
O CPF pode vir no nome do arquivo (ex.: <code>CPF12345678909.pdf</code>).</p>
<form id="f">
 <label for="key">Chave da API</label>
 <input id="key" type="password" autocomplete="off" required>
 <label for="files">PDFs</label>
 <input id="files" type="file" accept="application/pdf,.pdf" multiple required>
 <label for="cpf">CPF (opcional; só vale com um arquivo)</label>
 <input id="cpf" type="text" inputmode="numeric" autocomplete="off">
 <label><input id="dry" type="checkbox"> Simular (dry-run): não grava nada no Bitrix</label>
 <button type="submit">Enviar</button>
</form>
<div id="out"></div>
<script>
const out = document.getElementById('out');
function cell(tr, text, cls) { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); }
document.getElementById('f').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  out.textContent = 'Processando... (cada arquivo leva alguns segundos)';
  const fd = new FormData();
  for (const file of document.getElementById('files').files) fd.append('files', file);
  const cpf = document.getElementById('cpf').value.trim();
  if (cpf) fd.append('cpf', cpf);
  if (document.getElementById('dry').checked) fd.append('dry_run', 'true');
  try {
    const r = await fetch('/upload', {method: 'POST', headers: {'X-API-Key': document.getElementById('key').value}, body: fd});
    const body = await r.json();
    if (!r.ok) { out.textContent = 'Erro ' + r.status + ': ' + (body.detail || ''); return; }
    const table = document.createElement('table');
    const head = table.insertRow();
    for (const h of ['Arquivo', 'Situação', 'Contato', 'Card', 'Verificação', 'Avisos']) { const th = document.createElement('th'); th.textContent = h; head.appendChild(th); }
    for (const res of body.results) {
      const tr = table.insertRow();
      const cls = res.status === 'ok' ? 'ok' : (res.status === 'error' ? 'err' : 'warn');
      cell(tr, res.filename); cell(tr, res.status, cls);
      cell(tr, res.contact ? res.contact.action + (res.contact.id ? ' #' + res.contact.id : '') : '-');
      cell(tr, res.item ? res.item.action + (res.item.id ? ' #' + res.item.id : '') : '-');
      const bad = res.checks.filter(c => !c.ok).length;
      cell(tr, res.checks.length ? (res.checks.length - bad) + ' ok / ' + bad + ' divergente(s)' : '-');
      cell(tr, [res.error].concat(res.warnings).filter(Boolean).join(' | '));
    }
    out.textContent = ''; out.appendChild(table);
  } catch (e) { out.textContent = 'Falha de rede: ' + e.message; }
});
</script>
</body>
</html>
"""


def create_app(pipeline: Pipeline, settings: Settings, poller: Poller | None = None) -> FastAPI:
    app = FastAPI(title="Hermes AIH - Bitrix24", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    poll_lock = threading.Lock()  # o Pipeline já serializa o processamento; aqui só evita duas rodadas de poll sobrepostas

    def authorize(key: str | None) -> None:
        if settings.allow_no_auth:
            return
        if not settings.api_key:
            raise HTTPException(503, "API não configurada: defina HERMES_API_KEY (ou ALLOW_NO_AUTH=true só para uso local)")
        if not key or not hmac.compare_digest(key.encode(), settings.api_key.encode()):
            raise HTTPException(401, "chave de API ausente ou inválida")

    def process_one(upload: UploadFile, cpf: str | None, dry_run: bool) -> IngestResult:
        name = upload.filename or "arquivo.pdf"
        shown = mask_cpfs_in_text(name)
        data = upload.file.read(settings.max_pdf_bytes + 1)
        if len(data) > settings.max_pdf_bytes:
            return IngestResult(filename=shown, status="error", error=f"arquivo maior que o limite de {settings.max_pdf_mb} MB")
        if not data.startswith(b"%PDF-"):
            return IngestResult(filename=shown, status="error", error="o arquivo não é um PDF")
        return pipeline.process_pdf(data, name, cpf=cpf, source="upload", dry_run=dry_run)

    def envelope(results: list[IngestResult]) -> dict:
        return {"results": [r.to_public() for r in results], "summary": dict(Counter(r.status for r in results))}

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_PAGE, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.post("/upload")
    def upload(
        files: list[UploadFile] = File(...),
        cpf: str | None = Form(None),
        dry_run: bool = Form(False),
        x_api_key: str | None = Header(None),
    ) -> dict:
        authorize(x_api_key)
        if cpf and len(files) != 1:
            raise HTTPException(400, "o campo cpf só vale quando se envia um único arquivo")
        return envelope([process_one(f, cpf or None, dry_run) for f in files])

    @app.post("/poll")
    def poll(dry_run: bool = Form(False), x_api_key: str | None = Header(None)) -> dict:
        authorize(x_api_key)
        if poller is None:
            raise HTTPException(501, "poller não configurado (defina OL_DIALOG_IDS)")
        with poll_lock:
            return envelope(poller(dry_run))

    return app
