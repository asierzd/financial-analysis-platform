"""Cálculos financieros. Reglas de todo el módulo:

- Ingresos = suma de categorías de ingreso; gastos = -suma de categorías de gasto. Así una devolución
  clasificada en su categoría de gasto reduce ese gasto, y ingresos - gastos cuadra con la cuenta.
- Los traspasos entre cuentas propias no cuentan ni como ingreso ni como gasto.
- Las medias mensuales usan solo meses completos: un mes a medias falsearía la media.
"""
from __future__ import annotations

import calendar
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from statistics import mean, median, pstdev

from .recurring import detect_recurring

GROUPS = ("needs", "wants", "savings", "unknown")
WEEKDAYS = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
FORECAST_MONTHS = 6


def load_txs(conn: sqlite3.Connection, account_ids: list[int] | None = None) -> list[dict]:
    sql = (
        "SELECT t.*, c.name AS category_name, c.kind AS kind, c.grp AS grp FROM transactions t "
        "JOIN categories c ON c.id = t.category_id"
    )
    params: list = []
    if account_ids:
        sql += f" WHERE t.account_id IN ({','.join('?' * len(account_ids))})"
        params.extend(account_ids)
    return [dict(r) for r in conn.execute(sql + " ORDER BY t.date, t.seq", params)]


def in_range(txs: list[dict], start: str | None, end: str | None) -> list[dict]:
    return [t for t in txs if (not start or t["date"] >= start) and (not end or t["date"] <= end)]


def month_key(day: str) -> str:
    return day[:7]


def months_between(first: str, last: str) -> list[str]:
    year, month = int(first[:4]), int(first[5:7])
    end = last[:7]
    result = []
    while f"{year:04d}-{month:02d}" <= end:
        result.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return result


def complete_months(first: str, last: str) -> list[str]:
    months = months_between(first, last)
    if not months:
        return []
    last_day = date.fromisoformat(last)
    month_end = calendar.monthrange(last_day.year, last_day.month)[1]
    if last_day.day < month_end - 1:
        months = months[:-1]
    if months and int(first[8:10]) > 5:
        if months[0] == first[:7]:
            months = months[1:]
    return months


def totals(txs: list[dict]) -> dict:
    income = sum(t["amount_cents"] for t in txs if t["kind"] == "income")
    expense = -sum(t["amount_cents"] for t in txs if t["kind"] == "expense")
    return {
        "income": income,
        "expense": expense,
        "net": income - expense,
        "savings_rate": (income - expense) / income if income > 0 else None,
        "count": len(txs),
    }


def monthly_rows(txs: list[dict], first: str, last: str) -> list[dict]:
    rows = {m: {"month": m, "income": 0, "expense": 0, **{g: 0 for g in GROUPS}} for m in months_between(first, last)}
    for t in txs:
        row = rows.get(month_key(t["date"]))
        if row is None:
            continue
        if t["kind"] == "income":
            row["income"] += t["amount_cents"]
        elif t["kind"] == "expense":
            row["expense"] -= t["amount_cents"]
            if t["grp"] in GROUPS:
                row[t["grp"]] -= t["amount_cents"]
    result = []
    for m in sorted(rows):
        r = rows[m]
        r["net"] = r["income"] - r["expense"]
        r["savings_rate"] = r["net"] / r["income"] if r["income"] > 0 else None
        result.append(r)
    return result


def balance_series(txs_all: list[dict], start: str | None, end: str | None) -> list[dict]:
    """Saldo de cierre diario, sumando el último saldo conocido de cada cuenta."""
    latest: dict[int, int] = {}
    by_day: dict[str, int] = {}
    for t in txs_all:
        if t["balance_cents"] is None:
            continue
        latest[t["account_id"]] = t["balance_cents"]
        by_day[t["date"]] = sum(latest.values())
    return [{"date": d, "balance": v} for d, v in sorted(by_day.items())
            if (not start or d >= start) and (not end or d <= end)]


def current_balance(txs_all: list[dict]) -> int | None:
    latest: dict[int, int] = {}
    for t in txs_all:
        if t["balance_cents"] is not None:
            latest[t["account_id"]] = t["balance_cents"]
    return sum(latest.values()) if latest else None


def previous_range(start: str, end: str) -> tuple[str, str]:
    """El periodo comparable anterior: mismos días del mes si el periodo empieza en día 1; si no, igual duración."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if s.day == 1:
        span = (e.year - s.year) * 12 + e.month - s.month + 1
        return _shift_month(s, span).isoformat(), _shift_month(e, span).isoformat()
    days = (e - s).days + 1
    return (s - timedelta(days=days)).isoformat(), (s - timedelta(days=1)).isoformat()


def _shift_month(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def category_breakdown(txs: list[dict], kind: str = "expense") -> list[dict]:
    totals_by: dict[str, dict] = {}
    sign = -1 if kind == "expense" else 1
    for t in txs:
        if t["kind"] != kind:
            continue
        entry = totals_by.setdefault(t["category_id"], {
            "id": t["category_id"], "name": t["category_name"], "group": t["grp"], "amount": 0, "count": 0,
        })
        entry["amount"] += sign * t["amount_cents"]
        entry["count"] += 1
    entries = [e for e in totals_by.values() if e["amount"] > 0]
    total = sum(e["amount"] for e in entries)
    for e in entries:
        e["share"] = e["amount"] / total if total else 0
    return sorted(entries, key=lambda e: -e["amount"])


def merchant_breakdown(txs: list[dict], limit: int | None = 10) -> list[dict]:
    agg: dict[str, dict] = {}
    for t in txs:
        if t["kind"] != "expense":
            continue
        entry = agg.setdefault(t["merchant"], {"merchant": t["merchant"], "amount": 0, "count": 0, "category": t["category_name"]})
        entry["amount"] -= t["amount_cents"]
        entry["count"] += 1
    ranked = sorted((e for e in agg.values() if e["amount"] > 0), key=lambda e: -e["amount"])
    return ranked[:limit] if limit else ranked


def recurring_list(txs_all: list[dict]) -> list[dict]:
    return [asdict(r) for r in detect_recurring(txs_all)]


def dashboard(conn: sqlite3.Connection, start: str | None, end: str | None, account_ids: list[int] | None) -> dict:
    txs_all = load_txs(conn, account_ids)
    if not txs_all:
        return {"empty": True}
    first, last = txs_all[0]["date"], txs_all[-1]["date"]
    start = max(start or first, first)
    end = min(end or last, last)
    txs = in_range(txs_all, start, end)
    prev_start, prev_end = previous_range(start, end)
    # Solo si todas las cuentas cubren el periodo anterior: con historial parcial, el «antes» sale artificialmente bajo.
    starts: dict[int, str] = {}
    for t in txs_all:
        starts.setdefault(t["account_id"], t["date"])
    latest_start = max(starts.values())
    has_previous = prev_start[:7] >= latest_start[:7] and prev_end >= latest_start
    previous = totals(in_range(txs_all, prev_start, prev_end)) if has_previous else None
    upcoming = [r for r in recurring_list(txs_all) if r["active"] and r["next_date"] > r["data_until"]]
    upcoming.sort(key=lambda r: r["next_date"])
    alerts_sql = "SELECT * FROM alerts WHERE active=1 AND status='new'"
    alerts_params: list = []
    if account_ids:
        alerts_sql += f" AND (account_id IN ({','.join('?' * len(account_ids))}) OR account_id IS NULL)"
        alerts_params.extend(account_ids)
    alerts_sql += " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, date DESC LIMIT 5"
    return {
        "empty": False,
        "range": {"from": start, "to": end, "first": first, "last": last, "prev_from": prev_start, "prev_to": prev_end},
        "totals": totals(txs),
        "previous": previous,
        "balance": current_balance(txs_all),
        "balance_date": last,
        "balance_accounts": len({t["account_id"] for t in txs_all if t["balance_cents"] is not None}),
        "monthly": monthly_rows(txs, start, end),
        "balance_series": balance_series(txs_all, start, end),
        "categories": category_breakdown(txs),
        "income_categories": category_breakdown(txs, "income"),
        "merchants": merchant_breakdown(txs, 8),
        "upcoming": upcoming[:6],
        "alerts": [alert_row(r) for r in conn.execute(alerts_sql, alerts_params)],
    }


def alert_row(row: sqlite3.Row) -> dict:
    import json

    data = dict(row)
    data["tx_ids"] = json.loads(data["tx_ids"])
    return data


def _trend_per_month(values: list[int]) -> float | None:
    """Pendiente de mínimos cuadrados, como fracción de la media mensual."""
    n = len(values)
    avg = mean(values) if values else 0
    if n < 4 or avg <= 0:
        return None
    xbar = (n - 1) / 2
    denom = sum((i - xbar) ** 2 for i in range(n))
    slope = sum((i - xbar) * (v - avg) for i, v in enumerate(values)) / denom
    return slope / avg


def category_stats(txs: list[dict], months: list[str], all_txs: list[dict]) -> list[dict]:
    per_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    meta: dict[str, dict] = {}
    for t in txs:
        if t["kind"] != "expense":
            continue
        per_cat[t["category_id"]][month_key(t["date"])] -= t["amount_cents"]
        meta[t["category_id"]] = {"name": t["category_name"], "group": t["grp"]}
    grand = sum(sum(v[m] for m in months) for v in per_cat.values())
    result = []
    for cat, values in per_cat.items():
        series = [values[m] for m in months]
        avg = mean(series) if series else 0
        total = sum(series)
        if total <= 0:
            continue
        result.append({
            "id": cat,
            **meta[cat],
            "total": total,
            "share": total / grand if grand > 0 else 0,
            "avg": round(avg),
            "median": round(median(series)),
            "max": max(series),
            "volatility": pstdev(series) / avg if avg > 0 and len(series) > 1 else None,
            "trend": _trend_per_month(series),
            "yoy": _yoy(all_txs, cat),
            "series": series,
        })
    result.sort(key=lambda c: -c["total"])
    return result


def _yoy(all_txs: list[dict], category: str) -> float | None:
    """Últimos 12 meses completos frente a los 12 anteriores, sea cual sea el periodo elegido."""
    if not all_txs:
        return None
    whole = complete_months(all_txs[0]["date"], all_txs[-1]["date"])
    if len(whole) < 24:
        return None
    recent, before = set(whole[-12:]), set(whole[-24:-12])
    now = sum(-t["amount_cents"] for t in all_txs if t["category_id"] == category and month_key(t["date"]) in recent)
    then = sum(-t["amount_cents"] for t in all_txs if t["category_id"] == category and month_key(t["date"]) in before)
    return (now - then) / then if then > 0 else None


def month_end_balances(txs_all: list[dict]) -> list[dict]:
    """Último saldo conocido de cada mes, sin reconstruir nada: son saldos reales del extracto."""
    by_month: dict[str, int] = {}
    for point in balance_series(txs_all, None, None):
        by_month[point["date"][:7]] = point["balance"]
    return [{"month": m, "balance": by_month[m]} for m in sorted(by_month)]


def forecast(balance: int | None, nets: list[int], last_month: str, history: list[dict] | None = None) -> dict | None:
    """Proyección simple: neto medio de hasta 12 meses y banda de ±1 desviación, que crece con √t."""
    if balance is None or len(nets) < 3:
        return None
    sample = nets[-12:]
    avg, sigma = mean(sample), pstdev(sample)
    year, month = int(last_month[:4]), int(last_month[5:7])
    points = []
    for step in range(1, FORECAST_MONTHS + 1):
        month += 1
        if month == 13:
            year, month = year + 1, 1
        spread = sigma * step ** 0.5
        expected = balance + avg * step
        points.append({
            "month": f"{year:04d}-{month:02d}", "expected": round(expected),
            "low": round(expected - spread), "high": round(expected + spread),
        })
    return {"avg_net": round(avg), "sigma": round(sigma), "months_used": len(sample), "start": balance,
            "history": (history or [])[-12:], "points": points}


def analysis(conn: sqlite3.Connection, start: str | None, end: str | None, account_ids: list[int] | None) -> dict:
    txs_all = load_txs(conn, account_ids)
    if not txs_all:
        return {"empty": True}
    data_first, data_last = txs_all[0]["date"], txs_all[-1]["date"]
    start = max(start or data_first, data_first)
    end = min(end or data_last, data_last)
    txs = in_range(txs_all, start, end)
    monthly = monthly_rows(txs, start, end)
    whole = complete_months(start, end)
    whole_rows = [r for r in monthly if r["month"] in whole]
    rows_for_avg = whole_rows or monthly
    months_for_avg = whole or [r["month"] for r in monthly]

    avg_income = mean(r["income"] for r in rows_for_avg) if rows_for_avg else 0
    avg_expense = mean(r["expense"] for r in rows_for_avg) if rows_for_avg else 0
    balance = current_balance(txs_all)

    # Tasa de ahorro móvil a 3 meses (sobre meses completos).
    rolling = []
    for i, row in enumerate(whole_rows):
        window = whole_rows[max(0, i - 2): i + 1]
        income = sum(w["income"] for w in window)
        net = sum(w["net"] for w in window)
        rolling.append({"month": row["month"], "savings_rate": net / income if income > 0 else None})

    recurring = recurring_list(txs_all)
    active_out = [r for r in recurring if r["active"] and r["direction"] == "out"]
    active_in = [r for r in recurring if r["active"] and r["direction"] == "in"]
    fixed_monthly = sum(r["annual_cents"] for r in active_out) / 12

    groups = {g: mean(r[g] for r in rows_for_avg) if rows_for_avg else 0 for g in GROUPS}
    saved = avg_income - avg_expense + groups["savings"]

    # Mapa de calor: 12 categorías principales por los últimos 12 meses del periodo.
    heat_months = [r["month"] for r in monthly][-12:]
    per_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    names: dict[str, str] = {}
    for t in txs:
        if t["kind"] == "expense":
            per_cat[t["category_id"]][month_key(t["date"])] -= t["amount_cents"]
            names[t["category_id"]] = t["category_name"]
    ranked = sorted(per_cat, key=lambda c: -sum(per_cat[c][m] for m in heat_months))[:12]
    heatmap = {
        "months": heat_months,
        "rows": [{"id": c, "name": names[c], "values": [max(per_cat[c][m], 0) for m in heat_months]} for c in ranked],
    }

    # Patrón semanal: gasto medio por cada lunes, martes... del periodo, no por compra.
    weekday_totals = [0] * 7
    weekday_counts = [0] * 7
    day = date.fromisoformat(start)
    end_day = date.fromisoformat(end)
    while day <= end_day:
        weekday_counts[day.weekday()] += 1
        day += timedelta(days=1)
    for t in txs:
        if t["kind"] == "expense" and t["grp"] in ("wants", "needs") and t["amount_cents"] < 0:
            if t["category_id"] in ("vivienda", "prestamos", "impuestos", "recibos", "seguros"):
                continue  # los cargos domiciliados no reflejan hábitos de compra
            weekday_totals[date.fromisoformat(t["date"]).weekday()] -= t["amount_cents"]
    weekday = [
        {"day": WEEKDAYS[i], "avg": round(weekday_totals[i] / weekday_counts[i]) if weekday_counts[i] else 0}
        for i in range(7)
    ]

    # Concentración (Pareto) del gasto por comercio.
    merchants = merchant_breakdown(txs, None)
    merchant_total = sum(m["amount"] for m in merchants)
    cumulative, pareto = 0, []
    for m in merchants[:15]:
        cumulative += m["amount"]
        pareto.append({**m, "share": m["amount"] / merchant_total, "cumulative": cumulative / merchant_total})
    needed, running = 0, 0
    for m in merchants:
        running += m["amount"]
        needed += 1
        if merchant_total and running / merchant_total >= 0.8:
            break

    # Comisiones.
    fee_txs = [t for t in txs if t["category_id"] == "comisiones" and t["amount_cents"] < 0]
    fee_by_year: dict[str, int] = defaultdict(int)
    fee_by_concept: dict[str, dict] = {}
    for t in fee_txs:
        fee_by_year[t["date"][:4]] -= t["amount_cents"]
        entry = fee_by_concept.setdefault(t["concept"], {"concept": t["concept"], "amount": 0, "count": 0, "last": t["date"]})
        entry["amount"] -= t["amount_cents"]
        entry["count"] += 1
        entry["last"] = t["date"]
    fee_refunds = sum(t["amount_cents"] for t in txs if t["category_id"] == "comisiones" and t["amount_cents"] > 0)

    income_values = [r["income"] for r in rows_for_avg]
    expense_values = [r["expense"] for r in rows_for_avg]
    nets = [r["net"] for r in monthly_rows(txs_all, data_first, data_last)
            if r["month"] in complete_months(data_first, data_last)]

    return {
        "empty": False,
        "range": {"from": start, "to": end, "first": data_first, "last": data_last},
        "months_used": len(rows_for_avg),
        "complete": bool(whole_rows),
        "indicators": {
            "avg_income": round(avg_income),
            "avg_expense": round(avg_expense),
            "avg_net": round(avg_income - avg_expense),
            "savings_rate": (avg_income - avg_expense) / avg_income if avg_income > 0 else None,
            "balance": balance,
            "runway_months": balance / avg_expense if balance is not None and avg_expense > 0 else None,
            "fixed_monthly": round(fixed_monthly),
            "fixed_ratio": fixed_monthly / avg_income if avg_income > 0 else None,
            "free_after_fixed": round(avg_income - fixed_monthly),
            "income_volatility": pstdev(income_values) / avg_income if avg_income > 0 and len(income_values) > 1 else None,
            "expense_volatility": pstdev(expense_values) / avg_expense if avg_expense > 0 and len(expense_values) > 1 else None,
            "best_month": max(rows_for_avg, key=lambda r: r["net"])["month"] if rows_for_avg else None,
            "worst_month": min(rows_for_avg, key=lambda r: r["net"])["month"] if rows_for_avg else None,
        },
        "monthly": monthly,
        "rolling_savings": rolling,
        "rule_50_30_20": {
            "income": round(avg_income),
            "needs": round(groups["needs"]),
            "wants": round(groups["wants"]),
            "unknown": round(groups["unknown"]),
            "saved": round(saved),
        },
        "heatmap": heatmap,
        "category_stats": category_stats(txs, months_for_avg, txs_all),
        "stats_months": months_for_avg,
        "weekday": weekday,
        "pareto": pareto,
        "pareto_summary": {"merchants": len(merchants), "for_80pct": needed, "total": merchant_total},
        "fees": {
            "total": -sum(t["amount_cents"] for t in fee_txs),
            "refunded": fee_refunds,
            "count": len(fee_txs),
            "by_year": [{"year": y, "amount": fee_by_year[y]} for y in sorted(fee_by_year)],
            "by_concept": sorted(fee_by_concept.values(), key=lambda e: -e["amount"]),
        },
        "forecast": forecast(balance, nets, data_last[:7], month_end_balances(txs_all)),
        "recurring": {
            "out": active_out,
            "in": active_in,
            "inactive": [r for r in recurring if not r["active"]],
            "monthly_out": round(fixed_monthly),
            "monthly_in": round(sum(r["annual_cents"] for r in active_in) / 12),
        },
    }
