from __future__ import annotations

from aih_bitrix.bitrix.contacts import build_contact_fields, upsert_contact
from aih_bitrix.models import Laudo
from aih_bitrix.phones import same_phone

MOTHER, CITY, STATE, CEP, CPF = (
    "UF_CRM_1729515037979", "UF_CRM_1753903279519", "UF_CRM_1727815619072", "UF_CRM_1725391645", "UF_CRM_1524762221558",
)
CPF_DIGITS = "12345678909"


def seed_same_person(fake, **extra):
    base = {"NAME": "Marcos", "LAST_NAME": "De Oliveira", "BIRTHDATE": "1975-05-10", CPF: "123.456.789-09"}
    base.update(extra)
    return fake.seed_contact(**base)


def with_fields(laudo: Laudo, **changes: str) -> Laudo:
    return Laudo(fields={**laudo.fields, **changes}, source_name=laudo.source_name)


# ---------------------------------------------------------------- montagem dos campos

def test_build_contact_fields_maps_the_laudo(laudo, fmap):
    fields, warnings = build_contact_fields(laudo, CPF_DIGITS, fmap)
    assert fields["NAME"] == "MARCOS" and fields["LAST_NAME"] == "DE OLIVEIRA" and fields["OPENED"] == "Y"
    assert fields["BIRTHDATE"] == "1975-05-10"
    assert fields[MOTHER] == "HELENA DA COSTA OLIVEIRA" and fields[CITY] == "CUIABÁ"
    assert fields[STATE] == "377" and fields[CEP] == "78000000" and fields[CPF] == "123.456.789-09"
    assert "PHONE" not in fields and any("telefone" in w.lower() for w in warnings)


def test_real_phone_goes_as_a_work_multifield(laudo_real_phone, fmap):
    fields, _ = build_contact_fields(laudo_real_phone, None, fmap)
    assert fields["PHONE"] == [{"VALUE": "(65) 99123-4567", "VALUE_TYPE": "WORK"}]
    assert CPF not in fields


def test_unknown_uf_and_single_token_name_become_warnings(laudo, fmap):
    fields, warnings = build_contact_fields(with_fields(laudo, uf="ZZ", patient_name="MADONNA"), None, fmap)
    assert STATE not in fields and "LAST_NAME" not in fields and fields["NAME"] == "MADONNA"
    assert any("ZZ" in w for w in warnings) and any("sobrenome" in w for w in warnings)


# ---------------------------------------------------------------- criação

def test_create_new_contact(fake, fmap, laudo):
    result = upsert_contact(fake, fmap, laudo, CPF_DIGITS)
    stored = fake.contacts[result.id]
    assert result.action == "created"
    assert stored[STATE] == "377" and stored[CPF] == "123.456.789-09" and stored[CEP] == "78000000"
    assert stored["NAME"] == "Marcos" and stored[MOTHER] == "Helena Da Costa Oliveira"  # BP aplica Title Case
    assert not stored["PHONE"]  # telefone placeholder nunca é gravado
    assert result.expected["NAME"] == "MARCOS" and "OPENED" not in result.expected
    assert fake.writes[0][1]["params"] == {"REGISTER_SONET_EVENT": "N"}


def test_dry_run_writes_nothing(fake, fmap, laudo):
    assert upsert_contact(fake, fmap, laudo, CPF_DIGITS, dry_run=True).action == "dry_run_create"
    seed_same_person(fake)
    assert upsert_contact(fake, fmap, laudo, CPF_DIGITS, dry_run=True).action == "dry_run_update"
    assert fake.writes == []


# ---------------------------------------------------------------- localizar existente

def test_existing_contact_found_by_formatted_cpf_gets_only_its_empty_fields_filled(fake, fmap, laudo):
    cid = seed_same_person(fake, **{CEP: "11111111"}, PHONE=[{"VALUE": "(65) 98888-7777", "VALUE_TYPE": "MOBILE"}])
    phones_before = len(fake.contacts[cid]["PHONE"])
    result = upsert_contact(fake, fmap, laudo, CPF_DIGITS)
    stored = fake.contacts[cid]
    assert result.action == "updated" and result.id == cid
    assert stored[CEP] == "11111111" and CEP in result.skipped  # valor existente preservado
    assert stored[MOTHER].upper() == "HELENA DA COSTA OLIVEIRA" and stored[STATE] == "377"
    assert {MOTHER, CITY, STATE} <= set(result.filled) and CEP not in result.filled
    assert len(stored["PHONE"]) == phones_before  # placeholder não entra
    assert stored["NAME"] == "Marcos"  # nome existente nunca é sobrescrito


def test_contact_stored_with_digits_only_cpf_is_also_found(fake, fmap, laudo):
    cid = seed_same_person(fake, **{CPF: CPF_DIGITS})
    assert upsert_contact(fake, fmap, laudo, CPF_DIGITS).id == cid
    assert len(fake.contacts) == 1


def test_two_contacts_with_the_same_cpf_and_name_are_ambiguous_and_nothing_is_written(fake, fmap, laudo):
    seed_same_person(fake)
    seed_same_person(fake)
    result = upsert_contact(fake, fmap, laudo, CPF_DIGITS)
    assert result.action == "ambiguous" and result.id is None and fake.writes == []


def test_cpf_collision_is_resolved_by_the_exact_name(fake, fmap, laudo):
    wanted = seed_same_person(fake)
    fake.seed_contact(NAME="Fulano", LAST_NAME="Outro", **{CPF: "123.456.789-09"})
    assert upsert_contact(fake, fmap, laudo, CPF_DIGITS).id == wanted


def test_without_cpf_the_match_is_name_plus_birthdate(fake, fmap, laudo):
    cid = fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", BIRTHDATE="1975-05-10")
    result = upsert_contact(fake, fmap, laudo, None)
    assert result.id == cid and result.action == "updated" and CPF not in fake.contacts[cid]


def test_same_name_but_different_birthdate_is_a_different_person(fake, fmap, laudo):
    other = fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", BIRTHDATE="1990-01-01")
    result = upsert_contact(fake, fmap, laudo, None)
    assert result.action == "created" and result.id != other


def test_cpf_not_found_falls_back_to_name_plus_birthdate_and_fills_the_cpf(fake, fmap, laudo):
    cid = fake.seed_contact(NAME="Marcos", LAST_NAME="De Oliveira", BIRTHDATE="1975-05-10")  # contato antigo sem CPF
    result = upsert_contact(fake, fmap, laudo, CPF_DIGITS)
    assert result.id == cid and fake.contacts[cid][CPF] == "123.456.789-09"


# ---------------------------------------------------------------- telefone

def test_real_phone_is_added_once_even_though_the_portal_expands_variants(fake, fmap, laudo_real_phone):
    first = upsert_contact(fake, fmap, laudo_real_phone, None)
    count = len(fake.contacts[first.id]["PHONE"])
    assert count >= 1
    second = upsert_contact(fake, fmap, laudo_real_phone, None)
    assert second.id == first.id and len(fake.contacts[first.id]["PHONE"]) == count
    assert "PHONE" not in (second.filled or [])


def test_an_equivalent_variant_already_on_file_is_not_added_again(fake, fmap, laudo_real_phone):
    cid = seed_same_person(fake, PHONE=[{"VALUE": "5565991234567", "VALUE_TYPE": "MOBILE"}])
    before = len(fake.contacts[cid]["PHONE"])
    upsert_contact(fake, fmap, laudo_real_phone, CPF_DIGITS)
    stored = fake.contacts[cid]["PHONE"]
    assert len(stored) == before and any(same_phone(p["VALUE"], "(65) 99123-4567") for p in stored)
