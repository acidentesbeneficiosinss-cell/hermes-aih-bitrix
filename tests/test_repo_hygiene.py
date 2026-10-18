"""Guarda de privacidade: nada que pareça dado real pode entrar no repositório (nem em testes, nem em docs).

Roda sobre os arquivos que seriam publicados (`git ls-files` + não rastreados e não ignorados) ou, sem git, sobre a
árvore do projeto. Procura CPF válido, CNS e webhook do Bitrix24 que não sejam os exemplos conhecidos, e PDFs.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from aih_bitrix.cpf import is_valid_cpf, only_digits
from aih_bitrix.synthetic import is_sample_name

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".git", ".pytest_cache", "__pycache__", "data", "node_modules", ".ruff_cache"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".example", ".html", ""}

# CPFs de exemplo conhecidos (documentação, testes): válidos, mas de ninguém em particular
SAFE_CPFS = {"12345678909", "98765432100", "11144477735", "52998224725", "11122233396", "39053344705"}
SAFE_CNS_PREFIX = "70000000000"  # CNS fictícios do projeto: 700000000000012, ...
SAFE_WEBHOOK_TOKENS = {"abcdef0123456789"}

CPF_RX = re.compile(r"(?<!\d)(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})(?!\d)")
CNS_RX = re.compile(r"(?<!\d)[12789]\d{14}(?!\d)")
WEBHOOK_RX = re.compile(r"/rest/\d+/([A-Za-z0-9]{10,})/")


def find_leaks(text: str) -> list[str]:
    """Descrições (sem o valor) do que parece dado real em `text`."""
    leaks: list[str] = []
    for m in CPF_RX.finditer(text):
        digits = only_digits(m.group(1))
        if is_valid_cpf(digits) and digits not in SAFE_CPFS:
            leaks.append("CPF válido que não é um dos exemplos conhecidos")
    for m in CNS_RX.finditer(text):
        if not m.group(0).startswith(SAFE_CNS_PREFIX):
            leaks.append("número de 15 dígitos com cara de CNS")
    for m in WEBHOOK_RX.finditer(text):
        if m.group(1) not in SAFE_WEBHOOK_TOKENS and "TOKEN" not in m.group(1).upper():
            leaks.append("token de webhook do Bitrix24")
    return leaks


def stray_pdfs(files: list[Path]) -> list[Path]:
    """PDFs que não são as amostras fictícias geradas por `sample` (essas são inofensivas e ficam no .gitignore)."""
    return [p for p in files if p.suffix.lower() == ".pdf" and not is_sample_name(p.name)]


def publishable_files(root: Path = ROOT) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True, timeout=30,
        ).stdout
        listed = [root / name for name in out.decode("utf-8").split("\0") if name]
        return [p for p in listed if p.is_file()]
    except (OSError, subprocess.SubprocessError):
        return [p for p in root.rglob("*")
                if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS) and p.name != ".env"
                and not p.name.endswith(".env.txt")]


# ---------------------------------------------------------------- o detector em si
# Os valores plantados abaixo são montados em pedaços: este arquivo também é varrido e não pode conter um literal "vazado".

CPF_FORMATTED = "168.995." + "350-09"  # válido, fora da lista de exemplos
CPF_DIGITS = "168995" + "35009"
CNS_UNKNOWN = "898001" + "160660001"
WEBHOOK_REAL_LOOKING = "https://portal.bitrix24.com.br/rest/1/" + "k3j9x8d7" + "s6a5q4w2/"


def test_detector_flags_a_valid_unknown_cpf_in_any_notation():
    assert find_leaks("cpf 529.982.247-25") == []  # exemplo conhecido
    assert find_leaks(f"cpf {CPF_FORMATTED}") and find_leaks(f"cpf {CPF_DIGITS}")
    assert find_leaks("cpf 123.456.789-00") == []  # dígitos verificadores errados: não é CPF


def test_detector_flags_cns_like_numbers_but_not_the_projects_fictional_ones():
    assert find_leaks("cns 700000000000012") == []
    assert find_leaks(f"cns {CNS_UNKNOWN}") and find_leaks("cns " + "123456" + "789012345")
    assert find_leaks("id 1792335030647") == []  # 13 dígitos: id de campo do Bitrix, não CNS


def test_detector_flags_webhook_tokens_but_not_placeholders():
    assert find_leaks("https://fake.bitrix24.com.br/rest/1/abcdef0123456789/") == []
    assert find_leaks("https://portal.bitrix24.com.br/rest/1/SEU_TOKEN_AQUI/") == []
    assert find_leaks(WEBHOOK_REAL_LOOKING)


def test_detector_never_echoes_the_value_it_found():
    leaks = find_leaks(f"{CPF_FORMATTED} {CNS_UNKNOWN} {WEBHOOK_REAL_LOOKING}")
    assert len(leaks) == 3 and all("168" not in leak and "898" not in leak and "k3j9" not in leak for leak in leaks)


# ---------------------------------------------------------------- o repositório

def test_only_the_generated_fictional_samples_are_allowed_as_pdfs():
    files = [Path("x/AMOSTRA-FICTICIA-aih-a.pdf"), Path("amostra-ficticia-aih-b.PDF"), Path("laudo.pdf"), Path("a/b.txt"),
             Path("CPF12345678909.pdf")]
    assert stray_pdfs(files) == [Path("laudo.pdf"), Path("CPF12345678909.pdf")]


def test_the_repository_contains_no_pdf_files():
    pdfs = [p.relative_to(ROOT).as_posix() for p in stray_pdfs(publishable_files())]
    assert pdfs == [], "amostras são geradas em memória (aih_bitrix.synthetic); não versione PDFs"


def test_the_repository_contains_nothing_that_looks_like_real_personal_data():
    problems: list[str] = []
    for path in publishable_files():
        if path.suffix.lower() not in TEXT_SUFFIXES or path.name == "requirements.lock.txt":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        problems += [f"{path.relative_to(ROOT).as_posix()}: {leak}" for leak in find_leaks(text)]
    assert problems == []
