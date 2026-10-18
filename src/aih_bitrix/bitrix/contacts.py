"""Contato do paciente: localizar (CPF → nome+nascimento), criar ou completar, sem nunca sobrescrever.

Política (validada contra o portal real):
  * o CPF é buscado no valor FORMATADO (como o portal grava) e depois só em dígitos;
  * contato existente só recebe campos que estão VAZIOS; nome nunca é alterado;
  * telefone placeholder (ex.: 999999999) nunca é gravado, e telefones equivalentes (DDD + 8 dígitos) não se repetem;
  * dois ou mais candidatos => `ambiguous`: nada é gravado e o caso vai para revisão humana.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..cpf import format_cpf, only_digits
from ..models import Laudo, parse_br_date
from ..parser import fold
from ..phones import format_phone_br, is_placeholder_phone, same_phone
from .fieldmap import FieldMap


@dataclass
class ContactResult:
    id: int | None
    action: str  # created | updated | unchanged | ambiguous | dry_run_create | dry_run_update
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # valor existente diferente do laudo: mantido
    warnings: list[str] = field(default_factory=list)
    expected: dict[str, Any] = field(default_factory=dict)  # o que a verificação deve encontrar depois


def build_contact_fields(laudo: Laudo, cpf: str | None, fmap: FieldMap) -> tuple[dict[str, Any], list[str]]:
    keys = fmap.contact
    warnings: list[str] = []
    fields: dict[str, Any] = {"OPENED": "Y"}
    if laudo.first_name:
        fields["NAME"] = laudo.first_name
    else:
        warnings.append("nome do paciente não encontrado no laudo")
    if laudo.last_name:
        fields["LAST_NAME"] = laudo.last_name
    elif laudo.first_name:
        warnings.append("paciente com nome sem sobrenome")
    if laudo.birth_date:
        fields["BIRTHDATE"] = laudo.birth_date.isoformat()
    if laudo.mother_name:
        fields[keys["mother"]] = laudo.mother_name
    if laudo.city:
        fields[keys["city"]] = laudo.city
    if laudo.uf:
        state_id = fmap.state_ids.get(laudo.uf)
        if state_id:
            fields[keys["state"]] = state_id
        else:
            warnings.append(f"UF '{laudo.uf}' não existe na lista Estado do Bitrix")
    if laudo.cep:
        fields[keys["cep"]] = laudo.cep
    if cpf:
        fields[keys["cpf"]] = format_cpf(cpf)
    phone = laudo.get("phone")
    if phone:
        if is_placeholder_phone(phone):
            warnings.append("telefone do laudo ignorado (placeholder ou inválido)")
        else:
            fields["PHONE"] = [{"VALUE": format_phone_br(phone), "VALUE_TYPE": "WORK"}]
    return fields, warnings


def _is_empty(value: Any) -> bool:
    return value is None or str(value).strip() in ("", "0")


def _birth_of(row: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(row.get("BIRTHDATE") or "")[:10])
    except ValueError:
        return None


def _same_name(row: dict[str, Any], laudo: Laudo) -> bool:
    return fold(f"{row.get('NAME', '')} {row.get('LAST_NAME', '')}").split() == fold(laudo.patient_name).split()


def find_contacts(client: Any, fmap: FieldMap, laudo: Laudo, cpf: str | None) -> list[dict[str, Any]]:
    select = ["ID", "NAME", "LAST_NAME", "BIRTHDATE", "PHONE", *fmap.contact.values()]
    if cpf:
        found: dict[str, dict[str, Any]] = {}
        for value in (format_cpf(cpf), only_digits(cpf)):
            rows = client.result("crm.contact.list", {"filter": {fmap.contact["cpf"]: value}, "select": select}) or []
            for row in rows:
                found.setdefault(str(row["ID"]), row)
        rows = list(found.values())
        if len(rows) > 1:
            exact = [r for r in rows if _same_name(r, laudo)]
            if len(exact) == 1:
                return exact
        if rows:
            return rows
    if laudo.first_name and laudo.birth_date:  # contatos antigos podem não ter CPF gravado
        rows = client.result(
            "crm.contact.list",
            {"filter": {"NAME": laudo.first_name, "LAST_NAME": laudo.last_name}, "select": select},
        ) or []
        return [r for r in rows if _birth_of(r) == laudo.birth_date]
    return []


def _fill_empty(existing: dict[str, Any], fields: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    update: dict[str, Any] = {}
    filled: list[str] = []
    skipped: list[str] = []
    for key, value in fields.items():
        if key in ("OPENED", "NAME", "LAST_NAME"):
            continue
        if key == "PHONE":
            current = existing.get("PHONE") or []
            new = [e for e in value if not any(same_phone(e["VALUE"], p.get("VALUE")) for p in current)]
            if new:
                update["PHONE"] = new
                filled.append("PHONE")
        elif key == "BIRTHDATE":
            if _is_empty(existing.get(key)):
                update[key] = value
                filled.append(key)
        elif _is_empty(existing.get(key)):
            update[key] = value
            filled.append(key)
        elif str(existing.get(key)).strip() != str(value).strip():
            skipped.append(key)
    return update, filled, skipped


def upsert_contact(client: Any, fmap: FieldMap, laudo: Laudo, cpf: str | None, *, dry_run: bool = False) -> ContactResult:
    fields, warnings = build_contact_fields(laudo, cpf, fmap)
    candidates = find_contacts(client, fmap, laudo, cpf)
    if len(candidates) > 1:
        ids = ", ".join(str(c["ID"]) for c in candidates)
        return ContactResult(None, "ambiguous", warnings=[*warnings, f"{len(candidates)} contatos candidatos ({ids}); nada foi gravado"])

    expected_all = {k: v for k, v in fields.items() if k != "OPENED"}
    if not candidates:
        if dry_run:
            return ContactResult(None, "dry_run_create", filled=list(expected_all), warnings=warnings, expected=expected_all)
        new_id = int(client.result("crm.contact.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}}))
        return ContactResult(new_id, "created", filled=list(expected_all), warnings=warnings, expected=expected_all)

    existing = candidates[0]
    contact_id = int(existing["ID"])
    update, filled, skipped = _fill_empty(existing, fields)
    if not update:
        return ContactResult(contact_id, "unchanged", skipped=skipped, warnings=warnings)
    if dry_run:
        return ContactResult(contact_id, "dry_run_update", filled=filled, skipped=skipped, warnings=warnings, expected=update)
    client.result("crm.contact.update", {"id": contact_id, "fields": update, "params": {"REGISTER_SONET_EVENT": "N"}})
    return ContactResult(contact_id, "updated", filled=filled, skipped=skipped, warnings=warnings, expected=update)
