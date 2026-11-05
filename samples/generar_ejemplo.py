#!/usr/bin/env python3
"""Genera samples/bbva_ejemplo.xlsx: 26 meses de movimientos FICTICIOS con la forma de un Excel de BBVA.

Incluye a propósito comisiones, un cargo duplicado, una subida de precio, un cargo raro y un descubierto,
para ver funcionar las alertas. Uso: python samples/generar_ejemplo.py [salida.xlsx]
"""
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

END = date(2026, 10, 28)
START = date(2024, 9, 1)
CARD = "Pago con tarjeta"


def build_rows():
    rng = random.Random(42)
    events = []

    def add(day, concept, amount, movement="", notes=""):
        events.append((day, concept, round(amount, 2), movement, notes))

    day = START
    while day <= END:
        if day.day == 28:
            bonus = 1650 if day.month in (6, 12) else 0
            add(day, "Abono de nómina ACME SOLUCIONES SL", 2480 + bonus + (day.year - 2024) * 60, "Nómina")
        if day.day == 1:
            add(day, "Recibo alquiler INMOBILIARIA SOL", -780, "Adeudo", "Alquiler vivienda")
            add(day, "Traspaso a cuenta ahorro", -250, "Traspaso")
        if day.day == 3:
            add(day, "Recibo IBERDROLA CLIENTES SAU", -round(rng.uniform(48, 92), 2), "Adeudo")
        if day.day == 5:
            add(day, "Recibo VODAFONE ESPANA SAU", -39.9, "Adeudo")
            add(day, "Recibo CANAL DE ISABEL II", -round(rng.uniform(21, 34), 2), "Adeudo") if day.month % 2 else None
        if day.day == 7:
            price = 15.99 if day < date(2026, 8, 1) else 18.99
            add(day, "NETFLIX.COM", -price, CARD)
            add(day, "SPOTIFY AB", -10.99, CARD)
        if day.day == 10:
            add(day, "BASIC-FIT SPAIN", -24.99, "Adeudo")
            add(day, "SANITAS SA DE SEGUROS", -58.4, "Adeudo", "Seguro de salud")
        if day.month == 3 and day.day == 15:
            add(day, "MAPFRE ESPANA SEGURO COCHE", -412.6, "Adeudo", "Póliza anual")
        if day.month == 6 and day.day == 20:
            add(day, "Recibo IBI AYUNTAMIENTO DE MADRID", -318.55, "Adeudo")
        if day.day == 20 and day.month % 3 == 0:
            add(day, "Comisión de mantenimiento cuenta", -15.0, "Comisión")
        if day.month == 1 and day.day == 12:
            add(day, "Cuota anual tarjeta de crédito", -36.0, "Comisión")

        for concept, chance, low, high in [
            ("MERCADONA MADRID", 0.32, 18, 86), ("LIDL SUPERMERCADOS", 0.12, 12, 55),
            ("CARREFOUR EXPRESS", 0.08, 6, 34), ("REPSOL ESTACION SERVICIO", 0.08, 38, 66),
            ("RESTAURANTE LA TASCA DEL SUR", 0.09, 18, 62), ("BAR MANOLO", 0.14, 3.5, 14),
            ("GLOVO", 0.05, 15, 32), ("AMAZON EU SARL", 0.1, 9, 90), ("ZARA ESPANA", 0.03, 25, 110),
            ("FARMACIA GARCIA", 0.05, 6, 28), ("CINESA", 0.03, 9, 26), ("RENFE VIAJEROS", 0.03, 12, 68),
            ("METRO DE MADRID", 0.1, 1.7, 1.7), ("TIENDA DEL BARRIO PACO", 0.05, 4, 22),
        ]:
            if rng.random() < chance:
                add(day, concept, -round(rng.uniform(low, high), 2), CARD)
        if rng.random() < 0.03:
            add(day, "Reintegro cajero BBVA", -rng.choice([20, 50, 100]), "Reintegro")
        if rng.random() < 0.05:
            add(day, f"Bizum de {rng.choice(['Laura M', 'Pablo R', 'Ana G'])}", round(rng.uniform(5, 40), 2), "Bizum")
        if rng.random() < 0.03:
            add(day, f"Bizum a favor de {rng.choice(['Laura M', 'Pablo R'])}", -round(rng.uniform(5, 40), 2), "Bizum")
        if day.month == 8 and day.day == 4:
            add(day, "RYANAIR", -round(rng.uniform(120, 240), 2), CARD)
            add(day, "BOOKING.COM", -round(rng.uniform(300, 640), 2), CARD)
        day += timedelta(days=1)

    # Casos para las alertas.
    add(date(2026, 9, 14), "PCCOMPONENTES ATTENTO", -189.0, CARD)
    add(date(2026, 9, 14), "PCCOMPONENTES ATTENTO", -189.0, CARD)
    add(date(2026, 10, 9), "MERCADONA MADRID", -640.75, CARD)
    add(date(2026, 10, 2), "SERVICIOS DIGITALES XYZ LTD", -349.0, CARD)
    add(date(2026, 4, 22), "Comisión por descubierto", -30.0, "Comisión")
    add(date(2026, 4, 20), "TALLER MECANICO HERMANOS RUIZ", -3900.0, CARD)
    add(date(2025, 11, 5), "Devolución AMAZON EU SARL", 34.5, "Devolución")
    add(date(2026, 2, 17), "Transferencia recibida de MARIA LOPEZ", 500.0, "Transferencia")
    # Salidas hacia la cuenta ficticia de Santander (ver generar_ejemplo_santander.py).
    add(date(2026, 3, 10), "Transferencia a favor de TITULAR EJEMPLO", -400.0, "Transferencia")
    add(date(2026, 7, 6), "Transferencia a favor de TITULAR EJEMPLO", -250.0, "Transferencia")
    # Coincide en importe con un Bizum que recibe la otra cuenta, pero no es un traspaso: se descartaría a mano.
    add(date(2026, 9, 21), "Bizum a favor de Pablo R", -35.0, "Bizum")

    # Una entrada de piso que deja la cuenta en descubierto durante unos días.
    events.sort(key=lambda e: e[0])
    before = 3200.0 + sum(e[2] for e in events if e[0] <= date(2026, 4, 21))
    add(date(2026, 4, 21), "Transferencia a NOTARIA LOPEZ entrada piso", -round(before + 85, 2), "Transferencia")

    events.sort(key=lambda e: e[0])
    balance = 3200.0
    rows = []
    for day, concept, amount, movement, notes in events:
        balance = round(balance + amount, 2)
        rows.append((day, concept, amount, balance, movement, notes))
    return rows


def write(path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Movimientos"
    ws.append([None, "Últimos movimientos"])
    ws.append([None, "BBVA · Cuenta Online (datos ficticios)"])
    ws.append([None, "ES00 0182 0000 0000 0000 1111"])
    ws.append([])
    ws.append([None, "F. Valor", "Fecha", "Concepto", "Movimiento", "Importe", "Divisa", "Disponible", "Divisa", "Observaciones"])
    for day, concept, amount, balance, movement, notes in reversed(build_rows()):
        stamp = datetime(day.year, day.month, day.day)
        ws.append([None, stamp, stamp, concept, movement, amount, "EUR", balance, "EUR", notes])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "bbva_ejemplo.xlsx"
    print(write(target))
