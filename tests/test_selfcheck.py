from __future__ import annotations

from aih_bitrix.bitrix.fieldmap import load_field_map
from aih_bitrix.config import Settings
from aih_bitrix.cpf import format_cpf
from aih_bitrix.phones import same_phone
from aih_bitrix.selfcheck import SAFE_PHONE, TEST_BASES, cpf_from_base, run_selfcheck
from conftest import WEBHOOK
from fakes import FakeBitrix

CPF_KEY = "UF_CRM_1524762221558"
SETTINGS = Settings(webhook_url=WEBHOOK, verify_delay=0)


def run(fake, fmap):
    return run_selfcheck(fake, fmap, SETTINGS, sleep=lambda s: None)


def test_selfcheck_passes_and_leaves_clearly_marked_synthetic_records(fake, fmap):
    report = run(fake, fmap)
    assert report.ok and all(c.ok for c in report.checks), [c for c in report.checks if not c.ok]
    assert len(fake.contacts) == 1 and len(fake.items) == 1 and len(fake.comments) == 1
    contact = fake.contacts[report.contact_id]
    item = fake.items[report.item_id]
    assert contact["NAME"] == "Teste" and contact["LAST_NAME"] == "Hermes Selfcheck"
    assert item["title"].startswith("[TESTE HERMES - NAO ATENDER]")  # a casa reescreve o título; o selfcheck o restaura
    assert any(same_phone(p["VALUE"], SAFE_PHONE) for p in contact["PHONE"])


def test_the_phone_is_added_only_after_the_card_so_no_creation_robot_ever_sees_it(fake, fmap):
    run(fake, fmap)
    order = [method for method, _ in fake.writes]
    first_phone_write = next(i for i, (m, p) in enumerate(fake.writes) if m == "crm.contact.update" and "PHONE" in p["fields"])
    assert order.index("crm.item.add") < first_phone_write
    add_payload = next(p for m, p in fake.writes if m == "crm.contact.add")
    assert "PHONE" not in add_payload["fields"]


def test_selfcheck_is_idempotent_and_reuses_its_own_records(fake, fmap):
    first = run(fake, fmap)
    second = run(fake, fmap)
    assert second.ok and (second.contact_id, second.item_id) == (first.contact_id, first.item_id)
    assert len(fake.contacts) == 1 and len(fake.items) == 1 and len(fake.comments) == 1


def test_selfcheck_never_touches_a_cpf_that_belongs_to_a_real_contact(fake, fmap):
    real = fake.seed_contact(NAME="Fulano", LAST_NAME="Real", **{CPF_KEY: format_cpf(cpf_from_base(TEST_BASES[0]))})
    report = run(fake, fmap)
    assert report.ok and report.contact_id != real
    assert fake.contacts[real]["NAME"] == "Fulano" and fake.contacts[real].get("PHONE") == []
    assert fake.contacts[report.contact_id][CPF_KEY] == format_cpf(cpf_from_base(TEST_BASES[1]))


def test_selfcheck_aborts_without_writing_when_every_candidate_cpf_is_taken(fake, fmap):
    for base in TEST_BASES:
        fake.seed_contact(NAME="Real", LAST_NAME=base, **{CPF_KEY: format_cpf(cpf_from_base(base))})
    report = run(fake, fmap)
    assert not report.ok and fake.writes == [] and fake.items == {}
    assert any("CPF de teste" in c.name and not c.ok for c in report.checks)


def test_selfcheck_reports_a_failed_check_instead_of_hiding_it(fmap):
    class WrongCns(FakeBitrix):
        def _m_crm_item_add(self, p):
            p["fields"]["ufCrm53_1792335030647"] = "000000000000000"  # o portal "adulterou" um campo
            return super()._m_crm_item_add(p)

    report = run(WrongCns(), fmap)
    assert not report.ok and any("card" in c.name and not c.ok for c in report.checks)


def test_selfcheck_never_calls_delete(fake, fmap):
    run(fake, fmap)  # o FakeBitrix levanta AssertionError em qualquer *.delete
    assert not any(method.endswith(".delete") for method, _ in fake.calls)


def test_the_field_map_used_by_the_selfcheck_is_the_validated_one(fake):
    fmap = load_field_map(fake, SETTINGS)
    assert fmap.category_id == 101 and fmap.stage_id == "DT1146_101:NEW"
