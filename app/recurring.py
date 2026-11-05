"""Detección de pagos e ingresos periódicos a partir del historial de cada comercio."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import mean, median, pstdev

# nombre, intervalo mínimo y máximo (días), periodo nominal, veces al año, variación máxima de importe
FREQUENCIES = [
    ("Semanal", 6, 8, 7, 52.18, 0.15),
    ("Mensual", 26, 35, 30.44, 12, 0.40),
    ("Bimestral", 55, 66, 60.88, 6, 0.40),
    ("Trimestral", 85, 97, 91.31, 4, 0.40),
    ("Semestral", 175, 190, 182.62, 2, 0.40),
    ("Anual", 350, 380, 365.25, 1, 0.40),
]
_MIN_OCCURRENCES = {"Anual": 2, "Semestral": 2, "Trimestral": 3}
_HISTORY = 12
# Con dos apariciones al año, las vacaciones de agosto parecerían un cargo anual fijo.
_DISCRETIONARY = {"viajes", "compras", "ocio", "restaurantes", "supermercado", "hogar", "transfer_in", "transfer_out"}
_INFREQUENT = {"Semestral", "Anual"}


@dataclass
class Recurring:
    merchant: str
    category_id: str
    category: str
    direction: str  # 'in' | 'out'
    frequency: str
    period_days: float
    per_year: float
    occurrences: int
    avg_cents: int
    last_cents: int
    first_date: str
    last_date: str
    next_date: str
    annual_cents: int
    active: bool
    amount_variation: float
    amounts: list[int]
    dates: list[str]
    tx_ids: list[int]
    account_id: int


def detect_recurring(txs: list[dict], anchor: date) -> list[Recurring]:
    """txs: movimientos en orden cronológico. Excluye traspasos internos y retiradas de efectivo."""
    groups: dict[tuple, list[dict]] = {}
    for tx in txs:
        if tx["kind"] == "transfer" or tx["category_id"] == "efectivo" or tx["amount_cents"] == 0:
            continue
        direction = "in" if tx["amount_cents"] > 0 else "out"
        groups.setdefault((tx["account_id"], tx["merchant"], direction), []).append(tx)

    found: list[Recurring] = []
    for (account_id, merchant, direction), group in groups.items():
        recent = group[-_HISTORY:]
        if len(recent) < 2:
            continue
        days = [date.fromisoformat(t["date"]) for t in recent]
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        typical_gap = median(gaps)
        freq = next((f for f in FREQUENCIES if f[1] <= typical_gap <= f[2]), None)
        if not freq:
            continue
        name, low, high, period, per_year, max_variation = freq
        if len(recent) < _MIN_OCCURRENCES.get(name, 3):
            continue
        if name in _INFREQUENT and recent[-1]["category_id"] in _DISCRETIONARY:
            continue
        regular = sum(low <= g <= high for g in gaps) / len(gaps)
        if regular < 0.7:
            continue
        amounts = [abs(t["amount_cents"]) for t in recent]
        avg = mean(amounts)
        variation = pstdev(amounts) / avg if avg else 0
        if variation > max_variation:
            continue
        last_day = days[-1]
        active = (anchor - last_day).days <= period * 1.5 + 7
        step = timedelta(days=round(period))
        next_day = last_day + step
        found.append(Recurring(
            merchant=merchant,
            category_id=recent[-1]["category_id"],
            category=recent[-1]["category_name"],
            direction=direction,
            frequency=name,
            period_days=period,
            per_year=per_year,
            occurrences=len(group),
            avg_cents=round(avg),
            last_cents=amounts[-1],
            first_date=group[0]["date"],
            last_date=recent[-1]["date"],
            next_date=next_day.isoformat(),
            annual_cents=round(avg * per_year),
            active=active,
            amount_variation=round(variation, 4),
            amounts=amounts,
            dates=[t["date"] for t in recent],
            tx_ids=[t["id"] for t in recent],
            account_id=account_id,
        ))
    found.sort(key=lambda r: -r.annual_cents)
    return found
