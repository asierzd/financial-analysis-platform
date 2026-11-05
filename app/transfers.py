"""Traspasos entre cuentas propias de distintos bancos.

Cada extracto solo ve su lado: lo que sale de una cuenta parece un gasto y lo que entra en otra, un ingreso.
Aquí se emparejan importes iguales de signo contrario en fechas cercanas, pero solo como propuesta: pagarle
a alguien la misma cantidad que te ingresa otra persona es posible, así que decide siempre el usuario.

Las decisiones se guardan por huella del movimiento, así sobreviven a deshacer y repetir una importación.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from .analytics import load_txs
from .categorize import reclassify_all
from .db import get_settings

INTERNAL_CATEGORY = "internos"
# Solo lo que podría ser un traspaso. Un recibo o una compra no se proponen aunque coincida el importe.
OUT_CATEGORIES = {"transfer_out", "sin_clasificar", "ahorro"}
IN_CATEGORIES = {"transfer_in", "bizum_in", "otros_ingresos", "ahorro"}


def _pair_key(out_tx: dict, in_tx: dict) -> str:
    return f"{out_tx['fingerprint']}:{in_tx['fingerprint']}"


def _public(tx: dict) -> dict:
    return {key: tx[key] for key in ("id", "date", "concept", "merchant", "account_id", "category_name")}


def find_candidates(conn: sqlite3.Connection, account_ids: list[int] | None = None) -> list[dict]:
    """Empareja uno a uno, empezando por las parejas más próximas en el tiempo."""
    window = int(get_settings(conn)["transfer_window_days"])
    decided = {row["pair_key"] for row in conn.execute("SELECT pair_key FROM transfer_decisions")}
    outgoing: list[dict] = []
    incoming: dict[int, list[dict]] = {}
    for tx in load_txs(conn):
        if tx["category_source"] == "user":
            continue  # lo clasificado a mano ya está decidido
        if tx["amount_cents"] < 0 and tx["category_id"] in OUT_CATEGORIES:
            outgoing.append(tx)
        elif tx["amount_cents"] > 0 and tx["category_id"] in IN_CATEGORIES:
            incoming.setdefault(tx["amount_cents"], []).append(tx)

    options = []
    for out_tx in outgoing:
        for in_tx in incoming.get(-out_tx["amount_cents"], []):
            if in_tx["account_id"] == out_tx["account_id"] or _pair_key(out_tx, in_tx) in decided:
                continue
            gap = abs((date.fromisoformat(in_tx["date"]) - date.fromisoformat(out_tx["date"])).days)
            if gap <= window:
                options.append((gap, out_tx, in_tx))
    options.sort(key=lambda option: (option[0], option[1]["date"], option[1]["id"], option[2]["id"]))

    used: set[int] = set()
    pairs = []
    for gap, out_tx, in_tx in options:
        if out_tx["id"] in used or in_tx["id"] in used:
            continue
        used.update((out_tx["id"], in_tx["id"]))
        if account_ids and out_tx["account_id"] not in account_ids and in_tx["account_id"] not in account_ids:
            continue
        pairs.append({"out": _public(out_tx), "in": _public(in_tx), "amount_cents": in_tx["amount_cents"], "gap_days": gap})
    pairs.sort(key=lambda pair: pair["out"]["date"], reverse=True)
    return pairs


def summary(conn: sqlite3.Connection, account_ids: list[int] | None = None) -> dict:
    pairs = find_candidates(conn, account_ids)
    return {"count": len(pairs), "total_cents": sum(pair["amount_cents"] for pair in pairs)}


def _load_pair(conn: sqlite3.Connection, out_id: int, in_id: int) -> tuple[dict, dict]:
    rows = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, fingerprint, amount_cents, account_id FROM transactions WHERE id IN (?,?)", (out_id, in_id))}
    if out_id not in rows or in_id not in rows:
        raise ValueError("Alguno de los dos movimientos ya no existe.")
    out_tx, in_tx = rows[out_id], rows[in_id]
    if out_tx["amount_cents"] >= 0 or in_tx["amount_cents"] != -out_tx["amount_cents"]:
        raise ValueError("Esos movimientos no tienen importes opuestos.")
    if out_tx["account_id"] == in_tx["account_id"]:
        raise ValueError("Los dos movimientos son de la misma cuenta.")
    return out_tx, in_tx


def decide(conn: sqlite3.Connection, out_id: int, in_id: int, status: str) -> None:
    out_tx, in_tx = _load_pair(conn, out_id, in_id)
    conn.execute(
        "INSERT INTO transfer_decisions (pair_key, out_fingerprint, in_fingerprint, status) VALUES (?,?,?,?) "
        "ON CONFLICT(pair_key) DO UPDATE SET status=excluded.status",
        (_pair_key(out_tx, in_tx), out_tx["fingerprint"], in_tx["fingerprint"], status),
    )
    apply_confirmed(conn)


def apply_confirmed(conn: sqlite3.Connection) -> None:
    """Marca como internos los pares confirmados. También tras reimportar, cuando vuelven como movimientos nuevos."""
    conn.execute(
        f"""UPDATE transactions SET category_id='{INTERNAL_CATEGORY}', category_source='user', rule_id=NULL
            WHERE category_source != 'user' AND fingerprint IN (
                SELECT d.out_fingerprint FROM transfer_decisions d
                JOIN transactions t ON t.fingerprint = d.in_fingerprint WHERE d.status='confirmed'
                UNION
                SELECT d.in_fingerprint FROM transfer_decisions d
                JOIN transactions t ON t.fingerprint = d.out_fingerprint WHERE d.status='confirmed')"""
    )


def undo(conn: sqlite3.Connection, out_id: int, in_id: int) -> None:
    out_tx, in_tx = _load_pair(conn, out_id, in_id)
    row = conn.execute("SELECT status FROM transfer_decisions WHERE pair_key=?", (_pair_key(out_tx, in_tx),)).fetchone()
    conn.execute("DELETE FROM transfer_decisions WHERE pair_key=?", (_pair_key(out_tx, in_tx),))
    if row and row["status"] == "confirmed":
        conn.execute(
            f"UPDATE transactions SET category_source='rule' WHERE id IN (?,?) AND category_id='{INTERNAL_CATEGORY}'",
            (out_id, in_id),
        )
        reclassify_all(conn)


def decided(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT d.status, d.decided_at, o.id AS out_id, i.id AS in_id, i.amount_cents, o.date AS out_date, i.date AS in_date,
                  o.concept AS out_concept, i.concept AS in_concept, o.account_id AS out_account, i.account_id AS in_account
           FROM transfer_decisions d
           JOIN transactions o ON o.fingerprint = d.out_fingerprint
           JOIN transactions i ON i.fingerprint = d.in_fingerprint
           ORDER BY o.date DESC, o.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
