"""Autoteste ao vivo: valida a integração com o Bitrix usando registros SINTÉTICOS e isolados.

Garantias (aprendidas ao validar no portal real):
  * nunca apaga nada (a lixeira da SPA é desligada: apagar seria definitivo);
  * só usa um CPF de teste que NÃO pertença a contato real (o clássico 111.444.777-35, por exemplo, já era de um);
  * o telefone de teste (não roteável) só é adicionado DEPOIS de criar o card, para nenhum robô de criação
    (SMS/e-mail) enxergá-lo;
  * a casa reescreve o título do card novo, então a marca "[TESTE HERMES - NAO ATENDER]" é restaurada
    (somente neste card sintético) para a equipe não atender o lead falso;
  * idempotente: rodar de novo reaproveita os mesmos registros.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .bitrix.contacts import build_contact_fields, upsert_contact
from .bitrix.fieldmap import FieldMap
from .bitrix.items import MARKER, build_item_fields, make_xml_id, upsert_item
from .bitrix.verify import verify_contact, verify_item
from .config import Settings
from .cpf import complete_cpf, format_cpf, mask_cpf, only_digits
from .infer import infer_all
from .models import Laudo
from .parser import fold
from .phones import same_phone

TEST_BASES = ("987654321", "123456789", "529982247", "111222333", "246813579", "135792468")
TEST_NAME = "TESTE HERMES SELFCHECK"
SAFE_PHONE = "(65) 90000-0000"  # fora do plano de numeração: só valida o formato do multicampo
TITLE_MARK = "[TESTE HERMES - NAO ATENDER]"


def cpf_from_base(base9: str) -> str:
    return complete_cpf(base9)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class SelfcheckReport:
    checks: list[Check] = field(default_factory=list)
    contact_id: int | None = None
    item_id: int | None = None

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def _test_laudo() -> Laudo:
    return Laudo(
        fields={
            "hospital_requester": "HOSPITAL TESTE HERMES", "cnes_requester": "0000000", "patient_name": TEST_NAME,
            "medical_record": "000000", "cns": "700000000000099", "birth_date": "01/01/1990", "sex": "MASCULINO",
            "mother_name": "MAE DE TESTE HERMES", "address": "RUA DE TESTE, 0", "city": "CUIABÁ", "ibge": "510340",
            "uf": "MT", "cep": "78000000",
            "symptoms": "TESTE AUTOMATICO DO APP HERMES: QUEDA ONTEM COM DOR EM JOELHO ESQUERDO. RX SUGESTIVO DE FRATURA DE PLANALTO TIBIAL.",
            "justification": "PROPOSTA DE TRATAMENTO CIRURGICO", "exam_results": "RX",
            "initial_diagnosis": "Fratura da extremidade proximal da tibia", "cid_main": "S821",
            "procedure_desc": "TRATAMENTO CIRURGICO DE FRATURA DO PLANALTO TIBIAL", "procedure_code": "408050551",
            "clinic": "CIRURGICO", "character": "URGENCIA", "requester_name": "DR TESTE", "request_date": "18/10/2026",
        },
        source_name="selfcheck",
    )


def _contacts_with_cpf(client: Any, fmap: FieldMap, cpf: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for value in (format_cpf(cpf), only_digits(cpf)):
        rows = client.result("crm.contact.list", {"filter": {fmap.contact["cpf"]: value}, "select": ["ID", "NAME", "LAST_NAME"]}) or []
        for row in rows:
            found.setdefault(str(row["ID"]), row)
    return list(found.values())


def _is_test_contact(row: dict[str, Any]) -> bool:
    return fold(f"{row.get('NAME', '')} {row.get('LAST_NAME', '')}").split() == fold(TEST_NAME).split()


def _pick_test_cpf(client: Any, fmap: FieldMap) -> str | None:
    for base in TEST_BASES:
        cpf = cpf_from_base(base)
        rows = _contacts_with_cpf(client, fmap, cpf)
        if all(_is_test_contact(r) for r in rows):  # livre (sem linhas) ou de um selfcheck anterior
            return cpf
    return None


def run_selfcheck(client: Any, fmap: FieldMap, settings: Settings, *, sleep: Callable[[float], None] = time.sleep) -> SelfcheckReport:
    report = SelfcheckReport()

    def add(name: str, ok: bool, detail: str = "") -> None:
        report.checks.append(Check(name, ok, detail))

    cpf = _pick_test_cpf(client, fmap)
    if cpf is None:
        add("CPF de teste livre", False, "todos os CPFs de teste candidatos pertencem a contatos reais; nada foi gravado")
        return report
    add("CPF de teste livre (ou de um selfcheck anterior)", True, mask_cpf(cpf))

    laudo = _test_laudo()
    inferences = infer_all(laudo)
    etid = fmap.entity_type_id

    contact = upsert_contact(client, fmap, laudo, cpf)
    report.contact_id = contact.id
    add("contato criado ou reaproveitado", bool(contact.id) and contact.action in ("created", "updated", "unchanged"), contact.action)
    if not contact.id:
        return report
    sleep(settings.verify_delay)  # o BP #425 do portal roda de forma assíncrona
    expected_contact = {k: v for k, v in build_contact_fields(laudo, cpf, fmap)[0].items() if k != "OPENED"}
    checks = verify_contact(client, contact.id, expected_contact, fmap)
    add(f"contato: {len(checks)} campo(s) conferido(s) depois do BP do portal", all(c.ok for c in checks),
        ", ".join(c.field for c in checks if not c.ok))

    title_template = TITLE_MARK + " {nome} ➡ AIH {cid}"
    item = upsert_item(client, fmap, laudo, inferences, contact.id, cpf, title_template=title_template)
    report.item_id = item.id
    add("card criado ou reaproveitado", bool(item.id) and item.action in ("created", "updated", "unchanged"), item.action)
    if not item.id:
        return report
    sleep(settings.verify_delay)  # robôs da casa reescrevem título/etapa/responsável logo após a criação
    current = (client.result("crm.item.get", {"entityTypeId": etid, "id": item.id}) or {}).get("item", {})
    if not str(current.get("title", "")).startswith(TITLE_MARK):
        client.result("crm.item.update", {"entityTypeId": etid, "id": item.id,
                                          "fields": {"title": f"{TITLE_MARK} {laudo.patient_name} ➡ AIH {laudo.cid}"}})
    expected_item = {**build_item_fields(laudo, inferences, fmap), "contactId": contact.id, "categoryId": fmap.category_id}
    checks = verify_item(client, item.id, expected_item, fmap)
    add(f"card: {len(checks)} campo(s) conferido(s)", all(c.ok for c in checks), ", ".join(c.field for c in checks if not c.ok))
    marked = (client.result("crm.item.get", {"entityTypeId": etid, "id": item.id}) or {}).get("item", {})
    add("card marcado como teste no título", str(marked.get("title", "")).startswith(TITLE_MARK))

    entry = {"VALUE": SAFE_PHONE, "VALUE_TYPE": "WORK"}
    have = (client.result("crm.contact.get", {"id": contact.id}) or {}).get("PHONE") or []
    if not any(same_phone(SAFE_PHONE, p.get("VALUE")) for p in have):
        client.result("crm.contact.update", {"id": contact.id, "fields": {"PHONE": [entry]}, "params": {"REGISTER_SONET_EVENT": "N"}})
        sleep(settings.verify_delay)
    phone_checks = verify_contact(client, contact.id, {"PHONE": [entry]}, fmap)
    add("telefone (multicampo WORK) gravado depois do card e reconhecido como equivalente", all(c.ok for c in phone_checks))

    xml_id = make_xml_id(laudo, cpf)
    comments = client.result("crm.timeline.comment.list",
                             {"filter": {"ENTITY_ID": item.id, "ENTITY_TYPE": f"dynamic_{etid}"}, "select": ["ID", "COMMENT"]}) or []
    add("timeline: exatamente um comentário do laudo", sum(1 for c in comments if f"{MARKER}{xml_id}" in (c.get("COMMENT") or "")) == 1)

    again_contact = upsert_contact(client, fmap, laudo, cpf)
    again_item = upsert_item(client, fmap, laudo, inferences, contact.id, cpf, title_template=title_template)
    duplicates = (client.result("crm.item.list", {"entityTypeId": etid, "filter": {"xmlId": xml_id}, "select": ["id"]}) or {}).get("items", [])
    add("idempotência: repetir o processamento não duplica nem altera nada",
        again_contact.action == "unchanged" and again_item.action == "unchanged" and len(duplicates) == 1,
        f"contato {again_contact.action}, card {again_item.action}, cards com o mesmo xmlId: {len(duplicates)}")
    return report
