"""API y servidor de la interfaz. Sin login: pensado para escuchar solo en localhost."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path
from statistics import median
from typing import Iterator, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import alerts as alerts_engine
from . import analytics
from .categorize import normalize, reclassify_all
from .db import connect, get_settings, init_db
from .ingest import FormatError, parsers
from .ingest import service as ingest
from .seed import DEFAULT_SETTINGS

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
STATIC_DIR = Path(__file__).parent / "static"
_ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()]


@asynccontextmanager
async def lifespan(_: FastAPI):
    with closing(connect()) as conn:
        init_db(conn)
        alerts_engine.refresh_alerts(conn)
    yield


app = FastAPI(title="Finanzas", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
# No hay login, así que otra web abierta en el navegador no debe poder leer ni tocar los datos:
# Host evita DNS rebinding y el control de Origin evita peticiones cruzadas.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)


@app.middleware("http")
async def protect(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        if urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-cache"
    return response


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


DB = Depends(get_conn)
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


def _after_data_change(conn: sqlite3.Connection) -> None:
    conn.commit()
    alerts_engine.refresh_alerts(conn)


# ---------------------------------------------------------------- general


@app.get("/api/meta")
def meta(conn: sqlite3.Connection = DB) -> dict:
    span = conn.execute("SELECT MIN(date) AS first, MAX(date) AS last, COUNT(*) AS count FROM transactions").fetchone()
    return {
        "first": span["first"],
        "last": span["last"],
        "count": span["count"],
        "accounts": [dict(r) for r in conn.execute("SELECT id, name, bank, iban FROM accounts ORDER BY id")],
        "categories": [dict(r) for r in conn.execute("SELECT id, name, kind, grp, is_system FROM categories ORDER BY kind, name")],
        "alerts_new": conn.execute("SELECT COUNT(*) FROM alerts WHERE active=1 AND status='new'").fetchone()[0],
        "alert_types": alerts_engine.ALERT_TYPES,
        "parsers": [{"id": p.id, "bank": p.bank} for p in parsers()],
    }


@app.get("/api/dashboard")
def dashboard(
    start: str | None = Query(None, alias="from", pattern=DATE_RE),
    end: str | None = Query(None, alias="to", pattern=DATE_RE),
    account: int | None = None,
    conn: sqlite3.Connection = DB,
) -> dict:
    return analytics.dashboard(conn, start, end, account)


@app.get("/api/analysis")
def analysis(
    start: str | None = Query(None, alias="from", pattern=DATE_RE),
    end: str | None = Query(None, alias="to", pattern=DATE_RE),
    account: int | None = None,
    conn: sqlite3.Connection = DB,
) -> dict:
    return analytics.analysis(conn, start, end, account)


# ---------------------------------------------------------------- movimientos

SORTS = {
    "date": "t.date {0}, t.seq {0}",
    "amount": "t.amount_cents {0}, t.date DESC",
    "merchant": "t.merchant {0}, t.date DESC",
    "category": "c.name {0}, t.date DESC",
}


def _tx_filter(start, end, account, category, kind, search, min_amount, max_amount, ids=None) -> tuple[str, list]:
    where, params = ["1=1"], []
    if start:
        where.append("t.date >= ?"); params.append(start)
    if end:
        where.append("t.date <= ?"); params.append(end)
    if account:
        where.append("t.account_id = ?"); params.append(account)
    if category:
        where.append("t.category_id = ?"); params.append(category)
    if kind == "income":
        where.append("t.amount_cents > 0")
    elif kind == "expense":
        where.append("t.amount_cents < 0")
    if search:
        like = f"%{search.strip()}%"
        where.append("(t.concept LIKE ? OR t.merchant LIKE ? OR t.notes LIKE ? OR t.movement LIKE ? OR t.user_note LIKE ?)")
        params += [like] * 5
    if min_amount is not None:
        where.append("ABS(t.amount_cents) >= ?"); params.append(round(min_amount * 100))
    if max_amount is not None:
        where.append("ABS(t.amount_cents) <= ?"); params.append(round(max_amount * 100))
    if ids:
        where.append(f"t.id IN ({','.join('?' * len(ids))})"); params += ids
    return " AND ".join(where), params


_TX_SELECT = (
    "SELECT t.id, t.account_id, t.date, t.value_date, t.amount_cents, t.currency, t.balance_cents, t.concept, t.movement, "
    "t.notes, t.merchant, t.category_id, t.category_source, t.user_note, c.name AS category_name, c.kind AS kind "
    "FROM transactions t JOIN categories c ON c.id = t.category_id "
)


def _parse_ids(ids: str | None) -> list[int] | None:
    if not ids:
        return None
    try:
        return [int(x) for x in ids.split(",")][:500]
    except ValueError as exc:
        raise HTTPException(422, "Lista de movimientos no válida.") from exc


@app.get("/api/transactions")
def list_transactions(
    start: str | None = Query(None, alias="from", pattern=DATE_RE),
    end: str | None = Query(None, alias="to", pattern=DATE_RE),
    account: int | None = None,
    category: str | None = None,
    kind: Literal["income", "expense"] | None = None,
    q: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    ids: str | None = None,
    sort: Literal["date", "amount", "merchant", "category"] = "date",
    direction: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    conn: sqlite3.Connection = DB,
) -> dict:
    where, params = _tx_filter(start, end, account, category, kind, q, min_amount, max_amount, _parse_ids(ids))
    summary = conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN t.amount_cents>0 THEN t.amount_cents END),0) AS money_in, "
        f"COALESCE(SUM(CASE WHEN t.amount_cents<0 THEN t.amount_cents END),0) AS money_out "
        f"FROM transactions t JOIN categories c ON c.id=t.category_id WHERE {where}", params,
    ).fetchone()
    order = SORTS[sort].format(direction.upper())
    rows = conn.execute(
        f"{_TX_SELECT} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", [*params, size, (page - 1) * size],
    ).fetchall()
    return {
        "items": [dict(r) for r in rows],
        "total": summary["n"],
        "money_in": summary["money_in"],
        "money_out": summary["money_out"],
        "page": page,
        "size": size,
    }


def _csv_safe(text: str) -> str:
    """Evita que Excel ejecute como fórmula un concepto que empiece por =, +, - o @."""
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@app.get("/api/transactions.csv")
def export_transactions(
    start: str | None = Query(None, alias="from", pattern=DATE_RE),
    end: str | None = Query(None, alias="to", pattern=DATE_RE),
    account: int | None = None,
    category: str | None = None,
    kind: Literal["income", "expense"] | None = None,
    q: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    conn: sqlite3.Connection = DB,
) -> StreamingResponse:
    where, params = _tx_filter(start, end, account, category, kind, q, min_amount, max_amount)
    rows = conn.execute(f"{_TX_SELECT} WHERE {where} ORDER BY t.date DESC, t.seq DESC", params).fetchall()
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Fecha", "Comercio", "Concepto", "Categoría", "Importe", "Divisa", "Saldo", "Nota"])
    for r in rows:
        writer.writerow([
            r["date"], _csv_safe(r["merchant"]), _csv_safe(r["concept"]), r["category_name"],
            f"{r['amount_cents'] / 100:.2f}".replace(".", ","), r["currency"],
            "" if r["balance_cents"] is None else f"{r['balance_cents'] / 100:.2f}".replace(".", ","),
            _csv_safe(r["user_note"]),
        ])
    buffer.seek(0)
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="movimientos.csv"'})


class TxUpdate(BaseModel):
    category_id: str | None = None
    user_note: str | None = Field(None, max_length=500)
    create_rule: bool = False


def _category_exists(conn: sqlite3.Connection, category_id: str) -> None:
    if not conn.execute("SELECT 1 FROM categories WHERE id=?", (category_id,)).fetchone():
        raise HTTPException(422, "Esa categoría no existe.")


def _rule_pattern(conn: sqlite3.Connection, tx: sqlite3.Row) -> str:
    """El nombre del comercio; si es una marca unificada (AMZN → Amazon), el patrón que la reconoció."""
    pattern = normalize(tx["merchant"])
    haystack = f" {normalize(tx['concept'])} {normalize(tx['movement'])} {normalize(tx['notes'])} "
    if f" {pattern} " in haystack:
        return pattern
    if tx["rule_id"]:
        row = conn.execute("SELECT pattern FROM rules WHERE id=?", (tx["rule_id"],)).fetchone()
        if row:
            return row["pattern"]
    return normalize(tx["concept"])


@app.patch("/api/transactions/{tx_id}")
def update_transaction(tx_id: int, body: TxUpdate, conn: sqlite3.Connection = DB) -> dict:
    tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not tx:
        raise HTTPException(404, "Movimiento no encontrado.")
    affected = 1
    if body.category_id is not None:
        _category_exists(conn, body.category_id)
        conn.execute(
            "UPDATE transactions SET category_id=?, category_source='user', rule_id=NULL WHERE id=?",
            (body.category_id, tx_id),
        )
        if body.create_rule:
            pattern = _rule_pattern(conn, tx)
            _upsert_rule(conn, pattern, body.category_id, "any", tx["merchant"])
            affected += reclassify_all(conn)
    if body.user_note is not None:
        conn.execute("UPDATE transactions SET user_note=? WHERE id=?", (body.user_note, tx_id))
    _after_data_change(conn)
    return {"ok": True, "affected": affected}


# ---------------------------------------------------------------- alertas


@app.get("/api/alerts")
def list_alerts(
    status: Literal["new", "reviewed", "dismissed", "all"] = "new",
    type: str | None = None,
    severity: Literal["critical", "warning", "info"] | None = None,
    account: int | None = None,
    conn: sqlite3.Connection = DB,
) -> dict:
    where, params = ["active=1"], []
    if status != "all":
        where.append("status=?"); params.append(status)
    if type:
        where.append("type=?"); params.append(type)
    if severity:
        where.append("severity=?"); params.append(severity)
    if account:
        where.append("(account_id=? OR account_id IS NULL)"); params.append(account)
    rows = conn.execute(
        f"SELECT * FROM alerts WHERE {' AND '.join(where)} ORDER BY "
        "CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, date DESC, id DESC LIMIT 500", params,
    )
    counts = conn.execute("SELECT type, severity, COUNT(*) AS n, COALESCE(SUM(amount_cents),0) AS total FROM alerts "
                          "WHERE active=1 AND status='new' GROUP BY type, severity").fetchall()
    return {"items": [analytics.alert_row(r) for r in rows], "counts": [dict(r) for r in counts]}


class AlertUpdate(BaseModel):
    status: Literal["new", "reviewed", "dismissed"]


@app.patch("/api/alerts/{alert_id}")
def update_alert(alert_id: int, body: AlertUpdate, conn: sqlite3.Connection = DB) -> dict:
    cursor = conn.execute("UPDATE alerts SET status=? WHERE id=?", (body.status, alert_id))
    conn.commit()
    if not cursor.rowcount:
        raise HTTPException(404, "Alerta no encontrada.")
    return {"ok": True}


class AlertBulk(BaseModel):
    status: Literal["reviewed", "dismissed"]
    type: str | None = None


@app.post("/api/alerts/bulk")
def bulk_alerts(body: AlertBulk, conn: sqlite3.Connection = DB) -> dict:
    sql, params = "UPDATE alerts SET status=? WHERE active=1 AND status='new'", [body.status]
    if body.type:
        sql += " AND type=?"
        params.append(body.type)
    cursor = conn.execute(sql, params)
    conn.commit()
    return {"updated": cursor.rowcount}


# ---------------------------------------------------------------- presupuestos


@app.get("/api/budgets")
def list_budgets(account: int | None = None, conn: sqlite3.Connection = DB) -> dict:
    txs = analytics.load_txs(conn, account)
    budgets = {r["category_id"]: r["monthly_cents"] for r in conn.execute("SELECT * FROM budgets")}
    if not txs:
        current, whole = None, []
    else:
        current = txs[-1]["date"][:7]
        whole = analytics.complete_months(txs[0]["date"], txs[-1]["date"])[-6:]
    per_month: dict[str, dict[str, int]] = {}
    for t in txs:
        if t["kind"] == "expense":
            per_month.setdefault(t["category_id"], {}).setdefault(t["date"][:7], 0)
            per_month[t["category_id"]][t["date"][:7]] -= t["amount_cents"]
    items = []
    for cat in conn.execute("SELECT id, name, grp FROM categories WHERE kind='expense' ORDER BY name"):
        months = per_month.get(cat["id"], {})
        history = [max(months.get(m, 0), 0) for m in whole]
        items.append({
            "id": cat["id"],
            "name": cat["name"],
            "group": cat["grp"],
            "budget": budgets.get(cat["id"]),
            "spent": months.get(current, 0) if current else 0,
            "suggested": round(median(history)) if history else None,
            "history": history,
        })
    items.sort(key=lambda i: (i["budget"] is None, -(i["suggested"] or 0)))
    return {"month": current, "months": whole, "items": items}


class BudgetUpdate(BaseModel):
    monthly_eur: float = Field(gt=0, le=10_000_000)


@app.put("/api/budgets/{category_id}")
def set_budget(category_id: str, body: BudgetUpdate, conn: sqlite3.Connection = DB) -> dict:
    _category_exists(conn, category_id)
    conn.execute(
        "INSERT INTO budgets (category_id, monthly_cents) VALUES (?,?) "
        "ON CONFLICT(category_id) DO UPDATE SET monthly_cents=excluded.monthly_cents",
        (category_id, round(body.monthly_eur * 100)),
    )
    _after_data_change(conn)
    return {"ok": True}


@app.delete("/api/budgets/{category_id}")
def delete_budget(category_id: str, conn: sqlite3.Connection = DB) -> dict:
    conn.execute("DELETE FROM budgets WHERE category_id=?", (category_id,))
    _after_data_change(conn)
    return {"ok": True}


# ---------------------------------------------------------------- reglas y categorías


def _upsert_rule(conn: sqlite3.Connection, pattern: str, category_id: str, sign: str, label: str | None) -> None:
    conn.execute(
        "INSERT INTO rules (pattern, category_id, sign, priority, source, label) VALUES (?,?,?,100,'user',?) "
        "ON CONFLICT(pattern, sign, source) DO UPDATE SET category_id=excluded.category_id, label=excluded.label, enabled=1",
        (pattern, category_id, sign, label),
    )


@app.get("/api/rules")
def list_rules(conn: sqlite3.Connection = DB) -> dict:
    rows = conn.execute(
        "SELECT r.id, r.pattern, r.category_id, r.sign, r.source, r.label, r.enabled, c.name AS category_name, "
        "(SELECT COUNT(*) FROM transactions t WHERE t.rule_id = r.id) AS matches "
        "FROM rules r JOIN categories c ON c.id = r.category_id "
        "ORDER BY CASE r.source WHEN 'user' THEN 0 ELSE 1 END, matches DESC, r.pattern"
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


class RuleCreate(BaseModel):
    pattern: str = Field(min_length=2, max_length=100)
    category_id: str
    sign: Literal["any", "in", "out"] = "any"
    label: str | None = Field(None, max_length=60)


@app.post("/api/rules")
def create_rule(body: RuleCreate, conn: sqlite3.Connection = DB) -> dict:
    _category_exists(conn, body.category_id)
    pattern = normalize(body.pattern)
    if len(pattern) < 2:
        raise HTTPException(422, "El texto de la regla debe tener al menos dos letras o números.")
    _upsert_rule(conn, pattern, body.category_id, body.sign, (body.label or "").strip() or None)
    changed = reclassify_all(conn)
    _after_data_change(conn)
    return {"ok": True, "changed": changed}


class RuleToggle(BaseModel):
    enabled: bool


@app.patch("/api/rules/{rule_id}")
def toggle_rule(rule_id: int, body: RuleToggle, conn: sqlite3.Connection = DB) -> dict:
    cursor = conn.execute("UPDATE rules SET enabled=? WHERE id=?", (int(body.enabled), rule_id))
    if not cursor.rowcount:
        raise HTTPException(404, "Regla no encontrada.")
    changed = reclassify_all(conn)
    _after_data_change(conn)
    return {"ok": True, "changed": changed}


@app.delete("/api/rules/{rule_id}")
def delete_rule(rule_id: int, conn: sqlite3.Connection = DB) -> dict:
    row = conn.execute("SELECT source FROM rules WHERE id=?", (rule_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Regla no encontrada.")
    if row["source"] != "user":
        raise HTTPException(422, "Las reglas del sistema no se borran: desactívala.")
    conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    changed = reclassify_all(conn)
    _after_data_change(conn)
    return {"ok": True, "changed": changed}


class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    kind: Literal["income", "expense"] = "expense"
    grp: Literal["needs", "wants", "savings"] = "wants"


@app.post("/api/categories")
def create_category(body: CategoryCreate, conn: sqlite3.Connection = DB) -> dict:
    name = body.name.strip()
    slug = "u_" + re.sub(r"[^a-z0-9]+", "_", normalize(name).lower()).strip("_")
    if slug == "u_":
        raise HTTPException(422, "Elige un nombre con letras o números.")
    if conn.execute("SELECT 1 FROM categories WHERE id=? OR LOWER(name)=LOWER(?)", (slug, name)).fetchone():
        raise HTTPException(409, "Ya existe una categoría con ese nombre.")
    conn.execute(
        "INSERT INTO categories (id, name, kind, grp, is_system) VALUES (?,?,?,?,0)",
        (slug, name, body.kind, "income" if body.kind == "income" else body.grp),
    )
    conn.commit()
    return {"id": slug}


@app.get("/api/settings")
def read_settings(conn: sqlite3.Connection = DB) -> dict:
    return get_settings(conn)


@app.put("/api/settings")
def write_settings(body: dict[str, float], conn: sqlite3.Connection = DB) -> dict:
    for key, value in body.items():
        if key not in DEFAULT_SETTINGS:
            raise HTTPException(422, f"Ajuste desconocido: {key}")
        if value < 0 or value > 10_000_000:
            raise HTTPException(422, f"Valor fuera de rango: {key}")
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
    _after_data_change(conn)
    return get_settings(conn)


# ---------------------------------------------------------------- importación


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "El fichero supera el máximo de 25 MB.")
    return data


def _parse_mapping(mapping: str | None) -> ingest.ManualMapping | None:
    if not mapping:
        return None
    try:
        values = json.loads(mapping)
        return ingest.ManualMapping(**{k: (int(v) if v is not None else None) for k, v in values.items()})
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Mapeo de columnas no válido.") from exc


def _account_by_iban(conn: sqlite3.Connection, iban: str | None) -> int | None:
    if not iban:
        return None
    row = conn.execute("SELECT id FROM accounts WHERE iban=?", (iban,)).fetchone()
    return row["id"] if row else None


@app.post("/api/import/preview")
async def import_preview(
    file: UploadFile = File(...),
    mapping: str | None = Form(None),
    account_id: int | None = Form(None),
    conn: sqlite3.Connection = DB,
) -> dict:
    data = await _read_upload(file)
    try:
        raw, result = ingest.parse_upload(data, file.filename or "extracto", _parse_mapping(mapping))
    except FormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        return {"status": "needs_mapping", "raw": ingest.raw_preview(raw)}
    target = account_id or _account_by_iban(conn, result.account_hint)
    return {"status": "ok", "preview": ingest.preview(conn, result, target), "account_id": target}


@app.post("/api/import/commit")
async def import_commit(
    file: UploadFile = File(...),
    mapping: str | None = Form(None),
    account_id: int | None = Form(None),
    account_name: str | None = Form(None),
    conn: sqlite3.Connection = DB,
) -> dict:
    data = await _read_upload(file)
    try:
        _, result = ingest.parse_upload(data, file.filename or "extracto", _parse_mapping(mapping))
    except FormatError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result is None:
        raise HTTPException(422, "No reconozco este fichero: indica qué es cada columna.")
    target = account_id or _account_by_iban(conn, result.account_hint)
    if target and not conn.execute("SELECT 1 FROM accounts WHERE id=?", (target,)).fetchone():
        raise HTTPException(422, "Esa cuenta no existe.")
    if not target:
        name = (account_name or "").strip()[:60] or f"Cuenta {result.bank}"
        target = conn.execute(
            "INSERT INTO accounts (name, bank, iban) VALUES (?,?,?)", (name, result.bank, result.account_hint),
        ).lastrowid
    summary = ingest.store(conn, result, file.filename or "extracto", target)
    _after_data_change(conn)
    return {**summary, "account_id": target}


@app.get("/api/imports")
def list_imports(conn: sqlite3.Connection = DB) -> dict:
    rows = conn.execute(
        "SELECT i.*, a.name AS account_name, "
        "(SELECT COUNT(*) FROM transactions t WHERE t.import_id = i.id) AS tx_count "
        "FROM imports i JOIN accounts a ON a.id = i.account_id ORDER BY i.id DESC"
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.delete("/api/imports/{import_id}")
def delete_import(import_id: int, conn: sqlite3.Connection = DB) -> dict:
    row = conn.execute("SELECT account_id FROM imports WHERE id=?", (import_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Importación no encontrada.")
    removed = conn.execute("SELECT COUNT(*) FROM transactions WHERE import_id=?", (import_id,)).fetchone()[0]
    conn.execute("DELETE FROM transactions WHERE import_id=?", (import_id,))
    conn.execute("DELETE FROM imports WHERE id=?", (import_id,))
    conn.execute(
        "DELETE FROM accounts WHERE id=? AND NOT EXISTS (SELECT 1 FROM transactions WHERE account_id=?) "
        "AND NOT EXISTS (SELECT 1 FROM imports WHERE account_id=?)",
        (row["account_id"],) * 3,
    )
    _after_data_change(conn)
    return {"removed": removed}


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@app.patch("/api/accounts/{account_id}")
def rename_account(account_id: int, body: AccountUpdate, conn: sqlite3.Connection = DB) -> dict:
    cursor = conn.execute("UPDATE accounts SET name=? WHERE id=?", (body.name.strip(), account_id))
    conn.commit()
    if not cursor.rowcount:
        raise HTTPException(404, "Cuenta no encontrada.")
    return {"ok": True}


@app.get("/api/{rest:path}")
def api_not_found(rest: str) -> Response:
    raise HTTPException(404, "Ruta no encontrada.")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
