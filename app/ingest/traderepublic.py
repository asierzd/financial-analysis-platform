"""Importador de Trade Republic: la «exportación de transacciones» CSV de la app (desde abril de 2026).

Trade Republic no publica el formato. Esto se basa en lo observado en exportaciones reales por proyectos de código
abierto: 23 columnas con nombres fijos en inglés, separador coma, punto decimal y fechas ISO en UTC.

Diferencias con un banco:
- Es también un bróker: una compra o venta mueve dinero, pero no es un gasto ni un ingreso.
- `amount`, `fee` y `tax` van por separado, y aquí cada uno se convierte en su propio movimiento. Así las comisiones
  y las retenciones se ven como lo que son y la suma de los tres da lo que cambia el efectivo.
- No trae saldo: se calcula sumando movimientos (ver service.recompute_computed_balances).
- `transaction_id` identifica cada fila, así que deduplicar no depende del saldo.
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .base import (
    MOV_INTEREST,
    MOV_ORDER_FEE,
    MOV_WITHHOLDING,
    BankParser,
    FormatError,
    ParsedEvent,
    ParsedTx,
    ParseResult,
    RawFile,
    register,
)
from .tabular import parse_date

REQUIRED_COLUMNS = {"datetime", "category", "type", "amount", "fee", "tax", "transaction_id", "symbol", "shares"}
INVESTMENT_CATEGORY = "inversiones"
_DIRECT_DEBIT_TO = re.compile(r"(?i)\b(?:transfer|debit)\s+to\s+(.+)$")

# Categoría según el código de comercio de la tarjeta. Se usa solo si ninguna regla reconoce el comercio.
_MCC_RANGES = [
    (3000, 3350, "viajes"), (3351, 3500, "viajes"), (3501, 3999, "viajes"), (4511, 4511, "viajes"), (4722, 4722, "viajes"),
    (7011, 7012, "viajes"), (4011, 4131, "transporte"), (4784, 4789, "transporte"), (5511, 5599, "transporte"),
    (7511, 7549, "transporte"), (4812, 4816, "recibos"), (4899, 4900, "recibos"), (5200, 5261, "hogar"), (5712, 5722, "hogar"),
    (5300, 5311, "compras"), (5331, 5331, "compras"), (5600, 5699, "compras"), (5732, 5735, "compras"), (5940, 5940, "deporte"),
    (5941, 5941, "deporte"), (5942, 5999, "compras"), (5411, 5499, "supermercado"), (5811, 5814, "restaurantes"),
    (5815, 5818, "suscripciones"), (5912, 5912, "salud"), (8011, 8099, "salud"), (5977, 5977, "cuidado"), (7230, 7298, "cuidado"),
    (6010, 6011, "efectivo"), (6300, 6399, "seguros"), (7832, 7841, "ocio"), (7922, 7996, "ocio"), (7997, 7997, "deporte"),
    (8211, 8299, "educacion"), (8398, 8398, "donaciones"), (0, 0, "sin_clasificar"),
]


def mcc_category(code: str) -> str | None:
    if not code or not code.strip().isdigit():
        return None
    value = int(code)
    for low, high, category in _MCC_RANGES:
        if low <= value <= high:
            return category
    return None


def parse_decimal(cell) -> Decimal | None:
    """Número sin formatos locales. Tolera la coma decimal por si el fichero se guardó desde Excel en español."""
    if cell is None or isinstance(cell, bool):
        return None
    text = str(cell).strip().replace("−", "-")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".") if "." in text and text.rfind(",") > text.rfind(".") else text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def to_cents(value: Decimal | None) -> int:
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if value is not None else 0


def _text(cell) -> str:
    if cell is None:
        return ""
    # Las exportaciones reales traen a veces un espacio duro mal codificado («Â »).
    return " ".join(str(cell).replace("Â ", " ").replace("Â ", " ").replace(" ", " ").split())


@register
class TradeRepublicParser(BankParser):
    id = "traderepublic"
    bank = "Trade Republic"

    def detect(self, raw: RawFile) -> float:
        return 1.0 if _header_of(raw) else 0.0

    def parse(self, raw: RawFile) -> ParseResult:
        found = _header_of(raw)
        if not found:
            raise FormatError("No encuentro las columnas de la exportación de Trade Republic.")
        rows, columns = found
        records = [{name: row[i] if i < len(row) else None for name, i in columns.items()} for row in rows]
        records = [r for r in records if _text(r.get("transaction_id")) or _text(r.get("datetime"))]
        records.sort(key=lambda r: _text(r.get("datetime")))

        result = ParseResult(self.id, self.bank, [], computed_balance=True)
        unknown: Counter[str] = Counter()
        corrected_dividends = 0
        seen_ids: set[str] = set()
        for record in records:
            tx_id = _text(record.get("transaction_id"))
            if tx_id and tx_id in seen_ids:
                continue  # la misma fila repetida dentro del fichero
            seen_ids.add(tx_id)
            kind = _row(record, result, unknown)
            corrected_dividends += kind == "corrected_dividend"

        if not result.transactions and not result.events:
            raise FormatError("El fichero de Trade Republic no contiene movimientos.")
        if unknown:
            detail = ", ".join(f"{name} ({count})" for name, count in unknown.most_common(6))
            result.warnings.append(f"Hay tipos de operación que no conozco y he tratado según su signo: {detail}. Revísalos.")
        if corrected_dividends:
            result.warnings.append(
                f"{corrected_dividends} dividendos aparecen como «corregidos». Si también está el dividendo original, "
                "podría estar contado dos veces: compáralos con la app."
            )
        return result


def _header_of(raw: RawFile):
    for sheet in raw.sheets:
        for index, row in enumerate(sheet.rows[:5]):
            names = {str(cell).strip().lower(): i for i, cell in enumerate(row) if cell is not None}
            if REQUIRED_COLUMNS <= names.keys():
                return sheet.rows[index + 1:], names
    return None


def _row(record: dict, result: ParseResult, unknown: Counter) -> str:
    category = _text(record.get("category")).upper()
    row_type = _text(record.get("type")).upper()
    when = parse_date(_text(record.get("date")) or _text(record.get("datetime"))[:10])
    if when is None:
        result.warnings.append(f"Fila sin fecha legible omitida ({_text(record.get('transaction_id')) or row_type}).")
        return "skipped"
    occurred_at = _text(record.get("datetime"))
    tx_id = _text(record.get("transaction_id"))
    name = _text(record.get("name"))
    symbol = _text(record.get("symbol"))
    description = _text(record.get("description"))
    reference = _text(record.get("payment_reference"))
    notes = " · ".join(part for part in (description, reference) if part)
    original_amount = _text(record.get("original_amount"))
    original_currency = _text(record.get("original_currency"))
    if original_amount and original_currency and original_currency != "EUR":
        notes = " · ".join(part for part in (notes, f"Original: {original_amount} {original_currency}") if part)

    amount = to_cents(parse_decimal(record.get("amount")))
    fee = to_cents(parse_decimal(record.get("fee")))
    tax = to_cents(parse_decimal(record.get("tax")))
    shares = parse_decimal(record.get("shares"))
    price = parse_decimal(record.get("price"))
    currency = (_text(record.get("currency")) or "EUR").upper()[:3]
    asset_class = _text(record.get("asset_class")).upper()

    outcome = "ok"
    concept, movement, fixed, fallback = "", "", None, None
    event_kind: str | None = None
    is_order = False

    if row_type in ("BUY", "SELL", "BUY_CANCELLED") or (row_type == "PRIVATE_MARKET_BUY" and shares and shares > 0):
        is_order = True
        selling = row_type == "SELL"
        event_kind = {"SELL": "sell", "BUY_CANCELLED": "cancel"}.get(row_type, "buy")
        verb = "Venta" if selling else "Anulación de compra" if row_type == "BUY_CANCELLED" else "Compra"
        concept, movement, fixed = f"{verb} de {name or symbol}", verb, INVESTMENT_CATEGORY
    elif row_type == "DIVIDEND":
        event_kind = "dividend"
        concept, movement, fixed = f"Dividendo de {name or symbol}", "Dividendo", "intereses"
        if "correct" in description.lower():
            outcome = "corrected_dividend"
    elif row_type == "INTEREST_PAYMENT":
        concept, movement, fixed = "Intereses de la cuenta remunerada", MOV_INTEREST, "intereses"
    elif row_type == "BENEFITS_SAVEBACK":
        concept, movement, fixed = "Saveback de Trade Republic", "Saveback", "saveback"
    elif row_type == "CARD_ORDERING_FEE":
        concept, movement, fixed = "Comisión de emisión de tarjeta", "Comisión", "comisiones"
    elif row_type == "PRE_DETERMINED_TAX_BASE":
        concept, movement, fixed = f"Impuesto anticipado sobre {name or symbol}", "Impuesto", "impuestos"
    elif row_type.startswith("CARD_TRANSACTION"):
        concept, movement, fallback = name or description, "Pago con tarjeta", mcc_category(_text(record.get("mcc_code")))
    elif row_type == "TRANSFER_DIRECT_DEBIT_INBOUND":  # pese al nombre, es un cargo
        match = _DIRECT_DEBIT_TO.search(description)
        concept, movement, fallback = (match.group(1) if match else name or description), "Adeudo", "recibos"
    elif row_type.startswith("TRANSFER") or row_type == "CUSTOMER_INPAYMENT":
        concept = _text(record.get("counterparty_name")) or name or description
        movement, fallback = "Transferencia", "transfer_in" if amount >= 0 else "transfer_out"
    elif category == "DELIVERY":
        event_kind = "reward" if row_type == "FREE_RECEIPT" else "delivery"
        verb = "Recompensa" if row_type == "FREE_RECEIPT" else "Traspaso de títulos"
        concept, movement, fixed = f"{verb} de {name or symbol}", verb, INVESTMENT_CATEGORY
    elif category in ("CORPORATE_ACTION", "TRADING") or symbol:
        # Fusiones, liquidaciones, vencimientos... El significado de sus títulos no está claro: no se mueve la posición.
        event_kind = "corporate"
        unknown[row_type] += 1
        concept, movement, fixed = f"Operación corporativa de {name or symbol}", "Operación corporativa", INVESTMENT_CATEGORY
    else:
        unknown[row_type or "SIN TIPO"] += 1
        concept, movement = name or description or row_type, row_type.replace("_", " ").capitalize()

    def add(cents: int, text: str, mov: str, suffix: str, fixed_category: str | None, fallback_category: str | None) -> None:
        if cents == 0:
            return
        result.transactions.append(ParsedTx(
            date=when, amount_cents=cents, concept=text, movement=mov, notes=notes, currency=currency,
            external_id=f"{tx_id}{suffix}" if tx_id else None, occurred_at=occurred_at,
            fixed_category=fixed_category, fallback_category=fallback_category,
        ))

    add(amount, concept, movement, "", fixed, fallback)
    add(fee, f"Comisión: {concept}", MOV_ORDER_FEE if is_order else "Comisión", ":fee", "comisiones", None)
    add(tax, f"Retención: {concept}", MOV_WITHHOLDING, ":tax", "impuestos", None)

    if event_kind and symbol:
        result.events.append(ParsedEvent(
            external_id=tx_id, date=when, occurred_at=occurred_at, kind=event_kind, isin=symbol, name=name or symbol,
            asset_class=asset_class, shares=shares, price=price, amount_cents=amount, fee_cents=fee, tax_cents=tax, notes=description,
        ))
    return outcome
