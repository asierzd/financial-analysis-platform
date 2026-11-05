"""Importador de Banco Santander (España).

No hay documentación pública del Excel que descarga la banca online, así que este importador no asume
posiciones: localiza la tabla por el nombre de sus columnas (fecha de operación, fecha valor, concepto,
importe y saldo, también con «EUR» o cargo/abono por separado) y admite fechas como texto.

Se reconoce por la palabra Santander en los títulos o en el nombre del fichero, o por el código de entidad
0049 del IBAN. Esas columnas por sí solas son una pista débil: otro banco podría usarlas, por lo que
puntúan poco y cualquier importador con una prueba más fuerte gana.
"""
from __future__ import annotations

from ..categorize import normalize
from .base import RawFile, register
from .tabular import TableBankParser, above_header_words, find_header, find_iban, iban_entity

ENTITY_CODE = "0049"
_TYPICAL_COLUMNS = {"FECHA OPERACION", "FECHA VALOR", "CONCEPTO"}
_BALANCE_COLUMNS = {"SALDO", "SALDO EUR", "SALDO EUROS", "SALDO E"}


@register
class SantanderParser(TableBankParser):
    id = "santander"
    bank = "Santander"

    def detect(self, raw: RawFile) -> float:
        for sheet in raw.sheets:
            found = find_header(sheet)
            if not found:
                continue
            header_index, _ = found
            if "SANTANDER" in above_header_words(sheet.rows, header_index, raw.filename):
                return 1.0
            if iban_entity(find_iban(sheet.rows[:header_index])) == ENTITY_CODE:
                return 1.0
            names = {normalize(c) for c in sheet.rows[header_index] if isinstance(c, str)}
            if _TYPICAL_COLUMNS <= names and names & _BALANCE_COLUMNS:
                return 0.55
        return 0.0
