"""Mapa de campos do Bitrix (chaves validadas ao vivo) e verificação contra os metadados do portal.

As chaves abaixo foram confirmadas em `crm.contact.fields` e `crm.item.fields` (SPA 1146, tipo 53). A cada
inicialização o app confere se elas ainda existem com o tipo esperado, em vez de falhar no meio de uma gravação.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..config import Settings

# nome lógico -> chave Bitrix
DEFAULT_CONTACT_KEYS: Mapping[str, str] = {
    "mother": "UF_CRM_1729515037979",  # Nome da Mãe
    "city": "UF_CRM_1753903279519",  # Cidade - Texto -
    "state": "UF_CRM_1727815619072",  # Estado (lista)
    "cep": "UF_CRM_1725391645",  # CEP
    "cpf": "UF_CRM_1524762221558",  # CPF (gravado como NNN.NNN.NNN-NN)
}
DEFAULT_ITEM_KEYS: Mapping[str, str] = {
    "cns": "ufCrm53_1792335030647",
    "symptoms": "ufCrm53_1792335467",
    "justification": "ufCrm53_1792334983515",
    "exam_results": "ufCrm53_1792335000277",
    "initial_diagnosis": "ufCrm53_1792334925699",
    "cid": "ufCrm53_1792334945166",
    "procedure": "ufCrm53_1792335069220",
    "accident_date": "ufCrm53_1775651041283",
    "surgery": "ufCrm53_1775651057904",
    "limb": "ufCrm53_1775651072848",
    "sick_pay": "ufCrm53_1775651085742",
}
_ITEM_TEXT_TYPES = {"string", "rich_text"}


class FieldMapError(Exception):
    pass


@dataclass(frozen=True)
class FieldMap:
    entity_type_id: int
    category_id: int
    stage_id: str
    contact: Mapping[str, str]
    item: Mapping[str, str]
    state_ids: Mapping[str, str]  # "MT" -> ID do item da lista "Estado"


def _check_contact(meta: Mapping[str, Any], problems: list[str]) -> dict[str, str]:
    state_ids: dict[str, str] = {}
    for logical, key in DEFAULT_CONTACT_KEYS.items():
        field = meta.get(key)
        if field is None:
            problems.append(f"campo de contato ausente no Bitrix: {key} ({logical})")
            continue
        expected = "enumeration" if logical == "state" else "string"
        if field.get("type") != expected:
            problems.append(f"campo de contato {key} ({logical}) deveria ser {expected}, mas é {field.get('type')}")
        elif logical == "state":
            for entry in field.get("items") or []:
                state_ids[str(entry["VALUE"]).strip().upper()] = str(entry["ID"])
    return state_ids


def _check_item(meta: Mapping[str, Any], problems: list[str]) -> None:
    for logical, key in DEFAULT_ITEM_KEYS.items():
        field = meta.get(key)
        if field is None:
            problems.append(f"campo da SPA ausente no Bitrix: {key} ({logical})")
        elif field.get("type") not in _ITEM_TEXT_TYPES:
            problems.append(f"campo da SPA {key} ({logical}) deveria ser texto, mas é {field.get('type')}")


def _resolve_pipeline(client: Any, settings: Settings, problems: list[str]) -> tuple[int, str]:
    etid = settings.entity_type_id
    categories = (client.result("crm.category.list", {"entityTypeId": etid}) or {}).get("categories", [])
    ids = [int(c["id"]) for c in categories]
    if settings.category_id is not None:
        if categories and settings.category_id not in ids:
            problems.append(f"categoria {settings.category_id} não existe na SPA {etid} (existentes: {ids})")
        category = settings.category_id
    elif categories:
        category = int(next((c for c in categories if c.get("isDefault") == "Y"), categories[0])["id"])
    else:
        category = 0
    stages = client.result("crm.status.list", {"filter": {"ENTITY_ID": f"DYNAMIC_{etid}_STAGE_{category}"}}) or []
    stage_ids = [s["STATUS_ID"] for s in sorted(stages, key=lambda s: int(s.get("SORT") or 0))]
    if settings.stage_id:
        if stage_ids and settings.stage_id not in stage_ids:
            problems.append(f"etapa {settings.stage_id} não existe na categoria {category}")
        return category, settings.stage_id
    if not stage_ids:
        problems.append(f"a categoria {category} não tem etapas")
        return category, ""
    return category, stage_ids[0]


def load_field_map(client: Any, settings: Settings) -> FieldMap:
    problems: list[str] = []
    state_ids = _check_contact(client.result("crm.contact.fields") or {}, problems)
    item_meta = (client.result("crm.item.fields", {"entityTypeId": settings.entity_type_id}) or {}).get("fields", {})
    _check_item(item_meta, problems)
    category, stage = _resolve_pipeline(client, settings, problems)
    if problems:
        raise FieldMapError("; ".join(problems))
    return FieldMap(
        entity_type_id=settings.entity_type_id,
        category_id=category,
        stage_id=stage,
        contact=dict(DEFAULT_CONTACT_KEYS),
        item=dict(DEFAULT_ITEM_KEYS),
        state_ids=state_ids,
    )
