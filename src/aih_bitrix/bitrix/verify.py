"""Verificação por re-leitura: compara o que foi gravado com o que o portal devolve.

Modos de comparação (o portal normaliza o que grava):
  fold   nome/sobrenome/mãe/cidade: sem caixa nem acentos (o BP #425 aplica Title Case);
  digits CEP e CPF: só dígitos (o CPF é guardado formatado);
  date   só a parte AAAA-MM-DD (o portal devolve hora e fuso);
  phone  cada número esperado precisa ter um equivalente (DDD + 8 dígitos) entre os do contato;
  exact  demais campos.
Título, etapa e responsável do card NÃO são auditados: a automação da casa os reescreve.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..cpf import only_digits
from ..parser import fold
from ..phones import same_phone
from .fieldmap import FieldMap

_CONTACT_MODES = {
    "NAME": "fold", "LAST_NAME": "fold", "BIRTHDATE": "date", "PHONE": "phone",
    "mother": "fold", "city": "fold", "state": "exact", "cep": "digits", "cpf": "digits",
}


@dataclass(frozen=True)
class FieldCheck:
    scope: str  # contact | item
    field: str  # nome lógico do campo (nunca o valor)
    ok: bool
    expected: str  # valores só para diagnóstico local: a API nunca os devolve
    got: str
    mode: str


def _equal(mode: str, got: Any, want: Any) -> bool:
    got_s = "" if got is None else str(got)
    want_s = "" if want is None else str(want)
    if mode == "fold":
        return fold(got_s).split() == fold(want_s).split()
    if mode == "digits":
        return only_digits(got_s) == only_digits(want_s)
    if mode == "date":
        return got_s[:10] == want_s[:10]
    return got_s.strip() == want_s.strip()


def verify_contact(client: Any, contact_id: int, expected: Mapping[str, Any], fmap: FieldMap) -> list[FieldCheck]:
    contact = client.result("crm.contact.get", {"id": contact_id}) or {}
    reverse = {key: logical for logical, key in fmap.contact.items()}
    checks: list[FieldCheck] = []
    for key, want in expected.items():
        logical = reverse.get(key, key)
        mode = _CONTACT_MODES.get(logical, "exact")
        if key == "PHONE":
            wanted = [entry["VALUE"] for entry in want]
            have = [phone.get("VALUE") or "" for phone in contact.get("PHONE") or []]
            ok = all(any(same_phone(w, h) for h in have) for w in wanted)
            checks.append(FieldCheck("contact", "PHONE", ok, ", ".join(wanted), ", ".join(have), mode))
            continue
        got = contact.get(key)
        checks.append(FieldCheck("contact", logical, _equal(mode, got, want), str(want), "" if got is None else str(got), mode))
    return checks


def verify_item(client: Any, item_id: int, expected: Mapping[str, Any], fmap: FieldMap) -> list[FieldCheck]:
    item = (client.result("crm.item.get", {"entityTypeId": fmap.entity_type_id, "id": item_id}) or {}).get("item", {})
    reverse = {key: logical for logical, key in fmap.item.items()}
    checks: list[FieldCheck] = []
    for key, want in expected.items():
        got = item.get(key)
        checks.append(
            FieldCheck("item", reverse.get(key, key), _equal("exact", got, want), str(want), "" if got is None else str(got), "exact")
        )
    return checks
