# Prompt de instalação para o agente Hermes

**Como usar**

1. Preencha o bloco `DADOS` no começo do prompt. O que ficar entre `< >` o Hermes pergunta a você, uma única vez, antes
   de começar. Se o código ainda não estiver no GitHub, use o caminho da pasta local em `ORIGEM_DO_CODIGO`.
2. Cole **todo** o conteúdo do bloco abaixo (de `# TAREFA` até o fim) no Hermes.
3. Responda às perguntas dele. O webhook do Bitrix24 **você** coloca no arquivo `.env` (passo 5); ele nunca vai para o chat.
4. Ao final o Hermes entrega um relatório com evidências (sem dado de paciente). Envie esse relatório para revisão.

O prompt foi escrito para o Hermes Agent instalado em `%LOCALAPPDATA%\hermes` (Windows nativo), que é o que existe nesta
máquina. Se o Hermes rodar em WSL, Docker ou servidor, ele para no passo 1 e confirma com você antes de adaptar os caminhos.

````text
# TAREFA: instalar, validar e agendar o app "hermes-aih-bitrix" (laudos AIH → Bitrix24)

Você é o instalador. Execute os passos NA ORDEM, um de cada vez, e me mostre só o resultado resumido de cada um (sem dados
de pacientes e sem segredos). Se qualquer verificação falhar, PARE: diga qual comando falhou e o que ele imprimiu (a saída do
app já não contém dado pessoal) e aguarde minha orientação. Não improvise correções, não altere o código do app, não
"ajuste" testes para passar.

## DADOS (eu preencho; o que estiver entre < > você me pergunta, uma única vez, antes de começar)
- ORIGEM_DO_CODIGO: <URL do repositório no GitHub | pasta local | arquivo .zip>
- PASTA_DE_INSTALACAO: %USERPROFILE%\hermes-apps\hermes-aih-bitrix
- DIALOGOS_A_MONITORAR: chat360293   (grupo de WhatsApp "Docs protocolo", Canal Aberto 24Zap)
- ENTREGA_DO_CRON: <para onde vão os avisos do poller: origin | local | telegram | discord | signal | plataforma:chat_id>
- AUTORIZO_LER_LAUDOS_REAIS_EM_DRY_RUN: <sim | não>   (passo 7: só lê e imprime contagens e status, não grava)
- AUTORIZO_SELFCHECK: <sim | não>   (passo 8: GRAVA 1 contato e 1 card de teste no Bitrix real)
- AUTORIZO_EDITAR_CONFIG_DO_HERMES: <sim | não>   (passo 9: timeout de scripts do cron e, se preciso, iniciar o gateway)
- ARQUIVO_COM_O_WEBHOOK: <opcional: caminho de um arquivo de texto que contém a URL do webhook; se eu informar, autorizo você
  a copiar a URL para o .env sem exibi-la (atalho do passo 5); se ficar vazio, eu mesmo edito o .env>

## O QUE É O APP
Aplicação Python que recebe laudos "Solicitação de Autorização de Internação Hospitalar (AIH) – SUS" em PDF, extrai os
dados e, no Bitrix24 `brandaocorrea.bitrix24.com.br`, cria ou completa o Contato e o card da SPA "Leads Auxílio Acidente"
(entityTypeId 1146, categoria 101), relendo depois o que gravou para conferir campo a campo. Entradas: linha de comando
(`run.py ingest`) e monitor do grupo "Docs protocolo" (`run.py poll`, por polling dos Canais Abertos). Comandos:
doctor, sample, extract, discover, ingest, poll, selfcheck, serve, hermes-install. É idempotente (o mesmo laudo não duplica) e
NUNCA apaga nada. Códigos de saída: 0 ok · 1 pendência (needs_review/error/not_aih) · 2 uso, arquivo ou configuração ·
3 Bitrix inacessível ou campos incompatíveis.

## REGRAS INEGOCIÁVEIS (valem durante e depois da instalação)
1. Dado de paciente é dado de saúde (LGPD). Nunca leia o conteúdo de PDFs de pacientes; nunca copie nome, CPF, CNS,
   nascimento ou telefone para o chat, para a sua memória, para skills ou para arquivos. Use só as saídas dos comandos do
   app (elas já saem sem dado pessoal). NUNCA use `extract --show`.
2. Segredos: nunca peça, mostre, repita ou grave o token do webhook do Bitrix24. Não abra nem imprima o `.env` do app, o
   arquivo `bitrix.env.txt` nem o `.env` do Hermes. Quem coloca o webhook sou eu (passo 5).
3. Nunca apague nada. No Bitrix, nunca chame métodos `*.delete` (a lixeira da SPA está desligada: apagar é definitivo). No
   disco, só altere a PASTA_DE_INSTALACAO e os dois arquivos criados pelo `hermes-install`.
4. Escritas no Bitrix real só com minha autorização explícita acima: `selfcheck`, `ingest` sem `--dry-run` e `poll` quando
   já existe cursor. A 1ª rodada do poller só marca o ponto de partida do grupo (não grava no Bitrix).
5. Instale somente o que está em `requirements-dev.txt`. Não execute scripts baixados, não use `curl | sh`, não envie
   código, logs ou saídas a serviços externos.
6. Só altere o `config.yaml` do Hermes se AUTORIZO_EDITAR_CONFIG_DO_HERMES for "sim" (faça backup antes).
7. Na sua memória grave apenas: o caminho do app, o nome da skill (aih-bitrix) e o nome do cron (aih-bitrix-poller).

## PASSOS

Os comandos abaixo são PowerShell (Windows). Se o seu terminal for outro shell, rode-os com `powershell -NoProfile -Command "..."`.
As variáveis do PowerShell podem não persistir entre comandos; se não persistirem, escreva os caminhos por extenso. Se o
launcher `py` não existir, use `python`. Em Linux/WSL troque `.\.venv\Scripts\python.exe` por `.venv/bin/python` (e confirme
comigo antes).

### Passo 1: reconhecer o ambiente (somente leitura)
```powershell
$PSVersionTable.PSVersion; [Environment]::OSVersion.VersionString
hermes --version
py -0p
git --version
$hh = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }; $hh; Test-Path "$hh\skills"
```
Informe: sistema, versão do Hermes, interpretadores Python (precisa de 3.10 ou mais novo), se há git, e o HERMES_HOME. Se o
Hermes NÃO estiver neste Windows (WSL, Docker, servidor), pare e me pergunte.

### Passo 2: obter o código
```powershell
$dest = [Environment]::ExpandEnvironmentVariables("<PASTA_DE_INSTALACAO>"); $py = "$dest\.venv\Scripts\python.exe"
```
(a) GitHub: `git clone "<ORIGEM_DO_CODIGO>" $dest`. Se pedir login (repositório privado), me peça para autenticar
(`gh auth login` ou o Gerenciador de Credenciais do Git). Nunca peça token no chat.
(b) Pasta local: `robocopy "<ORIGEM_DO_CODIGO>" $dest /E /XD .venv .git data __pycache__ .pytest_cache /XF .env *.env.txt *.pdf`
(robocopy: códigos de saída 0 a 7 significam sucesso).
(c) .zip: `Expand-Archive -LiteralPath "<arquivo.zip>" -DestinationPath $dest`.
Confirme `Test-Path "$dest\run.py"`. Nunca copie `.env`, `*.env.txt`, `.venv`, `data` nem PDFs de pacientes.

### Passo 3: ambiente virtual e dependências
```powershell
Set-Location -LiteralPath $dest
py -3 -m venv .venv          # precisa ser Python 3.10+; se "py -3" for mais antigo use py -3.11 ou py -3.14
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements-dev.txt
& $py -m pip list --format=freeze | Select-String "pdfplumber|requests|fastapi|uvicorn|python-multipart|pytest"
```
Informe as versões instaladas. Este é o único download (PyPI, uns 25 MB).

### Passo 4: testes e diagnóstico OFFLINE
```powershell
& $py -m pytest
& $py run.py doctor --offline
& $py run.py sample --layout a
& $py run.py sample --layout b
& $py run.py extract AMOSTRA-FICTICIA-aih-a.pdf
& $py run.py extract AMOSTRA-FICTICIA-aih-b.pdf
```
Esperado: pytest sem nenhuma falha (informe o total); `doctor --offline` = 3 PASS e 2 SKIP; os dois `extract` terminam com
`"rotulos_nao_reconhecidos": []`. As amostras são de um paciente INVENTADO: o `ingest` sem `--dry-run` as recusa.

### Passo 5: configuração (.env). O segredo é meu, não seu
```powershell
Copy-Item .env.example .env
```
Peça-me para abrir `$dest\.env` e trocar a linha `BITRIX_WEBHOOK_URL=` pelo webhook de ENTRADA do Bitrix24 (formato
`https://brandaocorrea.bitrix24.com.br/rest/<ID>/<TOKEN>/`). Não me peça para colar o webhook no chat e não leia o arquivo.
Continue quando eu disser "feito". As demais linhas já vêm certas (`OL_DIALOG_IDS=chat360293`,
`LEDGER_PATH=data/ledger.sqlite3`); se DIALOGOS_A_MONITORAR for outro, mude só a linha `OL_DIALOG_IDS` (por um comando que
troque apenas essa linha, sem imprimir o arquivo). Diga-me uma vez: o ideal é um webhook dedicado, só com os escopos crm, im,
imopenlines e disk, e revogar depois o de permissão total.
Atalho, SOMENTE se ARQUIVO_COM_O_WEBHOOK estiver preenchido (extrai a URL sem exibi-la; não imprima o arquivo):
```powershell
$src = Get-Content -Raw -LiteralPath "<ARQUIVO_COM_O_WEBHOOK>"
$m = [regex]::Match($src, 'https://[^/\s]+/rest/\d+/[A-Za-z0-9]+/?')
if ($m.Success) {
  $lines = Get-Content -LiteralPath .env | ForEach-Object { if ($_ -like 'BITRIX_WEBHOOK_URL=*') { 'BITRIX_WEBHOOK_URL=' + $m.Value } else { $_ } }
  Set-Content -LiteralPath .env -Value $lines -Encoding UTF8; 'webhook copiado (não exibido)'
}
```

### Passo 6: validação com o Bitrix, SOMENTE LEITURA
```powershell
& $py run.py doctor
& $py run.py discover
& $py run.py ingest --dry-run AMOSTRA-FICTICIA-aih-a.pdf
```
Esperado: `doctor` sem nenhum `[FAIL]` (o único `[WARN]` aceitável é "Chave da API", usada só no modo servidor); `discover`
mostra categoria 101, etapa `DT1146_101:NEW`, 5 campos de contato e 11 da SPA validados; o `ingest --dry-run` termina em
`dry_run`. NADA é gravado nesses três comandos.

### Passo 7: laudos REAIS em dry-run (somente se AUTORIZO_LER_LAUDOS_REAIS_EM_DRY_RUN = sim)
```powershell
& $py run.py poll --once --dry-run --backfill 20 --dialog chat360293
```
Baixa em memória os últimos anexos do grupo, interpreta e imprime só o status por arquivo (nomes neutros, sem dado pessoal),
sem gravar no Bitrix e sem mexer no cursor. Resuma as contagens por status (dry_run, duplicate, needs_review, not_aih,
error). Para `needs_review`, `not_aih` ou `error`, diga a contagem e a mensagem exata impressa (que não contém dados).

### Passo 8: teste ao vivo com registros sintéticos (somente se AUTORIZO_SELFCHECK = sim)
```powershell
& $py run.py selfcheck
```
Grava 1 contato e 1 card `[TESTE HERMES - NAO ATENDER]` e lê de volta (idempotente: a 2ª execução reaproveita). Esperado:
todas as verificações PASS (hoje 9 de 9). Os registros ficam no portal (o app nunca apaga): informe os IDs impressos para eu
apagar à mão. O portal tem automações (o card novo gira o rodízio de etapas e pode disparar aviso): por isso só com meu OK.

### Passo 9: integração com o Hermes (skill + cron sem agente)
```powershell
& $py run.py hermes-install
```
Grava `SKILL.md` e `aih_poll.py` no HERMES_HOME (se o Hermes não estiver em `%LOCALAPPDATA%\hermes`, acrescente
`--hermes-home <pasta>`). Anote os dois caminhos impressos.

Marque o ponto de partida do grupo (NÃO processa o histórico; grava só no ledger local). Rode APENAS se `$dest\data\ledger.sqlite3`
ainda não existir (1ª instalação); se existir, não rode e me pergunte:
```powershell
if (-not (Test-Path "$dest\data\ledger.sqlite3")) { & $py run.py poll --once }
```
Esperado: `poll: 0 arquivo(s) analisado(s)`.

Agende o poller (sem agente: zero tokens, e dado de paciente nunca passa pelo modelo):
```powershell
hermes cron create "every 5m" --no-agent --script aih_poll.py --name aih-bitrix-poller --deliver <ENTREGA_DO_CRON>
hermes cron list
hermes cron status
```
(`--deliver` aceita origin, local, telegram, discord, signal ou plataforma:chat_id; "local" só grava a saída em arquivo. Se o
`hermes cron create` não estiver disponível no seu terminal, use a ferramenta `cronjob` com `action="create"`,
`schedule="every 5m"`, `script="aih_poll.py"`, `no_agent=True`, `name="aih-bitrix-poller"` e `deliver="<ENTREGA_DO_CRON>"`.)
O cron é disparado pelo gateway do Hermes: `hermes cron status` precisa mostrar o scheduler rodando. Se não estiver, me avise; iniciar ou instalar o
gateway (`hermes gateway install` ou `hermes gateway`) muda o Hermes, então só com AUTORIZO_EDITAR_CONFIG_DO_HERMES = sim.
Cada laudo leva uns 15 s (espera de conferência), e o limite padrão de scripts do cron é 120 s. Se AUTORIZO_EDITAR_CONFIG_DO_HERMES
= sim, faça backup do `config.yaml` e defina `cron.script_timeout_seconds: 600`; se não, apenas me avise desse limite.

### Passo 10: verificação final do cron e da skill
```powershell
hermes cron run aih-bitrix-poller
```
Aguarde 60 a 90 s (o gateway dispara a cada 60 s) e confira com `hermes cron list` que a última execução terminou bem, sem
alerta `[falha]`. Rodada silenciosa (nenhuma mensagem) é o resultado normal. Confirme que a skill `aih-bitrix` está carregada
(`hermes skills list`, ou abra uma nova sessão e pergunte quais comandos ela tem) e que ela cita as regras de privacidade.
Rode `& $py run.py doctor --json` e resuma apenas `ok` e `counts`.

### Passo 11: relatório final (obrigatório; só fatos que você verificou)
Entregue uma tabela com: Item | Resultado (PASS, FAIL ou NÃO FEITO) | Evidência (comando e saída resumida, sem dados pessoais):
1. Ambiente (sistema, Hermes, Python)  2. Código obtido (origem e `git rev-parse --short HEAD`, ou "pasta local")
3. Dependências (versões)  4. pytest (passou/falhou)  5. doctor --offline  6. extract das 2 amostras (rótulos não reconhecidos = 0)
7. .env configurado (sem exibir)  8. doctor online (contagens e cada WARN/FAIL por extenso)  9. discover (categoria, etapa, campos)
10. ingest --dry-run da amostra  11. poll dry-run em laudos reais (contagem por status) ou NÃO FEITO  12. selfcheck (x/9) ou NÃO FEITO
13. hermes-install (2 arquivos)  14. cron (nome, agenda, entrega, estado do gateway, última execução)  15. skill carregada.
Termine com "Pendências e riscos" (o que não foi verificado e por quê) e "Decisões que preciso de você" (por exemplo: liberar o
`selfcheck`; trocar para um webhook dedicado e revogar o de permissão total; autorizar o `ingest` real para laudos recebidos
no chat). Lembre-me de que a primeira gravação REAL do poller acontece quando chegar o primeiro laudo novo no grupo: quando
o cron avisar, indique-me os IDs do contato e do card para eu abrir no Bitrix e conferir antes de confiar no automático.

## DEFINIÇÃO DE PRONTO
Pronto = pytest verde + doctor sem [FAIL] + extract das duas amostras sem rótulos não reconhecidos + cron criado com o gateway
ativo + skill instalada + relatório entregue. Qualquer coisa fora disso é pendência, não sucesso.

## SE ALGO FALHAR (guia rápido; não improvise além disto)
- Sem Python 3.10+: peça-me para instalar o Python 3.11 ou mais novo; não instale por conta própria.
- `pip install` falha por rede ou proxy: relate a mensagem e pare.
- `[FAIL] Configuração`: `.env` ausente ou BITRIX_WEBHOOK_URL inválida (a mensagem não repete o valor).
- `[FAIL] Portal: autenticação`: webhook errado ou revogado, ou sem rede (códigos como NETWORK, HTTP_5xx, INVALID_CREDENTIALS).
- `[FAIL] Portal: permissões`: o webhook não tem o escopo crm; eu preciso criar outro com crm, im, imopenlines e disk.
- `[FAIL] Portal: campos da SPA`: alguém mudou ou removeu um campo no Bitrix; relate a mensagem completa (ela lista as chaves).
- `[FAIL] Portal: diálogo chat…`: sem acesso ao grupo, ou ID errado.
- Qualquer `needs_review`: o app NÃO sobrescreveu nada; relate o ID do contato/card impresso. Nunca tente "forçar" nem apagar.
````

## Depois da instalação: o que enviar para revisão

* O **relatório final** do Hermes (passo 11), copiado sem edição.
* A saída de `doctor --json` (só `ok` e `counts`) e do `pytest` (linha final).
* Se o Hermes rodou o passo 7: as contagens por status e, para cada `needs_review` / `not_aih` / `error`, a mensagem exata.
* Se houver um **hospital com layout diferente**: a saída de `extract <arquivo.pdf>` (sem `--show`), que lista só os
  *rótulos não reconhecidos*, sem dados do paciente.
