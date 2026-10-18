"""Parser do laudo AIH: rótulos casados pelo TEXTO, valores atribuídos pela GEOMETRIA.

Por que assim:
  * a numeração dos campos muda entre versões do formulário (49 x 52 campos; o "Cnes" do executante chega
    a ser impresso com o mesmo número do solicitante) => o texto do rótulo decide, nunca o número;
  * a ordem do stream do PDF varia por gerador (alguns desenham os valores antes dos rótulos) => a posição
    na página decide, nunca a ordem do texto;
  * o valor começa até ~17 pt à esquerda do rótulo e as colunas variam => faixas de coluna vêm dos rótulos
    vizinhos na mesma altura, e o dono de cada bloco de texto é o rótulo mais próximo acima dele.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from .models import Laudo, PageWords, Word
from .pdf_text import group_lines

LINE_TOL = 3.0  # pt: tolerância vertical para duas palavras estarem na mesma linha
GAP = 9.0  # pt: espaço horizontal a partir do qual palavras viram blocos (células) diferentes
ROW_TOL = 12.0  # pt: rótulos com no máximo esta diferença de altura estão na mesma fileira de colunas
PAD = 28.0  # pt: o valor pode começar até ~17 pt à esquerda do rótulo da sua coluna
MIN_ANCHORS = 8  # rótulos reconhecidos para aceitar o documento como AIH


class NotAihError(ValueError):
    pass


_SPECIAL = {"º": "O", "°": "O", "ª": "A"}


def fold(text: str) -> str:
    """Maiúsculas sem acento, preservando o comprimento (offsets do texto original continuam válidos)."""
    out = []
    for ch in text:
        if ch in _SPECIAL:
            out.append(_SPECIAL[ch])
            continue
        base = unicodedata.normalize("NFD", ch)[:1].upper()
        out.append(base if len(base) == 1 else ch)
    return "".join(out)


@dataclass(frozen=True)
class _Spec:
    key: str
    pattern: re.Pattern[str]
    occurrences: tuple[str, ...] = ()  # rótulos repetidos no formulário: 1ª, 2ª... ocorrência (ordem de leitura)


def _spec(key: str, pattern: str, occurrences: tuple[str, ...] = ()) -> _Spec:
    return _Spec(key, re.compile(pattern), occurrences)


_NO = r"(?:N\W{0,2}O?\W{0,2}\s*)?"  # "Nº", "N°", "N."  (º/° viram "O" no fold)

# Ordem importa: específicos antes dos genéricos. O padrão é aplicado com .match() no início do texto dobrado.
_SPECS: tuple[_Spec, ...] = (
    _spec("hospital_requester", r"NOME\s+DO\s+ESTABELECIMENTO\s+SOLICITANTE"),
    _spec("hospital_executor", r"NOME\s+DO\s+ESTABELECIMENTO\s+EXECUTANTE"),
    _spec("cnes", r"CNES\b", ("cnes_requester", "cnes_executor")),
    _spec("patient_name", r"NOME\s+(?:DO\s+)?PACIENTE"),
    _spec("medical_record", _NO + r"(?:DO\s+)?PRONTUARIO"),
    _spec("cns", r"CARTAO\s+NACIONAL\s+DE\s+SAUDE(?:\s*\(\s*CNS\s*\))?"),
    # a redação varia entre PDFs reais: "Data de", "Data Nascimento", "Data de Nascimento"
    _spec("birth_date", r"DATA\s+(?:DE\s+NASCIMENTO|NASCIMENTO|DE\b)"),
    _spec("sex", r"SEXO\b"),
    _spec("race", r"RACA\s*/\s*COR"),
    _spec("ethnicity", r"ETNIA\b"),
    _spec("mother_name", r"NOME\s+DA\s+MAE(?:\s+OU\s+RESPONSAVEL)?"),
    _spec("guardian_name", r"NOME\s+(?:DO\s+)?RESPONSAVEL"),
    _spec("phone", r"TELEFONE(?:\s+DE\s+CONTATO)?", ("phone", "guardian_phone")),
    _spec("address", r"ENDERECO(?:\s*\(\s*RUA\W*\s*N\w?\W*\s*BAIRRO\s*\))?"),
    _spec("city", r"MUNICIPIO\s+DE\s+RESIDENCIA"),
    _spec("ibge", r"COD\W{0,2}\s*IBGE\s+MUNICIPIO"),
    _spec("uf", r"UF\b"),
    _spec("cep", r"CEP\b"),
    _spec("symptoms", r"PRINCIPAIS\s+SINAIS\s+E\s+SINTOMAS\s+CLINICOS"),
    _spec("justification", r"CONDICOES\s+QUE\s+JUSTIFICAM\s+A\s+INTERNACAO"),
    _spec(
        "exam_results",
        r"PRINCIPAIS\s+RESULTADOS\s+DE\s+PROVAS\s+DIAGNOSTICAS(?:\s*\(\s*RESULTADOS?\s+DE\s+EXAMES\s+REALIZADOS\s*\))?",
    ),
    _spec("initial_diagnosis", r"DIAGNOSTICO\s+INICIAL"),
    _spec("cid_main", r"CID\s*10\s+PRINCIPAL"),
    _spec("cid_secondary", r"CID\s*10\s+SECUNDARIO"),
    _spec("cid_associated", r"CID\s*10\s+CAUS\w*\W*\s*ASSOC\w*\W*"),
    _spec("procedure_desc", r"DESCRICAO\s+DO\s+PROCEDIMENTO\s+SOLICITADO"),
    _spec("procedure_code", r"COD\w*\W{0,2}\s*(?:DO\s+)?PROCEDIMENTO"),
    _spec("clinic", r"CLINICA\b"),
    _spec("character", r"CARATER\s+DA\s+INTERNACAO"),
    _spec(
        "doc_number",
        _NO + r"DOC\w*\W*\s*(?:\(\s*CNS\s*/\s*CPF\s*\)\s*)?(?:DO\s+)?PROF\w*\W*\s*(?:SOLICITANTE|AUTORIZADOR)",
        ("requester_doc_number", "authorizer_doc_number"),
    ),
    _spec("doc_type", r"DOCUMENTO\b", ("requester_doc_type", "authorizer_doc_type")),
    _spec("requester_name", r"NOME\s+DO\s+PROFISSIONAL\s+SOLICITANTE"),
    _spec("request_date", r"DATA\s+DA\s+SOLICITACAO"),
    _spec("signature", r"ASSINATURA\s+E\s+CARIMBO(?:\s*\([^)]*\))?", ("requester_signature", "authorizer_signature")),
    _spec("accident_transit", r"\(\s*\w?\s*\)\s*ACIDENTE\s+DE\s+TRANSITO"),
    _spec("accident_work_typical", r"\(\s*\w?\s*\)\s*ACIDENTE\s+DE\s+TRABALHO\s+TIPICO"),
    _spec("accident_work_commute", r"\(\s*\w?\s*\)\s*ACIDENTE\s+(?:DE\s+)?TRABALHO\s+TRAJETO"),
    _spec("insurer_cnpj", r"CNPJ\s+DA\s+SEGURADORA"),
    _spec("ticket_number", _NO + r"(?:DO\s+)?BILHETE"),
    _spec("series", r"SERIE\b"),
    _spec("employer_cnpj", r"CNPJ\s+(?:DA\s+)?EMPRESA"),
    _spec("employer_cnae", r"CNAE\s+(?:DA\s+)?EMPRESA"),
    _spec("cbor", r"CBOR\b"),
    _spec("social_security_bond", r"VINCULO\s+COM\s+A\s+PREVIDENCIA"),
    _spec("authorizer_name", r"NOME\s+(?:DO\s+)?PROFISSIONAL\s+AUTORIZADOR"),
    _spec("issuer_code", r"COD\w*\W{0,2}\s*ORGAO\s+EMISSOR"),
    _spec("authorization_number", _NO + r"(?:DA\s+)?AUTORIZACAO\s+(?:DE\s+INTERNACAO\s+)?HOSPITALAR"),
    _spec("authorization_date", r"DATA\s+DA\s+AUTORIZACAO"),
)

_CORE = (
    "patient_name",
    "cns",
    "birth_date",
    "hospital_requester",
    "city",
    "symptoms",
    "initial_diagnosis",
    "cid_main",
    "procedure_desc",
    "request_date",
)
_ACCIDENT_FLAGS = {"accident_transit", "accident_work_typical", "accident_work_commute"}
_DATE_KEYS = {"birth_date", "request_date", "authorization_date"}
_CHECK = r"[xX✓✔√]"
_OPTION = re.compile(rf"\(\s*({_CHECK})?\s*\)\s*([^()]+?)(?=\s*\(|$)")
_DOC_OPTION = re.compile(rf"\(\s*({_CHECK})?\s*\)\s*(CNS|CPF)", re.I)
_HEADER = re.compile(
    r"IDENTIFICACAO\s+DO\s+(?:ESTABELECIMENTO|PACIENTE)|JUSTIFICATIVA\s+DA\s+INTERNACAO|"
    r"^PROCEDIMENTO\s+SOLICITADO$|CAUSAS\s+EXTERNAS|^AUTORIZACAO$|LAUDO\s+PARA\s+SOLICITACAO|"
    r"^HOSPITALAR$|^INTERNACAO\s+HOSPITALAR$"
)

_NUM = r"\d{1,2}(?:\.\d)?"
_ATTACHED = re.compile(rf"^({_NUM})-([A-Za-zÀ-ÿ(].*)$")
_NUMBER_HYPHEN = re.compile(rf"^({_NUM})-$")
_NUMBER_ONLY = re.compile(rf"^({_NUM})$")
_HYPHENS = ("-", "–", "—")


@dataclass
class _Cluster:
    words: list[Word]

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class _Anchor:
    label_text: str
    x0: float
    x1: float
    top: float
    bottom: float
    left: float = -math.inf
    right: float = math.inf
    clusters: list[_Cluster] = field(default_factory=list)


def _plausible(number: str) -> bool:
    return float(number) <= 60


def _marker_at(line: list[Word], i: int) -> tuple[str, int] | None:
    """Marcador de campo em line[i]: devolve (resto do rótulo na mesma palavra, índice da próxima palavra)."""
    text = line[i].text
    m = _ATTACHED.match(text)
    if m and _plausible(m.group(1)):
        return m.group(2), i + 1
    m = _NUMBER_HYPHEN.match(text)
    if m and _plausible(m.group(1)):
        return "", i + 1
    if _NUMBER_ONLY.match(text) and _plausible(text) and i + 1 < len(line) and line[i + 1].text in _HYPHENS:
        return "", i + 2
    return None


def _find_anchors(lines: list[list[Word]]) -> tuple[list[_Anchor], set[int]]:
    anchors: list[_Anchor] = []
    consumed: set[int] = set()
    for line in lines:
        i = 0
        while i < len(line):
            hit = _marker_at(line, i)
            if hit is None:
                i += 1
                continue
            rest, j = hit
            marker_words = line[i:j]
            run: list[Word] = []
            prev = marker_words[-1]
            while j < len(line):
                nxt = line[j]
                if nxt.x0 - prev.x1 > GAP or _marker_at(line, j) is not None:
                    break
                run.append(nxt)
                prev = nxt
                j += 1
            words = marker_words + run
            pieces = ([rest] if rest else []) + [w.text for w in run]
            anchors.append(
                _Anchor(
                    label_text=" ".join(pieces),
                    x0=marker_words[0].x0,
                    x1=words[-1].x1,
                    top=min(w.top for w in words),
                    bottom=max(w.bottom for w in words),
                )
            )
            consumed.update(id(w) for w in words)
            i = j
    return anchors, consumed


def _clusters(lines: list[list[Word]], skipped: set[int]) -> list[_Cluster]:
    out: list[_Cluster] = []
    for line in lines:
        current: list[Word] = []
        for word in line:
            if id(word) in skipped:
                if current:
                    out.append(_Cluster(current))
                    current = []
                continue
            if current and word.x0 - current[-1].x1 > GAP:
                out.append(_Cluster(current))
                current = []
            current.append(word)
        if current:
            out.append(_Cluster(current))
    return out


def _assign_bands(anchors: list[_Anchor]) -> None:
    for a in anchors:
        near = [b for b in anchors if b is not a and abs(b.top - a.top) <= ROW_TOL]
        rights = [b.x0 for b in near if b.x0 > a.x0 + 10]
        has_left = any(b.x0 < a.x0 - 10 for b in near)
        a.right = min(rights) - PAD if rights else math.inf
        a.left = a.x0 - PAD if has_left else -math.inf


def _owner(cluster: _Cluster, anchors: list[_Anchor]) -> _Anchor | None:
    best: tuple[tuple[float, float], _Anchor] | None = None
    for a in anchors:
        if not (a.left <= cluster.x0 < a.right):
            continue
        below = cluster.top >= a.bottom - 1.5
        inline = abs(cluster.top - a.top) <= LINE_TOL and cluster.x0 >= a.x1 - 1
        if not (below or inline):
            continue
        dy = max(cluster.top - a.bottom, 0.0) if below and not inline else 0.0
        key = (dy, abs(cluster.x0 - a.x0))
        if best is None or key < best[0]:
            best = (key, a)
    return best[1] if best else None


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace(" ", " ")).strip(" :;")
    return "" if re.fullmatch(r"[_/\\.\-\s]*", text) else text


def _checked_option(text: str, allowed: set[str]) -> str:
    marks = _DOC_OPTION.findall(text)
    if marks:
        picked = [name.upper() for mark, name in marks if mark]
        return picked[0] if picked else ""
    return text.upper() if text.upper() in allowed else ""


def _checked_options(text: str) -> str:
    options = _OPTION.findall(text)
    if not options:
        return text
    return ", ".join(name.strip() for mark, name in options if mark)


def _extract_value(key: str, label_part: str, remainder: str) -> str:
    text = _clean(remainder)
    if key in _ACCIDENT_FLAGS:
        return "SIM" if re.search(rf"\(\s*{_CHECK}\s*\)", label_part) else ""
    if key in ("requester_doc_type", "authorizer_doc_type"):
        return _checked_option(text, {"CNS", "CPF"})
    if key == "social_security_bond":
        return _checked_options(text)
    if key in _DATE_KEYS:
        match = re.search(r"\d{1,2}/\d{1,2}/\d{4}", text)
        return match.group(0) if match else ("" if not text else text)
    return text


def _match_spec(label_text: str, value_text: str) -> tuple[_Spec, str, int] | None:
    combined = f"{label_text} {value_text}".strip() if value_text else label_text.strip()
    folded = fold(combined)
    for spec in _SPECS:
        m = spec.pattern.match(folded)
        if m:
            return spec, combined, m.end()
    return None


def _parse_page(page: PageWords) -> tuple[dict[str, str], int, tuple[str, ...]]:
    lines = group_lines(page.words, LINE_TOL)
    anchors, consumed = _find_anchors(lines)
    skipped = set(consumed)
    for line in lines:
        if not any(id(w) in consumed for w in line) and _HEADER.search(fold(" ".join(w.text for w in line))):
            skipped.update(id(w) for w in line)
    _assign_bands(anchors)
    for cluster in _clusters(lines, skipped):
        owner = _owner(cluster, anchors)
        if owner is not None:
            owner.clusters.append(cluster)

    fields: dict[str, str] = {}
    counts: Counter[str] = Counter()
    unmatched: list[str] = []
    matched = 0
    for anchor in sorted(anchors, key=lambda a: (round(a.top), a.x0)):
        ordered = sorted(anchor.clusters, key=lambda c: (round(c.top), c.x0))
        hit = _match_spec(anchor.label_text, " ".join(c.text for c in ordered))
        if hit is None:
            unmatched.append(anchor.label_text[:60])  # só o texto do RÓTULO: nunca o valor
            continue
        spec, combined, end = hit
        matched += 1
        key = spec.key
        if spec.occurrences:
            index = counts[spec.key]
            counts[spec.key] += 1
            if index >= len(spec.occurrences):
                continue
            key = spec.occurrences[index]
        value = _extract_value(key, combined[:end], combined[end:])
        if not fields.get(key):
            fields[key] = value
    return fields, matched, tuple(unmatched)


def _looks_like_aih(pages: Sequence[PageWords]) -> bool:
    words = [w for p in pages for w in sorted(p.words, key=lambda w: (round(w.top), w.x0))]
    folded = fold(" ".join(w.text for w in words))
    return "SOLICITACAO DE AUTORIZACAO" in folded and "INTERNACAO HOSPITALAR" in folded


def parse_laudo(pages: Sequence[PageWords], source_name: str = "") -> Laudo:
    if not _looks_like_aih(pages):
        raise NotAihError("o documento não é um Laudo para Solicitação de Autorização de Internação Hospitalar")
    best: tuple[int, dict[str, str], int, tuple[str, ...]] | None = None
    for page in pages:
        fields, matched, unmatched = _parse_page(page)
        score = sum(1 for key in _CORE if fields.get(key))
        if best is None or score > best[0]:
            best = (score, fields, matched, unmatched)
    assert best is not None
    _, fields, matched, unmatched = best
    if matched < MIN_ANCHORS:
        raise NotAihError("poucos campos do laudo foram reconhecidos")
    return Laudo(fields=fields, source_name=source_name, unmatched=unmatched)
