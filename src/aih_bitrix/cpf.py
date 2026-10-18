"""CPF: validação, formatação, máscara e extração de nomes de arquivo / mensagens de chat."""
from __future__ import annotations

import re

_CPF_RE = re.compile(r"(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)")


def only_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _check_digit(nums: str, weight_start: int) -> int:
    total = sum(int(n) * w for n, w in zip(nums, range(weight_start, 1, -1)))
    rest = total % 11
    return 0 if rest < 2 else 11 - rest


def is_valid_cpf(value: str | None) -> bool:
    d = only_digits(value)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    d1 = _check_digit(d[:9], 10)
    d2 = _check_digit(d[:9] + str(d1), 11)
    return d[9:] == f"{d1}{d2}"


def complete_cpf(base9: str) -> str:
    """Acrescenta os dois dígitos verificadores aos 9 primeiros dígitos (gera CPFs válidos para testes)."""
    base = only_digits(base9)[:9]
    first = _check_digit(base, 10)
    return base + str(first) + str(_check_digit(base + str(first), 11))


def format_cpf(value: str) -> str:
    """Formato em que o portal guarda o CPF: NNN.NNN.NNN-NN."""
    d = only_digits(value)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def mask_cpf(value: str) -> str:
    """Máscara para logs: só os dois últimos dígitos."""
    return f"***.***.***-{only_digits(value)[-2:]}"


def mask_cpfs_in_text(text: str | None) -> str:
    """Troca todo CPF VÁLIDO do texto pela máscara (nomes de arquivo, mensagens). Outros números ficam intactos."""

    def _replace(match: re.Match[str]) -> str:
        digits = only_digits(match.group(1))
        return mask_cpf(digits) if is_valid_cpf(digits) else match.group(0)

    return _CPF_RE.sub(_replace, text or "")


def _first_valid(text: str | None) -> str | None:
    for match in _CPF_RE.finditer(text or ""):
        digits = only_digits(match.group(1))
        if is_valid_cpf(digits):
            return digits
    return None


def cpf_from_filename(name: str) -> str | None:
    return _first_valid(name)


def cpf_from_text(text: str) -> str | None:
    return _first_valid(text)
