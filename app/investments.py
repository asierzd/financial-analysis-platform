"""Cartera de inversión a partir de las operaciones importadas.

El coste se calcula por FIFO, el criterio español para valores homogéneos: al vender, salen primero los títulos
comprados antes. Las cifras son orientativas: no aplican la regla de los dos meses ni sustituyen a una declaración.

La aplicación no se conecta a internet, así que no conoce precios en vivo. Valora con el precio que escriba el usuario
o, si no hay, con el de la última operación del extracto, y siempre dice de cuándo es.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .ingest.base import MOV_INTEREST, MOV_ORDER_FEE, MOV_WITHHOLDING

CLOSED_THRESHOLD = Decimal("0.000001")
STALE_PRICE_DAYS = 14
_SAVINGS_PLAN = re.compile(r"(?i)savings?\s*plan|sparplan|plan\s+de\s+(inversi|ahorro)")
ASSET_LABELS = {
    "FUND": "ETF y fondos", "STOCK": "Acciones", "CRYPTO": "Criptomonedas", "BOND": "Bonos",
    "DERIVATIVE": "Derivados", "SYNTHETIC": "Materias primas (ETC)",
}


@dataclass
class Lot:
    shares: Decimal
    cost: Decimal  # en céntimos
    estimated: bool = False


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(0)


def _where_accounts(account_ids: list[int] | None, column: str = "account_id") -> tuple[str, list]:
    if not account_ids:
        return "", []
    return f" AND {column} IN ({','.join('?' * len(account_ids))})", list(account_ids)


def _take_fifo(lots: list[Lot], shares: Decimal) -> tuple[Decimal, Decimal, list[Lot]]:
    """Saca títulos de los lotes más antiguos. Devuelve el coste, lo que faltó y los lotes extraídos."""
    cost = Decimal(0)
    taken: list[Lot] = []
    while shares > CLOSED_THRESHOLD and lots:
        lot = lots[0]
        used = min(shares, lot.shares)
        part = lot.cost * used / lot.shares
        cost += part
        taken.append(Lot(used, part, lot.estimated))
        lot.shares -= used
        lot.cost -= part
        shares -= used
        if lot.shares <= CLOSED_THRESHOLD:
            lots.pop(0)
    return cost, max(shares, Decimal(0)), taken


def _replay(events: list[dict]) -> dict:
    """Recorre las operaciones en orden y reconstruye lotes, ganancias realizadas, aportaciones y flujos."""
    positions: dict[str, dict] = {}
    in_transit: dict[str, list[Lot]] = defaultdict(list)
    realized_by_year: dict[str, int] = defaultdict(int)
    contributions: dict[str, int] = defaultdict(int)
    flows: list[tuple[str, Decimal]] = []
    plan_buys = 0
    corporate = 0

    for event in events:
        isin = event["isin"]
        position = positions.setdefault(isin, {
            "isin": isin, "name": event["name"], "asset_class": event["asset_class"], "lots": [], "realized": Decimal(0),
            "dividends": 0, "dividend_tax": 0, "fees": 0, "last_price": None, "last_price_date": None,
            "oversold": False, "estimated_cost": False, "corporate": False, "reward_shares": Decimal(0), "operations": 0,
        })
        position["name"] = event["name"] or position["name"]
        position["asset_class"] = event["asset_class"] or position["asset_class"]
        position["operations"] += 1
        kind = event["kind"]
        shares = abs(_dec(event["shares"]))
        amount, fee, tax = event["amount_cents"], event["fee_cents"], event["tax_cents"]
        price = _dec(event["price"])
        if price > 0 and kind in ("buy", "sell", "reward", "delivery"):
            position["last_price"], position["last_price_date"] = price, event["date"]
        month, year = event["date"][:7], event["date"][:4]

        if kind == "buy":
            # Comisión e impuestos de la compra forman parte de su coste.
            cost = Decimal(-(amount + fee + tax))
            position["lots"].append(Lot(shares, cost))
            position["fees"] -= fee
            contributions[month] += int(cost)
            flows.append((event["date"], -cost))
            plan_buys += bool(_SAVINGS_PLAN.search(event["notes"] or ""))
        elif kind == "cancel":
            # Deshace una compra: quita los títulos más recientes, sin ganancia.
            remaining = shares
            refund = Decimal(amount + fee + tax)
            while remaining > CLOSED_THRESHOLD and position["lots"]:
                lot = position["lots"][-1]
                used = min(remaining, lot.shares)
                part = lot.cost * used / lot.shares
                lot.shares -= used
                lot.cost -= part
                remaining -= used
                if lot.shares <= CLOSED_THRESHOLD:
                    position["lots"].pop()
            contributions[month] -= int(refund)
            flows.append((event["date"], refund))
        elif kind == "sell":
            proceeds = Decimal(amount + fee)
            cost, missing, _ = _take_fifo(position["lots"], shares)
            if missing > CLOSED_THRESHOLD:
                position["oversold"] = True
            gain = proceeds - cost
            position["realized"] += gain
            position["fees"] -= fee
            realized_by_year[year] += int(gain)
            contributions[month] -= int(proceeds)
            flows.append((event["date"], proceeds))
        elif kind == "dividend":
            position["dividends"] += amount
            position["dividend_tax"] += tax
            flows.append((event["date"], Decimal(amount + tax + fee)))
        elif kind == "reward":
            # Sin coste de bolsillo: se toma su valor al recibirla, que es el criterio habitual para su venta posterior.
            position["lots"].append(Lot(shares, shares * price * 100, estimated=False))
            position["reward_shares"] += shares
        elif kind == "delivery":
            signed = _dec(event["shares"])
            if signed < 0:
                _, _, taken = _take_fifo(position["lots"], shares)
                in_transit[isin].extend(taken)
            else:
                # Un traspaso conserva el coste original. Si no se conoce, se estima con el precio del extracto.
                pending = shares
                while pending > CLOSED_THRESHOLD and in_transit[isin]:
                    lot = in_transit[isin][0]
                    used = min(pending, lot.shares)
                    part = lot.cost * used / lot.shares
                    position["lots"].append(Lot(used, part, lot.estimated))
                    lot.shares -= used
                    lot.cost -= part
                    pending -= used
                    if lot.shares <= CLOSED_THRESHOLD:
                        in_transit[isin].pop(0)
                if pending > CLOSED_THRESHOLD:
                    position["lots"].append(Lot(pending, pending * price * 100, estimated=True))
                    position["estimated_cost"] = True
        elif kind == "corporate":
            position["corporate"] = True
            corporate += 1

    return {
        "positions": positions, "realized_by_year": realized_by_year, "contributions": contributions,
        "flows": flows, "plan_buys": plan_buys, "corporate": corporate,
    }


def xirr(flows: list[tuple[str, float]]) -> float | None:
    """Rentabilidad anualizada ponderada por dinero (TIR). None si no hay datos suficientes para ser significativa."""
    if len(flows) < 2:
        return None
    days = [date.fromisoformat(d) for d, _ in flows]
    origin = min(days)
    if (max(days) - origin).days < 30 or not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    terms = [((d - origin).days / 365.25, amount) for d, (_, amount) in zip(days, flows)]

    def npv(rate: float) -> float:
        return sum(amount / (1 + rate) ** years for years, amount in terms)

    low, high = -0.99, 10.0
    f_low, f_high = npv(low), npv(high)
    if f_low * f_high > 0:
        return None
    for _ in range(120):
        middle = (low + high) / 2
        f_middle = npv(middle)
        if f_low * f_middle <= 0:
            high = middle
        else:
            low, f_low = middle, f_middle
    return (low + high) / 2


def _cash_yield(conn: sqlite3.Connection, account_ids: list[int], anchor: str) -> dict | None:
    """Rentabilidad de la cuenta remunerada: intereses de los últimos 12 meses sobre el efectivo medio diario."""
    if not account_ids:
        return None
    marks = ",".join("?" * len(account_ids))
    start = (date.fromisoformat(anchor) - timedelta(days=365)).isoformat()
    interest = conn.execute(
        f"SELECT COALESCE(SUM(amount_cents),0) FROM transactions WHERE movement=? AND date>? AND account_id IN ({marks})",
        [MOV_INTEREST, start, *account_ids],
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT account_id, date, balance_cents FROM transactions WHERE balance_cents IS NOT NULL AND account_id IN ({marks}) "
        "ORDER BY date, occurred_at, seq", account_ids,
    ).fetchall()
    if not rows:
        return None
    closing: dict[int, dict[str, int]] = defaultdict(dict)
    for row in rows:
        closing[row["account_id"]][row["date"]] = row["balance_cents"]
    total, counted = 0, 0
    begin = max(min(r["date"] for r in rows), start)
    day, end = date.fromisoformat(begin), date.fromisoformat(anchor)
    latest: dict[int, int] = {}
    for row in rows:
        if row["date"] < begin:
            latest[row["account_id"]] = row["balance_cents"]
    while day <= end:
        iso = day.isoformat()
        for account_id, days in closing.items():
            if iso in days:
                latest[account_id] = days[iso]
        total += sum(max(v, 0) for v in latest.values())
        counted += 1
        day += timedelta(days=1)
    average = total / counted if counted else 0
    span_years = counted / 365.25
    return {
        "interest_12m": interest,
        "average_cash": round(average),
        "days": counted,
        "effective_rate": interest / average / span_years if average > 0 and counted >= 30 else None,
    }


def overview(conn: sqlite3.Connection, account_ids: list[int] | None = None) -> dict:
    scope, params = _where_accounts(account_ids)
    events = [dict(r) for r in conn.execute(
        f"SELECT * FROM investment_events WHERE 1=1{scope} ORDER BY date, occurred_at, id", params)]
    broker_ids = [r["id"] for r in conn.execute("SELECT id FROM accounts WHERE balance_computed=1")]
    selected_brokers = [i for i in broker_ids if not account_ids or i in account_ids]
    has_broker_cash = bool(selected_brokers)
    if not events and not has_broker_cash:
        return {"empty": True}

    replay = _replay(events)
    overrides = {r["isin"]: dict(r) for r in conn.execute("SELECT * FROM investment_prices")}
    today = date.today()
    open_positions, closed_positions, warnings = [], [], []
    for position in replay["positions"].values():
        shares = sum((lot.shares for lot in position["lots"]), Decimal(0))
        cost = sum((lot.cost for lot in position["lots"]), Decimal(0))
        row = {
            "isin": position["isin"], "name": position["name"], "asset_class": position["asset_class"],
            "asset_label": ASSET_LABELS.get(position["asset_class"], position["asset_class"] or "Otros"),
            "shares": float(shares), "cost": round(cost), "realized": round(position["realized"]),
            "dividends": position["dividends"], "dividend_tax": position["dividend_tax"], "operations": position["operations"],
            "oversold": position["oversold"], "estimated_cost": position["estimated_cost"], "corporate": position["corporate"],
            "reward_shares": float(position["reward_shares"]),
        }
        if position["oversold"]:
            warnings.append(f"{position['name']}: se vendieron más títulos de los que constan. Falta historial, así que su ganancia no es fiable.")
        if position["corporate"]:
            warnings.append(f"{position['name']}: tiene operaciones corporativas (fusiones, liquidaciones...). Comprueba sus títulos en la app.")
        if position["estimated_cost"]:
            warnings.append(f"{position['name']}: entró por traspaso sin coste conocido. Se ha estimado con el precio del extracto.")
        if shares <= CLOSED_THRESHOLD:
            closed_positions.append(row)
            continue
        override = overrides.get(position["isin"])
        if override:
            price, price_date, source = _dec(override["price"]), override["as_of"], "manual"
        elif position["last_price"] is not None:
            price, price_date, source = position["last_price"], position["last_price_date"], "operation"
        else:
            price, price_date, source = Decimal(0), None, "none"
        value = shares * price * 100
        row.update({
            "price": float(price), "price_date": price_date, "price_source": source,
            "price_stale": bool(price_date) and (today - date.fromisoformat(price_date)).days > STALE_PRICE_DAYS,
            "value": round(value), "unrealized": round(value - cost) if source != "none" else None,
            "unrealized_pct": float((value - cost) / cost) if cost > 0 and source != "none" else None,
            "average_cost": float(cost / shares / 100),
        })
        open_positions.append(row)

    open_positions.sort(key=lambda p: -p["value"])
    closed_positions.sort(key=lambda p: -abs(p["realized"]))
    total_value = sum(p["value"] for p in open_positions)
    total_cost = sum(p["cost"] for p in open_positions)
    for p in open_positions:
        p["weight"] = p["value"] / total_value if total_value else 0

    allocation: dict[str, dict] = {}
    for p in open_positions:
        entry = allocation.setdefault(p["asset_class"] or "OTHER", {"id": p["asset_class"] or "OTHER", "name": p["asset_label"], "amount": 0})
        entry["amount"] += p["value"]
    allocation_rows = sorted(allocation.values(), key=lambda e: -e["amount"])
    for entry in allocation_rows:
        entry["share"] = entry["amount"] / total_value if total_value else 0

    # Lo fiscal y las rentas salen de los movimientos: ahí están separadas comisiones y retenciones.
    tx_scope, tx_params = _where_accounts(selected_brokers or [-1])
    by_year: dict[str, dict] = defaultdict(lambda: {"realized": 0, "dividends": 0, "interest": 0, "withholding": 0, "fees": 0})
    interest_by_month: dict[str, int] = defaultdict(int)
    for row in conn.execute(
        f"SELECT date, amount_cents, movement, category_id FROM transactions WHERE 1=1{tx_scope}", tx_params,
    ):
        year = row["date"][:4]
        if row["movement"] == MOV_INTEREST:
            by_year[year]["interest"] += row["amount_cents"]
            interest_by_month[row["date"][:7]] += row["amount_cents"]
        elif row["movement"] == "Dividendo":
            by_year[year]["dividends"] += row["amount_cents"]
        elif row["movement"] == MOV_WITHHOLDING:
            by_year[year]["withholding"] -= row["amount_cents"]
        elif row["movement"] == MOV_ORDER_FEE:
            by_year[year]["fees"] -= row["amount_cents"]
    for year, gain in replay["realized_by_year"].items():
        by_year[year]["realized"] += gain

    flows = [(d, float(a)) for d, a in replay["flows"]]
    last_event = max((e["date"] for e in events), default=None)
    valuation_day = max(
        [p["price_date"] for p in open_positions if p["price_date"]] + ([last_event] if last_event else []), default=None)
    annual_return = xirr(flows + [(valuation_day, float(total_value))]) if valuation_day and open_positions else None

    cash = None
    if selected_brokers:
        marks = ",".join("?" * len(selected_brokers))
        cash_total, complete = 0, True
        for account_id in selected_brokers:
            row = conn.execute(
                "SELECT balance_cents FROM transactions WHERE account_id=? ORDER BY date DESC, occurred_at DESC, seq DESC LIMIT 1", (account_id,),
            ).fetchone()
            if row is None or row["balance_cents"] is None:
                complete = False
            else:
                cash_total += row["balance_cents"]
        last = conn.execute(f"SELECT MAX(date) FROM transactions WHERE account_id IN ({marks})", selected_brokers).fetchone()[0]
        cash = {"balance": cash_total if complete else None, "complete": complete, "as_of": last}
        if not complete:
            warnings.insert(0, "El efectivo no se puede calcular: el extracto no empieza en la apertura de la cuenta. "
                               "En la app, exporta el periodo «desde la apertura».")

    return {
        "empty": False,
        "has_positions": bool(events),
        "summary": {
            "value": total_value,
            "cost": total_cost,
            "unrealized": total_value - total_cost,
            "unrealized_pct": (total_value - total_cost) / total_cost if total_cost > 0 else None,
            "realized": round(sum(y["realized"] for y in by_year.values())),
            "dividends": sum(y["dividends"] for y in by_year.values()),
            "interest": sum(y["interest"] for y in by_year.values()),
            "withholding": sum(y["withholding"] for y in by_year.values()),
            "order_fees": sum(y["fees"] for y in by_year.values()),
            "annual_return": annual_return,
            "valuation_day": valuation_day,
            "stale_prices": sum(p["price_stale"] for p in open_positions),
            "savings_plan_buys": replay["plan_buys"],
        },
        "cash": cash,
        "cash_yield": _cash_yield(conn, selected_brokers, cash["as_of"]) if cash and cash["as_of"] else None,
        "positions": open_positions,
        "closed": closed_positions,
        "allocation": allocation_rows,
        "contributions": [{"month": m, "amount": replay["contributions"][m]} for m in sorted(replay["contributions"])],
        "interest_by_month": [{"month": m, "amount": interest_by_month[m]} for m in sorted(interest_by_month)],
        "by_year": [{"year": y, **{k: round(v) for k, v in by_year[y].items()}} for y in sorted(by_year, reverse=True)],
        "warnings": warnings,
    }


def portfolio_value(conn: sqlite3.Connection, account_ids: list[int] | None) -> int | None:
    data = overview(conn, account_ids)
    if data.get("empty") or not data["positions"]:
        return None
    return data["summary"]["value"]
