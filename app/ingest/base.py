"""Contrato común de los importadores de bancos.

Para añadir un banco: crea un módulo en esta carpeta con una subclase de BankParser decorada con
@register. Se descubre automáticamente. Ver bbva.py como ejemplo.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


# Movimientos automáticos de un bróker: esperados, no deben avisar como una comisión o un cargo sorpresa.
MOV_ORDER_FEE = "Comisión de operación"
MOV_WITHHOLDING = "Retención"
MOV_INTEREST = "Intereses remunerados"


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
    external_id: str | None = None  # identificador del banco: deduplica mejor que comparar campos
    occurred_at: str | None = None  # fecha y hora, para ordenar dentro de un mismo día
    fixed_category: str | None = None  # el banco dice qué es: ninguna regla debe cambiarlo
    fallback_category: str | None = None  # pista del banco: se usa si ninguna regla reconoce el texto


@dataclass
class ParsedEvent:
    """Operación sobre un valor: da forma a las posiciones, aunque no mueva dinero (una recompensa, por ejemplo)."""
    external_id: str
    date: date
    kind: str  # buy, sell, cancel, dividend, reward, delivery, corporate
    isin: str
    name: str = ""
    asset_class: str = ""
    shares: Decimal | None = None
    price: Decimal | None = None
    amount_cents: int = 0
    fee_cents: int = 0
    tax_cents: int = 0
    notes: str = ""
    occurred_at: str | None = None


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
    events: list[ParsedEvent] = field(default_factory=list)
    computed_balance: bool = False  # el extracto no trae saldo: se calcula sumando movimientos


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
