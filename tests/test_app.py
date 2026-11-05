"""Pruebas del núcleo: lectura de formatos, clasificación, deduplicación, alertas, análisis y seguridad."""
import importlib
import io
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
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
    assert client.get("/api/dashboard", params={"account": done["account_id"]}).json()["totals"]["count"] == 2
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
