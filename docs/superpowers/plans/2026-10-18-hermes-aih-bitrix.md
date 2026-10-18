# Hermes AIH → Bitrix24 — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execução escolhida:** inline (superpowers:executing-plans) — o usuário pediu autonomia total e a sessão não autorizou subagentes.
> **Sem repositório git:** o "checkpoint" de cada tarefa é a suíte `pytest` verde (não há commits).

**Goal:** Serviço + CLI que lê laudos AIH (PDF), extrai os dados, faz upsert de Contato e card da SPA 1146 no Bitrix24 e audita o resultado, com entrada por upload e por poller de grupo dos Canais Abertos.

**Architecture:** Pipeline puro (PDF → `Laudo` → inferências) separado da camada Bitrix (client REST fino, mapa de campos validado ao vivo, upsert idempotente com "preencher só vazios", verificação por re-leitura). Entradas (API FastAPI, CLI, poller) só chamam `Pipeline.process_pdf`.

**Tech Stack:** Python ≥ 3.10 (dev em 3.14.3), `requests`, `pypdf`, `pdfplumber`, `fastapi`, `uvicorn`, `python-multipart`; testes `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-18-hermes-aih-bitrix-design.md`

## Global Constraints

- Python ≥ 3.10; **sem** sintaxe exclusiva 3.12+; `from __future__ import annotations` em todo módulo.
- Dependências **somente**: `requests`, `pypdf`, `pdfplumber`, `fastapi`, `uvicorn`, `python-multipart` (+ `pytest` em dev). **Sem `httpx`** (testes de API usam `uvicorn` em thread + `requests`).
- Segredos só por ambiente: `BITRIX_WEBHOOK_URL`, `HERMES_API_KEY`, `BITRIX_EVENT_TOKEN`. O token do webhook (`/rest/<id>/<token>/`) é **redigido** em logs, erros e respostas.
- **Nunca** chamar `*.delete`, `event.bind`, `crm.contact.merge`. **Nunca** escrever `COMMENTS` do contato. **Nunca** gravar telefone placeholder.
- PII fora dos logs: CPF mascarado (`***.***.***-09`), sem nome, sem texto clínico.
- Chaves Bitrix exatamente como no spec §2.2/§2.3; `categoryId=101`, etapa `DT1146_101:NEW`, `entityTypeId=1146`, UF "MT" = ID `377` (sempre lido dos metadados, 377 só como fixture).
- CPF no Bitrix: gravar **formatado** `NNN.NNN.NNN-NN`; buscar formatado e depois só dígitos.
- Verificação: título/etapa/responsável do card **não** são auditados; nome/sobrenome/mãe comparam com `fold`.
- Comentários e docs em português; identificadores em inglês.
- Executar testes com: `.venv\Scripts\python.exe -I -m pytest` (a raiz `src` entra por `pythonpath` no `pyproject.toml`).

## Estrutura de arquivos

```
hermes-aih-bitrix/
  pyproject.toml  requirements.txt  requirements-dev.txt  .env.example  .gitignore  README.md
  src/aih_bitrix/
    __init__.py  __main__.py  config.py  cpf.py  phones.py  models.py
    pdf_text.py  parser.py  infer.py  ledger.py  pipeline.py  api.py  cli.py
    bitrix/__init__.py  client.py  fieldmap.py  contacts.py  items.py  verify.py  openlines.py
  tests/
    conftest.py  fakes.py  fixtures/CPF12345678909.pdf  fixtures/CPF12345678909.stream.txt
    test_cpf.py test_phones.py test_pdf_text.py test_parser.py test_infer.py test_client.py
    test_config_fieldmap.py test_contacts.py test_items_verify.py test_pipeline.py test_api.py test_openlines.py test_cli.py
  scripts/  coverage_report.py (cobertura do extrator em PDFs reais, só estatísticas)
  docs/superpowers/{specs,plans}/  docs/HERMES.md
```

---

### Task 0: Ambiente e esqueleto

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.gitignore`, `.env.example`, `src/aih_bitrix/__init__.py`, `tests/conftest.py`
- Copy: `CPF12345678909.pdf` → `tests/fixtures/CPF12345678909.pdf`

**Interfaces:**
- Produces: pacote importável `aih_bitrix` (via `pythonpath=["src"]` e `pip install -e .`); `.venv` pronto.

- [ ] **Step 1: criar venv e instalar (aprovado pelo usuário)**
  `python -m venv .venv` → `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt` → `pip install -e .`
- [ ] **Step 2: smoke test** `.venv\Scripts\python.exe -I -c "import pypdf, pdfplumber, fastapi, uvicorn, requests, aih_bitrix"` → sem erro.
- [ ] **Step 3: capturar o texto do PDF modelo** (pypdf e pdfplumber) em `tests/fixtures/CPF12345678909.stream.txt` (golden do parser) e inspecionar a ordem das linhas.

### Task 1: CPF (`cpf.py`)

**Files:** Create `src/aih_bitrix/cpf.py`; Test `tests/test_cpf.py`

**Interfaces:**
- Produces: `only_digits(s) -> str`; `is_valid_cpf(v) -> bool`; `format_cpf(v) -> str`; `mask_cpf(v) -> str`; `cpf_from_filename(name) -> str | None`; `cpf_from_text(text) -> str | None` (sempre 11 dígitos válidos ou `None`).

- [ ] **Step 1: testes**
```python
from aih_bitrix.cpf import is_valid_cpf, format_cpf, mask_cpf, cpf_from_filename, cpf_from_text

def test_valid_invalid():
    assert is_valid_cpf("12345678909") and is_valid_cpf("123.456.789-09")
    assert not is_valid_cpf("12345678905") and not is_valid_cpf("11111111111") and not is_valid_cpf("123")

def test_format_mask():
    assert format_cpf("12345678909") == "123.456.789-09"
    assert mask_cpf("123.456.789-09") == "***.***.***-09"

def test_from_filename():
    assert cpf_from_filename("CPF12345678909.pdf") == "12345678909"
    assert cpf_from_filename("cpf 123.456.789-09 AIH.pdf") == "12345678909"
    assert cpf_from_filename("MARCOS DE OLIVEIRA, AIH .PDF") is None
    assert cpf_from_filename("CPF12345678905.pdf") is None

def test_from_text():
    assert cpf_from_text("cpf 123.456.789-09") == "12345678909"
    assert cpf_from_text("-------------") is None
```
- [ ] **Step 2: rodar → FAIL** (`ModuleNotFoundError`)
- [ ] **Step 3: implementar** — dígitos verificadores módulo 11 (pesos 10..2 e 11..2; resto<2 ⇒ 0); rejeitar sequências repetidas; regex `(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)` com validação do DV.
- [ ] **Step 4: rodar → PASS**

### Task 2: Telefones (`phones.py`)

**Files:** Create `src/aih_bitrix/phones.py`; Test `tests/test_phones.py`

**Interfaces:**
- Produces: `phone_digits(v) -> str`; `is_placeholder_phone(v) -> bool`; `phone_key(v) -> str` (DDD+8 últimos dígitos, `""` se insuficiente); `same_phone(a, b) -> bool`; `format_phone_br(v) -> str` (`(65) 99123-4567`).

- [ ] **Step 1: testes**
```python
from aih_bitrix.phones import is_placeholder_phone, phone_key, same_phone, format_phone_br

def test_placeholder():
    assert is_placeholder_phone("(65) 999999999") and is_placeholder_phone("(65) 90000-0000")
    assert is_placeholder_phone("(65) 912345678") and is_placeholder_phone("") and is_placeholder_phone(None)
    assert not is_placeholder_phone("(65) 99123-4567")

def test_variants_share_key():
    v = ["(65) 99123-4567", "65991234567", "+55 65 99123-4567", "5565991234567", "556591234567", "6591234567"]
    assert {phone_key(x) for x in v} == {"6591234567"}

def test_same_phone():
    assert same_phone("(65) 99123-4567", "5565991234567")
    assert not same_phone("(65) 99123-4567", "(65) 99123-4568")

def test_format():
    assert format_phone_br("5565991234567") == "(65) 99123-4567"
    assert format_phone_br("6591234567") == "(65) 9123-4567"
```
- [ ] **Step 2: FAIL** · **Step 3: implementar** — remover `55` quando `len>=12`; subscritor = 8 últimos dígitos; placeholder se `<10` dígitos, subscritor de dígito único, ou em `{12345678, 87654321, 01234567}`. · **Step 4: PASS**

### Task 3: Texto do PDF (`pdf_text.py`)

**Files:** Create `src/aih_bitrix/pdf_text.py`; Test `tests/test_pdf_text.py`

**Interfaces:**
- Produces: `PdfTextError`, `InvalidPdfError`, `ScannedPdfError`; `extract_text(data: bytes, *, max_pages: int = 5) -> str` (pypdf primeiro; se vazio/curto tenta pdfplumber; vazio ⇒ `ScannedPdfError`).

- [ ] **Step 1: testes**
```python
import io, pytest
from pathlib import Path
from pypdf import PdfWriter
from aih_bitrix.pdf_text import extract_text, InvalidPdfError, ScannedPdfError
SAMPLE = Path(__file__).parent / "fixtures" / "CPF12345678909.pdf"

def test_sample_has_labels():
    t = extract_text(SAMPLE.read_bytes())
    assert "5-NOME DO PACIENTE" in t and "MARCOS DE OLIVEIRA" in t

def test_blank_pdf_is_scanned():
    w = PdfWriter(); w.add_blank_page(200, 200); b = io.BytesIO(); w.write(b)
    with pytest.raises(ScannedPdfError): extract_text(b.getvalue())

def test_garbage():
    with pytest.raises(InvalidPdfError): extract_text(b"not a pdf")
```
- [ ] **Step 2: FAIL** · **Step 3: implementar** (limitar a `max_pages`; capturar exceções das libs e relançar como `InvalidPdfError`) · **Step 4: PASS**

### Task 4: Parser do laudo (`models.py`, `parser.py`)

**Files:** Create `src/aih_bitrix/models.py`, `src/aih_bitrix/parser.py`; Test `tests/test_parser.py`

**Interfaces:**
- Consumes: `extract_text`.
- Produces: `Laudo(raw: Mapping[int, str], source_name: str = "")` com propriedades `patient_name, first_name, last_name, cns, birth_date, request_date, uf, cep, city, mother_name, phone, cid, ...`; `NotAihError`; `LABELS: dict[int, str]` (1–49); `parse_laudo(text: str, source_name: str = "") -> Laudo`.

- [ ] **Step 1: testes (contrato = PDF modelo)**
```python
from datetime import date
from pathlib import Path
import pytest
from aih_bitrix.parser import parse_laudo, NotAihError
TEXT = (Path(__file__).parent / "fixtures" / "CPF12345678909.stream.txt").read_text(encoding="utf-8")

def test_sample_fields():
    r = parse_laudo(TEXT, "CPF12345678909.pdf").raw
    assert r[1] == "HOSPITAL GERAL DE EXEMPLO" and r[2] == "1234567" and r[3] == "" and r[4] == ""
    assert r[5] == "MARCOS DE OLIVEIRA" and r[6] == "123456" and r[7] == "700000000000012"
    assert r[8] == "10/05/1975" and r[9] == "MASCULINO" and r[10] == "HELENA DA COSTA OLIVEIRA"
    assert r[11] == "(65) 999999999" and r[12] == "RUA DAS PALMEIRAS, 100, CENTRO"
    assert r[13] == "CUIABÁ" and r[14] == "5103403" and r[15] == "MT" and r[16] == "78000000"
    assert r[17].startswith("PACIENTE VEM AO PS RELATANDO QUEDA DE MOTOCICLETA") and "\n" not in r[17]
    assert r[17].endswith("AGUARDANDO REALIZAÇÃO DO EXAME.")
    assert r[18] == "PROPOSTA DE TRATAMENTO CIRURGICO" and r[19] == "EXAME FÍSICO / RX"
    assert r[20] == "Fratura da extremidade proximal da tíbia" and r[21] == "S821" and r[22] == "" and r[23] == ""
    assert r[24] == "TRATAMENTO CIRÚRGICO DE FRATURA DO PLANALTO TIBIAL" and r[25] == "408050551"
    assert (r[26], r[27], r[28], r[29]) == ("CIRÚRGICO", "URGENCIA", "CNS", "700000000000044")
    assert r[30] == "FULANO DE TAL" and r[31] == "14/09/2026" and r[42] == ""

def test_typed_properties():
    l = parse_laudo(TEXT)
    assert l.birth_date == date(1975, 5, 10) and l.request_date == date(2026, 9, 14)
    assert (l.first_name, l.last_name) == ("MARCOS", "DE OLIVEIRA")

def test_section_headers_not_in_values():
    r = parse_laudo(TEXT).raw
    for n in (16, 23, 32, 42):
        assert "IDENTIFICA" not in r[n] and "JUSTIFICATIVA" not in r[n] and "PROCEDIMENTO SOLICITADO" not in r[n]

def test_not_aih():
    with pytest.raises(NotAihError): parse_laudo("Contrato de prestação de serviços ...")

def test_accent_insensitive_label():
    assert parse_laudo(TEXT.replace("ENDEREÇO", "ENDERECO")).raw[12] == "RUA DAS PALMEIRAS, 100, CENTRO"
```
- [ ] **Step 2: FAIL**
- [ ] **Step 3: implementar** — (a) normalizar texto; (b) cópia "dobrada" 1:1 (sem acento, maiúscula) para localizar rótulos com offsets preservados; (c) para n=1..49 localizar `(?<!\d)n\s*-\s*<rótulo com espaços flexíveis>` a partir do cursor; (d) valor = trecho até o próximo rótulo achado; (e) remover linhas que sejam cabeçalhos de seção/título; (f) juntar linhas com espaço; (g) vazios (`____/____/____`) ⇒ `""`; (h) exigir título "AUTORIZAÇÃO DE INTERNAÇÃO HOSPITALAR" e ≥ 8 rótulos, senão `NotAihError`.
- [ ] **Step 4: PASS**
- [ ] **Step 5: cobertura em PDFs reais** (`scripts/coverage_report.py`, só estatísticas) — se algum campo-chave (5, 7, 8, 17, 20, 21, 24, 31) falhar em > 10 % dos PDFs, adicionar estratégia geométrica (palavras do `pdfplumber` por posição) como fallback e repetir.

### Task 5: Inferências (`infer.py`)

**Files:** Create `src/aih_bitrix/infer.py`; Test `tests/test_infer.py`

**Interfaces:**
- Consumes: `Laudo`.
- Produces: `Inference(value: str, confidence: str, note: str)` (em `models.py`); `infer_accident_date`, `infer_limb`, `infer_surgery`, `infer_sick_pay` (cada um `Laudo -> Inference | None`); `infer_all(laudo) -> dict[str, Inference]` com chaves `accident_date, limb, surgery, sick_pay` (apenas as não nulas).

- [ ] **Step 1: testes**
```python
from aih_bitrix.infer import infer_accident_date, infer_limb, infer_surgery, infer_sick_pay
from aih_bitrix.models import Laudo

def L(**kw):
    base = {n: "" for n in range(1, 50)}
    base.update({int(k[1:]): v for k, v in kw.items()})
    return Laudo(raw=base)

def test_accident_relative():
    d = lambda t: infer_accident_date(L(r17=t, r31="14/09/2026"))
    assert d("QUEDA HÁ ALGUMAS HORAS").value == "14/09/2026" and d("QUEDA HÁ ALGUMAS HORAS").confidence == "high"
    assert d("QUEDA ONTEM").value == "15/08/2026" and d("ANTEONTEM").value == "14/08/2026"
    assert d("QUEDA HÁ 3 DIAS").value == "13/08/2026" and d("HÁ 2 SEMANAS").value == "02/08/2026"
    assert d("HÁ 1 MÊS").value == "16/07/2026" and d("EM 10/08/2026 SOFREU QUEDA").value == "10/08/2026"
    assert d("HÁ ALGUNS DIAS").value == "Até 14/09/2026" and d("DOR LOMBAR").value is None if d("DOR LOMBAR") else True

def test_limb_sample():
    l = L(r17="QUEDA DE MOTOCICLETA... DOR INTENSA EM JOELHO ESQUERDO. ... JOELHO ESQ ... PLANALTO TIBIAL ESQUERDO",
          r20="Fratura da extremidade proximal da tíbia", r21="S821",
          r24="TRATAMENTO CIRÚRGICO DE FRATURA DO PLANALTO TIBIAL")
    assert infer_limb(l).value == "Joelho esquerdo / Extremidade proximal da tíbia (Planalto tibial à esquerda)"

def test_limb_other():
    l = L(r17="CAIU DA BICICLETA, DOR EM OMBRO DIREITO", r20="Fratura da clavícula", r21="S420",
          r24="TRATAMENTO CIRÚRGICO DE FRATURA DA CLAVÍCULA")
    assert infer_limb(l).value.startswith("Ombro direito / Clavícula")

def test_surgery():
    assert infer_surgery(L(r18="PROPOSTA DE TRATAMENTO CIRURGICO", r26="CIRÚRGICO")).value == "Proposta cirúrgica"
    assert infer_surgery(L(r17="PÓS-OPERATÓRIO DE OSTEOSSÍNTESE")).value == "Sim"
    assert infer_surgery(L(r17="TRATAMENTO CONSERVADOR COM IMOBILIZAÇÃO")).value == "Não"
    assert infer_surgery(L(r17="DOR")) is None

def test_sick_pay():
    assert infer_sick_pay(L(r42="")) is None
    assert infer_sick_pay(L(r17="PACIENTE EM USO DE AUXÍLIO-DOENÇA")).value == "Sim"
```
- [ ] **Step 2: FAIL** · **Step 3: implementar** (regex PT-BR com acento opcional; subtração de meses sem `dateutil`; lado: `ESQ(UERD[OA])?|MIE|MSE` / `DIR(EIT[OA])?|MID|MSD`; região por dicionário joelho/ombro/tornozelo/quadril/punho/cotovelo/pé/mão/coluna/perna/braço/antebraço/coxa/clavícula/costela/bacia/crânio; "Fratura d[aeo]s? X" ⇒ `X` capitalizado; procedimento: `FRATURA D[AOE]S? (.+)$` ⇒ capitalizado em minúsculas + `à esquerda/à direita`; fallback por capítulo do CID-10 `S42, S52, S62, S72, S82, S92...`) · **Step 4: PASS**

### Task 6: Cliente REST (`bitrix/client.py`)

**Files:** Create `src/aih_bitrix/bitrix/__init__.py`, `src/aih_bitrix/bitrix/client.py`; Test `tests/test_client.py`

**Interfaces:**
- Produces: `BitrixError(code, description)`; `BitrixClient(webhook_url, *, session=None, timeout=60.0, min_interval=0.5, max_retries=3, sleep=time.sleep)` com `host: str`, `call(method, params=None) -> dict`, `result(method, params=None)`, `list_all(method, params, key=None, limit=2000) -> list`, `download(url, max_bytes) -> bytes`; função `redact(text, webhook_url) -> str`.

- [ ] **Step 1: testes** (sessão falsa): (a) `call` envia `POST <base>/<method>.json` com JSON e devolve o dict; (b) resposta `{"error": "...", "error_description": "..."}` ⇒ `BitrixError` **sem token** em `str(exc)`; (c) `QUERY_LIMIT_EXCEEDED` e HTTP 5xx ⇒ retenta com `sleep` injetado e depois levanta; (d) `list_all` segue `next` até acabar/`limit`; (e) `download` rejeita host diferente do portal, rejeita `Content-Length` > `max_bytes`, interrompe stream > `max_bytes`; (f) `redact` troca `/rest/1/<token>/` por `/rest/1/<TOKEN>/`.
- [ ] **Step 2: FAIL** · **Step 3: implementar** (rate limit por `min_interval`; nunca logar URL completa) · **Step 4: PASS**

### Task 7: Configuração e mapa de campos (`config.py`, `bitrix/fieldmap.py`)

**Files:** Create `src/aih_bitrix/config.py`, `src/aih_bitrix/bitrix/fieldmap.py`; Test `tests/test_config_fieldmap.py`; Create `tests/fakes.py` (FakeBitrix — usado nas Tasks 7–10)

**Interfaces:**
- Produces: `Settings` (frozen dataclass: `webhook_url, api_key, event_token, ledger_path, entity_type_id=1146, category_id: int | None, stage_id: str | None, title_template, verify_delay=8.0, max_pdf_mb=15, ol_dialog_ids: tuple[str, ...], poll_interval=30, allow_no_auth=False, log_level="INFO"`); `load_env_file(path) -> dict[str, str]` (`KEY=VALUE` e, se faltar `BITRIX_WEBHOOK_URL`, extrai a primeira URL `https://…/rest/<id>/<token>/` do texto); `load_settings(environ=None, env_file=None) -> Settings`.
- Produces: `FieldMapError`; `FieldMap(entity_type_id, category_id, stage_id, contact: Mapping[str, str], item: Mapping[str, str], state_ids: Mapping[str, str])`; `DEFAULT_CONTACT_KEYS`/`DEFAULT_ITEM_KEYS` (spec §2.2/§2.3: nomes lógicos → chaves); `load_field_map(client, settings) -> FieldMap`.
- Produces: `tests/fakes.py::FakeBitrix(bp=True, robots=True)` com `call(method, params)` para `crm.contact.{fields,list,get,add,update}`, `crm.item.{fields,list,get,add,update}`, `crm.category.list`, `crm.status.list`, `crm.timeline.comment.{add,list}`; `bp=True` aplica `ucwords` em NAME/LAST_NAME/mãe após gravar; `robots=True` reescreve `title` e `stageId` do card novo; multicampo `PHONE` com semântica do Bitrix.

- [ ] **Step 1: testes** — `load_settings` (env, arquivo `KEY=VALUE`, arquivo de texto livre com a URL, ausência ⇒ erro claro sem imprimir segredo); `load_field_map` com metadados fakes: chave ausente ⇒ `FieldMapError` citando a chave; `state_ids["MT"] == "377"`; categoria/etapa padrão resolvidas (`101`, `DT1146_101:NEW`) quando `category_id/stage_id` não configurados; tipo inesperado (ex.: Estado não-enumeration) ⇒ erro.
- [ ] **Step 2: FAIL** · **Step 3: implementar** · **Step 4: PASS**

### Task 8: Contato (`bitrix/contacts.py`)

**Files:** Create `src/aih_bitrix/bitrix/contacts.py`; Test `tests/test_contacts.py`

**Interfaces:**
- Consumes: `BitrixClient`, `FieldMap`, `Laudo`, `cpf`, `phones`.
- Produces: `split_name(full) -> tuple[str, str]`; `build_contact_fields(laudo, cpf, fmap) -> tuple[dict, list[str]]` (campos + avisos); `ContactResult(id, action, filled, skipped, warnings, expected)` com `action ∈ {"created","updated","unchanged","ambiguous","dry_run_create","dry_run_update"}`; `upsert_contact(client, fmap, laudo, cpf, *, dry_run=False) -> ContactResult`.

- [ ] **Step 1: testes (FakeBitrix)**
```python
def test_create_new_contact(fake, fmap, laudo):
    r = upsert_contact(fake, fmap, laudo, "12345678909")
    c = fake.contacts[r.id]
    assert r.action == "created" and c["UF_CRM_1727815619072"] == "377" and c["UF_CRM_1524762221558"] == "123.456.789-09"
    assert c["UF_CRM_1725391645"] == "78000000" and "PHONE" not in c or not c["PHONE"]   # placeholder ignorado

def test_existing_by_cpf_fills_only_empty(fake, fmap, laudo):
    cid = fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", BIRTHDATE="1975-05-10", UF_CRM_1524762221558="123.456.789-09",
                            UF_CRM_1725391645="11111111", PHONE=[{"VALUE": "(65) 98888-7777", "VALUE_TYPE": "MOBILE"}])
    r = upsert_contact(fake, fmap, laudo, "12345678909")
    c = fake.contacts[cid]
    assert r.action == "updated" and r.id == cid
    assert c["UF_CRM_1725391645"] == "11111111"                    # não sobrescreve
    assert c["UF_CRM_1729515037979"].upper() == "HELENA DA COSTA OLIVEIRA" and c["UF_CRM_1727815619072"] == "377"
    assert len(c["PHONE"]) == 1                                    # placeholder não entra

def test_ambiguous(fake, fmap, laudo):
    fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", UF_CRM_1524762221558="123.456.789-09")
    fake.seed_contact(NAME="Marcos", LAST_NAME="Oliveira", UF_CRM_1524762221558="123.456.789-09")
    assert upsert_contact(fake, fmap, laudo, "12345678909").action == "ambiguous"
    assert not fake.writes

def test_dry_run_writes_nothing(fake, fmap, laudo):
    assert upsert_contact(fake, fmap, laudo, "12345678909", dry_run=True).action == "dry_run_create" and not fake.writes

def test_real_phone_added_once(fake, fmap, laudo_real_phone):
    r1 = upsert_contact(fake, fmap, laudo_real_phone, None); r2 = upsert_contact(fake, fmap, laudo_real_phone, None)
    assert r2.action in ("unchanged", "updated") and len(fake.contacts[r1.id]["PHONE"]) == 1
```
- [ ] **Step 2: FAIL** · **Step 3: implementar** (busca CPF formatado→dígitos; sem CPF ⇒ nome+nascimento com `fold`; ≥2 candidatos ⇒ `ambiguous`; update só com chaves vazias; `PHONE` só se `not is_placeholder_phone` e sem `same_phone` entre os existentes; `params={"REGISTER_SONET_EVENT": "N"}`) · **Step 4: PASS**

### Task 9: Card, timeline e verificação (`bitrix/items.py`, `bitrix/verify.py`)

**Files:** Create `src/aih_bitrix/bitrix/items.py`, `src/aih_bitrix/bitrix/verify.py`; Test `tests/test_items_verify.py`

**Interfaces:**
- Consumes: `Laudo`, `Inference`, `FieldMap`, `BitrixClient`.
- Produces: `make_xml_id(laudo, cpf) -> str` (`AIH-{CNS|CPF|sha1[:10]}-{AAAAMMDD}-{CID}`); `build_item_fields(laudo, inferences, fmap) -> dict` (só chaves de campos UF com valor); `build_timeline_comment(laudo, inferences, xml_id, source_name) -> str` (contém `AIH-IMPORT:<xml_id>`); `ItemResult(id, action, xml_id, filled, warnings, expected)` com `action ∈ {"created","updated","unchanged","dry_run_create","dry_run_update"}`; `upsert_item(client, fmap, laudo, inferences, contact_id, cpf, *, title_template, dry_run=False) -> ItemResult`; `FieldCheck(scope, field, ok, expected, got, mode)`; `verify_contact(client, contact_id, expected, fmap) -> list[FieldCheck]`; `verify_item(client, item_id, expected, fmap) -> list[FieldCheck]`.

- [ ] **Step 1: testes** — `make_xml_id` determinístico e igual p/ o PDF modelo (`AIH-700000000000012-20260914-S821`); `build_item_fields` do PDF modelo devolve os 11 campos do spec com os valores do teste ao vivo (incl. `ufCrm53_1775651057904 == "Proposta cirúrgica"`, auxílio ausente); `upsert_item` cria (categoria 101, `opened=="Y"`, `contactId`), 2ª chamada acha por `xmlId` e **não duplica** (`unchanged`), preenche só vazios, comentário de timeline 1× (marcador `AIH-IMPORT:`); `verify_*` PASS mesmo com `ucwords` do BP e título/etapa reescritos pelas "robôs" do Fake; FAIL quando um valor diverge.
- [ ] **Step 2: FAIL** · **Step 3: implementar** (modos `fold|digits|date|enum|exact`; ignora `title, stageId, assignedById`) · **Step 4: PASS**

### Task 10: Ledger e Pipeline (`ledger.py`, `pipeline.py`)

**Files:** Create `src/aih_bitrix/ledger.py`, `src/aih_bitrix/pipeline.py`; Test `tests/test_pipeline.py`

**Interfaces:**
- Produces: `Ledger(path)` com `seen(sha) -> bool`, `record(sha, **info)`, `get_cursor(dialog) -> int`, `set_cursor(dialog, message_id)`; `IngestResult(filename, status, contact, item, checks, warnings, error)` com `status ∈ {"ok","duplicate","needs_review","not_aih","error","dry_run"}`; `Pipeline(client, fmap, ledger, settings, *, sleep=time.sleep)` com `process_pdf(data: bytes, filename: str, *, cpf: str | None = None, source: str = "upload", dry_run: bool = False) -> IngestResult`.

- [ ] **Step 1: testes** — PDF modelo + `FakeBitrix`: `status == "ok"`, contato e card criados, todos os `checks` ok, CPF vindo do nome do arquivo; reenvio (mesmo sha) ⇒ `duplicate` e nenhuma escrita nova; mesmo conteúdo com outro nome ⇒ `duplicate`; `dry_run=True` ⇒ `status == "dry_run"`, nenhuma escrita, ledger intocado; bytes aleatórios ⇒ `status == "error"` (sem vazar conteúdo); PDF de outro documento ⇒ `not_aih`; contato ambíguo ⇒ `needs_review` sem card; falha do Bitrix no meio ⇒ `error` com IDs parciais e ledger **não** marcado (retentável).
- [ ] **Step 2: FAIL** · **Step 3: implementar** (sha256; `settings.verify_delay` via `sleep` injetado) · **Step 4: PASS**

### Task 11: API (`api.py`)

**Files:** Create `src/aih_bitrix/api.py`; Test `tests/test_api.py`

**Interfaces:**
- Consumes: `Pipeline`, `Settings`.
- Produces: `create_app(pipeline, settings) -> FastAPI`; rotas `GET /` (HTML), `GET /health`, `POST /upload` (multipart `files`, `cpf` opcional, `dry_run` opcional; header `X-API-Key`), `POST /bitrix/events` (opcional, `BITRIX_EVENT_TOKEN`).

- [ ] **Step 1: testes** (uvicorn em thread, porta livre, `requests`): `/health` 200; `/upload` sem/errada chave ⇒ 401; com chave e PDF modelo ⇒ 200 + JSON `[{"filename":..., "status":"ok", ...}]` (pipeline com `FakeBitrix`); arquivo não-PDF ⇒ resultado `error` por arquivo (HTTP 200), `files` vazio ⇒ 400; arquivo > `max_pdf_mb` ⇒ 413; `allow_no_auth=True` dispensa a chave; resposta nunca contém o token do webhook nem o CPF completo.
- [ ] **Step 2: FAIL** · **Step 3: implementar** (`hmac.compare_digest`; limite de tamanho por leitura em blocos; HTML do formulário embutido) · **Step 4: PASS**

### Task 12: Poller dos Canais Abertos e CLI (`bitrix/openlines.py`, `cli.py`, `__main__.py`)

**Files:** Create `src/aih_bitrix/bitrix/openlines.py`, `src/aih_bitrix/cli.py`, `src/aih_bitrix/__main__.py`; Test `tests/test_openlines.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `BitrixClient`, `Pipeline`, `Ledger`, `cpf_from_text`.
- Produces: `Attachment(file_id: int, name: str, size: int | None, message_id: int, cpf_hint: str | None)`; `fetch_messages(client, dialog_id, after_id) -> tuple[list[dict], dict[int, dict]]`; `group_batches(messages, files) -> list[list[Attachment]]` (lote termina em separador `^\W{5,}$`, resposta de bot, ou intervalo > 5 min; `cpf_hint` vem de mensagem `cpf …` do mesmo lote); `poll_once(client, pipeline, ledger, dialog_ids, *, dry_run=False, max_pdf_bytes=15_000_000) -> list[IngestResult]`; `cli.main(argv=None) -> int` com subcomandos `extract`, `discover`, `ingest`, `poll`, `serve`, `selfcheck`.

- [ ] **Step 1: testes** — mensagens sintéticas no padrão observado (arquivos → `cpf NNN.NNN.NNN-NN` → `-----` → bot): `group_batches` associa o CPF aos PDFs do lote; `poll_once` com cliente fake (mensagens, `disk.file.get`, download) processa só PDFs que o conteúdo identifica como AIH (JPG e PDF não-AIH ignorados com aviso), usa o CPF do nome do arquivo antes do `cpf_hint`, avança o cursor, não reprocessa na 2ª rodada, ignora `DOWNLOAD_URL` de outro host; CLI: `extract <pdf> --json` imprime campos **mascarados** por padrão (`--show` revela), código de saída ≠ 0 para PDF inválido; `ingest --dry-run` não escreve.
- [ ] **Step 2: FAIL** · **Step 3: implementar** (`im.dialog.messages.get` com `FIRST_ID`; ordenar por `id`; `disk.file.get` → `DOWNLOAD_URL`; tamanho e host validados em `BitrixClient.download`) · **Step 4: PASS**

### Task 13: Validação ao vivo, `selfcheck`, documentação e entrega

**Files:** Modify `src/aih_bitrix/cli.py` (`selfcheck`); Create `scripts/coverage_report.py`, `README.md`, `docs/HERMES.md`, `.env.example`

- [ ] **Step 1: `selfcheck`** — porta do teste ao vivo: escolhe CPF de teste livre, cria/atualiza contato e card `[TESTE HERMES]` (idempotente, sem delete, telefone só depois do card), audita e imprime PASS/FAIL; `exit 1` se algum FAIL.
- [ ] **Step 2: E2E ao vivo** — `selfcheck` idempotente (2ª execução não duplica) + `ingest` do PDF modelo em `--dry-run` contra o contato real correspondente (mostra só "o que mudaria").
- [ ] **Step 3: cobertura em PDFs reais** (em memória, só estatísticas) — registrar % por campo e decisões tomadas.
- [ ] **Step 4: documentação** — `README.md` (instalação, variáveis, comandos, operação, LGPD, limites conhecidos), `docs/HERMES.md` (como o Hermes chama: HTTP e CLI), `.env.example`.
- [ ] **Step 5: verificação final** — `pytest` completo verde; `python -I -m aih_bitrix --help`; revisão do spec §1–§7 contra o código (lista de lacunas); relatório ao usuário com IDs de teste criados e o que ele deve apagar.

---

## Auto-revisão (spec × plano)

- §4.1 → Tasks 3–4 · §4.2 → Task 5 · §4.3 → Task 8 · §4.4 → Task 9 · §4.5 → Task 9 · §4.6 → Task 10/12 · §4.7 → Tasks 11–12 · §5 → Global Constraints + Tasks 6, 11 · §6 → todas + Task 13 · §7 → documentado no README.
- Tipos conferidos entre tarefas: `Laudo`, `Inference`, `FieldMap`, `ContactResult`, `ItemResult`, `FieldCheck`, `IngestResult`, `Attachment` definidos uma vez (Tasks 4, 5, 7–12) e consumidos com os mesmos nomes.
- Lacuna assumida: OCR (fora de escopo, `ScannedPdfError` com mensagem clara).

## Revisões durante a execução (o que mudou em relação ao plano acima)

- **Tarefas 3–4 refeitas** depois de medir PDFs reais do grupo: `pdf_text.read_pages(data) -> list[PageWords]` (palavras
  com geometria, pdfplumber) e `parser.parse_laudo(pages, source_name) -> Laudo` com **chaves canônicas**
  (`Laudo.get("patient_name")`, `Laudo.unmatched`); ver spec §8. Os testes usam o PDF modelo **e** um PDF sintético no layout
  real gerado por `tests/pdfgen.py` + `tests/layouts.py` (coordenadas medidas, valores fictícios, nenhum dado de paciente).
- **Tarefa 11:** `POST /poll` no lugar de `POST /bitrix/events`; testes com `uvicorn` real em thread + `requests`
  (sem `httpx`).
- **Tarefa 12:** poller com lotes fechados, cursor, `BitrixError.retryable`, nomes neutros; CLI com UTF-8 forçado.
- **Tarefa 13:** `selfcheck` virou módulo testado (`selfcheck.py`, 8 testes + 2 de CLI); validação ao vivo concluída.
- **Acréscimos não previstos:** lock no `Pipeline`; `display_name`; `retryable`; `mask_cpfs_in_text`; `complete_cpf`.
- **Resultado:** 207 testes passando; todos escritos antes do código (RED → GREEN), exceto ajustes triviais de formatação.
