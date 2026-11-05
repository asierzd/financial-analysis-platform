"""Convierte cualquier fichero tabular en filas de celdas, mirando su contenido y no la extensión.

Los bancos españoles exportan a menudo "falsos .xls": en realidad HTML o CSV renombrado.
"""
from __future__ import annotations

import csv
import io
import zipfile
from html.parser import HTMLParser

from .base import FormatError, RawFile, Sheet

MAX_UNCOMPRESSED = 200 * 1024 * 1024


def read_file(data: bytes, filename: str) -> RawFile:
    if not data:
        raise FormatError("El fichero está vacío.")
    head = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n")
    if data[:4] == b"%PDF":
        raise FormatError(
            "Es un PDF, y un PDF no conserva las columnas de forma fiable. En BBVA o Santander, descarga los movimientos "
            "en Excel desde la web. En Trade Republic, usa en la app «Exportación de transacciones», que genera un CSV."
        )
    if data[:2] == b"PK":
        return RawFile(filename, _read_xlsx(data))
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return RawFile(filename, _read_xls(data))
    if head[:1] == b"<" and b"<t" in data[:200000].lower():
        return RawFile(filename, _read_html(_decode(data)))
    return RawFile(filename, [Sheet(filename, _read_delimited(_decode(data)))])


def _read_xlsx(data: bytes) -> list[Sheet]:
    from openpyxl import load_workbook

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED:
                raise FormatError("El Excel es demasiado grande una vez descomprimido.")
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except FormatError:
        raise
    except Exception as exc:
        raise FormatError(f"No se ha podido abrir el Excel ({exc}).") from exc
    sheets = [Sheet(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in workbook.worksheets]
    workbook.close()
    return sheets


def _read_xls(data: bytes) -> list[Sheet]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        raise FormatError(f"No se ha podido abrir el Excel .xls ({exc}).") from exc
    sheets = []
    for sheet in book.sheets():
        rows = []
        for r in range(sheet.nrows):
            row = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    row.append(None)
                else:
                    row.append(cell.value)
            rows.append(row)
        sheets.append(Sheet(sheet.name, rows))
    return sheets


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _read_html(text: str) -> list[Sheet]:
    parser = _TableParser()
    parser.feed(text)
    if not parser.rows:
        raise FormatError("El fichero HTML no contiene ninguna tabla.")
    return [Sheet("tabla", parser.rows)]


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _read_delimited(text: str) -> list[list[str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise FormatError("El fichero no contiene datos.")
    # El separador correcto es el que da más columnas de forma constante en las líneas de datos.
    sample = lines[:60]
    best, best_score = ";", -1
    for delimiter in (";", "\t", ",", "|"):
        widths = [len(r) for r in csv.reader(sample, delimiter=delimiter)]
        most_common = max(set(widths), key=widths.count)
        score = widths.count(most_common) * (most_common - 1)
        if score > best_score:
            best, best_score = delimiter, score
    if best_score <= 0:
        raise FormatError("No reconozco el formato: no parece ni Excel ni un fichero de texto con columnas.")
    return [list(r) for r in csv.reader(lines, delimiter=best)]
