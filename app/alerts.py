"""Motor de alertas: comisiones, duplicados, importes raros, cambios de precio, recibos que faltan...

Cada alerta tiene una huella estable: al recalcular se conserva lo que el usuario marcó como revisado
o descartado, y las que dejan de cumplirse se ocultan sin borrarse.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, median, pstdev

from .analytics import load_txs
from .db import get_settings
from .recurring import Recurring, detect_recurring

MIN_MERCHANT_HISTORY = 8
NEW_MERCHANT_GRACE_DAYS = 60
REFUND_WINDOW_DAYS = 45
HABIT_MIN_OCCURRENCES = 4
CHAIN_MAX_BREAK_RATIO = 0.2
STABLE_PRICE_VARIATION = 0.01  # un recibo de luz o agua varía siempre: solo vigila precios fijos
NEW_RECURRING_MAX_PERIOD = 100  # con dos cobros anuales no se distingue lo nuevo de lo de siempre
OUTLIER_EXCLUDED = {"vivienda", "prestamos", "impuestos", "ahorro", "transfer_out", "internos", "comisiones"}

ALERT_TYPES = {
    "fee": "Comisión",
    "duplicate": "Posible duplicado",
    "outlier": "Importe inusual",
    "new_merchant": "Comercio nuevo",
    "price_increase": "Subida de precio",
    "missing_recurring": "Cargo o ingreso ausente",
    "new_recurring": "Nuevo cargo periódico",
    "negative_balance": "Saldo negativo",
    "low_balance": "Saldo bajo",
    "budget": "Presupuesto",
    "balance_gap": "Saldo que no cuadra",
    "foreign_currency": "Otra divisa",
}


def eur(cents: int) -> str:
    text = f"{abs(cents) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if cents < 0 else ''}{text} €"


@dataclass
class Candidate:
    fingerprint: str
    type: str
    severity: str
    title: str
    detail: str
    date: str
    amount_cents: int | None = None
    tx_ids: list[int] = field(default_factory=list)
    account_id: int | None = None


def _detect_fees(txs: list[dict]) -> list[Candidate]:
    return [
        Candidate(
            f"fee:{t['fingerprint']}", "fee", "warning",
            f"Comisión de {eur(-t['amount_cents'])}",
            f"{t['concept']}. Reclámala al banco si no te corresponde o si tu cuenta debería estar exenta.",
            t["date"], t["amount_cents"], [t["id"]], t["account_id"],
        )
        for t in txs if t["category_id"] == "comisiones" and t["amount_cents"] < 0
    ]


def _detect_duplicates(txs: list[dict], settings: dict) -> list[Candidate]:
    window = int(settings["dup_window_days"])
    minimum = settings["dup_min_eur"] * 100
    groups: dict[tuple, list[dict]] = defaultdict(list)
    refunds: dict[tuple, list[date]] = defaultdict(list)
    for t in txs:
        if t["kind"] == "transfer":
            continue
        key = (t["account_id"], t["merchant"], abs(t["amount_cents"]))
        if t["amount_cents"] < 0:
            groups[key].append(t)
        elif t["amount_cents"] > 0:
            refunds[key].append(date.fromisoformat(t["date"]))

    found = []
    for key, group in groups.items():
        if key[2] < minimum or len(group) < 2:
            continue
        days = [date.fromisoformat(t["date"]) for t in group]
        close = [(b - a).days <= window for a, b in zip(days, days[1:])]
        # Repetir el mismo importe es la norma en este comercio (el café de cada mañana): no es un duplicado.
        if len(group) >= HABIT_MIN_OCCURRENCES and sum(close) / len(close) > 0.5:
            continue
        cluster = [group[0]]
        for tx, is_close in zip(group[1:], close):
            if is_close:
                cluster.append(tx)
                continue
            found.extend(_duplicate_alert(cluster, refunds[key]))
            cluster = [tx]
        found.extend(_duplicate_alert(cluster, refunds[key]))
    return found


def _duplicate_alert(cluster: list[dict], refund_days: list[date]) -> list[Candidate]:
    if len(cluster) < 2:
        return []
    start = date.fromisoformat(cluster[0]["date"])
    limit = date.fromisoformat(cluster[-1]["date"]) + timedelta(days=REFUND_WINDOW_DAYS)
    if any(start <= d <= limit for d in refund_days):
        return []  # ya lo devolvieron
    first, last = cluster[0], cluster[-1]
    same_day = first["date"] == last["date"]
    cash = first["category_id"] == "efectivo"
    severity = "info" if cash else "critical" if same_day else "warning"
    when = "el mismo día" if same_day else f"en {(date.fromisoformat(last['date']) - start).days} días"
    return [Candidate(
        f"dup:{cluster[0]['fingerprint']}:{len(cluster)}", "duplicate", severity,
        f"{len(cluster)} cargos iguales de {eur(-first['amount_cents'])} en {first['merchant']}",
        f"Mismo comercio y mismo importe {when}. Si solo hiciste una compra, pide al comercio o al banco que anule el resto. "
        f"Podrías recuperar {eur(-first['amount_cents'] * (len(cluster) - 1))}.",
        last["date"], first["amount_cents"] * len(cluster), [t["id"] for t in cluster], first["account_id"],
    )]


def _robust_z(value: float, history: list[float]) -> tuple[float, float]:
    """z-score robusto: mediana y MAD, que una compra grande anterior no distorsiona como la media."""
    center = median(history)
    deviation = median(abs(x - center) for x in history)
    if deviation > 0:
        return 0.6745 * (value - center) / deviation, center
    spread = mean(abs(x - center) for x in history)
    return ((value - center) / (1.2533 * spread) if spread > 0 else 0.0), center


def _outlier_detail(basis: str, typical: float, amount: int) -> str:
    times = f"{amount / typical:.1f}".replace(".", ",")
    return f"{basis} ronda {eur(round(typical))}; este cargo multiplica esa cifra por {times}."


def _detect_outliers(txs: list[dict], settings: dict) -> list[Candidate]:
    """Compara solo con el historial del propio comercio. Comparar con la categoría da falsos positivos:
    un taller frente al billete de metro, o un seguro anual frente a uno mensual, parecen anomalías sin serlo.
    El primer cargo alto con un nombre desconocido ya lo cubre _detect_new_merchants."""
    threshold = settings["outlier_z"]
    minimum = settings["outlier_min_eur"] * 100
    history: dict[str, list[int]] = defaultdict(list)
    found = []
    for t in txs:  # en orden cronológico: se compara solo con lo anterior a cada movimiento
        if t["amount_cents"] >= 0 or t["kind"] != "expense":
            continue
        amount = -t["amount_cents"]
        past = history[t["merchant"]]
        if t["category_id"] not in OUTLIER_EXCLUDED and amount >= minimum and len(past) >= MIN_MERCHANT_HISTORY:
            z, typical = _robust_z(amount, past)
            if z >= threshold and amount >= 2 * typical and amount - typical >= minimum:
                found.append(Candidate(
                    f"outlier:{t['fingerprint']}", "outlier", "warning",
                    f"{eur(amount)} en {t['merchant']}: mucho más de lo habitual",
                    _outlier_detail(f"Lo normal en {t['merchant']}", typical, amount),
                    t["date"], t["amount_cents"], [t["id"]], t["account_id"],
                ))
        past.append(amount)
    return found


def _detect_new_merchants(txs: list[dict], settings: dict) -> list[Candidate]:
    if not txs:
        return []
    threshold = settings["new_merchant_eur"] * 100
    grace_end = (date.fromisoformat(txs[0]["date"]) + timedelta(days=NEW_MERCHANT_GRACE_DAYS)).isoformat()
    seen: set[str] = set()
    found = []
    for t in txs:
        merchant = t["merchant"]
        if merchant in seen:
            continue
        seen.add(merchant)
        # Al principio del historial todos los comercios parecen nuevos.
        if t["date"] < grace_end or t["kind"] == "transfer" or abs(t["amount_cents"]) < threshold:
            continue
        incoming = t["amount_cents"] > 0
        found.append(Candidate(
            f"new:{t['fingerprint']}", "new_merchant", "info" if incoming else "warning",
            f"Primer ingreso de {eur(abs(t['amount_cents']))} de {merchant}" if incoming
            else f"Primer cargo de {eur(abs(t['amount_cents']))} en {merchant}",
            "No hay movimientos anteriores con este nombre. Comprueba que lo reconoces.",
            t["date"], t["amount_cents"], [t["id"]], t["account_id"],
        ))
    return found


def _detect_recurring_changes(items: list[Recurring], anchor: date, settings: dict) -> list[Candidate]:
    found = []
    pct = settings["price_increase_pct"] / 100
    for r in items:
        # Recorre el historial, no solo el último cargo: la alerta debe seguir ahí cuando llegue el recibo siguiente.
        if r.direction == "out" and r.active:
            for i in range(3, len(r.amounts)):
                previous = r.amounts[max(0, i - 3):i]
                base = median(previous)
                current = r.amounts[i]
                stable = mean(previous) > 0 and pstdev(previous) / mean(previous) < STABLE_PRICE_VARIATION
                if stable and current > base * (1 + pct) and current - base >= 50:
                    found.append(Candidate(
                        f"price:{r.merchant}:{r.dates[i]}", "price_increase", "warning",
                        f"{r.merchant} ha subido de {eur(round(base))} a {eur(current)}",
                        f"Un {100 * (current - base) / base:.0f} % más: unos {eur(round((current - base) * r.per_year))} "
                        "al año. Revisa si te avisaron y si te compensa seguir.",
                        r.dates[i], -current, [r.tx_ids[i]], r.account_id,
                    ))

        due = date.fromisoformat(r.next_date)
        grace = timedelta(days=round(r.period_days * 0.25) + 5)
        limit = timedelta(days=round(r.period_days * 3))
        if due + grace < anchor <= due + limit:
            incoming = r.direction == "in"
            found.append(Candidate(
                f"missing:{r.merchant}:{r.direction}:{r.next_date}", "missing_recurring", "warning",
                f"{'No ha llegado' if incoming else 'No se ha cobrado'} {r.frequency.lower()} de {r.merchant}",
                f"Se esperaba hacia el {due.strftime('%d/%m/%Y')} ({eur(r.avg_cents)} de media). "
                + ("Comprueba que el pago sigue activo." if incoming else
                   "Puede que esté dado de baja o que el recibo haya fallado."),
                r.next_date, r.avg_cents if incoming else -r.avg_cents, r.tx_ids[-1:], r.account_id,
            ))

        started = date.fromisoformat(r.first_date)
        if (r.direction == "out" and r.active and r.period_days <= NEW_RECURRING_MAX_PERIOD
                and (anchor - started).days <= max(120, r.period_days * 4)):
            found.append(Candidate(
                f"newrec:{r.merchant}:{r.first_date}", "new_recurring", "info",
                f"Nuevo cargo {r.frequency.lower()} en {r.merchant}",
                f"Unos {eur(r.avg_cents)} cada vez, {eur(r.annual_cents)} al año. Comprueba que lo contrataste a propósito.",
                r.last_date, -r.avg_cents, r.tx_ids, r.account_id,
            ))
    return found


def _detect_balances(txs: list[dict], settings: dict) -> list[Candidate]:
    found = []
    per_account: dict[int, list[dict]] = defaultdict(list)
    for t in txs:
        if t["balance_cents"] is not None:
            per_account[t["account_id"]].append(t)
    for account_id, rows in per_account.items():
        # Saldo negativo: una alerta por episodio, al entrar en descubierto.
        was_negative = False
        for t in rows:
            negative = t["balance_cents"] < 0
            if negative and not was_negative:
                found.append(Candidate(
                    f"negative:{t['fingerprint']}", "negative_balance", "critical",
                    f"Saldo en negativo: {eur(t['balance_cents'])}",
                    f"La cuenta entró en descubierto tras «{t['concept']}». Puede generar intereses y comisiones.",
                    t["date"], t["balance_cents"], [t["id"]], account_id,
                ))
            was_negative = negative

        last = rows[-1]
        if 0 <= last["balance_cents"] < settings["low_balance_eur"] * 100:
            found.append(Candidate(
                f"low:{account_id}:{last['date']}", "low_balance", "warning",
                f"Saldo bajo: {eur(last['balance_cents'])}",
                "Está por debajo del mínimo que has configurado. Revisa los próximos cargos.",
                last["date"], last["balance_cents"], [last["id"]], account_id,
            ))

        # Cadena de saldos: saldo anterior + importe debe dar el saldo siguiente. Compara entre días, porque
        # el orden dentro de un mismo día no siempre es seguro.
        days: list[tuple[str, int, int]] = []  # fecha, saldo de cierre, suma de importes del día
        for t in rows:
            if days and days[-1][0] == t["date"]:
                days[-1] = (t["date"], t["balance_cents"], days[-1][2] + t["amount_cents"])
            else:
                days.append((t["date"], t["balance_cents"], t["amount_cents"]))
        breaks = [(a, b) for a, b in zip(days, days[1:]) if a[1] + b[2] != b[1]]
        if breaks and len(breaks) / max(len(days) - 1, 1) <= CHAIN_MAX_BREAK_RATIO:
            for a, b in breaks:
                gap = b[1] - (a[1] + b[2])
                found.append(Candidate(
                    f"gap:{account_id}:{a[0]}:{b[0]}", "balance_gap", "warning",
                    f"Faltan {eur(abs(gap))} por explicar entre el {_short(a[0])} y el {_short(b[0])}",
                    "El saldo no cuadra con los movimientos importados: seguramente falte algún movimiento. "
                    "Importa un extracto que cubra esas fechas.",
                    b[0], gap, [], account_id,
                ))
    return found


def _short(day: str) -> str:
    return date.fromisoformat(day).strftime("%d/%m/%Y")


def _detect_foreign(txs: list[dict]) -> list[Candidate]:
    return [
        Candidate(
            f"fx:{t['fingerprint']}", "foreign_currency", "info",
            f"Movimiento en {t['currency']}: {t['merchant']}",
            "La aplicación suma todos los importes como si fueran euros.",
            t["date"], t["amount_cents"], [t["id"]], t["account_id"],
        )
        for t in txs if t["currency"] != "EUR"
    ]


def _detect_budgets(conn: sqlite3.Connection, txs: list[dict], settings: dict) -> list[Candidate]:
    if not txs:
        return []
    month = txs[-1]["date"][:7]
    spent: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    for t in txs:
        if t["date"].startswith(month) and t["kind"] == "expense":
            spent[t["category_id"]] -= t["amount_cents"]
    for row in conn.execute("SELECT id, name FROM categories"):
        names[row["id"]] = row["name"]
    warn = settings["budget_warn_pct"] / 100
    found = []
    for row in conn.execute("SELECT category_id, monthly_cents FROM budgets"):
        category, budget = row["category_id"], row["monthly_cents"]
        used = spent.get(category, 0)
        if used > budget:
            found.append(Candidate(
                f"budget:{category}:{month}:over", "budget", "critical",
                f"Presupuesto superado en {names[category]}",
                f"Llevas {eur(used)} de {eur(budget)} este mes: {eur(used - budget)} de más.",
                txs[-1]["date"], -used,
            ))
        elif used >= budget * warn:
            found.append(Candidate(
                f"budget:{category}:{month}:near", "budget", "warning",
                f"Cerca del presupuesto en {names[category]}",
                f"Llevas {eur(used)} de {eur(budget)} este mes ({100 * used / budget:.0f} %).",
                txs[-1]["date"], -used,
            ))
    return found


def compute(conn: sqlite3.Connection) -> list[Candidate]:
    txs = load_txs(conn)
    if not txs:
        return []
    settings = get_settings(conn)
    anchor = date.fromisoformat(txs[-1]["date"])
    return [
        *_detect_fees(txs),
        *_detect_duplicates(txs, settings),
        *_detect_outliers(txs, settings),
        *_detect_new_merchants(txs, settings),
        *_detect_recurring_changes(detect_recurring(txs, anchor), anchor, settings),
        *_detect_balances(txs, settings),
        *_detect_foreign(txs),
        *_detect_budgets(conn, txs, settings),
    ]


def refresh_alerts(conn: sqlite3.Connection) -> int:
    candidates = compute(conn)
    conn.execute("UPDATE alerts SET active = 0")
    for c in candidates:
        conn.execute(
            "INSERT INTO alerts (fingerprint, type, severity, title, detail, date, amount_cents, tx_ids, account_id, active) "
            "VALUES (?,?,?,?,?,?,?,?,?,1) ON CONFLICT(fingerprint) DO UPDATE SET "
            "type=excluded.type, severity=excluded.severity, title=excluded.title, detail=excluded.detail, "
            "date=excluded.date, amount_cents=excluded.amount_cents, tx_ids=excluded.tx_ids, "
            "account_id=excluded.account_id, active=1",
            (c.fingerprint, c.type, c.severity, c.title, c.detail, c.date, c.amount_cents, json.dumps(c.tx_ids), c.account_id),
        )
    conn.commit()
    return len(candidates)
