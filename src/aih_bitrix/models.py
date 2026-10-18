"""Modelos de dados: palavras do PDF, laudo AIH e inferências."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Mapping


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float


@dataclass(frozen=True)
class PageWords:
    width: float
    height: float
    words: tuple[Word, ...]


def parse_br_date(text: str | None) -> date | None:
    """Primeira data DD/MM/AAAA encontrada no texto, ou None."""
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def split_name(full_name: str) -> tuple[str, str]:
    """Primeiro token = nome; o resto = sobrenome (regra do mapeamento aprovado)."""
    parts = (full_name or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


@dataclass(frozen=True)
class Laudo:
    """Campos do laudo AIH indexados por chave canônica (texto do rótulo, não o número).

    A numeração dos campos varia entre versões do formulário, por isso nada aqui depende dela.
    """

    fields: Mapping[str, str]
    source_name: str = ""
    unmatched: tuple[str, ...] = ()  # textos de RÓTULOS não reconhecidos (diagnóstico; nunca valores)

    def get(self, key: str) -> str:
        return self.fields.get(key, "")

    @property
    def patient_name(self) -> str:
        return self.get("patient_name")

    @property
    def first_name(self) -> str:
        return split_name(self.patient_name)[0]

    @property
    def last_name(self) -> str:
        return split_name(self.patient_name)[1]

    @property
    def birth_date(self) -> date | None:
        return parse_br_date(self.get("birth_date"))

    @property
    def request_date(self) -> date | None:
        return parse_br_date(self.get("request_date"))

    @property
    def cns(self) -> str:
        raw = self.get("cns")
        digits = re.sub(r"\D", "", raw)
        return digits if len(digits) == 15 else raw.strip()

    @property
    def cep(self) -> str:
        return re.sub(r"\D", "", self.get("cep"))

    @property
    def uf(self) -> str:
        match = re.search(r"[A-Za-z]{2}", self.get("uf"))
        return match.group(0).upper() if match else ""

    @property
    def city(self) -> str:
        return self.get("city")

    @property
    def mother_name(self) -> str:
        return self.get("mother_name") or self.get("guardian_name")

    @property
    def cid(self) -> str:
        return re.sub(r"\s+", "", self.get("cid_main")).upper()


@dataclass(frozen=True)
class Inference:
    """Valor inferido do texto clínico, com confiança (high|medium|low) e a regra que o produziu."""

    value: str
    confidence: str
    note: str
