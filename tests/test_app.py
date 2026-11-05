"""Pruebas del núcleo: lectura de formatos, clasificación, deduplicación, alertas, análisis y seguridad."""
import importlib
import io
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import xlwt
from fastapi.testclient import TestClient
from openpyxl import Workbook

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app import alerts as alerts_mod  # noqa: E402
from app import analytics  # noqa: E402
from app.categorize import Classifier, clean_merchant, normalize  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.ingest import detect_parser, service  # noqa: E402
from app.ingest.base import FormatError  # noqa: E402
from app.ingest.readers import read_file  # noqa: E402
from app.ingest.tabular import parse_amount, parse_date  # noqa: E402

HEADER = ["F. Valor", "Fecha", "Concepto", "Movimiento", "Importe", "Divisa", "Disponible", "Divisa", "Observaciones"]


def bbva_xlsx(rows, title=True) -> bytes:
    wb = Workbook()
    ws = wb.active
    if title:
        ws.append(["Últimos movimientos"])
        ws.append(["BBVA"])
        ws.append(["ES12 0182 0000 1234 5678 9012"])
        ws.append([])
    ws.append([None, *HEADER])
    for r in rows:
        ws.append([None, *r])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def row(day, concept, amount, balance=None, movement="", notes=""):
    stamp = datetime.fromisoformat(day)
    return [stamp, stamp, concept, movement, amount, "EUR", balance, "EUR", notes]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOWED_HOSTS", "testserver,localhost")
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def upload(client, data, name="movimientos.xlsx", path="/api/import/commit", **form):
    return client.post(path, files={"file": (name, data)}, data=form)


# ---- lectura


@pytest.mark.parametrize("text,cents", [
    ("1.234,56", 123456), ("-12,30 €", -1230), ("1,234.56", 123456), ("(45,00)", -4500), ("12,30-", -1230),
    ("1.000", 100000), ("3,5", 350), ("-0,01", -1), ("+5,00", 500), ("−10,00", -1000), ("1.234.567,89", 123456789),
])
def test_parse_amount_texts(text, cents):
    assert parse_amount(text) == cents


def test_parse_amount_floats_do_not_lose_cents():
    assert parse_amount(-19.99) == -1999
    assert parse_amount(1234.1) == 123410
    assert parse_amount(0.07) == 7
    assert parse_amount("hola") is None


@pytest.mark.parametrize("cell,expected", [
    ("31/12/2025", date(2025, 12, 31)), ("31-12-25", date(2025, 12, 31)), ("2025-12-31", date(2025, 12, 31)),
    (datetime(2025, 12, 31, 10), date(2025, 12, 31)), (46022, date(2025, 12, 31)), ("no", None),
])
def test_parse_date(cell, expected):
    assert parse_date(cell) == expected


def test_bbva_xlsx_detected_with_title_rows_and_duplicate_divisa():
    raw = read_file(bbva_xlsx([row("2026-01-02", "MERCADONA", -20.5, 979.5)]), "x.xlsx")
    parser = detect_parser(raw)
    assert parser and parser.id == "bbva"
    result = parser.parse(raw)
    assert result.account_hint == "ES1201820000123456789012"
    tx = result.transactions[0]
    assert (tx.amount_cents, tx.balance_cents, tx.currency, tx.date) == (-2050, 97950, "EUR", date(2026, 1, 2))


def test_bbva_detected_without_the_word_bbva_by_its_columns():
    raw = read_file(bbva_xlsx([row("2026-01-02", "X", -1, 1)], title=False), "export.xlsx")
    assert detect_parser(raw).id == "bbva"


def test_bbva_historico_layout():
    wb = Workbook()
    ws = wb.active
    ws.title = "Historico"
    for _ in range(15):
        ws.append(["BBVA"])
    ws.append([None, None, "F. CONTABLE", "F. VALOR", "CÓDIGO", "CONCEPTO", "BENEFICIARIO/ORDENANTE", "OBSERVACIONES",
               "IMPORTE", "SALDO", "REMESA"])
    ws.append([None, None, datetime(2026, 1, 5), datetime(2026, 1, 4), "00123", "RECIBO", "ENDESA", "Luz", -45.5, 900, "R1"])
    buffer = io.BytesIO()
    wb.save(buffer)
    raw = read_file(buffer.getvalue(), "historico.xlsx")
    result = detect_parser(raw).parse(raw)
    tx = result.transactions[0]
    assert tx.date == date(2026, 1, 5) and tx.value_date == date(2026, 1, 4)
    assert tx.amount_cents == -4550 and tx.balance_cents == 90000
    assert "ENDESA" in tx.notes


def test_real_old_xls():
    book = xlwt.Workbook()
    ws = book.add_sheet("Movimientos")
    style = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    for c, name in enumerate(HEADER):
        ws.write(4, c, name)
    ws.write(0, 0, "BBVA")
    ws.write(5, 0, datetime(2026, 1, 2), style)
    ws.write(5, 1, datetime(2026, 1, 2), style)
    ws.write(5, 2, "LIDL")
    ws.write(5, 4, -12.5)
    ws.write(5, 6, 100.0)
    buffer = io.BytesIO()
    book.save(buffer)
    raw = read_file(buffer.getvalue(), "m.xls")
    tx = detect_parser(raw).parse(raw).transactions[0]
    assert tx.date == date(2026, 1, 2) and tx.amount_cents == -1250


def test_fake_xls_that_is_html_and_csv_with_latin1():
    html = ("<html><table><tr><td>BBVA</td></tr><tr><th>F. Valor</th><th>Fecha</th><th>Concepto</th><th>Importe</th>"
            "<th>Disponible</th></tr><tr><td>02/01/2026</td><td>02/01/2026</td><td>CAF&Eacute; SOL</td><td>-3,50</td>"
            "<td>1.000,00</td></tr></table></html>").encode()
    raw = read_file(html, "falso.xls")
    tx = detect_parser(raw).parse(raw).transactions[0]
    assert tx.concept == "CAFÉ SOL" and tx.amount_cents == -350 and tx.balance_cents == 100000

    text = "BBVA\n\nF. Valor;Fecha;Concepto;Importe;Disponible\n02/01/2026;02/01/2026;Cafetería Ñu;-3,50;1.000,00\n"
    raw = read_file(text.encode("cp1252"), "m.csv")
    tx = detect_parser(raw).parse(raw).transactions[0]
    assert tx.concept == "Cafetería Ñu" and tx.amount_cents == -350


def test_pdf_rejected_with_explanation():
    with pytest.raises(FormatError, match="PDF"):
        read_file(b"%PDF-1.7 ...", "m.pdf")


def test_garbage_and_empty_are_rejected():
    with pytest.raises(FormatError):
        read_file(b"", "m.csv")
    with pytest.raises(FormatError):
        read_file(b"hola mundo sin columnas", "m.txt")
    with pytest.raises(FormatError):
        read_file(b"PK\x03\x04roto", "m.xlsx")


# ---- Santander


def santander_xlsx(rows, titles=("Banco Santander",), header=None, name=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    for title in titles:
        ws.append([title])
    ws.append([])
    ws.append(header or ["Fecha operación", "Fecha valor", "Concepto", "Importe", "Saldo"])
    for r in rows:
        ws.append(r)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


SANTANDER_ROWS = [
    ["05/03/2026", "06/03/2026", "Pago Movil En LIDL SUPERMERCADOS, MADRID, Tarj. :*123456", "-20,50", "979,50"],
    ["02/03/2026", "02/03/2026", "Bizum De Marta Ruiz Concepto Cena", "1.000,00", "1.000,00"],
]


def test_santander_text_dates_and_amounts_with_title():
    raw = read_file(santander_xlsx(SANTANDER_ROWS), "movimientos.xlsx")
    parser = detect_parser(raw)
    assert parser.id == "santander"
    tx = parser.parse(raw).transactions[0]
    assert tx.date == date(2026, 3, 5) and tx.value_date == date(2026, 3, 6)
    assert (tx.amount_cents, tx.balance_cents) == (-2050, 97950)


@pytest.mark.parametrize("titles,filename", [
    (("Movimientos de cuenta",), "extracto_santander.xlsx"),
    (("Cuenta ES12 0049 1234 5678 9012 3456",), "m.xlsx"),
    (("Movimientos",), "m.xlsx"),
])
def test_santander_detected_by_filename_entity_code_or_columns(titles, filename):
    raw = read_file(santander_xlsx(SANTANDER_ROWS, titles=titles), filename)
    assert detect_parser(raw).id == "santander"


@pytest.mark.parametrize("header", [
    ["FECHA OPERACIÓN", "FECHA VALOR", "CONCEPTO", "IMPORTE EUR", "SALDO EUR"],
    ["F. Operación", "F. Valor", "Concepto", "Importe (€)", "Saldo (€)"],
])
def test_santander_header_variants(header):
    raw = read_file(santander_xlsx(SANTANDER_ROWS, header=header), "santander.xlsx")
    tx = detect_parser(raw).parse(raw).transactions[0]
    assert (tx.amount_cents, tx.balance_cents) == (-2050, 97950)


def test_santander_separate_debit_and_credit_columns():
    rows = [["05/03/2026", "05/03/2026", "Compra", "20,50", None], ["06/03/2026", "06/03/2026", "Abono", None, "10,00"]]
    raw = read_file(santander_xlsx(rows, header=["Fecha operación", "Fecha valor", "Concepto", "Cargo", "Abono"]), "santander.csv.xlsx")
    assert [t.amount_cents for t in detect_parser(raw).parse(raw).transactions] == [-2050, 1000]


def test_santander_csv_export():
    text = "Santander\n\nFecha operación;Fecha valor;Concepto;Importe;Saldo\n05/03/2026;05/03/2026;Cafetería;-3,50;96,50\n"
    raw = read_file(text.encode("cp1252"), "m.csv")
    assert detect_parser(raw).id == "santander"


def test_each_bank_only_claims_its_own_file():
    other = read_file(santander_xlsx(SANTANDER_ROWS, titles=("Cuenta ES12 0182 1234 5678 9012 3456",)), "m.xlsx")
    assert detect_parser(other).id == "bbva"
    bbva = read_file(bbva_xlsx([row("2026-01-02", "Bizum de Santander Ana", -1, 1)]), "m.xlsx")
    assert detect_parser(bbva).id == "bbva"
    assert detect_parser(read_file(b"Dia;Texto;Valor\n01/01/2026;x;1\n", "m.csv")) is None


def test_santander_concept_mentioning_bbva_does_not_confuse_detection():
    rows = [["05/03/2026", "05/03/2026", "Transferencia A Favor De BBVA", "-5,00", "95,00"]]
    assert detect_parser(read_file(santander_xlsx(rows), "m.xlsx")).id == "santander"


# ---- varias cuentas


def two_banks(client):
    upload(client, bbva_xlsx([row("2026-10-01", "MERCADONA", -10.0, 500.0)]))
    return santander_xlsx(SANTANDER_ROWS)


def test_second_bank_is_not_proposed_into_the_first_banks_account(client):
    data = two_banks(client)
    answer = upload(client, data, path="/api/import/preview").json()
    assert answer["preview"]["bank"] == "Santander" and answer["account_id"] is None
    done = upload(client, data, account_name="Cuenta Santander").json()
    accounts = client.get("/api/meta").json()["accounts"]
    assert [(a["name"], a["bank"]) for a in accounts] == [("Cuenta BBVA", "BBVA"), ("Cuenta Santander", "Santander")]
    assert client.get("/api/dashboard", params={"accounts": done["account_id"]}).json()["totals"]["count"] == 2
    # Y el siguiente fichero de Santander sí se propone en su cuenta, sin duplicar.
    again = upload(client, data, path="/api/import/preview").json()
    assert again["account_id"] == done["account_id"] and again["preview"]["new"] == 0


def test_preview_for_new_account_ignores_the_suggested_one(client):
    data = bbva_xlsx([row("2026-10-01", "MERCADONA", -10.0, 500.0)])
    upload(client, data)
    answer = client.post("/api/import/preview", files={"file": ("m.xlsx", data)}, data={"new_account": "true"}).json()
    assert answer["account_id"] is None and answer["preview"]["new"] == 1


def test_total_balance_adds_both_accounts(client):
    data = two_banks(client)
    upload(client, data)
    assert client.get("/api/dashboard").json()["balance"] == 50000 + 97950


def test_older_statement_of_other_bank_does_not_raise_missing_recurring(client):
    rows = [row(f"2026-{m:02d}-05", "Recibo DIGI", -20.0) for m in range(1, 7)]
    upload(client, bbva_xlsx(rows, title=False), name="uno.xlsx", account_name="Uno")
    # Otra cuenta con datos mucho más recientes.
    upload(client, santander_xlsx([["20/10/2026", "20/10/2026", "COMPRA X", "-1,00", "9,00"]]), account_name="Dos")
    assert "missing_recurring" not in alert_types(client)
    recurring = client.get("/api/analysis").json()["recurring"]
    assert [r["merchant"] for r in recurring["out"]] == ["Digi"]


def test_new_merchant_grace_applies_to_each_account(client):
    rows = [row("2026-01-02", "MERCADONA", -10.0), row("2026-06-01", "MERCADONA", -10.0)]
    upload(client, bbva_xlsx(rows), account_name="Uno")
    upload(client, santander_xlsx([["02/06/2026", "02/06/2026", "COMPRA CARA SL", "-900,00", "100,00"]]), account_name="Dos")
    assert "new_merchant" not in alert_types(client)


def three_accounts(client):
    """Tres cuentas con importes reconocibles: 100, 20 y 3 euros de gasto."""
    ids = []
    for name, amount, day in [("Uno", -100.0, 1), ("Dos", -20.0, 2), ("Tres", -3.0, 3)]:
        rows = [["%02d/03/2026" % day, "%02d/03/2026" % day, f"COMPRA {name}", amount, 1000 + amount]]
        ids.append(upload(client, santander_xlsx(rows, titles=("Movimientos",)), name=f"{name}.xlsx", account_name=name).json()["account_id"])
    return ids


def test_any_combination_of_accounts_can_be_added_up(client):
    a, b, c = three_accounts(client)
    expense = lambda accounts: client.get("/api/dashboard", params={"accounts": accounts}).json()["totals"]["expense"]
    assert expense(f"{a}") == 10000
    assert expense(f"{a},{c}") == 10300
    assert expense(f"{b},{c}") == 2300
    assert expense(f"{a},{b},{c}") == client.get("/api/dashboard").json()["totals"]["expense"] == 12300
    assert client.get("/api/analysis", params={"accounts": f"{b},{c}"}).json()["fees"]["count"] == 0
    assert client.get("/api/dashboard", params={"accounts": f"{a},{c}"}).json()["balance"] == 90000 + 99700
    moved = client.get("/api/transactions", params={"accounts": f"{a},{c}"}).json()
    assert moved["total"] == 2 and {i["account_name"] for i in moved["items"]} == {"Uno", "Tres"}
    assert client.get("/api/dashboard", params={"accounts": "x"}).status_code == 422


def test_meta_scopes_to_selection_but_always_lists_every_account(client):
    a, b, c = three_accounts(client)
    meta = client.get("/api/meta", params={"accounts": f"{a}"}).json()
    assert meta["count"] == 1 and meta["last"] == "2026-03-01"
    assert [(x["name"], x["count"], x["balance"]) for x in meta["accounts"]] == [
        ("Uno", 1, 90000), ("Dos", 1, 98000), ("Tres", 1, 99700)]


def test_alerts_follow_selected_accounts_including_counts_and_bulk(client):
    uno = upload(client, bbva_xlsx([row("2026-01-20", "Comisión de mantenimiento", -15.0)]), account_name="Uno").json()["account_id"]
    dos = upload(client, santander_xlsx([["20/01/2026", "20/01/2026", "Comision Mantenimiento", "-9,00", "9,00"]]), account_name="Dos").json()["account_id"]
    assert [a["amount_cents"] for a in client.get("/api/alerts", params={"accounts": dos, "type": "fee"}).json()["items"]] == [-900]
    assert client.get("/api/meta", params={"accounts": uno}).json()["alerts_new"] == 1
    counts = client.get("/api/alerts", params={"accounts": dos}).json()["counts"]
    assert [c["total"] for c in counts if c["type"] == "fee"] == [-900]
    client.post("/api/alerts/bulk", json={"status": "reviewed", "accounts": str(dos)})
    assert client.get("/api/meta").json()["alerts_new"] == 1


def test_second_account_of_same_bank_gets_a_distinct_name(client):
    upload(client, santander_xlsx([["01/03/2026", "01/03/2026", "A", "-1,00", "1,00"]], titles=("Santander ES00 0049 0000 0000 0000 1111",)))
    upload(client, santander_xlsx([["02/03/2026", "02/03/2026", "B", "-1,00", "1,00"]], titles=("Santander ES00 0049 0000 0000 0000 2222",)))
    assert [a["name"] for a in client.get("/api/meta").json()["accounts"]] == ["Cuenta Santander", "Cuenta Santander 2"]


def test_previous_period_is_only_compared_when_every_selected_account_covers_it(client):
    uno = upload(client, bbva_xlsx(month_rows()), account_name="Uno").json()["account_id"]
    dos = upload(client, santander_xlsx([["15/10/2025", "15/10/2025", "COMPRA", "-5,00", "5,00"]]), account_name="Dos").json()["account_id"]
    period = {"from": "2025-10-01", "to": "2025-10-31"}
    assert client.get("/api/dashboard", params={**period, "accounts": uno}).json()["previous"] is not None
    assert client.get("/api/dashboard", params=period).json()["previous"] is None
    assert client.get("/api/dashboard", params={**period, "accounts": dos}).json()["previous"] is None


def test_csv_shows_account(client):
    upload(client, bbva_xlsx([row("2026-01-01", "MERCADONA", -5.0)]), account_name="Mi nómina")
    assert "Mi nómina" in client.get("/api/transactions.csv").text


# ---- traspasos entre cuentas


def transfer_setup(client):
    bbva = upload(client, bbva_xlsx([
        row("2026-03-10", "Transferencia a favor de MI OTRA CUENTA", -400.0, 600.0),
        row("2026-03-12", "MERCADONA", -50.0, 550.0),
        row("2026-04-01", "Bizum a favor de Pablo", -35.0, 515.0),
    ]), account_name="BBVA").json()["account_id"]
    sant = upload(client, santander_xlsx([
        ["01/04/2026", "01/04/2026", "Bizum De Marta Concepto Cena", "35,00", "485,00"],
        ["11/03/2026", "11/03/2026", "Transferencia De Yo Mismo", "400,00", "450,00"],
        ["12/03/2026", "12/03/2026", "Devolucion MERCADONA", "50,00", "50,00"],
    ]), account_name="Santander").json()["account_id"]
    return bbva, sant


def transfer_pairs(client, **params):
    return client.get("/api/transfers", params=params).json()["pending"]


def test_transfer_candidates_pair_opposite_amounts_across_accounts(client):
    bbva, sant = transfer_setup(client)
    pairs = transfer_pairs(client)
    # La compra en Mercadona no se propone aunque coincida con una devolución del mismo importe.
    assert [(p["amount_cents"], p["gap_days"]) for p in pairs] == [(3500, 0), (40000, 1)]
    assert pairs[1]["out"]["account_id"] == bbva and pairs[1]["in"]["account_id"] == sant
    assert client.get("/api/meta").json()["transfers"] == {"count": 2, "total_cents": 43500}


def test_nothing_is_excluded_until_the_user_confirms(client):
    transfer_setup(client)
    before = client.get("/api/dashboard").json()["totals"]
    assert before["income"] == 48500 and before["expense"] == 48500
    pair = transfer_pairs(client)[1]
    body = {"pairs": [{"out_id": pair["out"]["id"], "in_id": pair["in"]["id"]}], "status": "confirmed"}
    assert client.post("/api/transfers/decide", json=body).status_code == 200
    after = client.get("/api/dashboard").json()["totals"]
    assert after["income"] == 8500 and after["expense"] == 8500
    assert [p["amount_cents"] for p in transfer_pairs(client)] == [3500]


def test_transfer_confirmation_can_be_undone_and_survives_reimport(client):
    transfer_setup(client)
    pair = transfer_pairs(client)[1]
    ids = {"out_id": pair["out"]["id"], "in_id": pair["in"]["id"]}
    client.post("/api/transfers/decide", json={"pairs": [ids], "status": "confirmed"})
    client.post("/api/rules", json={"pattern": "nada que ver", "category_id": "ocio"})  # fuerza reclasificar
    assert client.get("/api/transactions", params={"category": "internos"}).json()["total"] == 2

    client.post("/api/transfers/undo", json=ids)
    assert client.get("/api/transactions", params={"category": "internos"}).json()["total"] == 0
    assert client.get("/api/transactions", params={"category": "transfer_out"}).json()["total"] == 2
    assert len(transfer_pairs(client)) == 2


def test_transfer_decision_is_reapplied_after_undoing_and_repeating_an_import(client):
    rows = [["11/03/2026", "11/03/2026", "Transferencia De Yo Mismo", "400,00", "400,00"]]
    upload(client, bbva_xlsx([row("2026-03-10", "Transferencia a favor de YO", -400.0, 600.0)]), account_name="A")
    data = santander_xlsx(rows)
    upload(client, data, account_name="B")
    pair = transfer_pairs(client)[0]
    client.post("/api/transfers/decide", json={"pairs": [{"out_id": pair["out"]["id"], "in_id": pair["in"]["id"]}], "status": "confirmed"})
    import_id = client.get("/api/imports").json()["items"][0]["id"]
    client.delete(f"/api/imports/{import_id}")
    # Ya confirmaste que ese dinero fue a una cuenta tuya: no reaparece como gasto por deshacer la otra importación.
    assert client.get("/api/dashboard").json()["totals"]["expense"] == 0
    upload(client, data, account_name="B")
    totals = client.get("/api/dashboard").json()["totals"]
    assert totals["income"] == 0 and totals["expense"] == 0
    assert client.get("/api/transactions", params={"category": "internos"}).json()["total"] == 2
    assert transfer_pairs(client) == []


def test_rejected_transfer_is_not_proposed_again_but_can_be_recovered(client):
    transfer_setup(client)
    pair = transfer_pairs(client)[0]
    ids = {"out_id": pair["out"]["id"], "in_id": pair["in"]["id"]}
    client.post("/api/transfers/decide", json={"pairs": [ids], "status": "rejected"})
    assert [p["amount_cents"] for p in transfer_pairs(client)] == [40000]
    assert client.get("/api/transactions", params={"category": "transfer_out", "q": "Pablo"}).json()["total"] == 1
    decided = client.get("/api/transfers").json()["decided"]
    assert [(d["status"], d["amount_cents"]) for d in decided] == [("rejected", 3500)]
    client.post("/api/transfers/undo", json=ids)
    assert len(transfer_pairs(client)) == 2


def test_transfer_pairing_is_one_to_one_and_prefers_the_closest_date(client):
    upload(client, bbva_xlsx([row("2026-03-10", "Transferencia a favor de YO", -100.0, 900.0),
                              row("2026-03-12", "Transferencia a favor de YO", -100.0, 800.0)]), account_name="A")
    upload(client, santander_xlsx([["12/03/2026", "12/03/2026", "Transferencia De Yo", "100,00", "100,00"]]), account_name="B")
    pairs = transfer_pairs(client)
    assert len(pairs) == 1 and pairs[0]["out"]["date"] == "2026-03-12"


def test_transfer_window_same_account_and_manual_categories_are_respected(client):
    upload(client, bbva_xlsx([
        row("2026-03-01", "Transferencia a favor de A", -100.0), row("2026-03-02", "Transferencia de A", 100.0),  # misma cuenta
        row("2026-05-01", "Transferencia a favor de B", -200.0),
        row("2026-07-01", "Transferencia a favor de C", -300.0),
    ]), account_name="A")
    upload(client, santander_xlsx([
        ["10/05/2026", "10/05/2026", "Transferencia De B", "200,00", "200,00"],  # fuera de plazo
        ["01/07/2026", "01/07/2026", "Transferencia De C", "300,00", "500,00"],
    ]), account_name="B")
    assert [p["amount_cents"] for p in transfer_pairs(client)] == [30000]
    tx = next(t for t in client.get("/api/transactions", params={"q": "favor de C"}).json()["items"])
    client.patch(f"/api/transactions/{tx['id']}", json={"category_id": "ocio"})
    assert transfer_pairs(client) == []
    client.put("/api/settings", json={"transfer_window_days": 10})
    assert [p["amount_cents"] for p in transfer_pairs(client)] == [20000]


def test_invalid_transfer_decisions_are_refused(client):
    upload(client, bbva_xlsx([row("2026-03-01", "X", -100.0), row("2026-03-02", "Y", 100.0), row("2026-03-03", "Z", 50.0)]))
    items = {t["concept"]: t["id"] for t in client.get("/api/transactions").json()["items"]}
    bad = {"pairs": [{"out_id": items["X"], "in_id": items["Y"]}], "status": "confirmed"}
    assert client.post("/api/transfers/decide", json=bad).status_code == 422  # misma cuenta
    bad["pairs"][0]["in_id"] = items["Z"]
    assert client.post("/api/transfers/decide", json=bad).status_code == 422  # importes distintos
    bad["pairs"][0]["in_id"] = 99999
    assert client.post("/api/transfers/decide", json=bad).status_code == 422


# ---- Trade Republic

TR_HEADER = ["datetime", "date", "account_type", "category", "type", "asset_class", "name", "symbol", "shares", "price", "amount", "fee", "tax",
             "currency", "original_amount", "original_currency", "fx_rate", "description", "transaction_id", "counterparty_name",
             "counterparty_iban", "payment_reference", "mcc_code"]
_tr_counter = [0]


def tr(day, category, kind, asset="", name="", symbol="", shares="", price="", amount="", fee="", tax="", description="", tx_id=None, mcc="", counterparty=""):
    _tr_counter[0] += 1
    return [f"{day}T10:00:{_tr_counter[0] % 60:02d}.123Z", day, "DEFAULT", category, kind, asset, name, symbol, shares, price, amount, fee, tax, "EUR",
            "", "", "", description, tx_id or f"id-{_tr_counter[0]}", counterparty, "", "", mcc]


def tr_csv(rows, delimiter=","):
    import csv as _csv

    buffer = io.StringIO()
    writer = _csv.writer(buffer, delimiter=delimiter, quoting=_csv.QUOTE_ALL)
    writer.writerow(TR_HEADER)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def tr_upload(client, rows, **kw):
    return upload(client, tr_csv(rows), name="export.csv", **kw)


DEPOSIT = lambda day="2026-01-01", amount="1000.000000": tr(day, "CASH", "TRANSFER_INSTANT_INBOUND", name="Yo", amount=amount, description="Instant transfer received", counterparty="Yo")
WORLD = dict(asset="FUND", name="MSCI World", symbol="IE00B4L5Y983")


def fifo_rows():
    return [
        DEPOSIT(),
        tr("2026-01-02", "TRADING", "BUY", **WORLD, shares="10.0000000000", price="10.0000000000", amount="-100.00", fee="-1.00", description="Buy order"),
        tr("2026-02-02", "TRADING", "BUY", **WORLD, shares="10.0000000000", price="20.0000000000", amount="-200.00", description="Savings plan execution"),
        tr("2026-03-02", "TRADING", "SELL", **WORLD, shares="-15.0000000000", price="30.0000000000", amount="450.00", fee="-1.00", description="Sell order"),
    ]


def test_trade_republic_detected_by_columns_not_filename():
    raw = read_file(tr_csv(fifo_rows()), "cualquier_nombre.csv")
    assert detect_parser(raw).id == "traderepublic"


def test_trade_republic_detected_after_excel_resave_with_semicolons_and_decimal_commas(client):
    rows = [DEPOSIT(amount="1000,50"), tr("2026-01-02", "TRADING", "BUY", **WORLD, shares="1,5", price="10", amount="-15,00")]
    raw = read_file(tr_csv(rows, delimiter=";"), "e.csv")
    result = detect_parser(raw).parse(raw)
    assert [t.amount_cents for t in result.transactions] == [100050, -1500]
    assert result.events[0].shares == Decimal("1.5")


def test_fifo_cost_gain_and_cash_on_known_numbers(client):
    tr_upload(client, fifo_rows())
    data = client.get("/api/investments").json()
    position = data["positions"][0]
    # Vende 15: 10 a coste 101 (con comisión) + 5 a coste 100 = 201. Cobra 450 - 1 = 449. Ganancia 248.
    assert data["by_year"][0]["realized"] == 24800
    assert position["shares"] == 5 and position["cost"] == 10000
    # Quedan 5 títulos a 20 de coste; el último precio es 30.
    assert position["value"] == 15000 and position["unrealized"] == 5000 and position["price_source"] == "operation"
    assert data["summary"]["order_fees"] == 200
    assert data["cash"] == {"balance": 114800, "complete": True, "as_of": "2026-03-02"}  # 1000 - 101 - 200 + 449
    assert data["summary"]["savings_plan_buys"] == 1


def test_buying_and_selling_are_neither_expense_nor_income(client):
    tr_upload(client, fifo_rows())
    totals = client.get("/api/dashboard").json()["totals"]
    # Lo único que cuenta: el ingreso inicial y las dos comisiones.
    assert totals["income"] == 100000 and totals["expense"] == 200
    fee = client.get("/api/transactions", params={"category": "comisiones"}).json()
    assert fee["total"] == 2


def test_order_fee_does_not_alert_but_card_fee_does(client):
    rows = fifo_rows() + [tr("2026-03-05", "CASH", "CARD_ORDERING_FEE", name="Trade Republic Card", amount="0.000000", fee="-5.00", description="Trade Republic Card")]
    tr_upload(client, rows)
    fees = client.get("/api/alerts", params={"type": "fee"}).json()["items"]
    assert [a["amount_cents"] for a in fees] == [-500]


def test_interest_and_dividend_are_gross_income_with_separate_withholding(client):
    tr_upload(client, [
        DEPOSIT(),
        tr("2026-01-02", "TRADING", "BUY", asset="STOCK", name="ACME", symbol="US0000000001", shares="10", price="10", amount="-100.00"),
        tr("2026-02-01", "CASH", "INTEREST_PAYMENT", amount="2.000000", tax="-0.380000", description="Interest payment"),
        tr("2026-02-02", "CASH", "DIVIDEND", asset="STOCK", name="ACME", symbol="US0000000001", shares="10", amount="5.000000", tax="-0.750000", description="Cash Dividend"),
        tr("2026-02-03", "CASH", "BENEFITS_SAVEBACK", amount="3.000000", tax="-0.570000", description="Saveback"),
    ])
    income = {c["id"]: c["amount"] for c in client.get("/api/dashboard").json()["income_categories"]}
    assert income == {"transfer_in": 100000, "intereses": 700, "saveback": 300}
    data = client.get("/api/investments").json()
    assert data["summary"]["interest"] == 200 and data["summary"]["dividends"] == 500 and data["summary"]["withholding"] == 170
    assert data["cash"]["balance"] == 100000 - 10000 + 200 - 38 + 500 - 75 + 300 - 57
    assert data["positions"][0]["dividends"] == 500


def test_card_uses_rules_first_then_merchant_code(client):
    tr_upload(client, [
        DEPOSIT(),
        tr("2026-01-03", "CASH", "CARD_TRANSACTION", name="Mercadona", amount="-10.000000", mcc="5812"),
        tr("2026-01-04", "CASH", "CARD_TRANSACTION", name="Casa Pepe", amount="-20.000000", mcc="5812"),
        tr("2026-01-05", "CASH", "CARD_TRANSACTION", name="Cosa Rara", amount="-30.000000", mcc="1234"),
        tr("2026-01-06", "CASH", "CARD_TRANSACTION", name="Hotel Sol", amount="52.400000", mcc="7011", description="refund"),
        tr("2026-01-07", "CASH", "TRANSFER_DIRECT_DEBIT_INBOUND", amount="-12.990000", description="Sepa Direct Debit transfer to SPOTIFY AB"),
    ])
    items = {t["concept"]: t for t in client.get("/api/transactions").json()["items"]}
    assert items["Mercadona"]["category_id"] == "supermercado"
    assert items["Casa Pepe"]["category_id"] == "restaurantes"
    assert items["Cosa Rara"]["category_id"] == "sin_clasificar"
    assert items["SPOTIFY AB"]["category_id"] == "suscripciones" and items["SPOTIFY AB"]["amount_cents"] == -1299


def test_bank_given_category_survives_a_rule_and_reclassification(client):
    tr_upload(client, fifo_rows())
    client.post("/api/rules", json={"pattern": "MSCI World", "category_id": "ocio"})
    assert client.get("/api/transactions", params={"category": "inversiones"}).json()["total"] == 3


def test_reimport_dedupes_by_transaction_id_even_with_different_window(client):
    rows = fifo_rows()
    first = tr_upload(client, rows[:2]).json()
    assert first["new"] == 3  # depósito, compra y su comisión
    second = tr_upload(client, rows, account_id=first["account_id"]).json()
    assert second["new"] == 3 and second["skipped"] == 3
    assert client.get("/api/investments").json()["cash"]["balance"] == 114800
    assert len(client.get("/api/meta").json()["accounts"]) == 1
    data = client.get("/api/investments").json()
    assert data["positions"][0]["shares"] == 5


def test_partial_history_refuses_to_invent_a_cash_balance(client):
    rows = [tr("2026-01-02", "TRADING", "BUY", **WORLD, shares="10", price="10", amount="-100.00")]
    preview = upload(client, tr_csv(rows), name="e.csv", path="/api/import/preview").json()["preview"]
    assert any("apertura" in w for w in preview["warnings"])
    account = tr_upload(client, rows).json()["account_id"]
    data = client.get("/api/investments").json()
    assert data["cash"]["complete"] is False and data["cash"]["balance"] is None
    assert any("apertura" in w for w in data["warnings"])
    assert client.get("/api/dashboard").json()["balance"] is None
    # Importar después el principio completa el historial.
    # La interfaz propone la cuenta existente del mismo banco.
    assert upload(client, tr_csv([DEPOSIT()]), name="e.csv", path="/api/import/preview").json()["account_id"] == account
    tr_upload(client, [DEPOSIT()], account_id=account)
    assert client.get("/api/investments").json()["cash"]["balance"] == 90000


def test_invested_cash_is_not_a_low_or_negative_balance_alert(client):
    tr_upload(client, [DEPOSIT(amount="100.000000"), tr("2026-01-02", "TRADING", "BUY", **WORLD, shares="1", price="99", amount="-99.00")])
    kinds = alert_types(client)
    assert "low_balance" not in kinds and "negative_balance" not in kinds and "balance_gap" not in kinds


def test_staking_reward_adds_shares_without_moving_cash(client):
    eth = dict(asset="CRYPTO", name="Ethereum", symbol="XF000ETH0019")
    tr_upload(client, [DEPOSIT(), tr("2026-01-19", "DELIVERY", "FREE_RECEIPT", **eth, shares="0.5", price="2000", description="Staking")])
    data = client.get("/api/investments").json()
    position = data["positions"][0]
    assert position["shares"] == 0.5 and position["reward_shares"] == 0.5 and position["cost"] == 100000
    assert data["cash"]["balance"] == 100000
    assert client.get("/api/dashboard").json()["totals"]["income"] == 100000


def test_migration_pair_keeps_original_cost_and_realizes_nothing(client):
    stock = dict(asset="STOCK", name="Santander", symbol="ES0113900J37")
    tr_upload(client, [
        DEPOSIT(),
        tr("2026-01-02", "TRADING", "BUY", **stock, shares="10", price="5", amount="-50.00"),
        tr("2026-06-01", "DELIVERY", "MIGRATION", **stock, shares="-10", price="12"),
        tr("2026-06-01", "DELIVERY", "MIGRATION", **stock, shares="10", price="12"),
    ])
    data = client.get("/api/investments").json()
    assert data["positions"][0]["cost"] == 5000 and data["positions"][0]["shares"] == 10
    assert data["summary"]["realized"] == 0 and data["warnings"] == []


def test_cancelled_buy_reverses_position_without_gain(client):
    tr_upload(client, [
        DEPOSIT(),
        tr("2026-01-02", "TRADING", "BUY", **WORLD, shares="1", price="10", amount="-10.00"),
        tr("2026-01-02", "TRADING", "BUY", **WORLD, shares="2", price="20", amount="-40.00"),
        tr("2026-01-03", "TRADING", "BUY_CANCELLED", **WORLD, shares="-2", price="20", amount="40.00"),
    ])
    data = client.get("/api/investments").json()
    assert data["positions"][0]["shares"] == 1 and data["positions"][0]["cost"] == 1000 and data["summary"]["realized"] == 0


def test_oversold_and_corporate_actions_warn_instead_of_guessing(client):
    tr_upload(client, [
        DEPOSIT(),
        tr("2026-01-02", "TRADING", "SELL", **WORLD, shares="-1", price="10", amount="10.00"),
        tr("2026-01-03", "CORPORATE_ACTION", "LIQUIDATION_PROCEEDS", asset="FUND", name="Fondo Cerrado", symbol="IE00EXAMPLE99", shares="2", price="1", amount="2.00"),
    ])
    warnings = " ".join(client.get("/api/investments").json()["warnings"])
    assert "más títulos" in warnings and "corporativas" in warnings
    preview = upload(client, tr_csv([tr("2026-01-03", "CORPORATE_ACTION", "RARE_THING", asset="FUND", name="X", symbol="IE0", amount="2.00")]),
                     name="e.csv", path="/api/import/preview").json()["preview"]
    assert any("RARE_THING" in w for w in preview["warnings"])


def test_manual_price_values_position_and_can_be_removed(client):
    tr_upload(client, fifo_rows())
    client.put("/api/investments/prices/IE00B4L5Y983", json={"price_eur": 40, "as_of": "2026-11-01"})
    position = client.get("/api/investments").json()["positions"][0]
    assert position["value"] == 20000 and position["unrealized"] == 10000 and position["price_source"] == "manual"
    client.delete("/api/investments/prices/IE00B4L5Y983")
    assert client.get("/api/investments").json()["positions"][0]["value"] == 15000
    assert client.put("/api/investments/prices/NOEXISTE", json={"price_eur": 1}).status_code == 404


def test_investments_follow_account_selection_and_import_undo(client):
    bbva = upload(client, bbva_xlsx([row("2026-01-02", "MERCADONA", -10.0, 90.0)])).json()["account_id"]
    done = tr_upload(client, fifo_rows()).json()
    assert client.get("/api/investments", params={"accounts": bbva}).json()["empty"] is True
    assert client.get("/api/investments", params={"accounts": done["account_id"]}).json()["empty"] is False
    assert client.get("/api/dashboard").json()["investments_value"] == 15000
    assert client.get("/api/dashboard", params={"accounts": bbva}).json()["investments_value"] is None
    client.delete(f"/api/imports/{done['import_id']}")
    assert client.get("/api/investments").json()["empty"] is True


def test_bank_transfer_to_trade_republic_is_proposed_as_own_transfer(client):
    upload(client, bbva_xlsx([row("2026-01-01", "Transferencia a favor de Trade Republic Bank", -1000.0, 500.0)]), account_name="BBVA")
    tr_upload(client, [DEPOSIT()])
    pairs = client.get("/api/transfers").json()["pending"]
    assert [p["amount_cents"] for p in pairs] == [100000]
    upload(client, bbva_xlsx([row("2026-05-01", "Transferencia recibida de Trade Republic", 200.0, 700.0)]), account_name="BBVA")
    tr_upload(client, [tr("2026-05-01", "CASH", "TRANSFER_INSTANT_OUTBOUND", name="Yo", amount="-200.000000", counterparty="Yo")])
    assert {p["amount_cents"] for p in client.get("/api/transfers").json()["pending"]} == {100000, 20000}


def test_withholdings_and_order_fees_are_not_recurring_charges(client):
    rows = [DEPOSIT()]
    for month in range(1, 8):
        rows.append(tr(f"2026-{month:02d}-02", "TRADING", "BUY", **WORLD, shares="1", price="10", amount="-10.00", fee="-1.00"))
        rows.append(tr(f"2026-{month:02d}-03", "CASH", "INTEREST_PAYMENT", amount="2.000000", tax="-0.380000"))
        rows.append(tr(f"2026-{month:02d}-08", "CASH", "TRANSFER_DIRECT_DEBIT_INBOUND", amount="-12.990000", description="Sepa Direct Debit transfer to SPOTIFY AB"))
    tr_upload(client, rows)
    assert [r["merchant"] for r in client.get("/api/analysis").json()["recurring"]["out"]] == ["Spotify"]


def test_xirr_on_known_flows():
    from app.investments import xirr

    assert xirr([("2025-01-01", -100.0), ("2026-01-01", 110.0)]) == pytest.approx(0.1, abs=0.001)
    assert xirr([("2025-01-01", -100.0)]) is None
    assert xirr([("2025-01-01", -100.0), ("2025-01-10", 110.0)]) is None  # demasiado corto para anualizar


def test_trade_republic_sample_is_consistent(client, tmp_path):
    sys.path.insert(0, str(ROOT / "samples"))
    import generar_ejemplo_traderepublic

    path = generar_ejemplo_traderepublic.write(tmp_path / "tr.csv")
    preview = upload(client, path.read_bytes(), name="tr.csv", path="/api/import/preview").json()["preview"]
    assert preview["bank"] == "Trade Republic" and preview["positions"] == 4
    upload(client, path.read_bytes(), name="tr.csv")
    data = client.get("/api/investments").json()
    assert data["cash"]["complete"] is True and data["cash"]["balance"] > 0
    assert data["summary"]["annual_return"] is not None and data["summary"]["realized"] > 0
    assert {a["id"] for a in data["allocation"]} == {"FUND", "STOCK", "CRYPTO"}
    # Lo que entra en la cartera más el efectivo debe cuadrar con el dinero real de las filas del CSV.
    import csv as _csv

    rows = list(_csv.DictReader(io.StringIO(path.read_text())))
    expected = sum(Decimal(r["amount"] or 0) + Decimal(r["fee"] or 0) + Decimal(r["tax"] or 0) for r in rows)
    assert data["cash"]["balance"] == pytest.approx(int(expected * 100), abs=len(rows))


def test_upgrade_adds_new_columns_to_an_older_database(client, tmp_path):
    upload(client, bbva_xlsx([row("2026-01-02", "MERCADONA", -10.0, 90.0)]))
    conn = connect(tmp_path / "finanzas.db")
    for column in ("occurred_at", "fixed_category", "fallback_category"):
        conn.execute(f"ALTER TABLE transactions DROP COLUMN {column}")
    conn.execute("ALTER TABLE accounts DROP COLUMN balance_computed")
    conn.commit()
    conn.close()
    import app.main as main

    with TestClient(main.app) as fresh:
        assert fresh.get("/api/meta").json()["count"] == 1
        assert tr_upload(fresh, fifo_rows()).status_code == 200
        assert fresh.get("/api/investments").json()["positions"][0]["shares"] == 5


# ---- actualización de una base de datos existente


def test_upgrade_moves_existing_bizum_income_to_its_new_category(client, tmp_path):
    rows = [row("2026-01-02", "Bizum de Laura", 15.0, 100.0), row("2026-01-03", "Bizum de Ana", 20.0, 120.0)]
    upload(client, bbva_xlsx(rows))
    conn = connect(tmp_path / "finanzas.db")
    # Estado de la versión anterior: Bizum dentro de transferencias; uno corregido a mano.
    conn.execute("UPDATE categories SET name='Transferencias y Bizum recibidos' WHERE id='transfer_in'")
    conn.execute("UPDATE rules SET category_id='transfer_in' WHERE pattern='BIZUM' AND sign='in'")
    conn.execute("UPDATE transactions SET category_id='transfer_in'")
    conn.execute("UPDATE transactions SET category_id='nomina', category_source='user', rule_id=NULL WHERE concept='Bizum de Ana'")
    conn.commit()
    conn.close()
    import app.main as main

    with TestClient(main.app):  # un nuevo arranque aplica la actualización
        pass
    conn = connect(tmp_path / "finanzas.db")
    categories = dict(conn.execute("SELECT concept, category_id FROM transactions").fetchall())
    assert categories == {"Bizum de Laura": "bizum_in", "Bizum de Ana": "nomina"}
    assert conn.execute("SELECT name FROM categories WHERE id='transfer_in'").fetchone()[0] == "Transferencias recibidas"
    conn.close()


def test_upgrade_keeps_disabled_system_rules_and_user_rules(client, tmp_path):
    rules = client.get("/api/rules").json()["items"]
    target = next(r for r in rules if r["pattern"] == "MERCADONA")
    client.patch(f"/api/rules/{target['id']}", json={"enabled": False})
    client.post("/api/rules", json={"pattern": "tienda pepe", "category_id": "ocio"})
    import app.main as main

    with TestClient(main.app):
        pass
    after = {r["pattern"]: r for r in client.get("/api/rules").json()["items"]}
    assert after["MERCADONA"]["enabled"] == 0 and after["TIENDA PEPE"]["category_name"] == "Ocio y cultura"


def test_income_breakdown_separates_salary_bizum_and_transfers(client):
    rows = [row("2026-01-02", "Nómina ACME", 2000.0), row("2026-01-03", "Bizum de Laura", 50.0),
            row("2026-01-04", "Transferencia de Juan", 150.0)]
    upload(client, bbva_xlsx(rows))
    income = {c["id"]: c["amount"] for c in client.get("/api/dashboard").json()["income_categories"]}
    assert income == {"nomina": 200000, "bizum_in": 5000, "transfer_in": 15000}


# ---- clasificación


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    yield c
    c.close()


@pytest.mark.parametrize("concept,amount,category,merchant", [
    ("Pago con tarjeta en MERCADONA 1234 VALENCIA", -30, "supermercado", "Mercadona"),
    ("Recibo IBERDROLA CLIENTES SAU", -60, "recibos", "Iberdrola"),
    ("MAPFRE ESPANA SEGURO COCHE", -400, "seguros", "Mapfre"),
    ("NETFLIX.COM", -13, "suscripciones", "Netflix"),
    ("MOVISTAR PLUS", -30, "suscripciones", "Movistar Plus"),
    ("Recibo MOVISTAR", -30, "recibos", "Movistar"),
    ("UBER EATS MADRID", -20, "restaurantes", "Uber Eats"),
    ("UBER TRIP", -9, "transporte", "Uber"),
    ("ZARA HOME CENTRO", -40, "hogar", "Zara Home"),
    ("Abono de nómina ACME SL", 2000, "nomina", None),
    ("Comisión de mantenimiento", -15, "comisiones", None),
    ("Traspaso a cuenta ahorro", -250, "internos", None),
    ("Reintegro cajero", -50, "efectivo", None),
    ("Bizum de Laura", 10, "bizum_in", None),
    ("Transferencia recibida de Juan", 10, "transfer_in", None),
    ("Pago Movil En LIDL SUPERMERCADOS, MADRID, Tarj. :*123456", -20, "supermercado", "Lidl"),
    ("Transaccion Contactless En BAR MANOLO, MADRID, Tarj. :*123456", -5, "restaurantes", "Bar Manolo"),
    ("Compra Internet En AMAZON EU SARL, LUXEMBURGO, Tarj. :*123456", -30, "compras", "Amazon"),
    ("Pago Movil En TIENDA PEPE, BILBAO, Tarj. :*123456", -20, "sin_clasificar", "Tienda Pepe"),
    ("Bizum De Marta Ruiz Concepto Cena", 15, "bizum_in", "Marta Ruiz"),
    ("Bizum A Favor De Carlos Gil Concepto Regalo", -15, "transfer_out", "Carlos Gil"),
    ("Transferencia De Juan Garcia, Concepto Alquiler.", 500, "transfer_in", "Juan Garcia"),
    ("Recibo Digi Spain Telecom Sl Nº Recibo 0049 55 Ref. Mandato 11", -20, "recibos", "Digi"),
    ("Disposicion En Cajero SANTANDER, MADRID, Tarj. :*123456", -50, "efectivo", None),
    ("Bizum a favor de Laura", -10, "transfer_out", None),
    ("Recibo PROVEEDOR RARO", -10, "recibos", None),
    ("TIENDA DESCONOCIDA", -10, "sin_clasificar", None),
    ("INGRESO DESCONOCIDO", 10, "transfer_in", None),
    ("BARCELONA COSAS SL", -10, "sin_clasificar", None),
    ("Compra Mesa de pino", -10, "sin_clasificar", None),
])
def test_classification(conn, concept, amount, category, merchant):
    result = Classifier(conn).classify(concept, "", "", amount * 100)
    assert result.category_id == category
    if merchant:
        assert result.merchant == merchant


def test_clean_merchant_strips_operation_and_noise():
    assert clean_merchant("PAGO CON TARJETA EN CAFE LUNA 12345 *9876") == "Cafe Luna"
    assert clean_merchant("Bizum a favor de Laura Martinez") == "Laura Martinez"
    assert normalize("Nº3 CAFÉ-Bar") == "NO3 CAFE BAR"


# ---- importación por API


def month_rows():
    balance = 1000.0
    rows = []
    for month in range(1, 13):
        for concept, amount, day in [("Nómina ACME", 2000.0, 1), ("Recibo alquiler SOL", -800.0, 2), ("MERCADONA", -100.0, 3)]:
            balance += amount
            rows.append(row(f"2025-{month:02d}-{day:02d}", concept, amount, balance))
    return rows


def test_import_is_idempotent_and_overlapping_files_merge(client):
    rows = month_rows()
    data = bbva_xlsx(list(reversed(rows)))
    preview = upload(client, data, path="/api/import/preview").json()
    assert preview["status"] == "ok" and preview["preview"]["new"] == 36

    first = upload(client, data).json()
    assert first["new"] == 36 and first["skipped"] == 0

    again = upload(client, data, path="/api/import/preview").json()
    assert again["preview"]["new"] == 0 and again["preview"]["existing"] == 36

    second = upload(client, data).json()
    assert second["new"] == 0 and second["skipped"] == 36
    assert client.get("/api/meta").json()["count"] == 36
    assert len(client.get("/api/meta").json()["accounts"]) == 1


def test_identical_double_charge_survives_import_as_two_rows(client):
    rows = [row("2026-03-02", "TIENDA X", -50.0, 950.0), row("2026-03-02", "TIENDA X", -50.0, 900.0)]
    assert upload(client, bbva_xlsx(rows)).json()["new"] == 2
    assert upload(client, bbva_xlsx(rows)).json()["new"] == 0
    # Sin saldo: el número de aparición sigue distinguiéndolos.
    plain = [row("2026-03-03", "TIENDA Y", -50.0), row("2026-03-03", "TIENDA Y", -50.0)]
    assert upload(client, bbva_xlsx(plain)).json()["new"] == 2


def test_unknown_format_asks_for_mapping_then_imports(client):
    csv_data = "Dia;Texto;Valor\n01/02/2026;Compra MERCADONA;-10,00\n02/02/2026;Otra cosa;5,00\n".encode()
    answer = upload(client, csv_data, name="otro.csv", path="/api/import/preview").json()
    assert answer["status"] == "needs_mapping" and answer["raw"]["rows"][0] == ["Dia", "Texto", "Valor"]
    mapping = json.dumps({"sheet": 0, "header_row": 0, "date": 0, "concept": 1, "amount": 2})
    done = upload(client, csv_data, name="otro.csv", mapping=mapping, account_name="Otra").json()
    assert done["new"] == 2
    items = client.get("/api/transactions").json()["items"]
    assert {i["amount_cents"] for i in items} == {-1000, 500}


def test_format_error_returns_readable_422(client):
    response = upload(client, b"%PDF-1.4", name="x.pdf", path="/api/import/preview")
    assert response.status_code == 422 and "PDF" in response.json()["detail"]


def test_delete_import_removes_movements_and_empty_account(client):
    upload(client, bbva_xlsx(month_rows()))
    import_id = client.get("/api/imports").json()["items"][0]["id"]
    assert client.delete(f"/api/imports/{import_id}").json()["removed"] == 36
    meta = client.get("/api/meta").json()
    assert meta["count"] == 0 and meta["accounts"] == []


# ---- categorías y reglas


def test_manual_category_survives_reclassify_and_rule_applies_to_others(client):
    rows = [row("2026-01-02", "TIENDA PEPE", -20.0, 980.0), row("2026-02-02", "TIENDA PEPE", -25.0, 955.0)]
    upload(client, bbva_xlsx(rows))
    items = client.get("/api/transactions", params={"sort": "date", "direction": "asc"}).json()["items"]
    assert items[0]["category_id"] == "sin_clasificar"
    answer = client.patch(f"/api/transactions/{items[0]['id']}", json={"category_id": "ocio", "create_rule": True}).json()
    assert answer["affected"] == 2
    categories = {i["category_id"] for i in client.get("/api/transactions").json()["items"]}
    assert categories == {"ocio"}

    client.patch(f"/api/transactions/{items[0]['id']}", json={"category_id": "hogar"})
    client.post("/api/rules", json={"pattern": "tienda pepe", "category_id": "compras"})
    after = {i["id"]: i["category_id"] for i in client.get("/api/transactions").json()["items"]}
    assert after[items[0]["id"]] == "hogar" and after[items[1]["id"]] == "compras"


def test_system_rule_cannot_be_deleted_but_can_be_disabled(client):
    rules = client.get("/api/rules").json()["items"]
    target = next(r for r in rules if r["source"] == "system")
    assert client.delete(f"/api/rules/{target['id']}").status_code == 422
    assert client.patch(f"/api/rules/{target['id']}", json={"enabled": False}).status_code == 200


# ---- alertas


def alert_types(client, **params):
    return [a["type"] for a in client.get("/api/alerts", params={"status": "all", **params}).json()["items"]]


def test_fee_alert_and_status_survives_refresh(client):
    upload(client, bbva_xlsx([row("2026-01-20", "Comisión de mantenimiento", -15.0, 985.0)]))
    alerts = client.get("/api/alerts").json()["items"]
    fee = next(a for a in alerts if a["type"] == "fee")
    assert fee["amount_cents"] == -1500
    client.patch(f"/api/alerts/{fee['id']}", json={"status": "dismissed"})
    upload(client, bbva_xlsx([row("2026-02-20", "MERCADONA", -15.0, 970.0)]))
    assert all(a["id"] != fee["id"] for a in client.get("/api/alerts").json()["items"])
    assert any(a["id"] == fee["id"] for a in client.get("/api/alerts", params={"status": "dismissed"}).json()["items"])


def test_duplicate_alert_and_exclusions(client):
    rows = [
        row("2026-03-02", "TIENDA X", -50.0), row("2026-03-02", "TIENDA X", -50.0),      # duplicado
        row("2026-03-05", "TIENDA REEMB", -60.0), row("2026-03-06", "TIENDA REEMB", -60.0),
        row("2026-03-10", "TIENDA REEMB", 60.0),                                           # devuelto
        row("2026-03-07", "CAFE Z", -2.0), row("2026-03-07", "CAFE Z", -2.0),              # bajo el mínimo
    ]
    upload(client, bbva_xlsx(rows))
    items = client.get("/api/alerts", params={"type": "duplicate"}).json()["items"]
    assert len(items) == 1 and "Tienda X" in items[0]["title"] and items[0]["severity"] == "critical"


def test_habitual_same_amount_is_not_a_duplicate(client):
    rows = [row((date(2026, 1, 5) + timedelta(days=i)).isoformat(), "GIMNASIO CASA", -7.0) for i in range(8)]
    upload(client, bbva_xlsx(rows))
    assert "duplicate" not in alert_types(client)


def test_outlier_uses_only_past_history(client):
    rows = [row(f"2026-{m:02d}-10", "FRUTAS PEPE", -30.0 - m) for m in range(1, 9)]
    rows.append(row("2026-09-10", "FRUTAS PEPE", -400.0))
    rows.append(row("2026-09-20", "FRUTAS PEPE", -38.0))
    upload(client, bbva_xlsx(rows))
    items = client.get("/api/alerts", params={"type": "outlier"}).json()["items"]
    assert len(items) == 1 and items[0]["amount_cents"] == -40000


def test_price_increase_missing_and_new_merchant(client):
    rows = []
    for month in range(1, 10):
        rows.append(row(f"2026-{month:02d}-07", "NETFLIX.COM", -(18.99 if month == 9 else 15.99)))
    rows.append(row("2026-10-30", "NETFLIX.COM", -18.99))
    for month in range(1, 7):
        rows.append(row(f"2026-{month:02d}-05", "Recibo GIMNASIO ANTIGUO", -30.0))
    rows.append(row("2026-10-29", "COMPRA CARISIMA SL", -900.0))
    upload(client, bbva_xlsx(rows))
    all_alerts = client.get("/api/alerts").json()["items"]
    assert any(a["type"] == "price_increase" and "Netflix" in a["title"] for a in all_alerts)
    assert any(a["type"] == "new_merchant" and a["amount_cents"] == -90000 for a in all_alerts)


def test_holiday_booking_is_not_an_annual_charge_but_insurance_is(client):
    rows = [row("2025-08-04", "BOOKING.COM", -400.0), row("2026-08-04", "BOOKING.COM", -420.0),
            row("2025-03-15", "MAPFRE", -400.0), row("2026-03-15", "MAPFRE", -410.0),
            row("2026-08-05", "MERCADONA", -1.0)]
    upload(client, bbva_xlsx(rows))
    fixed = {r["merchant"] for r in client.get("/api/analysis").json()["recurring"]["out"]}
    assert "Mapfre" in fixed and "Booking" not in fixed


def test_variable_utility_bill_is_not_a_price_increase(client):
    amounts = [40.0, 41.5, 39.0, 40.7, 48.0, 44.0, 47.0]
    upload(client, bbva_xlsx([row(f"2026-{i + 1:02d}-05", "IBERDROLA", -a) for i, a in enumerate(amounts)]))
    assert "price_increase" not in alert_types(client)


def test_budget_exactly_reached_is_not_exceeded(client):
    upload(client, bbva_xlsx([row("2026-01-02", "Recibo alquiler SOL", -780.0)]))
    client.put("/api/budgets/vivienda", json={"monthly_eur": 780})
    items = client.get("/api/alerts", params={"type": "budget"}).json()["items"]
    assert [a["severity"] for a in items] == ["warning"]


def test_forecast_history_uses_real_month_end_balances(client):
    upload(client, bbva_xlsx(month_rows()))
    history = client.get("/api/analysis").json()["forecast"]["history"]
    assert history[-1] == {"month": "2025-12", "balance": 1420000}
    assert history[-2] == {"month": "2025-11", "balance": 1310000}


def test_yoy_is_independent_of_selected_period(client):
    rows = [row(f"{y}-{m:02d}-10", "MERCADONA", -(100.0 if y == 2024 else 150.0)) for y in (2024, 2025) for m in range(1, 13)]
    rows.append(row("2025-12-31", "Nómina ACME", 1000.0))
    rows.insert(0, row("2024-01-01", "Nómina ACME", 1000.0))
    upload(client, bbva_xlsx(rows))
    stats = client.get("/api/analysis", params={"from": "2025-11-01"}).json()["category_stats"]
    assert next(c for c in stats if c["id"] == "supermercado")["yoy"] == pytest.approx(0.5)


def test_balance_alerts(client):
    rows = [row("2026-01-01", "A", -10.0, 500.0), row("2026-01-02", "B", -10.0, 490.0),
            row("2026-01-03", "C", -600.0, -110.0), row("2026-01-04", "D", 150.0, 40.0)]
    upload(client, bbva_xlsx(rows))
    kinds = alert_types(client)
    assert "negative_balance" in kinds and "low_balance" in kinds
    assert "balance_gap" not in kinds


def test_balance_gap_when_movements_are_missing(client):
    rows = []
    balance = 1000.0
    for day in range(1, 26):
        balance -= 10.0
        if day == 15:
            balance -= 77.0  # falta un movimiento en el fichero
        rows.append(row(f"2026-01-{day:02d}", f"Compra {day}", -10.0, balance))
    upload(client, bbva_xlsx(rows))
    gaps = client.get("/api/alerts", params={"type": "balance_gap"}).json()["items"]
    assert len(gaps) == 1 and gaps[0]["amount_cents"] == -7700


def test_budget_alert(client):
    upload(client, bbva_xlsx([row("2026-01-10", "MERCADONA", -95.0), row("2026-01-11", "BAR MANOLO", -60.0)]))
    client.put("/api/budgets/supermercado", json={"monthly_eur": 100})
    client.put("/api/budgets/restaurantes", json={"monthly_eur": 50})
    severities = {a["title"]: a["severity"] for a in client.get("/api/alerts", params={"type": "budget"}).json()["items"]}
    assert severities["Cerca del presupuesto en Supermercados"] == "warning"
    assert severities["Presupuesto superado en Restaurantes y bares"] == "critical"


# ---- análisis


def test_totals_exclude_internal_and_refund_reduces_expense(client):
    rows = [
        row("2026-01-01", "Nómina ACME", 2000.0), row("2026-01-02", "Traspaso a ahorro", -500.0),
        row("2026-01-03", "AMAZON", -100.0), row("2026-01-04", "AMAZON", 40.0),
    ]
    upload(client, bbva_xlsx(rows))
    totals = client.get("/api/dashboard").json()["totals"]
    assert totals["income"] == 200000 and totals["expense"] == 6000 and totals["net"] == 194000


def test_analysis_numbers_on_known_data(client):
    upload(client, bbva_xlsx(month_rows()))
    a = client.get("/api/analysis").json()
    ind = a["indicators"]
    assert a["months_used"] == 11  # diciembre termina el día 3: mes incompleto
    assert ind["avg_income"] == 200000 and ind["avg_expense"] == 90000
    assert ind["savings_rate"] == pytest.approx(0.55)
    assert ind["balance"] == 100000 + 12 * 110000
    assert ind["runway_months"] == pytest.approx(1420000 / 90000)
    rec = {r["merchant"]: r for r in a["recurring"]["out"]}
    assert rec["Mercadona"]["frequency"] == "Mensual" and rec["Mercadona"]["annual_cents"] == 120000
    assert a["rule_50_30_20"]["needs"] == 90000
    f = a["forecast"]
    assert f["avg_net"] == 110000 and f["points"][0]["expected"] == 1420000 + 110000 and f["sigma"] == 0


def test_filters_by_period_and_account(client):
    upload(client, bbva_xlsx(month_rows()))
    d = client.get("/api/dashboard", params={"from": "2025-03-01", "to": "2025-03-31"}).json()
    assert d["totals"]["income"] == 200000 and d["previous"]["income"] == 200000
    assert len(d["monthly"]) == 1
    t = client.get("/api/transactions", params={"q": "mercadona", "from": "2025-03-01", "to": "2025-04-30"}).json()
    assert t["total"] == 2 and t["money_out"] == -20000


def test_csv_export_neutralises_formulas(client):
    upload(client, bbva_xlsx([row("2026-01-01", "@SUM(1)", -5.0)]))
    body = client.get("/api/transactions.csv").text
    assert "'@SUM(1)" in body


def test_previous_range():
    assert analytics.previous_range("2026-03-01", "2026-03-31") == ("2026-02-01", "2026-02-28")
    assert analytics.previous_range("2026-10-10", "2026-10-19") == ("2026-09-30", "2026-10-09")


def test_complete_months_excludes_partial_edges():
    assert analytics.complete_months("2026-01-20", "2026-04-10") == ["2026-02", "2026-03"]
    assert analytics.complete_months("2026-01-02", "2026-03-31") == ["2026-01", "2026-02", "2026-03"]


# ---- seguridad


def test_foreign_host_and_origin_blocked(client):
    assert client.get("/api/meta", headers={"host": "evil.example"}).status_code == 400
    assert client.post("/api/alerts/bulk", json={"status": "reviewed"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/alerts/bulk", json={"status": "reviewed"}, headers={"origin": "http://testserver"}).status_code == 200


def test_santander_sample_imports_and_is_detected(client, tmp_path):
    sys.path.insert(0, str(ROOT / "samples"))
    import generar_ejemplo_santander

    path = generar_ejemplo_santander.write(tmp_path / "santander.xlsx")
    preview = upload(client, path.read_bytes(), path="/api/import/preview").json()["preview"]
    assert preview["bank"] == "Santander" and preview["total"] > 100 and preview["classified"] > preview["total"] * 0.9
    assert upload(client, path.read_bytes()).json()["new"] == preview["total"]
    income = {c["id"] for c in client.get("/api/dashboard").json()["income_categories"]}
    assert {"bizum_in", "transfer_in", "intereses"} <= income


def test_both_samples_propose_the_planted_transfers(client, tmp_path):
    sys.path.insert(0, str(ROOT / "samples"))
    import generar_ejemplo
    import generar_ejemplo_santander

    upload(client, generar_ejemplo.write(tmp_path / "b.xlsx").read_bytes())
    upload(client, generar_ejemplo_santander.write(tmp_path / "s.xlsx").read_bytes())
    amounts = {p["amount_cents"] for p in transfer_pairs(client)}
    assert {40000, 25000, 3500} <= amounts
    assert [a["iban"][-4:] for a in client.get("/api/meta").json()["accounts"]] == ["1111", "2222"]


def test_sample_file_generates_alert_types(client, tmp_path):
    sys.path.insert(0, str(ROOT / "samples"))
    import generar_ejemplo

    path = generar_ejemplo.write(tmp_path / "x.xlsx")
    done = upload(client, path.read_bytes()).json()
    assert done["new"] > 1000
    kinds = set(alert_types(client))
    assert {"fee", "duplicate", "price_increase", "outlier", "negative_balance"} <= kinds
    assert sqlite3.sqlite_version
    assert alerts_mod.ALERT_TYPES.keys() >= kinds
