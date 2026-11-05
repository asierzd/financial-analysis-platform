"""Importador de BBVA (España).

Formatos conocidos:
- Movimientos de cuenta de particulares: filas de título y luego
  `F. Valor | Fecha | Concepto | Movimiento | Importe | Divisa | Disponible | Divisa | Observaciones`.
- Histórico: hoja 'Historico', cabecera hacia la fila 16, datos desde la columna C, y
  `F. CONTABLE | F. VALOR | CÓDIGO | CONCEPTO | BENEFICIARIO/ORDENANTE | OBSERVACIONES | IMPORTE | SALDO | REMESA`.

La cabecera se localiza por nombre de columna, así que tolera cambios de posición. Los movimientos
vienen del más reciente al más antiguo; el orden lo resuelve el servicio de importación.
"""
from __future__ import annotations

import re

from ..categorize import normalize
from .base import BankParser, FormatError, ParseResult, RawFile, register
from .tabular import find_header, rows_to_transactions

_IBAN = re.compile(r"\bES\s?\d{2}(?:[\s-]?[\dX*•]{2,4}){3,6}\b")
_DISTINCTIVE = ({"F VALOR", "DISPONIBLE"}, {"F CONTABLE", "F VALOR"})


@register
class BBVAParser(BankParser):
    id = "bbva"
    bank = "BBVA"

    def detect(self, raw: RawFile) -> float:
        for sheet in raw.sheets:
            found = find_header(sheet)
            if not found:
                continue
            header_index, _ = found
            above = " ".join(str(c) for row in sheet.rows[:header_index] for c in row if c is not None)
            if "BBVA" in normalize(above).split() or "BBVA" in normalize(raw.filename):
                return 1.0
            names = {normalize(c) for c in sheet.rows[header_index] if isinstance(c, str)}
            if any(signature <= names for signature in _DISTINCTIVE):
                return 0.8
        return 0.0

    def parse(self, raw: RawFile) -> ParseResult:
        for sheet in raw.sheets:
            found = find_header(sheet)
            if not found:
                continue
            header_index, mapping = found
            transactions, warnings = rows_to_transactions(sheet.rows[header_index + 1:], mapping)
            if not transactions:
                continue
            return ParseResult(self.id, self.bank, transactions, warnings, _find_iban(sheet.rows[:header_index]))
        raise FormatError("He encontrado la cabecera de BBVA, pero ningún movimiento debajo.")


def _find_iban(rows: list[list]) -> str | None:
    for row in rows:
        for cell in row:
            match = _IBAN.search(str(cell).upper()) if cell is not None else None
            if match:
                return re.sub(r"[\s-]", "", match.group(0))
    return None
