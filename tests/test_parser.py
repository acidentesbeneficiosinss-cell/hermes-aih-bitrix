from __future__ import annotations

from datetime import date

import pytest

from aih_bitrix.parser import NotAihError, parse_laudo
from aih_bitrix.pdf_text import read_pages
from aih_bitrix.synthetic import layout_b_page, layout_b_pdf, make_pdf


def parse(pdf: bytes, name: str = ""):
    return parse_laudo(read_pages(pdf), name)


# ---------- layout A: o PDF modelo do usuário (formulário antigo, 49 campos, rótulos em CAIXA ALTA) ----------

def test_model_pdf_all_fields(sample_pdf_bytes):
    f = parse(sample_pdf_bytes, "CPF12345678909.pdf").get
    assert f("hospital_requester") == "HOSPITAL GERAL DE EXEMPLO" and f("cnes_requester") == "1234567"
    assert f("hospital_executor") == "" and f("cnes_executor") == ""
    assert f("patient_name") == "MARCOS DE OLIVEIRA" and f("medical_record") == "123456"
    assert f("cns") == "700000000000012" and f("birth_date") == "10/05/1975" and f("sex") == "MASCULINO"
    assert f("mother_name") == "HELENA DA COSTA OLIVEIRA" and f("phone") == "(65) 999999999"
    assert f("address") == "RUA DAS PALMEIRAS, 100, CENTRO"
    assert f("city") == "CUIABÁ" and f("ibge") == "5103403" and f("uf") == "MT" and f("cep") == "78000000"
    assert f("symptoms").startswith("PACIENTE VEM AO PS RELATANDO QUEDA DE MOTOCICLETA")
    assert f("symptoms").endswith("AGUARDANDO REALIZAÇÃO DO EXAME.") and "\n" not in f("symptoms")
    assert f("justification") == "PROPOSTA DE TRATAMENTO CIRURGICO" and f("exam_results") == "EXAME FÍSICO / RX"
    assert f("initial_diagnosis") == "Fratura da extremidade proximal da tíbia" and f("cid_main") == "S821"
    assert f("cid_secondary") == "" and f("cid_associated") == ""
    assert f("procedure_desc") == "TRATAMENTO CIRÚRGICO DE FRATURA DO PLANALTO TIBIAL"
    assert f("procedure_code") == "408050551" and f("clinic") == "CIRÚRGICO" and f("character") == "URGENCIA"
    assert f("requester_doc_type") == "CNS" and f("requester_doc_number") == "700000000000044"
    assert f("requester_name") == "FULANO DE TAL" and f("request_date") == "14/09/2026"
    assert f("social_security_bond") == ""


def test_model_pdf_typed_accessors(sample_pdf_bytes):
    laudo = parse(sample_pdf_bytes)
    assert laudo.birth_date == date(1975, 5, 10) and laudo.request_date == date(2026, 9, 14)
    assert (laudo.first_name, laudo.last_name) == ("MARCOS", "DE OLIVEIRA")
    assert laudo.cns == "700000000000012" and laudo.cep == "78000000" and laudo.uf == "MT"


def test_section_headers_never_leak_into_values(sample_pdf_bytes):
    for value in parse(sample_pdf_bytes).fields.values():
        assert "IDENTIFICAÇÃO" not in value.upper() and "JUSTIFICATIVA" not in value.upper()
        assert "CAUSAS EXTERNAS" not in value.upper()


# ---------- layout B: AIH real (formulário novo, 52 campos, Title Case, 2 páginas idênticas) ----------

def test_layout_b_all_fields():
    f = parse(layout_b_pdf()).get
    assert f("hospital_requester") == "HOSPITAL TESTE DE EXEMPLO - CENTRO" and f("cnes_requester") == "1234567"
    assert f("cnes_executor") == "" and f("hospital_executor") == ""
    assert f("patient_name") == "MARIA APARECIDA TESTE" and f("medical_record") == "998877"
    assert f("cns") == "700000000000001" and f("birth_date") == "12/03/1985" and f("sex") == "FEMININO"
    assert f("race") == "PARDA" and f("mother_name") == "JOANA TESTE DA SILVA" and f("phone") == "(65) 99876-5432"
    assert f("guardian_name") == "" and f("guardian_phone") == ""
    assert f("address") == "RUA DAS FLORES, 100, CENTRO"
    assert f("city") == "VARZEA GRANDE" and f("ibge") == "5108402" and f("uf") == "MT" and f("cep") == "78110000"
    assert f("symptoms") == (
        "PACIENTE REFERE QUEDA DE MOTOCICLETA ONTEM A NOITE COM DOR INTENSA EM PUNHO DIREITO. "
        "RX COM FRATURA DE RADIO DISTAL DIREITO. SEM DEFICIT NEUROVASCULAR."
    )
    assert f("justification") == "NECESSIDADE DE TRATAMENTO CIRURGICO" and f("exam_results") == "RX DE PUNHO DIREITO"
    assert f("initial_diagnosis") == "Fratura da extremidade distal do radio"
    assert f("cid_main") == "S525" and f("cid_secondary") == "" and f("cid_associated") == "V294"
    assert f("procedure_desc") == "TRATAMENTO CIRURGICO DE FRATURA DA EXTREMIDADE DISTAL DO RADIO"
    assert f("procedure_code") == "0408020059" and f("clinic") == "CIRURGICO" and f("character") == "URGENCIA"
    assert f("requester_doc_type") == "CNS" and f("requester_doc_number") == "700000000000002"
    assert f("requester_name") == "DR FULANO DE TAL" and f("request_date") == "20/09/2026"


def test_layout_b_values_are_matched_by_label_text_not_by_number():
    # no formulário real o "Cnes" do executante vem numerado "2" (igual ao do solicitante): o texto decide
    f = parse(layout_b_pdf(values={"cnes_executor": "7654321", "hospital_executor": "HOSPITAL EXECUTOR TESTE"})).get
    assert f("cnes_requester") == "1234567" and f("cnes_executor") == "7654321"
    assert f("hospital_executor") == "HOSPITAL EXECUTOR TESTE"


def test_layout_b_page_two_is_a_copy_and_single_page_gives_same_result():
    assert parse(layout_b_pdf(pages=2)).fields == parse(layout_b_pdf(pages=1)).fields


def test_layout_b_stray_number_and_headers_do_not_pollute_fields():
    values = parse(layout_b_pdf()).fields.values()
    assert all("20260001" not in v for v in values)
    assert all("Identificação" not in v and "Justificativa" not in v and "Procedimento Solicitado" not in v for v in values)


def test_layout_b_checkboxes():
    f = parse(layout_b_pdf(checked_accidents=("accident_work_typical",), vinculo="Empregado", requester_doc_type="CPF")).get
    assert f("accident_work_typical") == "SIM" and f("accident_transit") == "" and f("accident_work_commute") == ""
    assert f("social_security_bond") == "Empregado"
    assert f("requester_doc_type") == "CPF" and f("authorizer_doc_type") == ""


def test_layout_b_populated_cells_in_the_interleaved_accident_region():
    # as linhas de acidente (coluna esquerda) se intercalam na vertical com os rótulos de CNPJ (colunas à direita)
    f = parse(layout_b_pdf(values={"insurer_cnpj": "11.222.333/0001-81", "employer_cnpj": "44.555.666/0001-02", "series": "AB"})).get
    assert f("insurer_cnpj") == "11.222.333/0001-81" and f("employer_cnpj") == "44.555.666/0001-02" and f("series") == "AB"
    assert f("accident_transit") == "" and f("accident_work_typical") == ""


def test_wrapped_label_is_completed_from_the_next_line():
    page = layout_b_page()
    page = [w for w in page if w[2] not in ("8 - Data de", "12/03/1985")]
    page += [(300, 167.1, "8 - Data de", 7.0), (300, 175.0, "Nascimento", 7.0), (298, 186.0, "12/03/1985", 8.0)]
    assert parse_laudo(read_pages(make_pdf([page])), "").get("birth_date") == "12/03/1985"


@pytest.mark.parametrize("label", ["8 - Data de", "8 - Data Nascimento", "8 - Data de Nascimento"])
def test_birth_date_label_wording_varies_between_templates(label):
    page = [w for w in layout_b_page() if w[2] not in ("8 - Data de", "12/03/1985")]
    page += [(300, 167.1, label, 7.0), (298, 178.0, "12/03/1985", 8.0)]
    assert parse_laudo(read_pages(make_pdf([page]))).get("birth_date") == "12/03/1985"


def test_unrecognised_labels_are_reported_without_their_values():
    page = layout_b_page() + [(33, 340.0, "53 - Campo Inventado Pelo Hospital", 7.0), (26, 350.0, "VALOR SECRETO 123", 8.0)]
    laudo = parse_laudo(read_pages(make_pdf([page])))
    assert laudo.unmatched == ("Campo Inventado Pelo Hospital",)
    assert "SECRETO" not in " ".join(laudo.unmatched)


def test_a_fully_recognised_laudo_has_no_unmatched_labels(sample_pdf_bytes):
    assert parse(sample_pdf_bytes).unmatched == ()
    assert parse(layout_b_pdf()).unmatched == ()


# ---------- rejeições ----------

def test_a_pdf_that_is_not_an_aih_is_rejected():
    words = (
        "Contrato de prestação de serviços advocatícios entre as partes abaixo qualificadas "
        "firmam o presente instrumento mediante as cláusulas e condições a seguir"
    ).split()
    pdf = make_pdf([[(30 + 40 * (i % 10), 40 + 12 * (i // 10), w, 9.0) for i, w in enumerate(words)]])
    with pytest.raises(NotAihError):
        parse(pdf)


def test_title_without_enough_labelled_fields_is_rejected():
    page = [(30, 20, "Laudo para Solicitação de Autorização de Internação Hospitalar", 9.0)]
    page += [(30, 40 + 12 * i, f"texto{i}", 8.0) for i in range(20)]
    with pytest.raises(NotAihError):
        parse(make_pdf([page]))
