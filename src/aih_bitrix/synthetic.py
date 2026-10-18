"""Amostras de AIH 100 % fictícias, geradas em memória: nenhum dado de paciente existe neste repositório.

Servem aos testes, ao comando `sample` (conferir a instalação sem tocar em dado real) e à documentação.

* layout "a": formulário antigo (49 campos, rótulos em CAIXA ALTA, 1 página);
* layout "b": formulário novo dos hospitais (52 campos, rótulos em Title Case, 2 páginas idênticas).

`Placement` = (x0, top, texto, tamanho); `top` é a distância do topo da página (mesma convenção do pdfplumber),
então a geometria das amostras é a mesma que o parser enxerga em PDFs reais.
"""
from __future__ import annotations

from pathlib import PurePath

from ._layout_a import WORDS as _LAYOUT_A_WORDS

Placement = tuple[float, float, str, float]

SAMPLE_LAYOUTS = ("a", "b")
SAMPLE_PREFIX = "AMOSTRA-FICTICIA"  # arquivos com este prefixo nunca são gravados no portal (ver Pipeline)


# ---------------------------------------------------------------- gerador mínimo de PDF (texto posicionado, sem dependências)

def _escape(text: str) -> bytes:
    raw = text.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(pages: list[list[Placement]], *, width: int = 595, height: int = 842) -> bytes:
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    kids: list[int] = []
    next_id = 4
    for placements in pages:
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        kids.append(page_id)
        stream = bytearray(b"BT\n")
        for x0, top, text, size in placements:
            baseline = height - top - 0.793 * size
            stream += b"/F1 %.1f Tf 1 0 0 1 %.2f %.2f Tm (" % (size, x0, baseline) + _escape(text) + b") Tj\n"
        stream += b"ET"
        objects[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (width, height, content_id)
        )
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + bytes(stream) + b"\nendstream"
    objects[2] = b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids) + b"] /Count %d >>" % len(kids)

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for obj_id in sorted(objects):
        offsets[obj_id] = len(out)
        out += b"%d 0 obj\n" % obj_id + objects[obj_id] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for obj_id in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[obj_id]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


# ---------------------------------------------------------------- layout A: formulário antigo (49 campos)

def layout_a_page() -> list[Placement]:
    return [(x0, top, text, size) for x0, top, text, size in _LAYOUT_A_WORDS]


def layout_a_pdf(*, pages: int = 1) -> bytes:
    page = layout_a_page()
    return make_pdf([list(page) for _ in range(pages)])


# ---------------------------------------------------------------- layout B: formulário novo (52 campos)
# As coordenadas dos RÓTULOS foram medidas em laudos reais (só geometria); todos os VALORES abaixo são fictícios.

LABEL_SIZE = 7.0
VALUE_SIZE = 8.0

# (chave, x do rótulo, top do rótulo, texto do rótulo, x do valor ou None)
ROWS_B: list[tuple[str, float, float, str, float | None]] = [
    ("hospital_requester", 32, 78.1, "1 - Nome do Estabelecimento Solicitante", 26),
    ("cnes_requester", 461, 78.1, "2 - Cnes", 459),
    ("hospital_executor", 32, 103.1, "3 - Nome do Estabelecimento Executante", 26),
    ("cnes_executor", 462, 103.1, "2 - Cnes", 459),  # o formulário real imprime "2" aqui
    ("patient_name", 32, 143.1, "5 - Nome Paciente", 26),
    ("medical_record", 462, 143.1, "6 - Nº Prontuário", 459),
    ("cns", 33, 167.1, "7 - Cartão Nacional de saúde (CNS)", 26),
    ("birth_date", 300, 167.1, "8 - Data de", 298),  # o formulário real trunca o rótulo: "Nascimento" não existe no PDF
    ("sex", 381, 167.1, "9 - Sexo", 378),
    ("race", 456, 167.1, "10 - Raça / Cor", 454),
    ("ethnicity", 523, 167.1, "10.1 - Etnia", 521),
    ("mother_name", 33, 193.1, "11 - Nome da Mãe", 26),
    ("phone", 441, 193.1, "12 - Telefone de Contato", 438),
    ("guardian_name", 33, 218.1, "13 - Nome Responsável", 26),
    ("guardian_phone", 441, 218.1, "14 - Telefone de Contato", 438),
    ("address", 33, 242.1, "15 - Endereço (Rua, Nº, Bairro)", 26),
    ("city", 33, 265.1, "16 - Município de Residência", 26),
    ("ibge", 345, 265.1, "17 - Cód. IBGE Município", 343),
    ("uf", 440, 265.1, "18 - UF", 438),
    ("cep", 475, 265.1, "19 - CEP", 471),
    ("symptoms", 33, 305.1, "20 - Principais Sinais e Sintomas Clínicos", 26),
    ("justification", 33, 412.2, "21 - Condições que Justificam a Internação", 26),
    ("exam_results", 33, 468.2, "22 - Principais Resultados de Provas Diagnósticas (Resultados de Exames Realizados)", 26),
    ("initial_diagnosis", 33, 525.2, "23 - Diagnóstico Inicial", 27),
    ("cid_main", 263, 525.2, "24 - Cid 10 Principal", 256),
    ("cid_secondary", 362, 525.2, "25 - Cid 10 Secundário", 355),
    ("cid_associated", 461, 525.2, "26 - Cid 10 Causas Associadas", 454),
    ("procedure_desc", 33, 563.2, "27 - Descrição do Procedimento Solicitado", 26),
    ("procedure_code", 465, 563.2, "28 - Código Procedimento", 458),
    ("clinic", 32, 587.1, "29 - Clínica", 26),
    ("character", 138, 587.1, "30 - Caráter da Internação", 133),
    ("requester_doc_type", 242, 587.1, "31 - Documento", None),
    ("requester_doc_number", 335, 587.1, "32 - Nº Documento (CNS/CPF) do Profissional Solicitante", 335),
    ("requester_name", 32, 611.1, "33 - Nome do Profissional Solicitante", 26),
    ("request_date", 287, 611.1, "34 - Data da Solicitação", 285),
    ("requester_signature", 372, 611.1, "35 - Assinatura e Carimbo (Nº do Registro do Conselho)", None),
    ("insurer_cnpj", 194, 651.1, "39 - CNPJ da Seguradora", 190),
    ("ticket_number", 445, 651.1, "40 - Nº do Bilhete", 441),
    ("series", 525, 651.1, "41 - Série", 521),
    ("accident_transit", 26, 659.2, "36 - ( ) Acidente de Trânsito", None),
    ("accident_work_typical", 26, 670.2, "37 - ( ) Acidente de Trabalho Típico", None),
    ("employer_cnpj", 194, 675.1, "42 - CNPJ da Empresa", 190),
    ("employer_cnae", 438, 675.1, "43 - CNAE da Empresa", 434),
    ("cbor", 525, 675.1, "44 - CBOR", 521),
    ("accident_work_commute", 26, 681.2, "38 - ( ) Acidente Trabalho Trajeto", None),
    ("social_security_bond", 29, 699.1, "45 - Vínculo com a Previdência", None),
    ("authorizer_name", 29, 739.1, "46 - Nome Profissional Autorizador", 26),
    ("issuer_code", 314, 739.1, "47 - Cód. Órgão Emissor", 310),
    ("authorization_number", 404, 739.1, "52 - Nº da Autorização de Internação Hospitalar", 400),
    ("authorizer_doc_type", 29, 768.1, "48 - Documento", None),
    ("authorizer_doc_number", 134, 768.3, "49 - Nº Documento (CNS/CPF) do Profissional Autorizador", 130),
    ("authorization_date", 28, 796.1, "50 - Data da Autorização", 25),
    ("authorizer_signature", 121, 796.1, "51 - Assinatura e Carimbo (Nº do Registro do Conselho)", None),
]

HEADERS_B: list[tuple[float, float, str]] = [
    (32, 66, "Identificação do Estabelecimento de Saúde"),
    (30, 130, "Identificação do Paciente"),
    (221, 293, "Justificativa da Internação"),
    (221, 551, "Procedimento Solicitado"),
    (147, 639, "Identificação de Causas Externas (Acidentes ou Violências)"),
    (258, 727, "Autorização"),
]

# Valores 100 % fictícios. Texto longo (symptoms) é quebrado em linhas como no PDF real.
DEFAULT_VALUES_B: dict[str, str | list[str]] = {
    "hospital_requester": "HOSPITAL TESTE DE EXEMPLO - CENTRO",
    "cnes_requester": "1234567",
    "patient_name": "MARIA APARECIDA TESTE",
    "medical_record": "998877",
    "cns": "700000000000001",
    "birth_date": "12/03/1985",
    "sex": "FEMININO",
    "race": "PARDA",
    "mother_name": "JOANA TESTE DA SILVA",
    "phone": "(65) 99876-5432",
    "address": "RUA DAS FLORES, 100, CENTRO",
    "city": "VARZEA GRANDE",
    "ibge": "5108402",
    "uf": "MT",
    "cep": "78110000",
    "symptoms": [
        "PACIENTE REFERE QUEDA DE MOTOCICLETA ONTEM A NOITE COM DOR INTENSA EM PUNHO DIREITO.",
        "RX COM FRATURA DE RADIO DISTAL DIREITO. SEM DEFICIT NEUROVASCULAR.",
    ],
    "justification": "NECESSIDADE DE TRATAMENTO CIRURGICO",
    "exam_results": "RX DE PUNHO DIREITO",
    "initial_diagnosis": "Fratura da extremidade distal do radio",
    "cid_main": "S525",
    "cid_associated": "V294",
    "procedure_desc": "TRATAMENTO CIRURGICO DE FRATURA DA EXTREMIDADE DISTAL DO RADIO",
    "procedure_code": "0408020059",
    "clinic": "CIRURGICO",
    "character": "URGENCIA",
    "requester_doc_number": "700000000000002",
    "requester_name": "DR FULANO DE TAL",
    "request_date": "20/09/2026",
}

VINCULO_OPTIONS = ["Empregador", "Empregado", "Autônomo", "Desempregado", "Aposentado", "Não segurado"]
VINCULO_X = [30, 116, 207, 288, 393, 486]


def layout_b_page(
    values: dict[str, str | list[str]] | None = None,
    *,
    checked_accidents: tuple[str, ...] = (),
    vinculo: str | None = None,
    requester_doc_type: str = "CNS",
) -> list[Placement]:
    data = {**DEFAULT_VALUES_B, **(values or {})}
    out: list[Placement] = [
        (266, 28.1, "Laudo para Solicitação de Autorização de", 9.0),
        (107, 36.0, "Sistema Único de Saúde", 6.0),
        (329, 43.1, "Internação Hospitalar", 9.0),
        (534, 54.0, "20260001", 7.0),  # número solto no topo: não pode poluir nenhum campo
    ]
    out += [(x, top, text, 8.5) for x, top, text in HEADERS_B]
    for key, lx, ltop, ltext, vx in ROWS_B:
        if key.startswith("accident_") and key in checked_accidents:
            ltext = ltext.replace("( )", "( X )")
        out.append((lx, ltop, ltext, LABEL_SIZE))
        raw = data.get(key)
        if raw and vx is not None:
            lines = raw if isinstance(raw, list) else [raw]
            for i, line in enumerate(lines):
                out.append((vx, ltop + 10.5 + 10 * i, line, VALUE_SIZE))
    # checkboxes do documento (solicitante/autorizador) e opções do vínculo previdenciário
    box = "( X ) CNS ( ) CPF" if requester_doc_type == "CNS" else "( ) CNS ( X ) CPF"
    out.append((243, 598.0, box, VALUE_SIZE))
    out.append((36, 780.0, "( ) CNS ( ) CPF", VALUE_SIZE))
    for option, x in zip(VINCULO_OPTIONS, VINCULO_X):
        mark = "( X )" if option == vinculo else "( )"
        out.append((x, 710.0, f"{mark}{option}", VALUE_SIZE))
    return out


def layout_b_pdf(*, pages: int = 2, **kwargs) -> bytes:
    """O AIH real tem 2 páginas idênticas (cópias)."""
    page = layout_b_page(**kwargs)
    return make_pdf([list(page) for _ in range(pages)])


def _layout_key(layout: str) -> str:
    key = layout.lower()
    if key not in SAMPLE_LAYOUTS:
        raise ValueError(f"layout desconhecido: {layout!r} (use um destes: {', '.join(SAMPLE_LAYOUTS)})")
    return key


def sample_pdf(layout: str = "a") -> bytes:
    """PDF de amostra fictício no layout pedido ("a" = formulário antigo, "b" = formulário novo)."""
    return layout_a_pdf() if _layout_key(layout) == "a" else layout_b_pdf()


def sample_filename(layout: str = "a") -> str:
    return f"{SAMPLE_PREFIX}-aih-{_layout_key(layout)}.pdf"


def is_sample_name(filename: str) -> bool:
    """O arquivo tem cara de amostra fictícia (gerada por `sample`)? O pipeline recusa gravá-la no portal."""
    return PurePath(filename.replace("\\", "/")).name.upper().startswith(SAMPLE_PREFIX)
