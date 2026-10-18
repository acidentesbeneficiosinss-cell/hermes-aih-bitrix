from __future__ import annotations

import pytest

from aih_bitrix.infer import infer_accident_date, infer_all, infer_limb, infer_sick_pay, infer_surgery
from aih_bitrix.models import Laudo
from aih_bitrix.parser import parse_laudo
from aih_bitrix.pdf_text import read_pages

SAMPLE_SYMPTOMS = (
    "PACIENTE VEM AO PS RELATANDO QUEDA DE MOTOCICLETA HÁ ALGUMAS HORAS, EVOLUINDO COM DOR INTENSA EM "
    "JOELHO ESQUERDO. AO EXAME: BEG, LÚCIDO, EDEMA 2+/4+ EM JOELHO ESQ, LIMITAÇÃO FUNCIONAL, SEM DEFICIT "
    "NEUROVASCULAR. RX SUGESTIVO DE FRATURA DE PLANALTO TIBIAL ESQUERDO - SCHATZKER II. TC SOLICITADA "
    "PARA PLANEJAMENTO CIRÚRGICO, AGUARDANDO REALIZAÇÃO DO EXAME."
)


def L(**fields: str) -> Laudo:
    return Laudo(fields=fields)


# ---------- data do acidente ----------

def accident(text: str, request: str = "14/09/2026"):
    return infer_accident_date(L(symptoms=text, request_date=request))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("QUEDA HÁ ALGUMAS HORAS", "14/09/2026"),
        ("QUEDA HA POUCAS HORAS", "14/09/2026"),
        ("QUEDA HÁ 3 HORAS", "14/09/2026"),
        ("SOFREU QUEDA HOJE PELA MANHÃ", "14/09/2026"),
        ("QUEDA ONTEM À NOITE", "13/09/2026"),
        ("ATROPELADO ANTEONTEM", "12/09/2026"),
        ("QUEDA HÁ 3 DIAS", "11/09/2026"),
        ("QUEDA HÁ DOIS DIAS", "12/09/2026"),
        ("QUEDA HÁ 2 SEMANAS", "31/08/2026"),
        ("QUEDA HÁ 1 MÊS", "14/08/2026"),
        ("QUEDA HÁ 3 MESES", "14/06/2026"),
        ("EM 10/08/2026 SOFREU QUEDA", "10/08/2026"),
        ("NO DIA 10/08 SOFREU QUEDA", "10/08/2026"),
    ],
)
def test_accident_date_from_the_clinical_story(text, expected):
    assert accident(text).value == expected


def test_accident_date_confidence_and_vague_cases():
    assert accident("QUEDA HÁ ALGUMAS HORAS").confidence == "high"
    assert accident("QUEDA HÁ 2 SEMANAS").confidence == "medium"
    vague = accident("QUEDA HÁ ALGUNS DIAS")
    assert vague.value == "Até 14/09/2026" and vague.confidence == "low"


def test_accident_date_month_arithmetic_clamps_the_day():
    assert accident("QUEDA HÁ 1 MÊS", request="31/03/2026").value == "28/02/2026"


def test_accident_date_absent_without_time_clue_or_request_date():
    assert accident("DOR LOMBAR CRÔNICA") is None
    assert infer_accident_date(L(symptoms="QUEDA ONTEM")) is None


def test_accident_date_absolute_date_after_request_means_previous_year():
    assert accident("QUEDA EM 20/12", request="14/09/2026").value == "20/12/2025"


# ---------- membro fraturado ----------

def test_limb_for_the_model_laudo():
    laudo = L(
        symptoms=SAMPLE_SYMPTOMS,
        initial_diagnosis="Fratura da extremidade proximal da tíbia",
        cid_main="S821",
        procedure_desc="TRATAMENTO CIRÚRGICO DE FRATURA DO PLANALTO TIBIAL",
    )
    inf = infer_limb(laudo)
    assert inf.value == "Joelho esquerdo / Extremidade proximal da tíbia (Planalto tibial à esquerda)"
    assert inf.confidence == "high"


def test_limb_other_region_and_side():
    laudo = L(
        symptoms="CAIU DA BICICLETA, DOR EM OMBRO DIREITO",
        initial_diagnosis="Fratura da clavícula",
        cid_main="S420",
        procedure_desc="TRATAMENTO CIRÚRGICO DE FRATURA DA CLAVÍCULA",
    )
    assert infer_limb(laudo).value == "Ombro direito / Clavícula"


def test_limb_falls_back_to_the_cid_chapter_when_the_story_names_no_region():
    laudo = L(
        symptoms="QUEDA, DOR NO LADO DIREITO",
        initial_diagnosis="Fratura da extremidade distal do radio",
        cid_main="S525",
    )
    inf = infer_limb(laudo)
    assert inf.value == "Antebraço direito / Extremidade distal do radio" and inf.confidence == "medium"


def test_limb_absent_without_any_anatomical_clue():
    assert infer_limb(L(symptoms="MAL ESTAR GERAL")) is None


# ---------- cirurgia ----------

@pytest.mark.parametrize(
    "fields, expected",
    [
        ({"justification": "PROPOSTA DE TRATAMENTO CIRURGICO", "clinic": "CIRÚRGICO"}, "Proposta cirúrgica"),
        ({"procedure_desc": "TRATAMENTO CIRÚRGICO DE FRATURA DO PLANALTO TIBIAL"}, "Proposta cirúrgica"),
        ({"symptoms": "PÓS-OPERATÓRIO DE OSTEOSSÍNTESE DE TÍBIA"}, "Sim"),
        ({"symptoms": "FOI OPERADO HÁ DOIS DIAS", "justification": "PROPOSTA DE REOPERAÇÃO"}, "Sim"),
        ({"symptoms": "TRATAMENTO CONSERVADOR COM IMOBILIZAÇÃO"}, "Não"),
    ],
)
def test_surgery(fields, expected):
    assert infer_surgery(L(**fields)).value == expected


def test_surgery_unknown_is_none():
    assert infer_surgery(L(symptoms="DOR")) is None


# ---------- auxílio-doença ----------

def test_sick_pay_only_with_an_explicit_mention():
    assert infer_sick_pay(L(social_security_bond="")) is None
    assert infer_sick_pay(L(social_security_bond="Empregado")) is None  # vínculo não é benefício
    assert infer_sick_pay(L(symptoms="PACIENTE EM USO DE AUXÍLIO-DOENÇA")).value == "Sim"
    assert infer_sick_pay(L(symptoms="NÃO RECEBE AUXÍLIO DOENÇA")).value == "Não"


# ---------- conjunto ----------

def test_infer_all_for_the_model_pdf(sample_pdf_bytes):
    laudo = parse_laudo(read_pages(sample_pdf_bytes), "CPF12345678909.pdf")
    result = infer_all(laudo)
    assert set(result) == {"accident_date", "limb", "surgery"}  # auxílio-doença: campo 42 vazio => sem inferência
    assert result["accident_date"].value == "14/09/2026"
    assert result["limb"].value == "Joelho esquerdo / Extremidade proximal da tíbia (Planalto tibial à esquerda)"
    assert result["surgery"].value == "Proposta cirúrgica"
