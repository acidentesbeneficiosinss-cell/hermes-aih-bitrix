from __future__ import annotations

from aih_bitrix.bitrix.contacts import upsert_contact
from aih_bitrix.bitrix.items import build_item_fields, build_timeline_comment, build_title, make_xml_id, upsert_item
from aih_bitrix.bitrix.verify import verify_contact, verify_item
from aih_bitrix.config import DEFAULT_TITLE_TEMPLATE
from aih_bitrix.infer import infer_all
from aih_bitrix.models import Laudo

CPF_DIGITS = "12345678909"
K = {
    "cns": "ufCrm53_1792335030647", "symptoms": "ufCrm53_1792335467", "justification": "ufCrm53_1792334983515",
    "exam_results": "ufCrm53_1792335000277", "initial_diagnosis": "ufCrm53_1792334925699", "cid": "ufCrm53_1792334945166",
    "procedure": "ufCrm53_1792335069220", "accident_date": "ufCrm53_1775651041283", "surgery": "ufCrm53_1775651057904",
    "limb": "ufCrm53_1775651072848", "sick_pay": "ufCrm53_1775651085742",
}
XML_ID = "AIH-700000000000012-20260914-S821"


def new_contact(fake) -> int:
    return fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", BIRTHDATE="1975-05-10")


def run_upsert(fake, fmap, laudo, contact_id, **kw):
    return upsert_item(fake, fmap, laudo, infer_all(laudo), contact_id, CPF_DIGITS, title_template=DEFAULT_TITLE_TEMPLATE, **kw)


# ---------------------------------------------------------------- idempotência e montagem

def test_xml_id_prefers_cns_then_cpf_then_a_stable_hash(laudo):
    assert make_xml_id(laudo, CPF_DIGITS) == XML_ID
    no_cns = Laudo(fields={**laudo.fields, "cns": ""})
    assert make_xml_id(no_cns, CPF_DIGITS) == "AIH-12345678909-20260914-S821"
    anonymous = make_xml_id(no_cns, None)
    assert anonymous == make_xml_id(no_cns, None) and anonymous.endswith("-20260914-S821") and anonymous.startswith("AIH-")


def test_item_fields_for_the_model_laudo(laudo, fmap):
    f = build_item_fields(laudo, infer_all(laudo), fmap)
    assert f[K["cns"]] == "700000000000012" and f[K["cid"]] == "S821"
    assert f[K["symptoms"]].startswith("PACIENTE VEM AO PS RELATANDO QUEDA DE MOTOCICLETA")
    assert f[K["justification"]] == "PROPOSTA DE TRATAMENTO CIRURGICO" and f[K["exam_results"]] == "EXAME FÍSICO / RX"
    assert f[K["initial_diagnosis"]] == "Fratura da extremidade proximal da tíbia"
    assert f[K["procedure"]] == "TRATAMENTO CIRÚRGICO DE FRATURA DO PLANALTO TIBIAL"
    assert f[K["accident_date"]] == "14/09/2026" and f[K["surgery"]] == "Proposta cirúrgica"
    assert f[K["limb"]] == "Joelho esquerdo / Extremidade proximal da tíbia (Planalto tibial à esquerda)"
    assert K["sick_pay"] not in f  # campo 42 vazio e sem menção => fica vazio


def test_title_template_is_configurable_and_never_crashes(laudo):
    assert build_title(laudo, DEFAULT_TITLE_TEMPLATE) == "MARCOS DE OLIVEIRA ➡ AIH S821 ➡ 14/09/2026"
    assert build_title(laudo, "{nome} - {cns}") == "MARCOS DE OLIVEIRA - 700000000000012"
    assert build_title(laudo, "{nome} {campo_que_nao_existe}") == "MARCOS DE OLIVEIRA"


def test_timeline_comment_carries_the_unmapped_data_and_the_review_list(laudo):
    text = build_timeline_comment(laudo, infer_all(laudo), XML_ID, "CPF12345678909.pdf")
    for expected in ("AIH-IMPORT:" + XML_ID, "Arquivo: CPF***.***.***-09.pdf", "HOSPITAL GERAL DE EXEMPLO", "1234567",
                     "123456", "408050551", "FULANO DE TAL", "CIRÚRGICO", "URGENCIA", "14/09/2026", "Campos inferidos"):
        assert expected in text
    # o CPF (que vem no nome do arquivo pela convenção do escritório) não é repetido na timeline
    assert "123.456.789-09" not in text and "12345678909" not in text


# ---------------------------------------------------------------- criação / idempotência do card

def test_create_item_with_the_validated_payload_and_a_single_timeline_comment(fake, fmap, laudo):
    cid = new_contact(fake)
    result = run_upsert(fake, fmap, laudo, cid)
    assert result.action == "created" and result.xml_id == XML_ID
    payload = next(p for m, p in fake.writes if m == "crm.item.add")
    assert payload["entityTypeId"] == 1146
    sent = payload["fields"]
    assert sent["categoryId"] == 101 and sent["stageId"] == "DT1146_101:NEW" and sent["opened"] == "Y"
    assert sent["contactId"] == cid and sent["xmlId"] == XML_ID and sent["title"].startswith("MARCOS DE OLIVEIRA")
    stored = fake.items[result.id]  # a casa reescreve título/etapa/responsável: o app não depende disso
    assert stored["title"] != sent["title"] and stored["stageId"] != "DT1146_101:NEW"
    assert len(fake.comments) == 1 and ("AIH-IMPORT:" + XML_ID) in fake.comments[0]["COMMENT"]
    assert fake.comments[0]["ENTITY_TYPE"] == "dynamic_1146" and fake.comments[0]["ENTITY_ID"] == str(result.id)


def test_second_run_finds_the_item_by_xml_id_and_duplicates_nothing(fake, fmap, laudo):
    cid = new_contact(fake)
    first = run_upsert(fake, fmap, laudo, cid)
    second = run_upsert(fake, fmap, laudo, cid)
    assert second.id == first.id and second.action == "unchanged"
    assert len(fake.items) == 1 and len(fake.comments) == 1


def test_existing_item_only_gets_its_empty_fields_filled(fake, fmap, laudo):
    cid = new_contact(fake)
    item_id = fake.seed_item(xmlId=XML_ID, contactId=cid, **{K["cns"]: "700000000000012", K["cid"]: "S82.1"})
    result = run_upsert(fake, fmap, laudo, cid)
    stored = fake.items[item_id]
    assert result.action == "updated" and result.id == item_id
    assert stored[K["cid"]] == "S82.1" and K["cid"] in result.skipped  # edição manual preservada
    assert stored[K["symptoms"]].startswith("PACIENTE VEM") and K["symptoms"] in result.filled


def test_existing_item_without_a_contact_gets_it_linked(fake, fmap, laudo):
    cid = new_contact(fake)
    item_id = fake.seed_item(xmlId=XML_ID, contactId=None)
    run_upsert(fake, fmap, laudo, cid)
    assert fake.items[item_id]["contactId"] == cid


def test_item_dry_run_writes_nothing(fake, fmap, laudo):
    cid = new_contact(fake)
    assert run_upsert(fake, fmap, laudo, cid, dry_run=True).action == "dry_run_create"
    fake.seed_item(xmlId=XML_ID, contactId=cid)
    assert run_upsert(fake, fmap, laudo, cid, dry_run=True).action == "dry_run_update"
    assert fake.writes == [] and fake.comments == []


def test_expected_values_for_verification(fake, fmap, laudo):
    cid = new_contact(fake)
    result = run_upsert(fake, fmap, laudo, cid)
    assert result.expected[K["cns"]] == "700000000000012" and result.expected["contactId"] == cid
    assert result.expected["categoryId"] == 101 and "title" not in result.expected and "stageId" not in result.expected


# ---------------------------------------------------------------- verificação por re-leitura

def test_contact_verification_passes_despite_the_portal_title_casing(fake, fmap, laudo):
    result = upsert_contact(fake, fmap, laudo, CPF_DIGITS)
    checks = verify_contact(fake, result.id, result.expected, fmap)
    assert checks and all(c.ok for c in checks)
    assert fake.contacts[result.id]["NAME"] == "Marcos"  # o BP mudou a caixa; a auditoria tolera
    assert {c.field for c in checks} >= {"NAME", "LAST_NAME", "BIRTHDATE", "mother", "city", "state", "cep", "cpf"}


def test_contact_verification_detects_a_divergent_value(fake, fmap, laudo):
    result = upsert_contact(fake, fmap, laudo, CPF_DIGITS)
    fake.contacts[result.id]["UF_CRM_1725391645"] = "00000000"
    bad = [c for c in verify_contact(fake, result.id, result.expected, fmap) if not c.ok]
    assert [c.field for c in bad] == ["cep"] and bad[0].expected == "78000000" and bad[0].got == "00000000"


def test_phone_verification_accepts_the_variants_the_portal_adds(fake, fmap, laudo_real_phone):
    result = upsert_contact(fake, fmap, laudo_real_phone, None)
    phone = [c for c in verify_contact(fake, result.id, result.expected, fmap) if c.field == "PHONE"]
    assert phone and phone[0].ok and len(fake.contacts[result.id]["PHONE"]) > 1


def test_item_verification_ignores_title_stage_and_assignee_rewritten_by_the_portal(fake, fmap, laudo):
    cid = new_contact(fake)
    result = run_upsert(fake, fmap, laudo, cid)
    checks = verify_item(fake, result.id, result.expected, fmap)
    assert checks and all(c.ok for c in checks)
    assert {"title", "stageId", "assignedById"}.isdisjoint({c.field for c in checks})


def test_item_verification_detects_a_missing_value(fake, fmap, laudo):
    cid = new_contact(fake)
    result = run_upsert(fake, fmap, laudo, cid)
    fake.items[result.id][K["symptoms"]] = ""
    bad = [c for c in verify_item(fake, result.id, result.expected, fmap) if not c.ok]
    assert [c.field for c in bad] == ["symptoms"]
