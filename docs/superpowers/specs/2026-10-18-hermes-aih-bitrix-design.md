# Hermes AIH → Bitrix24 — Design (aprovado em 2026-10-18)

Portal: `https://brandaocorrea.bitrix24.com.br` · SPA "Leads Auxílio Acidente" (`entityTypeId 1146`, type id 53)

## 1. Objetivo

Receber PDFs de **"Laudo para Solicitação de Autorização de Internação Hospitalar (AIH – SUS)"**, extrair os
dados e, no Bitrix24:

1. criar ou atualizar o **Contato** do paciente;
2. criar (ou atualizar) um **card** da SPA 1146 vinculado a esse contato;
3. **auditar** o que foi gravado (re-leitura) e reportar PASS/FAIL por campo.

Entradas: (a) upload manual (HTTP/CLI); (b) monitoramento de um grupo de WhatsApp conectado aos Canais Abertos
(mesma captura de anexos do grupo "Docs protocolo"); (c) endpoint de push opcional.

## 2. Fatos validados ao vivo no portal (2026-10-18)

### 2.1 Acesso
- Webhook de entrada do usuário 1 (admin) com escopos completos. **`event.get`/`event.bind` e `imconnector.*` são
  negados para webhook** (só app OAuth) ⇒ a captura do grupo é por **polling**.
- O MCP oficial do Bitrix24 (`mcp.bitrix24.com`) **não** expõe contatos, itens de SPA, `*.fields` nem Canais Abertos.

### 2.2 Contato (`crm.contact.*`)
| Laudo | Chave Bitrix | Tipo | Observação |
|---|---|---|---|
| 5 nome | `NAME` / `LAST_NAME` | string | 1º token / resto. BP #425 aplica `ucwords` (Title Case) |
| 10 mãe | `UF_CRM_1729515037979` | string | normalizada pelo BP #425 |
| 8 nascimento | `BIRTHDATE` | date | enviar `YYYY-MM-DD` |
| 11 telefone | `PHONE` | multicampo `[{VALUE, VALUE_TYPE:"WORK"}]` | o portal expande em variantes (com/sem 55, com/sem 9º dígito) |
| 13 município | `UF_CRM_1753903279519` | string ("Cidade - Texto -") | um BP o sobrescreve se a lista de cidades do estado (MT: `UF_CRM_1772030615`) estiver preenchida |
| 15 UF | `UF_CRM_1727815619072` | **enumeration** | `MT`=377 … (lido dos metadados) |
| 16 CEP | `UF_CRM_1725391645` | string | dígitos |
| CPF (arquivo) | `UF_CRM_1524762221558` | string | gravado como `NNN.NNN.NNN-NN`; filtro só acha o valor formatado |

`NAME`, `LAST_NAME`, `SECOND_NAME` aparecem como `isRequired` nos metadados. `COMMENTS` é mutilado pelo BP
(remove `[...]`, escapa `>`) ⇒ **o app não escreve `COMMENTS`**.

### 2.3 SPA 1146 (`crm.item.*`)
`categoryId` = **101** ("Pipeline padrão", único); etapa inicial `DT1146_101:NEW` ("Iniciar"). **Não é `0`.**
Lixeira da SPA desativada (`isRecyclebinEnabled=N`): **apagar é definitivo — o app nunca apaga.**

| Laudo | Chave | Tipo |
|---|---|---|
| 7 CNS | `ufCrm53_1792335030647` | string |
| 17 sinais e sintomas | `ufCrm53_1792335467` | string (existe um `rich_text` duplicado `ufCrm53_1792334965106`, não usado) |
| 18 condições | `ufCrm53_1792334983515` | string |
| 19 provas diagnósticas | `ufCrm53_1792335000277` | string |
| 20 diagnóstico inicial | `ufCrm53_1792334925699` | string |
| 21 CID 10 | `ufCrm53_1792334945166` | string |
| 24 procedimento | `ufCrm53_1792335069220` | string |
| quando ocorreu o acidente | `ufCrm53_1775651041283` | **string** (não data/lista) |
| houve cirurgia | `ufCrm53_1775651057904` | **string** |
| membro fraturado | `ufCrm53_1775651072848` | **string** |
| recebeu auxílio doença | `ufCrm53_1775651085742` | **string** |

Também usados: `contactId`, `categoryId`, `stageId`, `opened="Y"`, `title`, `xmlId` (**chave de idempotência**,
gravável e filtrável — validado). Existem campos em *lista* paralelos (acidente/cirurgia/membro/auxílio) que o
mapeamento aprovado **não** usa; o app não os toca.

### 2.4 Automações da casa (efeitos observados)
- **BP #425 do contato** (roda na criação e em toda atualização, assíncrono, sem mensagens): `ucwords` em nome,
  sobrenome, mãe etc.; deriva "Cidade - Texto" da lista de cidades do estado. ⇒ o verificador compara com `fold`
  (sem caixa/acentos) e espera alguns segundos antes de reler.
- **Card novo** é reescrito por automação: o **título** vira `NOME ➡ TELEFONE ➡ ID`, a **etapa** é movida por rodízio
  (`UC_SL43B7` / `UC_2WIOW1`) e o **responsável** é atribuído. ⇒ título/etapa/responsável **não**
  entram na auditoria.
- Cards recentes tinham SMS e e-mail de saída automáticos. Num card sem telefone/e-mail no contato **nenhuma
  atividade foi gerada**. ⇒ o app **nunca grava telefone placeholder** (ex.: `(65) 999999999`).
- Telefones: equivalência por **DDD + últimos 8 dígitos**, senão o app reinsere variantes.

### 2.5 Canais Abertos — "Docs protocolo"
- Chat `chat360293` ("Docs protocolo - 99679-4931"): Canal Aberto, conector `24zap`, linha **31**, grupo WhatsApp
  `120363412591276808-group`, lead 135051. O bot **"Triagem de Documentos Bot" (id 151851)** participa e responde
  `✅ Recebido — NOME · N arq. …`.
- Padrão de mensagens por cliente: arquivos (PDF/JPG) → mensagem `cpf NNN.NNN.NNN-NN` → separador `-----` → resposta do bot.
- Captura validada (HTTP 200, `%PDF`, tamanho idêntico): `im.dialog.messages.get` → `params.FILE_ID` / array `files`
  → `disk.file.get {id}` → `DOWNLOAD_URL` (contém o token do webhook ⇒ tratar como segredo).

## 3. Arquitetura

```
PDF bytes ──► pdf_text ──► parser ──► Laudo ──► infer ──► Inferences
                                         │
upload / poller / push ──► Pipeline ─────┤
                                         ▼
                          contacts.upsert ─► items.upsert ─► verify (re-leitura) ─► IngestResult
                                         ▲                         │
                          fieldmap (chaves validadas)        ledger (SQLite, sha256)
```

Pacote `src/aih_bitrix/`: `config`, `cpf`, `phones`, `models`, `pdf_text`, `parser`, `infer`, `ledger`,
`pipeline`, `api`, `cli`, e `bitrix/{client,fieldmap,contacts,items,verify,openlines}`.
Python ≥ 3.10; serviço FastAPI + CLI (`python -m aih_bitrix ...`).

## 4. Regras

### 4.1 Extração
`pdf_text` usa **pypdf** (ordem do stream: rótulo→valor) e **pdfplumber** como alternativa; PDF sem camada de texto
⇒ `ScannedPdfError` (OCR fora de escopo). `parser` localiza os rótulos numerados `N-ROTULO` (1–49) em ordem, usa o
texto entre rótulos como valor, remove cabeçalhos de seção, junta linhas quebradas e normaliza vazios. Não é AIH ⇒
`NotAihError`.

### 4.2 Inferências (cada uma devolve valor + confiança + nota; baixa confiança vai para a revisão na timeline)
- **Data do acidente**: data absoluta no relato ≤ data da solicitação; relativas (`há N horas|algumas horas|hoje|ontem|
  anteontem|há N dias/semanas/meses`) a partir do campo 31; vago (`há alguns dias`) ⇒ `Até DD/MM/AAAA`; sem prazo ⇒ vazio.
  Formato `DD/MM/AAAA`.
- **Membro fraturado**: região/lado do relato (17) + diagnóstico (20) + procedimento (24) + capítulo do CID-10, no formato
  `Joelho esquerdo / Extremidade proximal da tíbia (Planalto tibial à esquerda)`.
- **Houve cirurgia**: `Proposta cirúrgica` (proposta/indicação/clínica cirúrgica), `Sim` (já operado), `Não`
  (tratamento conservador explícito), vazio se desconhecido.
- **Recebeu auxílio doença**: só com menção explícita (`auxílio-doença`, `INSS` + benefício); campo 42 vazio ⇒ vazio.

### 4.3 Contato (upsert)
Resolução: (1) CPF (nome do arquivo → mensagem `cpf …` → parâmetro) buscando o valor **formatado** e depois só
dígitos; (2) nome+nascimento; (3) criar. ≥2 candidatos ⇒ `needs_review` (nada é gravado). Contato existente ⇒
**preenche só campos vazios** (nunca sobrescreve nome nem valor já preenchido). Telefone: ignora placeholder
(subscritor com dígito único repetido / sequencial) e equivalentes (DDD+8). UF → ID da lista via metadados.

### 4.4 Card (upsert)
`xmlId = AIH-{CNS|CPF|hash}-{AAAAMMDD}-{CID}`. Existe ⇒ preenche só campos vazios; senão cria em `categoryId` 101 /
etapa inicial, `opened="Y"`, `contactId`. Título por template configurável (a casa pode reescrevê-lo). Comentário
de timeline com os demais dados do laudo + inferências a conferir (uma vez por `xmlId`).

### 4.5 Verificação
Após gravar, aguarda `VERIFY_DELAY_SEC` (8 s) e relê (`crm.contact.get`, `crm.item.get`). Modos: `fold` (nome,
sobrenome, mãe), `digits` (CEP, CPF), `date`, `enum`, `exact` (demais). Ignora título/etapa/responsável. Qualquer FAIL ⇒
`status="needs_review"` e saída ≠ 0 no CLI.

### 4.6 Idempotência
Ledger SQLite por `sha256` do arquivo (+ `last_message_id` por diálogo no poller). Reenvio ⇒ `duplicate` sem escrita.

### 4.7 Entradas
- **Upload**: `GET /` (formulário) e `POST /upload` (multipart, vários PDFs, `cpf` opcional, `dry_run`). Exige `X-API-Key`.
- **Poller** (`poll`/`poll --once`): `im.dialog.messages.get` por `OL_DIALOG_IDS` com `FIRST_ID`; agrupa por lote
  (separador, mensagem `cpf`, resposta de bot, intervalo > 5 min); baixa só PDF ≤ `MAX_PDF_MB` do **host do portal**;
  só processa o que o conteúdo identifica como AIH.
- **Push** (`POST /bitrix/events`): opcional; exige app/bot OAuth e `BITRIX_EVENT_TOKEN`.

## 5. Segurança e privacidade (LGPD — dado de saúde)
Segredos só por ambiente (`BITRIX_WEBHOOK_URL`, `HERMES_API_KEY`); token **redigido** em logs/erros; CPF/nome/dados
clínicos **nunca** em log; sem DELETE; sem `event.bind`; download restrito ao host do portal; limite de tamanho;
modo `dry_run`; recomenda-se webhook dedicado com escopos `crm, im, imopenlines, disk` (revogar o de permissão total).

## 6. Testes
Unitários (cpf, phones, parser com o PDF modelo, infer, fieldmap, contacts/items/verify contra um `FakeBitrix` que
reproduz o `ucwords` do BP), API (`TestClient`), poller com mensagens sintéticas; E2E ao vivo isolado (`selfcheck`,
registros `[TESTE HERMES]`, idempotente, sem delete); dry-run no contato real e cobertura do extrator em PDFs reais
do grupo (em memória, só estatísticas).

## 7. Fora de escopo / decisões abertas
OCR (PDF escaneado/JPG); responder no grupo do WhatsApp; registrar eventos/apps no Bitrix; preencher os campos em lista
paralelos; ID/nome do **novo grupo** (parametrizado; teste com `chat360293`); template de título (default
`{nome} ➡ AIH {cid} ➡ {data_solicitacao}`).

## 8. Revisões feitas na execução (2026-10-18) — este documento prevalece sobre o texto acima onde divergir

1. **Extração (§4.1) foi redesenhada ao testar em laudos reais do grupo.** O PDF modelo (1 página, rótulos `N-ROTULO`
   em CAIXA ALTA, stream rótulo→valor) **não** representa os AIH reais: estes têm 2 páginas idênticas, rótulos em Title
   Case com hífen espaçado (`5 - Nome Paciente`), **valores antes dos rótulos no stream** e a numeração do formulário novo
   (52 campos: Raça/Cor, Nome Responsável…), além de rótulos truncados/variáveis (`Data de`, `Data Nascimento`). Por isso o
   parser **casa o rótulo pelo TEXTO** (regex sem acento/caixa) e **atribui o valor pela GEOMETRIA** (pdfplumber: faixas de
   coluna pelos rótulos vizinhos na mesma altura, dono = rótulo mais próximo acima). O `Laudo` usa **chaves canônicas**
   (`patient_name`, `symptoms`…), nunca o número do campo. Rótulos não reconhecidos são devolvidos em `Laudo.unmatched`
   (só texto de rótulo). pypdf deixou de ser usado.
2. **§4.7 — sem endpoint de eventos.** Webhook não registra eventos; no lugar de `POST /bitrix/events` há `POST /poll`
   (gatilho sob demanda de uma rodada do poller). `BITRIX_EVENT_TOKEN` foi removido.
3. **Poller:** só lotes fechados são processados; 1ª execução inicializa o cursor sem importar o histórico (`--backfill N`);
   erro **transitório** (`BitrixError.retryable`) segura o cursor, erro **permanente** (anexo grande, host estranho,
   validação) é reportado sem travar a fila (um PDF escaneado de 15 MB travaria o grupo inteiro); resultados com nome
   neutro (`anexo chat…#id`).
4. **Pipeline:** processamento serializado por lock (API + poller no mesmo processo criariam duplicatas); `display_name`;
   `retryable` no resultado.
5. **Novo:** `selfcheck` (porta testada do teste ao vivo), `complete_cpf`, `mask_cpfs_in_text` (CPF mascarado no nome do
   arquivo na timeline) e saída da CLI forçada para UTF-8.
6. **Validado ao vivo:** `discover`; `selfcheck` 9/9 duas vezes (2ª reaproveitou contato #71892 / card #2882);
   `ingest --dry-run` do PDF modelo contra o contato real correspondente (só leitura); poller em dry-run no grupo real:
   **9 de 9 laudos AIH lidos e casados com contatos existentes**.
