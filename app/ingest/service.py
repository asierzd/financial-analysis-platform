"""Orquesta la importación: lee, detecta el banco, deduplica, clasifica y guarda."""
from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from dataclasses import dataclass

from ..categorize import Classifier, normalize
from . import detect_parser
from .base import FormatError, ParsedTx, ParseResult, RawFile
from .readers import read_file
from .tabular import ColumnMap, find_header, map_header, rows_to_transactions

PREVIEW_ROWS = 25


@dataclass
class ManualMapping:
    """Columnas elegidas a mano en la pantalla de importación, para ficheros que no se reconocen."""
    sheet: int
    header_row: int
    date: int
    concept: int
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    value_date: int | None = None
    notes: int | None = None
    balance: int | None = None


def parse_upload(data: bytes, filename: str, manual: ManualMapping | None = None) -> tuple[RawFile, ParseResult | None]:
    raw = read_file(data, filename)
    if manual:
        return raw, _parse_manual(raw, manual)
    parser = detect_parser(raw)
    return raw, parser.parse(raw) if parser else None


def _parse_manual(raw: RawFile, m: ManualMapping) -> ParseResult:
    if not 0 <= m.sheet < len(raw.sheets):
        raise FormatError("La hoja seleccionada no existe.")
    rows = raw.sheets[m.sheet].rows
    if not 0 <= m.header_row < len(rows):
        raise FormatError("La fila de cabecera seleccionada no existe.")
    if m.amount is None and m.debit is None and m.credit is None:
        raise FormatError("Indica la columna de importe, o las de cargo y abono.")
    mapping = ColumnMap(
        date=m.date, value_date=m.value_date, concept=m.concept, amount=m.amount, debit=m.debit, credit=m.credit,
        balance=m.balance, notes=[m.notes] if m.notes is not None else [],
    )
    transactions, warnings = rows_to_transactions(rows[m.header_row + 1:], mapping)
    if not transactions:
        raise FormatError("Con esas columnas no se ha podido leer ningún movimiento. Revisa la fecha y el importe.")
    return ParseResult("manual", "Otro banco", transactions, warnings)


def raw_preview(raw: RawFile) -> dict:
    """Datos para la pantalla de mapeo manual, con la mejor suposición de cabecera y columnas."""
    best_sheet, best_header, suggestion = 0, 0, {}
    for sheet_index, sheet in enumerate(raw.sheets):
        found = find_header(sheet)
        if found:
            best_sheet, best_header = sheet_index, found[0]
            break
    else:
        # Sin cabecera conocida: la primera fila ancha seguida de más filas anchas.
        for sheet_index, sheet in enumerate(raw.sheets):
            widths = [sum(c not in (None, "") for c in r) for r in sheet.rows[:40]]
            if widths and max(widths) >= 2:
                best_sheet, best_header = sheet_index, widths.index(max(widths))
                break
    sheet = raw.sheets[best_sheet] if raw.sheets else None
    rows = sheet.rows if sheet else []
    if rows and best_header < len(rows):
        m = map_header(rows[best_header])
        suggestion = {k: getattr(m, k) for k in ("date", "value_date", "concept", "amount", "debit", "credit", "balance")}
        suggestion["notes"] = m.notes[0] if m.notes else None
    return {
        "sheets": [s.name for s in raw.sheets],
        "sheet": best_sheet,
        "header_row": best_header,
        "rows": [[_show(c) for c in r] for r in rows[:40]],
        "suggestion": suggestion,
    }


def _show(cell) -> str:
    return "" if cell is None else str(cell)[:80]


def fingerprints(account_id: int, transactions: list[ParsedTx]) -> list[str]:
    """Huella estable para no duplicar al reimportar ficheros solapados.

    Dos movimientos realmente idénticos el mismo día se distinguen por el saldo; si el fichero no trae
    saldo, por su número de aparición. Así no se confunde un cargo doble real (que la alerta de duplicados
    debe detectar) con haber subido dos veces el mismo extracto.
    """
    seen: Counter[str] = Counter()
    result = []
    for tx in transactions:
        base = "|".join(str(p) for p in (
            account_id, tx.date.isoformat(), tx.value_date.isoformat() if tx.value_date else "",
            tx.amount_cents, normalize(tx.concept), tx.balance_cents if tx.balance_cents is not None else "",
        ))
        result.append(hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest())
        seen[base] += 1
    return result


def chronological(transactions: list[ParsedTx]) -> list[ParsedTx]:
    """Los extractos suelen venir del más reciente al más antiguo; el orden original manda dentro de cada día."""
    if len(transactions) > 1 and transactions[0].date > transactions[-1].date:
        transactions = list(reversed(transactions))
    return sorted(transactions, key=lambda t: t.date)


def count_existing(conn: sqlite3.Connection, prints: list[str]) -> int:
    existing = 0
    for start in range(0, len(prints), 500):
        chunk = prints[start:start + 500]
        marks = ",".join("?" * len(chunk))
        existing += conn.execute(f"SELECT COUNT(*) FROM transactions WHERE fingerprint IN ({marks})", chunk).fetchone()[0]
    return existing


def preview(conn: sqlite3.Connection, result: ParseResult, account_id: int | None) -> dict:
    ordered = chronological(result.transactions)
    existing = count_existing(conn, fingerprints(account_id, ordered)) if account_id else 0
    classifier = Classifier(conn)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM categories")}
    sample, unclassified = [], 0
    for tx in ordered:
        c = classifier.classify(tx.concept, tx.movement, tx.notes, tx.amount_cents)
        unclassified += c.source == "default"
    for tx in reversed(ordered[-PREVIEW_ROWS:]):
        c = classifier.classify(tx.concept, tx.movement, tx.notes, tx.amount_cents)
        sample.append({
            "date": tx.date.isoformat(), "concept": tx.concept, "merchant": c.merchant,
            "amount_cents": tx.amount_cents, "balance_cents": tx.balance_cents,
            "category": names.get(c.category_id, c.category_id),
        })
    return {
        "parser": result.parser_id,
        "bank": result.bank,
        "total": len(ordered),
        "new": len(ordered) - existing,
        "existing": existing,
        "date_from": ordered[0].date.isoformat(),
        "date_to": ordered[-1].date.isoformat(),
        "account_hint": result.account_hint,
        "warnings": result.warnings + _currency_warning(ordered),
        "classified": len(ordered) - unclassified,
        "sample": sample,
    }


def _currency_warning(transactions: list[ParsedTx]) -> list[str]:
    others = sorted({t.currency for t in transactions if t.currency != "EUR"})
    if not others:
        return []
    return [f"Hay movimientos en {', '.join(others)}. La aplicación los sumará como si fueran euros, sin convertirlos."]


def store(conn: sqlite3.Connection, result: ParseResult, filename: str, account_id: int) -> dict:
    ordered = chronological(result.transactions)
    prints = fingerprints(account_id, ordered)
    cursor = conn.execute(
        "INSERT INTO imports (account_id, filename, parser, rows_total, rows_new, rows_skipped, date_from, date_to) "
        "VALUES (?,?,?,?,0,0,?,?)",
        (account_id, filename[:200], result.parser_id, len(ordered), ordered[0].date.isoformat(), ordered[-1].date.isoformat()),
    )
    import_id = cursor.lastrowid
    classifier = Classifier(conn)
    start_seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM transactions").fetchone()[0]
    added = 0
    for offset, (tx, fingerprint) in enumerate(zip(ordered, prints), start=1):
        c = classifier.classify(tx.concept, tx.movement, tx.notes, tx.amount_cents)
        cur = conn.execute(
            "INSERT OR IGNORE INTO transactions (account_id, import_id, date, value_date, amount_cents, currency, "
            "balance_cents, concept, movement, notes, merchant, category_id, category_source, rule_id, seq, fingerprint) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (account_id, import_id, tx.date.isoformat(), tx.value_date.isoformat() if tx.value_date else None,
             tx.amount_cents, tx.currency, tx.balance_cents, tx.concept, tx.movement, tx.notes, c.merchant,
             c.category_id, c.source, c.rule_id, start_seq + offset, fingerprint),
        )
        added += cur.rowcount
    skipped = len(ordered) - added
    conn.execute("UPDATE imports SET rows_new=?, rows_skipped=? WHERE id=?", (added, skipped, import_id))
    return {"import_id": import_id, "new": added, "skipped": skipped, "total": len(ordered), "warnings": result.warnings}
