"""Integração com o agente Hermes (Nous Research): gera a skill e o script do cron sem agente.

O Hermes carrega skills de `<HERMES_HOME>/skills/<categoria>/<nome>/SKILL.md` e só executa scripts de cron que estejam
em `<HERMES_HOME>/scripts/` (em modo `--no-agent` a saída do script vai direto ao chat, sem gastar o modelo; saída vazia
= rodada silenciosa). `hermes-install` escreve esses dois arquivos já com os caminhos absolutos desta instalação, para que
ninguém precise montá-los à mão.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

SKILL_NAME = "aih-bitrix"
SKILL_CATEGORY = "productivity"
CRON_SCRIPT_NAME = "aih_poll.py"
CRON_JOB_NAME = "aih-bitrix-poller"


class HermesInstallError(Exception):
    pass


def default_hermes_home(
    environ: Mapping[str, str] | None = None, *, platform: str | None = None, home: Path | None = None
) -> Path:
    env = os.environ if environ is None else environ
    if env.get("HERMES_HOME"):
        return Path(env["HERMES_HOME"])
    base = home if home is not None else Path.home()
    if (platform or sys.platform).startswith("win"):
        return Path(env.get("LOCALAPPDATA") or base / "AppData" / "Local") / "hermes"
    return base / ".hermes"


def default_app_dir() -> Path:
    """Pasta do projeto (onde está o run.py): src/aih_bitrix/hermes_install.py -> sobe dois níveis."""
    return Path(__file__).resolve().parents[2]


_WRAPPER = '''"""Cron do Hermes (modo --no-agent): uma rodada do poller AIH -> Bitrix24.

Gerado por `hermes-install`; refaça o comando em vez de editar à mão.
stdout vazio = rodada silenciosa · stdout com texto = vai para o chat · código de saída != 0 = alerta de falha.
Só repassa o stdout do app (que já sai sem dado pessoal). O stderr do app nunca é repassado.
"""
import subprocess
import sys

APP_DIR = @@APP_DIR@@
PYTHON = @@PYTHON@@

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

try:
    done = subprocess.run(
        [PYTHON, "run.py", "poll", "--once", "--quiet"], cwd=APP_DIR, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=900,
    )
except (OSError, subprocess.SubprocessError) as exc:
    print(f"[falha] não foi possível executar o poller ({type(exc).__name__}); rode `doctor` na pasta do app")
    sys.exit(2)

out = (done.stdout or "").strip()
if out:
    print(out)
if done.returncode != 0:
    print(f"[falha] o poller terminou com código {done.returncode}; rode `doctor` na pasta do app")
    sys.exit(done.returncode)
'''

_SKILL = '''---
name: aih-bitrix
description: "Processa laudos AIH (SUS) em PDF e cria ou atualiza o Contato e o card 'Leads Auxílio Acidente' no Bitrix24, conferindo o que gravou. Use ao receber um PDF de AIH, ao pedirem para processar ou consultar laudos, ou para diagnosticar a integração."
version: 1.0.0
metadata:
  hermes:
    tags: [bitrix24, aih, sus, pdf, crm, lgpd]
---

# AIH → Bitrix24

App local (Python) instalado em `@@APP_DIR@@`. Lê o laudo AIH (PDF), cria ou atualiza o **Contato** e o **card da SPA
"Leads Auxílio Acidente"** (entityTypeId 1146) e **relê o Bitrix para conferir** cada campo gravado.

## Como executar

Todos os comandos rodam **dentro da pasta do app** (lá estão o `.env` e o banco `data/ledger.sqlite3`):

- PowerShell: `Set-Location -LiteralPath "@@APP_DIR@@"; & "@@PYTHON@@" run.py <comando>`
- bash/cmd: `cd "@@APP_DIR@@" && "@@PYTHON@@" run.py <comando>`

Comandos: `doctor`, `ingest`, `poll`, `discover`, `extract`, `sample`, `selfcheck`, `serve`. Ajuda: `run.py <comando> --help`.

## Regras inegociáveis

1. **Dado de paciente é sensível (LGPD, dado de saúde).** Nunca copie para o chat, para a memória, para outras skills ou
   para arquivos o conteúdo de um PDF, nome, CPF, CNS, data de nascimento ou telefone. Use só a saída dos comandos: ela já
   sai sem dado pessoal (CPF mascarado, nomes neutros).
2. **Nunca** use `extract --show` (imprime os valores do paciente).
3. **Nunca** imprima, peça nem grave o token do webhook do Bitrix24. Ele fica só no `.env` da pasta do app; não peça a
   ninguém para colá-lo no chat.
4. O app **nunca apaga nada** no Bitrix, e você também não deve (a lixeira da SPA está desligada: apagar é definitivo).
5. `selfcheck` **grava registros de teste no portal real**. Só rode se o usuário pedir expressamente nesta conversa.
6. Arquivos `AMOSTRA-FICTICIA-*.pdf` são amostras inventadas: use só com `extract` ou `ingest --dry-run` (o app recusa
   gravá-las).
7. Antes de gravar um laudo, rode `ingest --dry-run`, resuma o resultado e grave só com a confirmação do usuário (ou se ele
   já deu autorização permanente para processar laudos sem perguntar).
8. Rode o monitoramento em **um único lugar**: o cron abaixo **ou** `serve --with-poller`, nunca os dois.

## Processar um PDF recebido

1. Salve o PDF numa pasta local. Se o usuário informar o CPF do paciente, nomeie o arquivo `CPF<11 dígitos>.pdf` (o app lê
   o CPF do nome) ou use `--cpf`. Não repita o CPF no chat.
2. Simule: `run.py ingest --dry-run "<arquivo>"`.
3. Grave: `run.py ingest "<arquivo>"`. Sai uma linha por arquivo, por exemplo:
   `[ok] CPF***.***.***-09.pdf | contato: created #123 | card: created #456 | verificação: 11 ok/0 divergente(s)`
4. Status: `ok` (gravado e conferido) · `duplicate` (já processado antes) · `dry_run` (simulação) · `needs_review` (algo
   diverge ou há ambiguidade, por exemplo dois contatos com o mesmo CPF: conferir no Bitrix) · `not_aih` (não é laudo AIH
   ou PDF escaneado sem texto) · `error` (leia a mensagem; erro transitório do Bitrix pode ser repetido mais tarde).
5. Códigos de saída: 0 ok · 1 pendência (`needs_review`, `error`, `not_aih`) · 2 uso, arquivo ou configuração · 3 Bitrix
   inacessível ou campos incompatíveis.

## Monitoramento automático do grupo "Docs protocolo"

Um cron **sem agente** (`aih-bitrix-poller`, script `aih_poll.py` em `scripts/` do Hermes) roda `poll --once --quiet` e só
fala quando processa algo ou falha. Para forçar uma rodada: `run.py poll --once`; para ver sem gravar:
`run.py poll --once --dry-run`. O poller só processa lotes **fechados** (arquivos, mensagem `cpf …`, separador) e começa a
partir do momento em que foi inicializado, sem importar o histórico do grupo. Para marcar esse ponto de partida SEM processar
nada (e sem nunca mover um já existente), use `run.py poll --init-only`; nunca rode `poll --once` só para "inicializar".

## Diagnóstico

`run.py doctor` (somente leitura; `--json` para máquina; `--offline` não acessa o Bitrix). `[FAIL]` precisa de correção;
`[WARN]` merece revisão.

## O que é normal no portal

- O portal reescreve **título, etapa e responsável** do card e aplica *Title Case* nos nomes; o app já compensa isso na
  conferência.
- Telefone `(65) 999999999` nos laudos é *placeholder*: o app o ignora.
- Telefones viram variantes no Bitrix (com e sem 55).
'''


def render_cron_wrapper(app_dir: Path, python: str) -> str:
    return _WRAPPER.replace("@@APP_DIR@@", repr(str(app_dir))).replace("@@PYTHON@@", repr(str(python)))


def render_skill(app_dir: Path, python: str) -> str:
    return _SKILL.replace("@@APP_DIR@@", str(app_dir)).replace("@@PYTHON@@", str(python))


def install(hermes_home: Path, app_dir: Path, python: str, *, force: bool = False) -> list[Path]:
    """Escreve a skill e o script do cron. Tudo ou nada: se algum destino já existir (e não houver `force`), não grava nada."""
    hermes_home, app_dir = Path(hermes_home), Path(app_dir)
    if not hermes_home.is_dir():
        raise HermesInstallError(f"pasta do Hermes não encontrada: {hermes_home} (informe --hermes-home)")
    if not (app_dir / "run.py").is_file():
        raise HermesInstallError(f"{app_dir} não parece a pasta do app (falta run.py); informe --app-dir")
    targets = {
        hermes_home / "skills" / SKILL_CATEGORY / SKILL_NAME / "SKILL.md": render_skill(app_dir, python),
        hermes_home / "scripts" / CRON_SCRIPT_NAME: render_cron_wrapper(app_dir, python),
    }
    existing = [p for p in targets if p.exists()]
    if existing and not force:
        raise HermesInstallError("já existe: " + ", ".join(str(p) for p in existing) + " — use --force para sobrescrever")
    for path, text in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return list(targets)
