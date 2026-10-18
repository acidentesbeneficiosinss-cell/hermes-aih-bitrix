# Integração com o Hermes

O app roda **na mesma máquina do agente Hermes** e tem quatro formas de uso. Todas passam pela mesma `Pipeline` e têm as
mesmas garantias: idempotente, nunca apaga nada, e nenhuma saída traz dado pessoal.

> Para instalar tudo com o próprio agente, use o prompt pronto em [`PROMPT_HERMES.md`](PROMPT_HERMES.md).

## A. Agente Hermes (recomendado): skill + cron sem agente

`python run.py hermes-install` grava, já com os caminhos desta instalação:

| Arquivo | Para quê |
|---|---|
| `<HERMES_HOME>/skills/productivity/aih-bitrix/SKILL.md` | ensina o agente a usar o app e fixa as regras de privacidade (não colar dado de paciente no chat, nunca `extract --show`, nunca apagar, `selfcheck` só com pedido expresso) |
| `<HERMES_HOME>/scripts/aih_poll.py` | script que o **cron sem agente** do Hermes executa a cada rodada |

`<HERMES_HOME>` é `HERMES_HOME` se definida; senão `%LOCALAPPDATA%\hermes` (Windows nativo) ou `~/.hermes`. Use
`--hermes-home` para outro lugar e `--force` para regravar.

Depois, agende (o comando aparece na saída do `hermes-install`):

```bash
hermes cron create "every 5m" --no-agent --script aih_poll.py --name aih-bitrix-poller --deliver <destino>
hermes cron list
hermes cron status        # o cron é disparado pelo gateway do Hermes: ele precisa estar rodando
```

Como o cron sem agente funciona (documentação do Hermes): o `aih_poll.py` roda `poll --once --quiet` na pasta do app e

* **saída vazia = rodada silenciosa** (nenhum arquivo novo, nada é enviado);
* **saída com texto** (uma linha por arquivo processado, sem dado pessoal) vai para o destino de `--deliver`;
* **código de saída ≠ 0** gera alerta de falha (`[falha] …`); o `stderr` do app **nunca** é repassado;
* não usa o modelo: zero tokens e nenhum dado de paciente passa pelo LLM.

Se uma rodada passar do tempo limite de scripts do Hermes (120 s por padrão; cada laudo leva ~15 s por causa da espera
`VERIFY_DELAY_SEC`), aumente `cron.script_timeout_seconds` no `config.yaml` do Hermes (ou a variável
`HERMES_CRON_SCRIPT_TIMEOUT`) — isso é configuração do Hermes, então peça autorização antes de editar.

**Rode o monitoramento em um único lugar**: este cron **ou** `serve --with-poller`, nunca os dois.

### Hermes em Docker/Linux

* Instale o app **dentro do volume do Hermes** (`$HERMES_HOME/apps/hermes-aih-bitrix`): em Docker só ele sobrevive a uma
  recriação do container ou a uma atualização da imagem. A skill e o script de cron já ficam em `HERMES_HOME/skills` e
  `HERMES_HOME/scripts`.
* O gateway é o processo do container: `hermes cron status` deve mostrar o scheduler rodando (não instale serviço).
* Repositório privado: o container não tem credencial do GitHub. Envie o `.zip` (`git archive --format=zip -o hermes-aih-bitrix.zip HEAD`)
  ou use uma *deploy key* somente leitura; nunca cole token no chat.
* Webhook: grave-o dentro do container com `docker exec -it <container> bash -c 'read -rs ...'` (comando pronto em
  [`PROMPT_HERMES.md`](PROMPT_HERMES.md)), nunca pelo chat.
* Se `python3 -m venv` falhar (Debian/Ubuntu sem `python3-venv`), use `uv venv` ou, com autorização, `apt-get install python3.10-venv`.
* Já testado em Linux (Ubuntu, Python 3.10 a 3.14) pela CI do repositório.

Para o agente processar um PDF que recebeu no chat, a skill manda: salvar o PDF → `ingest --dry-run` → resumir → `ingest`
com a confirmação do usuário.

## B. Serviço HTTP (para chamar de outra máquina)

```bash
python run.py serve --host 0.0.0.0 --port 8080 --with-poller     # atrás de proxy com TLS
```

Variáveis mínimas: `BITRIX_WEBHOOK_URL`, `HERMES_API_KEY`, e `OL_DIALOG_IDS` se for monitorar grupos.

| Chamada | Uso |
|---|---|
| `POST /upload` — multipart `files` (1+ PDFs), `cpf` (opcional, só com 1 arquivo), `dry_run` (`true`/`false`) | processa os PDFs |
| `POST /poll` — form `dry_run` | roda **agora** uma rodada do poller (gatilho "webhook-like") |
| `GET /health` | verificação de vida (sem autenticação, sem dados) |

Ambas as rotas de escrita exigem o header `X-API-Key: <HERMES_API_KEY>`.

Resposta (`/upload` e `/poll`):

```json
{
  "summary": {"ok": 1},
  "results": [{
    "filename": "CPF***.***.***-09.pdf",
    "status": "ok",
    "sha256": "a1b2c3d4e5f6",
    "contact": {"id": 1001, "action": "updated", "filled": ["mother", "city", "state"], "skipped": []},
    "item":    {"id": 2001,  "action": "created", "filled": ["cns", "symptoms", "..."], "skipped": []},
    "checks":  [{"scope": "contact", "field": "cep", "ok": true}, "..."],
    "warnings": ["telefone do laudo ignorado (placeholder ou inválido)"],
    "error": null,
    "retryable": false
  }]
}
```

`status`: `ok` · `duplicate` (arquivo já processado) · `dry_run` · `needs_review` (**nada foi sobrescrito**; veja `warnings`:
contato ambíguo ou auditoria divergente) · `not_aih` · `error` (`retryable=true` só em falha transitória do Bitrix).

## C. CLI pontual (o Hermes chama como comando)

```bash
python run.py ingest --dry-run /caminho/CPF12345678909.pdf     # 1º: o que faria
python run.py ingest /caminho/CPF12345678909.pdf               # depois: grava e audita
python run.py ingest /pasta/de/laudos                          # todos os *.pdf da pasta
```

Saída: uma linha por arquivo, sem dado pessoal, por exemplo
`[ok] CPF***.***.***-09.pdf | contato: updated #1001 | card: created #2001 | verificação: 11 ok/0 divergente(s)`.
Código de saída `0` só se tudo foi `ok`/`duplicate`/`dry_run`.

## D. Rodada agendada fora do Hermes

```bash
python run.py poll --once --quiet       # a cada 1–5 min no Agendador de Tarefas/cron do sistema; o cursor fica no SQLite
```

## Diagnóstico

`python run.py doctor` (somente leitura) confere Python, dependências, o extrator de PDF (com as amostras fictícias
embutidas), a configuração, o local do ledger e, no Bitrix, autenticação, permissões do webhook, campos da SPA e leitura de
cada diálogo monitorado. `--offline` pula o Bitrix; `--json` é para máquina. Código de saída `1` se houver `[FAIL]`.

## Fluxo sugerido para um agente

1. `doctor` após configurar; `selfcheck` **só se o usuário pedir** (grava 1 contato + 1 card `[TESTE HERMES]` no portal real).
2. Para cada laudo novo: `ingest --dry-run` e **ler o resultado**; se `contact.action` for `ambiguous`, pedir decisão
   humana (há 2+ contatos para o mesmo paciente).
3. Gravar de verdade e conferir `status == ok` e `checks` todos `ok`. Em `needs_review`, avisar a equipe com o ID do
   contato/card (nunca tentar "forçar" ou apagar).
4. Nunca expor `HERMES_API_KEY` nem o webhook; ambos são segredos.

## O que o Hermes não deve esperar

* Que o card fique no título/etapa/responsável enviados: a automação da casa os reescreve (rodízio de etapas).
* Que o app envie mensagem no grupo do WhatsApp (não envia) ou apague registros (nunca apaga).
* Leitura de PDF escaneado ou imagem (sem OCR).
