"""A CI só roda se o YAML do workflow for válido.

O erro clássico: um valor SEM aspas que contém ': ' (por exemplo `name: Testes (guarda: nada real)`). O GitHub rejeita o arquivo
inteiro ("workflow file issue"), nenhum job começa, e isso só aparece depois do push. Este teste pega o problema antes,
sem depender de PyYAML.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
KEY_VALUE = re.compile(r"^\s*(?:-\s+)?[A-Za-z0-9_.-]+:\s+(?P<value>\S.*?)\s*$")
NOT_PLAIN = ("'", '"', "|", ">", "[", "{", "&", "*", "!", "#", "%", "@", "`")


def plain_scalar_problems(text: str) -> list[int]:
    """Números das linhas cujo valor simples (sem aspas, fora de bloco literal) contém ': ' ou ' #' ou termina em ':'."""
    problems: list[int] = []
    block_parent_indent: int | None = None
    for number, line in enumerate(text.splitlines(), 1):
        indent = len(line) - len(line.lstrip(" "))
        if block_parent_indent is not None:
            if not line.strip() or indent > block_parent_indent:
                continue  # conteúdo de um bloco literal (run: | ...)
            block_parent_indent = None
        match = KEY_VALUE.match(line)
        if not match:
            continue
        value = match.group("value")
        if value[0] in "|>":
            block_parent_indent = indent
        elif not value.startswith(NOT_PLAIN) and (": " in value or " #" in value or value.endswith(":")):
            problems.append(number)
    return problems


def test_detector_flags_the_unquoted_colon_that_breaks_github_workflows():
    assert plain_scalar_problems("      - name: Testes (guarda: nada real)\n        run: x") == [1]
    assert plain_scalar_problems("      - name: 'Testes (guarda: nada real)'") == []
    assert plain_scalar_problems('      - name: "Testes (guarda: nada real)"') == []
    assert plain_scalar_problems("name: etapa # comentario no valor") == [1]


def test_detector_ignores_block_scalars_flow_lists_and_expressions():
    text = (
        "    steps:\n"
        "      - name: Instalar\n"
        "        run: |\n"
        "          echo 'a: b'\n"
        "          python -m pip install -r requirements-dev.txt\n"
        "      - name: py${{ matrix.python-version }} / ${{ matrix.os }}\n"
        "        with:\n"
        "          python-version: ${{ matrix.python-version }}\n"
        "          branches: [main]\n"
    )
    assert plain_scalar_problems(text) == []


def test_every_workflow_file_in_the_repository_is_free_of_that_problem():
    files = sorted(WORKFLOWS.glob("*.y*ml"))
    assert files, "esperava ao menos um workflow em .github/workflows"
    problems = {f.name: plain_scalar_problems(f.read_text(encoding="utf-8")) for f in files}
    assert all(not lines for lines in problems.values()), problems


def test_the_test_matrix_keeps_python_versions_quoted_so_that_3_10_is_not_read_as_3_1():
    text = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    versions = re.search(r"python-version:\s*\[(?P<list>[^\]]+)\]", text)
    assert versions, "matriz de versões do Python não encontrada"
    items = [item.strip() for item in versions.group("list").split(",")]
    assert items and all(item.startswith('"') and item.endswith('"') for item in items), items
