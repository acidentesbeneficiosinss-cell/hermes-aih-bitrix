"""Telefones brasileiros: equivalência por DDD + 8 dígitos e detecção de número placeholder.

O portal expande cada telefone em variantes (com/sem 55, com/sem 9º dígito), então a comparação
nunca pode ser por igualdade de texto.
"""
from __future__ import annotations

import re

_SEQUENCES = {"12345678", "87654321", "01234567", "76543210", "23456789", "98765432"}


def phone_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _national(value: str | None) -> str:
    """Dígitos sem o DDI 55 (só removido quando há 12 ou mais dígitos)."""
    digits = phone_digits(value)
    if len(digits) >= 12 and digits.startswith("55"):
        digits = digits[2:]
    return digits


def is_placeholder_phone(value: str | None) -> bool:
    """True para vazio, curto demais ou padrão de preenchimento (99999999, 12345678...)."""
    digits = _national(value)
    if len(digits) < 10:
        return True
    last8 = digits[-8:]
    return len(set(last8)) == 1 or last8 in _SEQUENCES


def phone_key(value: str | None) -> str:
    """DDD + 8 últimos dígitos; vazio quando não há DDD e número."""
    digits = _national(value)
    if len(digits) < 10:
        return ""
    return digits[:2] + digits[-8:]


def same_phone(a: str | None, b: str | None) -> bool:
    key_a, key_b = phone_key(a), phone_key(b)
    if key_a and key_b:
        return key_a == key_b
    da, db = _national(a), _national(b)
    return len(da) >= 8 and len(db) >= 8 and da[-8:] == db[-8:]


def format_phone_br(value: str | None) -> str:
    digits = _national(value)
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return (value or "").strip()
