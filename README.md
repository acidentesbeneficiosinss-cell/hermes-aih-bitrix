# Hermes — Laudos AIH (SUS) → Bitrix24

Lê PDFs de **"Laudo para Solicitação de Autorização de Internação Hospitalar (AIH)"**, extrai os dados e, no
Bitrix24 (`brandaocorrea.bitrix24.com.br`):

1. cria ou **completa** o **Contato** do paciente (achado por CPF; senão por nome + nascimento);
2. cria (ou completa) o **card** da SPA **"Leads Auxílio Acidente"** (`entityTypeId 1146`) ligado a esse contato;
3. **audita** o que gravou, relendo o registro e comparando campo a campo.

Entradas: **upload manual** (CLI e HTTP) e **grupo de WhatsApp dos Canais Abertos** (poller, mesma captura de anexos
do grupo "Docs protocolo"). Python ≥ 3.10 (suíte completa validada em instalação limpa no Python 3.14 e no 3.11, Windows).

```
PDF ─► texto + geometria ─► parser (rótulo→valor) ─► inferências ─► contato ─► card ─► re-leitura (PASS/FAIL)
 upload / CLI / poller ──────────────────────────────► Pipeline ─────────────► ledger (SQLite, idempotência)
```

## Instalação

```bash
git clone https://github.com/acidentesbeneficiosinss-cell/hermes-aih-bitrix.git   # repositório privado
cd hermes-aih-bitrix
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt        # Windows  (Linux/macOS: .venv/bin/python)
copy .env.example .env                                              # preencha só a linha BITRIX_WEBHOOK_URL
.venv\Scripts\python.exe run.py doctor                              # confere tudo (somente leitura)
```

`run.py` roda direto da pasta, **sem instalar o pacote** (`python -m aih_bitrix` equivale quando o pacote está no
`PYTHONPATH` ou instalado com `pip install .`). `requirements.lock.txt` traz as versões exatas com que os testes passaram
(Python 3.14 no Windows); para instalar em outro ambiente use `requirements.txt`.

**Instalar com o agente Hermes:** o prompt completo está em [`docs/PROMPT_HERMES.md`](docs/PROMPT_HERMES.md) e a integração
(skill + cron sem agente) em [`docs/HERMES.md`](docs/HERMES.md).

## Configuração (variáveis de ambiente ou `.env`)

| Variável | Padrão | Para quê |
|---|---|---|
| `BITRIX_WEBHOOK_URL` | — (**obrigatória**) | webhook de **entrada** `https://<portal>/rest/<id>/<token>/` (**segredo**) |
| `HERMES_API_KEY` | — | chave do header `X-API-Key` da API (sem ela `/upload` e `/poll` respondem **503**) |
| `ALLOW_NO_AUTH` | `false` | só para uso local: dispensa a chave |
| `BITRIX_SPA_ENTITY_TYPE_ID` | `1146` | SPA "Leads Auxílio Acidente" |
| `BITRIX_SPA_CATEGORY_ID` / `BITRIX_SPA_STAGE_ID` | descobertos | padrão: categoria **101**, etapa `DT1146_101:NEW` |
| `CARD_TITLE_TEMPLATE` | `{nome} ➡ AIH {cid} ➡ {data_solicitacao}` | título inicial (a casa o reescreve, ver abaixo) |
| `LEDGER_PATH` | `data/ledger.sqlite3` | idempotência por hash do arquivo + cursor do poller |
| `OL_DIALOG_IDS` | — | diálogos a monitorar, ex.: `chat360293` ("Docs protocolo") |
| `POLL_INTERVAL_SEC` | `30` | intervalo do poller |
| `MAX_PDF_MB` | `15` | tamanho máximo de PDF |
| `VERIFY_DELAY_SEC` | `8` | espera antes de reler (o BP do portal é assíncrono) |
| `LOG_LEVEL` | `INFO` | |

A URL do webhook também é aceita num arquivo de texto livre (`--env-file bitrix.env.txt`): o app extrai a URL.
**Recomendação:** crie um webhook dedicado só com os escopos `crm`, `im`, `imopenlines`, `disk` e revogue o de
permissão total.

## Uso

```bash
python run.py doctor                           # diagnóstico SOMENTE LEITURA (--offline sem Bitrix; --json para máquina)
python run.py sample                           # gera um PDF de AIH 100% fictício (--layout a|b) para testar a instalação
python run.py discover                         # valida chaves/tipos de campo e mostra categoria/etapa
python run.py extract laudo.pdf                # lê o PDF localmente; não imprime dados pessoais (--show revela)
python run.py ingest --dry-run laudo.pdf       # mostra o que faria, sem gravar
python run.py ingest CPF12345678909.pdf        # processa (CPF vem do nome do arquivo ou de --cpf)
python run.py poll --once --dry-run --backfill 100 --dialog chat360293   # lê o grupo sem gravar
python run.py poll --init-only                 # marca o ponto de partida do grupo; não processa nada, nunca move cursor existente
python run.py poll --once --quiet              # uma rodada; imprime só se houver novidade (cron)
python run.py poll                             # laço contínuo (OL_DIALOG_IDS)
python run.py serve --with-poller              # API HTTP (+ poller em segundo plano)
python run.py hermes-install                   # grava a skill e o script de cron no agente Hermes
python run.py selfcheck                        # teste ao vivo: GRAVA 1 contato + 1 card sintéticos [TESTE HERMES] (idempotente)
```

`sample` gera arquivos `AMOSTRA-FICTICIA-*.pdf` com um paciente inventado: use só com `extract` ou `ingest --dry-run` (o
`ingest` sem `--dry-run` recusa esses arquivos).

Códigos de saída: `0` ok · `1` há pendência (needs_review / erro / não-AIH) · `2` uso, arquivo ou configuração ·
`3` Bitrix inacessível ou campos incompatíveis. Situações por arquivo: `ok`, `duplicate`, `dry_run`,
`needs_review` (ambíguo ou auditoria divergente: **nada é sobrescrito**), `not_aih`, `error`.

### API HTTP (`serve`)

`GET /` (formulário de upload) · `GET /health` · `POST /upload` (multipart `files`, `cpf` opcional só com 1 arquivo,
`dry_run`) · `POST /poll`. As duas últimas exigem `X-API-Key`. A resposta traz **só** status, IDs do Bitrix e nomes de
campo — nenhum dado pessoal.

```bash
curl -H "X-API-Key: $HERMES_API_KEY" -F "files=@CPF12345678909.pdf" -F "dry_run=true" http://127.0.0.1:8080/upload
```

Sirva atrás de um proxy com TLS; por padrão o servidor escuta só em `127.0.0.1`.

### Poller dos Canais Abertos

Webhook **não** registra eventos (`event.*` só com app OAuth), então a captura é por **polling**:
`im.dialog.messages.get` (paginado por `FIRST_ID`) → `params.FILE_ID` → `disk.file.get` → `DOWNLOAD_URL` (só do host do
portal, sem seguir redirecionamentos, limite de tamanho). Regras:

* **1ª execução** só marca o ponto de partida (não importa o histórico); `--backfill N` processa as últimas N mensagens.
  `poll --init-only` faz só isso, em qualquer momento: cria o cursor que falta e **nunca** move um existente nem processa nada.
* O padrão do grupo é *arquivos → mensagem `cpf NNN.NNN.NNN-NN` → separador `-----` → resposta do bot*; só **lotes
  fechados** (separador, resposta de bot, intervalo > 5 min ou chat quieto por 60 s) são processados, assim o CPF que chega
  depois dos arquivos não se perde. O CPF do **nome do arquivo** vale mais que o da mensagem.
* Erro **transitório** (rede, 5xx, limite de taxa) mantém o cursor e tenta de novo; erro **permanente** (anexo grande demais,
  host estranho, validação do Bitrix) é reportado mas **não trava** a fila.
* Imagens (JPG/PNG) e PDFs escaneados são ignorados (sem OCR). Os resultados do poller usam nomes neutros (`anexo chat…#id`):
  no grupo real os arquivos se chamam "NOME DO PACIENTE, AIH .PDF".

## Mapeamento (validado ao vivo)

**Contato** (`crm.contact.*`): nome → `NAME` (1º token) / `LAST_NAME` (resto) · nascimento → `BIRTHDATE` ·
mãe → `UF_CRM_1729515037979` · município → `UF_CRM_1753903279519` · UF → `UF_CRM_1727815619072` (**lista**: `MT`=377) ·
CEP → `UF_CRM_1725391645` · CPF → `UF_CRM_1524762221558` (gravado `NNN.NNN.NNN-NN`) · telefone → `PHONE` (WORK; **placeholder
como `(65) 999999999` nunca é gravado**).

**Card** (`crm.item.*`, **`categoryId` 101**, etapa `DT1146_101:NEW`, `xmlId` = chave de idempotência):
CNS `ufCrm53_1792335030647` · sinais e sintomas `…335467` · condições da internação `…334983515` · provas diagnósticas
`…335000277` · diagnóstico inicial `…334925699` · CID-10 `…334945166` · procedimento `…335069220` · quando ocorreu o acidente
`ufCrm53_1775651041283` · houve cirurgia `…651057904` · membro fraturado `…651072848` · recebeu auxílio-doença `…651085742`.
Os 4 últimos são **texto** no portal. O restante do laudo (hospital, CNES, prontuário, endereço, clínica, caráter,
solicitante, causas externas…) vai num **comentário na linha do tempo** do card, junto com a lista "Campos inferidos
(conferir)".

**Os campos casam pelo TEXTO do rótulo, não pelo número**: o formulário novo (52 campos) numera diferente do antigo
(49), e o "Cnes" do executante chega impresso com o mesmo número do solicitante.

### Inferências (sempre com confiança e a regra aplicada; sem evidência o campo fica vazio)

data do acidente (relato "há algumas horas/ontem/há N dias…" a partir da data da solicitação) · membro (região + lado +
diagnóstico + procedimento + capítulo do CID-10) · cirurgia (`Proposta cirúrgica` / `Sim` / `Não`) · auxílio-doença
(só com menção explícita; vínculo previdenciário não conta).

## Comportamentos do portal que o app respeita

* O BP "Automações que rodam na Criação e Atualização" do contato aplica **Title Case** (`MARCOS`→`Marcos`) e deriva
  "Cidade - Texto" da lista de cidades do estado → a auditoria compara sem diferenciar caixa e espera `VERIFY_DELAY_SEC`.
* Todo card novo tem **título, etapa e responsável reescritos** pela casa (`NOME ➡ TELEFONE ➡ ID`, rodízio entre duas atendentes):
  não são auditados.
* O portal expande cada telefone em variantes (com/sem 55, com/sem 9º dígito): a equivalência é por **DDD + 8 dígitos**.
* O filtro de CPF só acha o valor **formatado**; o app busca formatado e depois só dígitos.
* **A lixeira da SPA está desligada**: apagar é definitivo. O app **nunca** chama `*.delete`.
* `COMMENTS` do contato é mutilado pelo BP (remove `[...]`, escapa `>`): o app não o usa.

## Privacidade e segurança (dado de saúde, LGPD)

Segredos só por ambiente; o token do webhook é **redigido** em logs, erros e respostas; CPF e dados clínicos **nunca** vão para
log nem para a resposta da API; nomes de arquivo aparecem com o CPF mascarado; o ledger guarda só hash, status e IDs; downloads
só do host do portal; API fecha por padrão sem chave. A base legal e a retenção do tratamento são do controlador (escritório).

## Limites conhecidos

* **Sem OCR**: PDF escaneado e imagem são ignorados (`not_aih`/ignorados), não falham.
* O parser foi calibrado no PDF modelo e em **3 laudos reais** (2 páginas, rótulos em Title Case, valores acima dos rótulos no
  stream). Um layout novo de hospital pode trazer rótulo com outra redação: `extract` lista os **rótulos não reconhecidos**
  (`rotulos_nao_reconhecidos`, só texto de rótulo) e a timeline do card os repete.
* O novo grupo precisa ter o ID informado em `OL_DIALOG_IDS` (o ID do diálogo é `chat<N>`; ex.: "Docs protocolo" = `chat360293`).
* `Dockerfile` incluído mas **não testado** neste ambiente.

## Registros de teste criados no portal em 2026-10-18 (apague à mão, se quiser; o app não apaga)

contato **#71886** + card **#2876** (teste manual com os dados do PDF modelo) · contato **#71892** + card **#2882**
(`selfcheck`). O card #2876 consumiu uma vez do rodízio de etapas.

## Testes

```bash
.venv\Scripts\python.exe -m pytest        # 270 testes; usam um FakeBitrix fiel ao que foi medido no portal (sem rede)
```

Os PDFs de teste são **gerados em memória** (`aih_bitrix.synthetic`) com pacientes inventados: o repositório não contém
nenhum PDF nem dado de paciente. O teste `tests/test_repo_hygiene.py` varre tudo o que seria publicado e **falha** se achar
PDF, CPF válido fora dos exemplos conhecidos, número com cara de CNS ou token de webhook. A CI
(`.github/workflows/tests.yml`) roda a suíte a cada push em Windows e Linux, Python 3.10 a 3.14 (os resultados ficam na aba
Actions do repositório).

Estrutura: `src/aih_bitrix/` (`parser`, `infer`, `pipeline`, `api`, `cli`, `doctor`, `selfcheck`, `synthetic`, `hermes_install`,
`bitrix/{client,fieldmap,contacts,items,verify,openlines}`), `tests/`, `docs/` (integração com o Hermes em `docs/HERMES.md`,
prompt de instalação em `docs/PROMPT_HERMES.md`, desenho aprovado e plano em `docs/superpowers/`).
