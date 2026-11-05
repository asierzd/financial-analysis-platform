"""Contrato común de los importadores de bancos.

Para añadir un banco: crea un módulo en esta carpeta con una subclase de BankParser decorada con
@register. Se descubre automáticamente. Ver bbva.py como ejemplo.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass
class ParsedTx:
    date: date
    amount_cents: int
    concept: str = ""
    movement: str = ""
    notes: str = ""
    value_date: date | None = None
    balance_cents: int | None = None
    currency: str = "EUR"


@dataclass
class Sheet:
    name: str
    rows: list[list]


@dataclass
class RawFile:
    filename: str
    sheets: list[Sheet]


@dataclass
class ParseResult:
    parser_id: str
    bank: str
    transactions: list[ParsedTx]
    warnings: list[str] = field(default_factory=list)
    account_hint: str | None = None


class FormatError(ValueError):
    """Fichero ilegible; el mensaje se muestra tal cual al usuario."""


class BankParser(ABC):
    id: str = ""
    bank: str = ""

    @abstractmethod
    def detect(self, raw: RawFile) -> float:
        """Confianza de 0 a 1 en que este fichero es de este banco."""

    @abstractmethod
    def parse(self, raw: RawFile) -> ParseResult:
        ...


_REGISTRY: dict[str, BankParser] = {}


def register(cls: type[BankParser]) -> type[BankParser]:
    _REGISTRY[cls.id] = cls()
    return cls


def parsers() -> list[BankParser]:
    return list(_REGISTRY.values())


def detect_parser(raw: RawFile, threshold: float = 0.5) -> BankParser | None:
    scored = [(p.detect(raw), p) for p in parsers()]
    scored = [s for s in scored if s[0] >= threshold]
    return max(scored, key=lambda s: s[0])[1] if scored else None
