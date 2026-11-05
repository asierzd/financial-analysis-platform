"""Normalización de textos bancarios, limpieza del nombre de comercio y clasificación por reglas."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from .seed import FALLBACK_EXPENSE, FALLBACK_INCOME

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalize(text: str | None) -> str:
    """'Pago en CAFÉ-Bar Nº3' → 'PAGO EN CAFE BAR N 3'. Mayúsculas, sin acentos ni signos."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c)).upper()
    return _NON_ALNUM.sub(" ", ascii_only).strip()


# Prefijos que describen la operación, no al comercio.
_PREFIXES = [
    "PAGO MOVIL EN", "COMPRA INTERNET EN", "COMPRA INTERNET", "TRANSACCION CONTACTLESS EN", "TRANSACCION CONTACTLESS",
    "TRANSFERENCIA INMEDIATA A FAVOR DE", "TRANSFERENCIA INMEDIATA DE", "TRANSFERENCIA INMEDIATA A",
    "TRANSFERENCIA INMEDIATA", "DISPOSICION EN CAJERO", "DISPOSICION EFECTIVO",
    "PAGO CON TARJETA DE CREDITO", "PAGO CON TARJETA DE DEBITO", "PAGO CON TARJETA EN", "PAGO CON TARJETA",
    "PAGO CON MOVIL", "PAGO MOVIL", "PAGO EN", "COMPRA TARJETA", "COMPRA TARJ", "COMPRA CON TARJETA", "COMPRA EN",
    "COMPRA", "ADEUDO DIRECTO", "ADEUDO RECIBO", "ADEUDO DE", "ADEUDO", "RECIBO DE", "RECIBO", "CARGO POR", "CARGO DE",
    "DOMICILIACION", "TRANSFERENCIA RECIBIDA DE", "TRANSFERENCIA RECIBIDA", "TRANSFERENCIA REALIZADA A",
    "TRANSFERENCIA REALIZADA", "TRANSFERENCIA DE", "TRANSFERENCIA A FAVOR DE", "TRANSFERENCIA A", "TRANSFERENCIA",
    "TRANSF A", "TRANSF DE", "TRANSF", "BIZUM A FAVOR DE", "BIZUM DE", "BIZUM A", "BIZUM ENVIADO A", "BIZUM RECIBIDO DE",
    "BIZUM ENVIADO", "BIZUM RECIBIDO", "ABONO DE", "ABONO", "SEPA", "TARJETA", "TARJ",
]
_PREFIXES.sort(key=len, reverse=True)
# Lo que sigue a estas palabras describe la operación, no a quién paga o cobra.
_TRAILING_DETAIL = re.compile(r"\b(?:CONCEPTO|TARJ|TARJETA|REF|REFERENCIA|MANDATO|NO RECIBO)\b.*$")
_LEGAL = {"SA", "SL", "SAU", "SLU", "SLL", "SCP", "SCCL", "CB", "SOCIEDAD", "ANONIMA", "LIMITADA", "ESPANA", "SPAIN", "EU", "ES", "COM"}
_ARTICLES = {"LA", "EL", "LOS", "LAS", "DE", "DEL", "THE"}
_NOISE = re.compile(r"^(?:\d+|X{2,}|[A-Z]?\d{3,}[A-Z0-9]*|[0-9X]{4,}|REF|REFERENCIA|NUM|N)$")
_LOWER_WORDS = {"de", "del", "la", "el", "los", "las", "y", "en"}


def _strip_prefix(text: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if text == prefix:
                return ""
            if text.startswith(prefix + " "):
                text = text[len(prefix) + 1:]
                changed = True
                break
    return text


def clean_merchant(concept: str, movement: str = "", notes: str = "") -> str:
    """Saca un nombre estable de comercio/pagador. Se queda con dos palabras significativas."""
    for source in (concept, notes, movement):
        # Hasta la primera coma: "TIENDA X, MADRID, Tarj." y "TIENDA X, BILBAO" son el mismo comercio.
        text = _TRAILING_DETAIL.sub("", _strip_prefix(normalize((source or "").split(",")[0])))
        tokens = [t for t in text.split() if t not in _LEGAL and not _NOISE.match(t)]
        if not tokens:
            continue
        picked: list[str] = []
        significant = 0
        for token in tokens:
            picked.append(token)
            if token not in _ARTICLES:
                significant += 1
            if significant == 2:
                break
        while picked and picked[-1] in _ARTICLES:
            picked.pop()
        if picked:
            return _title(" ".join(picked))
    return normalize(concept).title() or "Desconocido"


def _title(text: str) -> str:
    words = text.lower().split()
    return " ".join(w if (w in _LOWER_WORDS and i) else w.capitalize() for i, w in enumerate(words))


@dataclass(frozen=True)
class Rule:
    id: int
    needle: str  # patrón rodeado de espacios: garantiza coincidencia de palabra completa
    category_id: str
    sign: str
    label: str | None


@dataclass(frozen=True)
class Classification:
    category_id: str
    source: str  # 'rule' | 'default'
    rule_id: int | None
    merchant: str


class Classifier:
    """Reglas de usuario primero; luego las del sistema por prioridad y, a igualdad, la más específica."""

    def __init__(self, conn: sqlite3.Connection):
        rows = conn.execute(
            "SELECT id, pattern, category_id, sign, label FROM rules WHERE enabled = 1 "
            "ORDER BY CASE source WHEN 'user' THEN 0 ELSE 1 END, priority DESC, LENGTH(pattern) DESC, id"
        ).fetchall()
        self.rules = [Rule(r["id"], f" {r['pattern']} ", r["category_id"], r["sign"], r["label"]) for r in rows]

    def classify(self, concept: str, movement: str, notes: str, amount_cents: int) -> Classification:
        haystack = f" {normalize(concept)} {normalize(movement)} {normalize(notes)} "
        direction = "in" if amount_cents >= 0 else "out"
        merchant = clean_merchant(concept, movement, notes)
        for rule in self.rules:
            if rule.sign != "any" and rule.sign != direction:
                continue
            if rule.needle in haystack:
                return Classification(rule.category_id, "rule", rule.id, rule.label or merchant)
        fallback = FALLBACK_INCOME if amount_cents >= 0 else FALLBACK_EXPENSE
        return Classification(fallback, "default", None, merchant)


def reclassify_all(conn: sqlite3.Connection) -> int:
    """Reaplica reglas respetando las categorías puestas a mano (category_source='user')."""
    classifier = Classifier(conn)
    changed = 0
    rows = conn.execute(
        "SELECT id, concept, movement, notes, amount_cents, category_id, category_source, merchant, rule_id FROM transactions"
    ).fetchall()
    for tx in rows:
        result = classifier.classify(tx["concept"], tx["movement"], tx["notes"], tx["amount_cents"])
        if tx["category_source"] == "user":
            if result.merchant != tx["merchant"]:
                conn.execute("UPDATE transactions SET merchant = ? WHERE id = ?", (result.merchant, tx["id"]))
            continue
        if (result.category_id, result.source, result.rule_id, result.merchant) != (
            tx["category_id"], tx["category_source"], tx["rule_id"], tx["merchant"],
        ):
            conn.execute(
                "UPDATE transactions SET category_id=?, category_source=?, rule_id=?, merchant=? WHERE id=?",
                (result.category_id, result.source, result.rule_id, result.merchant, tx["id"]),
            )
            changed += 1
    return changed
