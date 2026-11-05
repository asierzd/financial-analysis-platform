#!/usr/bin/env python3
"""Genera samples/santander_ejemplo.xlsx: 14 meses de movimientos FICTICIOS para probar el importador de Santander.

La forma del fichero y el estilo de los conceptos son una aproximación, no una copia de un extracto real.
Uso: python samples/generar_ejemplo_santander.py [salida.xlsx]
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

START = date(2025, 8, 1)
END = date(2026, 10, 20)
TAIL = ", MADRID, Tarj. :*123456"


def build_rows():
    rng = random.Random(7)
    events = []

    def add(day, concept, amount):
        events.append((day, concept, round(amount, 2)))

    day = START
    while day <= END:
        if day.day == 2:
            add(day, "Recibo Mutua Madrileña Automovilista Nº Recibo 0049 1234 Ref. Mandato 998877", -46.5)
        if day.day == 6:
            add(day, "Recibo Digi Spain Telecom Sl Nº Recibo 0049 5566 Ref. Mandato 112233", -20.0)
        if day.day == 15:
            add(day, "Transferencia De Alquileres Garcia Sl, Concepto Arrendamiento Local.", 650.0)
        if day.day == 25 and day.month % 3 == 1:
            add(day, "Abono Intereses Cuenta", round(rng.uniform(3, 9), 2))
        if day.day == 20 and day.month % 3 == 0:
            add(day, "Comision Mantenimiento Cuenta", -9.0)
        for concept, chance, low, high in [
            ("Pago Movil En LIDL SUPERMERCADOS", 0.08, 12, 60),
            ("Transaccion Contactless En CAFETERIA LA PLAZA", 0.12, 3, 15),
            ("Compra Internet En AMAZON EU SARL", 0.05, 9, 70),
            ("Pago Movil En FERRETERIA RAMIREZ", 0.02, 6, 45),
        ]:
            if rng.random() < chance:
                add(day, concept + TAIL, -round(rng.uniform(low, high), 2))
        if rng.random() < 0.06:
            name = rng.choice(["Marta Ruiz", "Carlos Gil"])
            add(day, f"Bizum De {name} Concepto Cena", round(rng.uniform(8, 45), 2))
        if rng.random() < 0.02:
            add(day, "Bizum A Favor De Carlos Gil Concepto Regalo", -round(rng.uniform(10, 30), 2))
        if rng.random() < 0.015:
            add(day, "Disposicion En Cajero SANTANDER" + TAIL, -rng.choice([20, 50]))
        day += timedelta(days=1)

    # Entradas desde la cuenta ficticia de BBVA (ver generar_ejemplo.py).
    add(date(2026, 3, 11), "Transferencia De Titular Ejemplo, Concepto Ahorro.", 400.0)
    add(date(2026, 7, 6), "Transferencia De Titular Ejemplo, Concepto Ahorro.", 250.0)
    add(date(2026, 9, 22), "Bizum De Marta Ruiz Concepto Cena", 35.0)

    events.sort(key=lambda e: e[0])
    balance = 1500.0
    rows = []
    for day, concept, amount in events:
        balance = round(balance + amount, 2)
        rows.append((day, concept, amount, balance))
    return rows


def write(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Movimientos"
    ws.append(["Banco Santander (datos ficticios)"])
    ws.append(["Cuenta", "ES00 0049 0000 **** **** 2222"])
    ws.append([])
    ws.append(["Fecha operación", "Fecha valor", "Concepto", "Importe", "Saldo"])
    for day, concept, amount, balance in reversed(build_rows()):
        text = day.strftime("%d/%m/%Y")
        ws.append([text, text, concept, amount, balance])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "santander_ejemplo.xlsx"
    print(write(target))
