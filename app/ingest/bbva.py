"""Importador de BBVA (España).

Formatos conocidos:
- Movimientos de cuenta de particulares: filas de título y luego
  `F. Valor | Fecha | Concepto | Movimiento | Importe | Divisa | Disponible | Divisa | Observaciones`.
- Histórico: hoja 'Historico', cabecera hacia la fila 16, datos desde la columna C, y
  `F. CONTABLE | F. VALOR | CÓDIGO | CONCEPTO | BENEFICIARIO/ORDENANTE | OBSERVACIONES | IMPORTE | SALDO | REMESA`.

La cabecera se localiza por nombre de columna, así que tolera cambios de posición.
"""
from __future__ import annotations

from ..categorize import normalize
from .base import RawFile, register
from .tabular import TableBankParser, above_header_words, find_header, find_iban, iban_entity

ENTITY_CODE = "0182"
_DISTINCTIVE = ({"F VALOR", "DISPONIBLE"}, {"F CONTABLE", "F VALOR"})


@register
class BBVAParser(TableBankParser):
    id = "bbva"
    bank = "BBVA"

    def detect(self, raw: RawFile) -> float:
        for sheet in raw.sheets:
            found = find_header(sheet)
            if not found:
                continue
            header_index, _ = found
            if "BBVA" in above_header_words(sheet.rows, header_index, raw.filename):
                return 1.0
            if iban_entity(find_iban(sheet.rows[:header_index])) == ENTITY_CODE:
                return 1.0
            names = {normalize(c) for c in sheet.rows[header_index] if isinstance(c, str)}
            if any(signature <= names for signature in _DISTINCTIVE):
                return 0.8
        return 0.0
