"""Inferências a partir do texto clínico do laudo.

Cada regra devolve valor + confiança (high|medium|low) + a regra aplicada. Sem evidência devolve None:
o campo fica vazio e a pendência vai para a revisão (timeline), em vez de chutar um valor.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .models import Inference, Laudo, parse_br_date
from .parser import fold

# ---------------------------------------------------------------------------------------------
# data do acidente
# ---------------------------------------------------------------------------------------------

_NUMBER_WORDS = {
    "UM": 1, "UMA": 1, "DOIS": 2, "DUAS": 2, "TRES": 3, "QUATRO": 4, "CINCO": 5,
    "SEIS": 6, "SETE": 7, "OITO": 8, "NOVE": 9, "DEZ": 10,
}
_N = r"(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")"
_SAME_DAY = re.compile(
    r"\bHA\s+(?:ALGUMAS|POUCAS|UMAS|UMA|DUAS|TRES|\d{1,2})\s+HORAS?\b|\bHOJE\b|"
    r"\bESTA\s+(?:MANHA|MADRUGADA|TARDE|NOITE)\b|\bHA\s+POUCO\b|\bHA\s+(?:\d+|POUCOS)\s+MINUTOS\b"
)
_DAYS = re.compile(rf"\bHA\s+{_N}\s+DIAS?\b")
_WEEKS = re.compile(rf"\bHA\s+{_N}\s+SEMANAS?\b")
_MONTHS = re.compile(rf"\bHA\s+{_N}\s+(?:MES|MESES)\b")
_YEARS = re.compile(rf"\bHA\s+{_N}\s+ANOS?\b")
_VAGUE = re.compile(r"\bHA\s+(?:ALGUNS|POUCOS|VARIOS)\s+DIAS\b|\bHA\s+DIAS\b|\bHA\s+ALGUMAS?\s+SEMANAS\b|\bHA\s+ALGUM\s+TEMPO\b")
_ABSOLUTE = re.compile(
    r"(?:\bEM\b|\bDIA\b|\bDATA\b|\bOCORRIDO\b|\bACIDENTE\b|\bQUEDA\b|\bTRAUMA\b)[^0-9]{0,25}"
    r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?!\d)"
)


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token]


def _fmt(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _minus_months(day: date, months: int) -> date:
    year, month0 = divmod(day.year * 12 + (day.month - 1) - months, 12)
    return _clamped(year, month0 + 1, day.day)


def infer_accident_date(laudo: Laudo) -> Inference | None:
    request = laudo.request_date
    if request is None:
        return None
    text = fold(f"{laudo.get('symptoms')} {laudo.get('justification')}")
    candidates: list[tuple[int, Inference]] = []

    for m in _ABSOLUTE.finditer(text):
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else request.year
        year = year + 2000 if year < 100 else year
        try:
            found = date(year, month, day)
        except ValueError:
            continue
        if not m.group(3) and found > request:
            found = date(year - 1, month, day) if (month, day) != (2, 29) else found
        if found <= request:
            candidates.append((m.start(), Inference(_fmt(found), "high", "data informada no relato")))

    for m in _SAME_DAY.finditer(text):
        candidates.append((m.start(), Inference(_fmt(request), "high", "relato indica poucas horas antes da solicitação")))
    for m in re.finditer(r"\bANTEONTEM\b", text):
        candidates.append((m.start(), Inference(_fmt(request - timedelta(days=2)), "high", "relato: anteontem")))
    for m in re.finditer(r"\bONTEM\b", text):
        candidates.append((m.start(), Inference(_fmt(request - timedelta(days=1)), "high", "relato: ontem")))
    for m in _DAYS.finditer(text):
        n = _number(m.group(1))
        candidates.append((m.start(), Inference(_fmt(request - timedelta(days=n)), "high" if n <= 3 else "medium", f"relato: há {n} dia(s)")))
    for m in _WEEKS.finditer(text):
        n = _number(m.group(1))
        candidates.append((m.start(), Inference(_fmt(request - timedelta(weeks=n)), "medium", f"relato: há {n} semana(s)")))
    for m in _MONTHS.finditer(text):
        n = _number(m.group(1))
        candidates.append((m.start(), Inference(_fmt(_minus_months(request, n)), "medium", f"relato: há {n} mês(es)")))
    for m in _YEARS.finditer(text):
        n = _number(m.group(1))
        candidates.append((m.start(), Inference(_fmt(_clamped(request.year - n, request.month, request.day)), "low", f"relato: há {n} ano(s)")))
    for m in _VAGUE.finditer(text):
        candidates.append((m.start(), Inference(f"Até {_fmt(request)}", "low", "relato sem prazo exato; usada a data da solicitação como limite")))

    if not candidates:
        return None
    absolute = [c for c in candidates if c[1].note == "data informada no relato"]
    pool = absolute or candidates
    return min(pool, key=lambda c: c[0])[1]


# ---------------------------------------------------------------------------------------------
# membro fraturado
# ---------------------------------------------------------------------------------------------

# (palavra-chave dobrada, nome exibido, gênero)
_REGIONS = (
    ("JOELHO", "Joelho", "m"), ("TORNOZELO", "Tornozelo", "m"), ("QUADRIL", "Quadril", "m"),
    ("OMBRO", "Ombro", "m"), ("COTOVELO", "Cotovelo", "m"), ("PUNHO", "Punho", "m"),
    ("CLAVICULA", "Clavícula", "f"), ("ANTEBRACO", "Antebraço", "m"), ("BRACO", "Braço", "m"),
    ("COXA", "Coxa", "f"), ("FEMUR", "Fêmur", "m"), ("PERNA", "Perna", "f"), ("CALCANEO", "Calcâneo", "m"),
    ("DEDO", "Dedo", "m"), ("MAO", "Mão", "f"), ("PE", "Pé", "m"), ("COSTELA", "Costela", "f"),
    ("BACIA", "Bacia", "f"), ("PELVE", "Pelve", "f"), ("LOMBAR", "Coluna lombar", "f"),
    ("CERVICAL", "Coluna cervical", "f"), ("COLUNA", "Coluna", "f"), ("CRANIO", "Crânio", "m"),
    ("FACE", "Face", "f"), ("MANDIBULA", "Mandíbula", "f"),
)
# capítulo do CID-10 (S02..S92): regiões compatíveis e região padrão quando o relato não cita nenhuma
_CID_CHAPTERS = {
    "S02": ({"CRANIO", "FACE", "MANDIBULA"}, "Face", "f"),
    "S12": ({"CERVICAL", "COLUNA"}, "Coluna cervical", "f"),
    "S22": ({"COSTELA"}, "Tórax", "m"),
    "S32": ({"LOMBAR", "COLUNA", "BACIA", "PELVE", "QUADRIL"}, "Bacia", "f"),
    "S42": ({"OMBRO", "CLAVICULA", "BRACO"}, "Braço", "m"),
    "S52": ({"ANTEBRACO", "PUNHO", "COTOVELO"}, "Antebraço", "m"),
    "S62": ({"MAO", "DEDO", "PUNHO"}, "Mão", "f"),
    "S72": ({"FEMUR", "QUADRIL", "COXA"}, "Coxa", "f"),
    "S82": ({"PERNA", "JOELHO", "TORNOZELO"}, "Perna", "f"),
    "S92": ({"PE", "CALCANEO", "DEDO", "TORNOZELO"}, "Pé", "m"),
}
_SIDES = (
    ("E", re.compile(r"\b(?:ESQUERD[OA]S?|ESQ|MIE|MSE)\b")),
    ("D", re.compile(r"\b(?:DIREIT[OA]S?|DIR|MID|MSD)\b")),
    ("B", re.compile(r"\bBILATERAL(?:MENTE)?\b|\bAMBOS\b")),
)
_SIDE_WORD = {("E", "m"): "esquerdo", ("E", "f"): "esquerda", ("D", "m"): "direito", ("D", "f"): "direita",
              ("B", "m"): "bilateral", ("B", "f"): "bilateral"}
_SIDE_PREP = {"E": "à esquerda", "D": "à direita", "B": "bilateral"}


def _pick_side(text: str, near: int, *others: str) -> str | None:
    for pool in (text, *others):
        hits = [(m.start(), code) for code, rx in _SIDES for m in rx.finditer(pool)]
        if hits:
            if pool is text and near >= 0:
                after = [h for h in hits if h[0] >= near]
                return min(after or hits, key=lambda h: abs(h[0] - near))[1]
            return min(hits)[1]
    return None


def _sentence(text: str) -> str:
    text = text.strip()
    if text.isupper():
        text = text.lower()
    return text[:1].upper() + text[1:]


def _dx_phrase(diagnosis: str) -> str:
    return _sentence(re.sub(r"^\s*fratura\s+(?:d[aeo]s?\s+)?", "", diagnosis, flags=re.I))


def _procedure_phrase(procedure: str) -> str:
    m = re.search(r"FRATURA\s+D[AOE]S?\s+(.+?)(?:\s*\(.*)?$", procedure, flags=re.I)
    return _sentence(m.group(1)) if m else ""


def infer_limb(laudo: Laudo) -> Inference | None:
    story = fold(laudo.get("symptoms"))
    diagnosis, procedure, cid = laudo.get("initial_diagnosis"), laudo.get("procedure_desc"), laudo.cid
    chapter = _CID_CHAPTERS.get(cid[:3])
    allowed = chapter[0] if chapter else set()

    found = [(m.start(), key, name, gender) for key, name, gender in _REGIONS if (m := re.search(rf"\b{key}\b", story))]
    region_pos = -1
    if found:
        region_pos, _, region, gender = min([f for f in found if f[1] in allowed] or found)
        in_story = True
    elif chapter:
        region, gender, in_story = chapter[1], chapter[2], False
    else:
        region, gender, in_story = "", "m", False

    side = _pick_side(story, region_pos, fold(diagnosis), fold(procedure))
    dx = _dx_phrase(diagnosis)
    if not region and not dx:
        return None

    head = f"{region} {_SIDE_WORD[(side, gender)]}".strip() if side and region else region
    parts = [head] if head else []
    if dx:
        proc = _procedure_phrase(procedure)
        detail = dx
        if proc and fold(proc) != fold(dx):
            detail += f" ({proc}{' ' + _SIDE_PREP[side] if side else ''})"
        parts.append(detail)
    if region and in_story and dx:
        confidence, note = "high", "região e lado do relato + diagnóstico + procedimento"
    elif region:
        confidence, note = "medium", "região do relato ou do capítulo do CID-10"
    else:
        confidence, note = "low", "apenas o diagnóstico; região não identificada"
    return Inference(" / ".join(parts), confidence, note)


# ---------------------------------------------------------------------------------------------
# cirurgia e auxílio-doença
# ---------------------------------------------------------------------------------------------

_POST_OP = re.compile(
    r"POS[- ]?OPERATORIO|SUBMETID[OA]\s+A\s+(?:CIRURGIA|PROCEDIMENTO)|FOI\s+OPERAD[OA]|JA\s+OPERAD[OA]|CIRURGIA\s+REALIZADA|\bOPEROU\b"
)
_PROPOSAL = re.compile(
    r"PROPOSTA\s+(?:DE\s+)?(?:TRATAMENTO\s+)?CIRURG|INDICACAO\s+CIRURGICA|TRATAMENTO\s+CIRURGICO|"
    r"NECESSIDADE\s+DE\s+CIRURGIA|\bCIRURGICO\b|\bCIRURGICA\b"
)
_CONSERVATIVE = re.compile(r"TRATAMENTO\s+CONSERVADOR|SEM\s+INDICACAO\s+CIRURGICA|NAO\s+HOUVE\s+CIRURGIA|\bSEM\s+CIRURGIA\b")


def infer_surgery(laudo: Laudo) -> Inference | None:
    story = fold(" ".join(laudo.get(k) for k in ("symptoms", "justification", "exam_results")))
    request = fold(" ".join(laudo.get(k) for k in ("justification", "procedure_desc", "clinic")))
    if _POST_OP.search(story):
        return Inference("Sim", "high", "relato de pós-operatório/cirurgia já realizada")
    if _PROPOSAL.search(request) or _PROPOSAL.search(story):
        explicit = bool(re.search(r"PROPOSTA|INDICACAO", request + " " + story))
        return Inference("Proposta cirúrgica", "high" if explicit else "medium", "tratamento cirúrgico proposto/solicitado no laudo")
    if _CONSERVATIVE.search(story + " " + request):
        return Inference("Não", "medium", "tratamento conservador explícito")
    return None


_SICK_NO = re.compile(r"NAO\s+(?:RECEBE|RECEBEU|POSSUI|TEM)\s+AUXILIO[- ]?DOENCA|\bSEM\s+AUXILIO[- ]?DOENCA")
_SICK_YES = re.compile(
    r"(?:EM\s+USO\s+DE|RECEBE|BENEFICIARI[OA]\s+DE)\s+AUXILIO[- ]?DOENCA|AUXILIO[- ]?DOENCA\s+(?:ATIVO|CONCEDIDO)"
)


def infer_sick_pay(laudo: Laudo) -> Inference | None:
    text = fold(" ".join(laudo.get(k) for k in ("symptoms", "justification", "exam_results", "social_security_bond")))
    if _SICK_NO.search(text):
        return Inference("Não", "medium", "laudo informa que não recebe auxílio-doença")
    if _SICK_YES.search(text):
        return Inference("Sim", "medium", "laudo informa uso/recebimento de auxílio-doença")
    return None


def infer_all(laudo: Laudo) -> dict[str, Inference]:
    results = {
        "accident_date": infer_accident_date(laudo),
        "limb": infer_limb(laudo),
        "surgery": infer_surgery(laudo),
        "sick_pay": infer_sick_pay(laudo),
    }
    return {key: value for key, value in results.items() if value is not None}
