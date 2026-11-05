"""Utilidades para extractos en tabla: localizar la cabecera, leer fechas e importes, mapear columnas."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..categorize import normalize
from .base import BankParser, FormatError, ParsedTx, ParseResult, RawFile, Sheet

HEADER_SCAN_ROWS = 40

SYNONYMS: dict[str, set[str]] = {
    "date": {"FECHA", "F CONTABLE", "FECHA CONTABLE", "FECHA OPERACION", "F OPERACION", "FECHA DE OPERACION",
             "FECHA MOVIMIENTO", "FECHA DE CONTABILIZACION", "DATE"},
    "value_date": {"F VALOR", "FECHA VALOR", "FECHA DE VALOR"},
    "concept": {"CONCEPTO", "DESCRIPCION", "CONCEPTO COMUN", "DETALLE", "DESCRIPTION"},
    "movement": {"MOVIMIENTO", "TIPO", "TIPO DE MOVIMIENTO", "CODIGO"},
    "notes": {"OBSERVACIONES", "BENEFICIARIO ORDENANTE", "ORDENANTE BENEFICIARIO", "MAS DATOS", "INFORMACION ADICIONAL",
              "CONCEPTO PROPIO", "CONCEPTO AMPLIADO"},
    "amount": {"IMPORTE", "CANTIDAD", "IMPORTE EUR", "IMPORTE EUROS", "IMPORTE E", "MONTO", "AMOUNT"},
    "debit": {"CARGO", "CARGOS", "DEBE"},
    "credit": {"ABONO", "ABONOS", "HABER"},
    "balance": {"DISPONIBLE", "SALDO", "SALDO EUR", "SALDO EUROS", "SALDO E", "SALDO DISPONIBLE", "SALDO CONTABLE",
                "SALDO TRAS MOVIMIENTO", "BALANCE"},
    "currency": {"DIVISA", "MONEDA", "CURRENCY"},
}

_IBAN = re.compile(r"\bES\s?\d{2}(?:[\s-]?[\dX*•]{2,4}){3,6}\b")
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y", "%Y-%m-%d", "%Y/%m/%d",
                 "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
_CURRENCY = re.compile(r"(?i)eur(os?)?|€|\s| |\+")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+$")


def parse_date(cell) -> date | None:
    if cell is None or cell == "":
        return None
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        # Número de serie de Excel, en el rango 1955-2064.
        if 20000 <= cell <= 60000:
            return date(1899, 12, 30) + timedelta(days=int(cell))
        return None
    text = str(cell).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(cell) -> int | None:
    """Importe en céntimos. Admite '1.234,56', '-12,30 €', '1,234.56', '(45,00)', '12,30-' y números reales."""
    if cell is None or cell == "" or isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return _to_cents(Decimal(repr(cell)))
    text = _CURRENCY.sub("", str(cell)).replace("−", "-").replace("–", "-")
    if not text:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        negative, text = True, text[:-1]
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", "") if _THOUSANDS_COMMA.match(text) and text.count(",") > 1 else text.replace(",", ".")
    elif "." in text and _THOUSANDS_DOT.match(text):
        text = text.replace(".", "")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return _to_cents(-value if negative else value)


def _to_cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class ColumnMap:
    date: int | None = None
    value_date: int | None = None
    concept: int | None = None
    movement: int | None = None
    notes: list[int] = field(default_factory=list)
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    balance: int | None = None
    currency: int | None = None

    @property
    def usable(self) -> bool:
        has_date = self.date is not None or self.value_date is not None
        has_amount = self.amount is not None or self.debit is not None or self.credit is not None
        return has_date and has_amount and self.concept is not None


def map_header(row: list) -> ColumnMap:
    """Reconoce columnas por nombre. Si un nombre se repite (BBVA trae dos 'Divisa'), gana la primera."""
    mapping = ColumnMap()
    for index, cell in enumerate(row):
        if not isinstance(cell, str):
            continue
        name = normalize(cell)
        for fieldname, names in SYNONYMS.items():
            if name not in names:
                continue
            if fieldname == "notes":
                mapping.notes.append(index)
            elif getattr(mapping, fieldname) is None:
                setattr(mapping, fieldname, index)
            break
    return mapping


def find_header(sheet: Sheet) -> tuple[int, ColumnMap] | None:
    for index, row in enumerate(sheet.rows[:HEADER_SCAN_ROWS]):
        mapping = map_header(row)
        if mapping.usable:
            return index, mapping
    return None


def _cell(row: list, index: int | None):
    return row[index] if index is not None and index < len(row) else None


def _text(row: list, index: int | None) -> str:
    value = _cell(row, index)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


def rows_to_transactions(rows: list[list], mapping: ColumnMap) -> tuple[list[ParsedTx], list[str]]:
    transactions: list[ParsedTx] = []
    unreadable: list[int] = []
    for number, row in enumerate(rows, start=1):
        if not any(c not in (None, "") for c in row):
            continue
        booking = parse_date(_cell(row, mapping.date))
        value = parse_date(_cell(row, mapping.value_date))
        when = booking or value
        if mapping.amount is not None:
            amount = parse_amount(_cell(row, mapping.amount))
        else:
            debit = parse_amount(_cell(row, mapping.debit))
            credit = parse_amount(_cell(row, mapping.credit))
            amount = None if debit is None and credit is None else (credit or 0) - abs(debit or 0)
        if when is None or amount is None:
            # Filas de totales o pie de página: sin fecha ni importe válidos no son movimientos.
            if when is not None or amount is not None:
                unreadable.append(number)
            continue
        notes = [_text(row, i) for i in mapping.notes]
        concept = _text(row, mapping.concept)
        transactions.append(ParsedTx(
            date=when,
            value_date=value,
            amount_cents=amount,
            concept=concept,
            movement=_text(row, mapping.movement),
            notes=" · ".join(dict.fromkeys(n for n in notes if n and n != concept)),
            balance_cents=parse_amount(_cell(row, mapping.balance)),
            currency=(_text(row, mapping.currency) or "EUR").upper()[:3],
        ))
    warnings = []
    if unreadable:
        sample = ", ".join(str(n) for n in unreadable[:5])
        warnings.append(f"{len(unreadable)} filas con la fecha o el importe ilegibles se han omitido (nº {sample}…).")
    return transactions, warnings


def find_iban(rows: list[list]) -> str | None:
    for row in rows:
        for cell in row:
            match = _IBAN.search(str(cell).upper()) if cell is not None else None
            if match:
                return re.sub(r"[\s-]", "", match.group(0))
    return None


def iban_entity(iban: str | None) -> str | None:
    """Código de entidad del IBAN español (0182 BBVA, 0049 Santander), si no viene enmascarado."""
    return iban[4:8] if iban and iban[4:8].isdigit() else None


def above_header_words(rows: list[list], header_index: int, filename: str) -> set[str]:
    """Palabras de los títulos y del nombre del fichero. No mira los movimientos: un concepto puede citar a otro banco."""
    above = " ".join(str(c) for row in rows[:header_index] for c in row if c is not None)
    return set(normalize(f"{above} {filename}").split())


class TableBankParser(BankParser):
    """Banco cuyo extracto es una tabla con títulos de columna. Cada banco solo aporta cómo reconocerse."""

    def parse(self, raw: RawFile) -> ParseResult:
        for sheet in raw.sheets:
            found = find_header(sheet)
            if not found:
                continue
            header_index, mapping = found
            transactions, warnings = rows_to_transactions(sheet.rows[header_index + 1:], mapping)
            if transactions:
                return ParseResult(self.id, self.bank, transactions, warnings, find_iban(sheet.rows[:header_index]))
        raise FormatError(f"He encontrado la cabecera de {self.bank}, pero ningún movimiento debajo.")
