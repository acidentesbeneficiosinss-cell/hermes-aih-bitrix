"""Card da SPA "Leads Auxílio Acidente": idempotente por `xmlId`, completa só campos vazios, comenta uma vez.

O portal reescreve título, etapa e responsável de todo card novo (automação da casa), então o app grava
valores iniciais razoáveis para esses três, mas nunca depende deles (nem os audita).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..cpf import mask_cpfs_in_text, only_digits
from ..models import Inference, Laudo
from ..parser import fold
from .client import BitrixError
from .fieldmap import FieldMap

_CONFIDENCE_PT = {"high": "alta", "medium": "média", "low": "baixa"}
_INFERRED_LABELS = (
    ("accident_date", "Data do acidente"),
    ("limb", "Membro fraturado"),
    ("surgery", "Houve cirurgia"),
    ("sick_pay", "Recebeu auxílio-doença"),
)
MARKER = "AIH-IMPORT:"


@dataclass
class ItemResult:
    id: int | None
    action: str  # created | updated | unchanged | dry_run_create | dry_run_update
    xml_id: str
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expected: dict[str, Any] = field(default_factory=dict)


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def make_xml_id(laudo: Laudo, cpf: str | None) -> str:
    """Chave de idempotência: AIH-{CNS | CPF | hash}-{AAAAMMDD da solicitação}-{CID}."""
    ident = laudo.cns or (only_digits(cpf) if cpf else "")
    if not ident:
        ident = hashlib.sha1(f"{fold(laudo.patient_name)}|{laudo.get('birth_date')}".encode()).hexdigest()[:10]
    day = laudo.request_date.strftime("%Y%m%d") if laudo.request_date else "00000000"
    return f"AIH-{ident}-{day}-{laudo.cid or 'SEMCID'}"


def build_item_fields(laudo: Laudo, inferences: Mapping[str, Inference], fmap: FieldMap) -> dict[str, str]:
    values = {
        "cns": laudo.cns,
        "symptoms": laudo.get("symptoms"),
        "justification": laudo.get("justification"),
        "exam_results": laudo.get("exam_results"),
        "initial_diagnosis": laudo.get("initial_diagnosis"),
        "cid": laudo.get("cid_main"),
        "procedure": laudo.get("procedure_desc"),
    }
    for logical in ("accident_date", "surgery", "limb", "sick_pay"):
        if logical in inferences:
            values[logical] = inferences[logical].value
    return {fmap.item[logical]: value for logical, value in values.items() if value}


def build_title(laudo: Laudo, template: str) -> str:
    values = _SafeDict(
        nome=laudo.patient_name,
        cid=laudo.cid,
        data_solicitacao=laudo.request_date.strftime("%d/%m/%Y") if laudo.request_date else "",
        cns=laudo.cns,
        hospital=laudo.get("hospital_requester"),
    )
    try:
        text = template.format_map(values)
    except (ValueError, IndexError, KeyError, AttributeError):
        text = f"{laudo.patient_name} AIH {laudo.cid}"
    return re.sub(r"\s+", " ", text).strip()[:255]


def build_timeline_comment(laudo: Laudo, inferences: Mapping[str, Inference], xml_id: str, source_name: str) -> str:
    g = laudo.get

    def join(parts: list[str], sep: str = " | ") -> str:
        return sep.join(p for p in parts if p)

    lines = [f"[b]Laudo AIH importado automaticamente[/b] ({MARKER}{xml_id})"]
    if source_name:
        lines.append(f"Arquivo: {mask_cpfs_in_text(source_name)}")
    hospital = join([g("hospital_requester"), f"CNES {g('cnes_requester')}" if g("cnes_requester") else ""], " - ")
    rows = [
        ("Estabelecimento solicitante", hospital),
        ("Prontuário / sexo / CNS", join([g("medical_record"), g("sex"), laudo.cns])),
        ("Endereço", join([g("address"), join([laudo.city, laudo.uf], "/"), f"IBGE {g('ibge')}" if g("ibge") else "",
                           f"CEP {laudo.cep}" if laudo.cep else ""], ", ")),
        ("Procedimento", join([g("procedure_desc"), f"cód. {g('procedure_code')}" if g("procedure_code") else ""], " - ")),
        ("Clínica / caráter", join([g("clinic"), g("character")])),
        ("Profissional solicitante", join([g("requester_name"), join([g("requester_doc_type"), g("requester_doc_number")], " ")], " - ")),
        ("Data da solicitação", laudo.request_date.strftime("%d/%m/%Y") if laudo.request_date else ""),
    ]
    lines += [f"{label}: {value}" for label, value in rows if value]

    external = [name for key, name in (("accident_transit", "acidente de trânsito"),
                                       ("accident_work_typical", "acidente de trabalho típico"),
                                       ("accident_work_commute", "acidente de trabalho (trajeto)")) if g(key) == "SIM"]
    if g("social_security_bond"):
        external.append(f"vínculo previdenciário: {g('social_security_bond')}")
    if g("employer_cnpj"):
        external.append(f"CNPJ da empresa: {g('employer_cnpj')}")
    if external:
        lines.append("[b]Causas externas:[/b] " + "; ".join(external))

    lines.append("[b]Campos inferidos (conferir):[/b]")
    inferred = [f"- {label}: {inferences[key].value} [confiança {_CONFIDENCE_PT.get(inferences[key].confidence, '?')}] "
                f"({inferences[key].note})" for key, label in _INFERRED_LABELS if key in inferences]
    lines += inferred or ["- nenhum campo foi inferido"]
    missing = [label for key, label in _INFERRED_LABELS if key not in inferences]
    if missing:
        lines.append("Sem evidência no laudo (ficam vazios): " + ", ".join(missing))
    if laudo.unmatched:
        lines.append("Atenção: rótulos do PDF não reconhecidos (conferir o laudo): " + "; ".join(laudo.unmatched))
    return "\n".join(lines)


def _is_empty(value: Any) -> bool:
    return value is None or str(value).strip() in ("", "0")


def _ensure_comment(client: Any, fmap: FieldMap, item_id: int, text: str, xml_id: str, warnings: list[str]) -> None:
    entity = {"ENTITY_ID": item_id, "ENTITY_TYPE": f"dynamic_{fmap.entity_type_id}"}
    try:
        existing = client.result("crm.timeline.comment.list", {"filter": entity, "select": ["ID", "COMMENT"]}) or []
        if any(f"{MARKER}{xml_id}" in (c.get("COMMENT") or "") for c in existing):
            return
        client.result("crm.timeline.comment.add", {"fields": {**entity, "COMMENT": text}})
    except BitrixError as exc:  # o card já existe: o comentário é refeito na próxima execução
        warnings.append(f"comentário da timeline não gravado ({exc.code})")


def upsert_item(
    client: Any,
    fmap: FieldMap,
    laudo: Laudo,
    inferences: Mapping[str, Inference],
    contact_id: int | None,
    cpf: str | None,
    *,
    title_template: str,
    dry_run: bool = False,
) -> ItemResult:
    etid = fmap.entity_type_id
    xml_id = make_xml_id(laudo, cpf)
    uf_fields = build_item_fields(laudo, inferences, fmap)
    select = ["id", "xmlId", "contactId", "categoryId", *fmap.item.values()]
    found = (client.result("crm.item.list", {"entityTypeId": etid, "filter": {"xmlId": xml_id}, "select": select}) or {}).get("items", [])
    comment = build_timeline_comment(laudo, inferences, xml_id, laudo.source_name)
    warnings: list[str] = []

    if not found:
        expected: dict[str, Any] = {**uf_fields, "categoryId": fmap.category_id}
        if contact_id:
            expected["contactId"] = contact_id
        if dry_run:
            return ItemResult(None, "dry_run_create", xml_id, filled=list(uf_fields), expected=expected)
        fields: dict[str, Any] = {
            "title": build_title(laudo, title_template),
            "xmlId": xml_id,
            "categoryId": fmap.category_id,
            "stageId": fmap.stage_id,
            "opened": "Y",
            **uf_fields,
        }
        if contact_id:
            fields["contactId"] = contact_id
        created = client.result("crm.item.add", {"entityTypeId": etid, "fields": fields})
        item_id = int(created["item"]["id"])
        _ensure_comment(client, fmap, item_id, comment, xml_id, warnings)
        return ItemResult(item_id, "created", xml_id, filled=list(uf_fields), warnings=warnings, expected=expected)

    existing = found[0]
    if len(found) > 1:
        warnings.append(f"{len(found)} cards com o mesmo xmlId; usado o primeiro")
    item_id = int(existing["id"])
    update: dict[str, Any] = {}
    filled: list[str] = []
    skipped: list[str] = []
    for key, value in uf_fields.items():
        if _is_empty(existing.get(key)):
            update[key] = value
            filled.append(key)
        elif str(existing.get(key)).strip() != str(value).strip():
            skipped.append(key)
    if contact_id and _is_empty(existing.get("contactId")):
        update["contactId"] = contact_id
        filled.append("contactId")
    if dry_run:
        return ItemResult(item_id, "dry_run_update" if update else "unchanged", xml_id, filled, skipped, warnings, dict(update))
    if update:
        client.result("crm.item.update", {"entityTypeId": etid, "id": item_id, "fields": update})
    _ensure_comment(client, fmap, item_id, comment, xml_id, warnings)
    return ItemResult(item_id, "updated" if update else "unchanged", xml_id, filled, skipped, warnings, dict(update))
