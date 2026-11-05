#!/usr/bin/env python3
"""Genera samples/traderepublic_ejemplo.csv: 20 meses FICTICIOS con la forma de la exportación de Trade Republic.

Incluye planes de inversión en ETF, acciones con dividendos en dólares, criptomonedas con recompensas de staking,
ventas con comisión, intereses y saveback con retención, tarjeta y adeudos. Uso:
python samples/generar_ejemplo_traderepublic.py [salida.csv]
"""
import csv
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HEADER = ["datetime", "date", "account_type", "category", "type", "asset_class", "name", "symbol", "shares", "price", "amount",
          "fee", "tax", "currency", "original_amount", "original_currency", "fx_rate", "description", "transaction_id",
          "counterparty_name", "counterparty_iban", "payment_reference", "mcc_code"]
START = date(2025, 2, 3)
END = date(2026, 10, 25)
ETF_WORLD = ("iShares Core MSCI World USD (Acc)", "IE00B4L5Y983", "FUND", 88.0)
ETF_EM = ("iShares Core MSCI EM IMI USD (Acc)", "IE00BKM4GZ66", "FUND", 31.0)
STOCK = ("Ejemplo Tecnologia Inc", "US0000000AA1", "STOCK", 140.0)
CRYPTO = ("Ethereum", "XF000ETH0019", "CRYPTO", 2300.0)
CARD = [("Mercadona", "5411", 12, 70), ("Bar La Esquina", "5812", 4, 25), ("Tienda Rara Sin Regla", "5999", 8, 60),
        ("Repsol", "5541", 35, 60), ("Cafe Sin Nombre", "5814", 3, 9)]


def build_rows():
    rng = random.Random(21)
    rows = []

    def row(day, category, kind, asset="", name="", symbol="", shares="", price="", amount="", fee="", tax="", description="",
            counterparty="", mcc="", original=("", "", "")):
        stamp = datetime(day.year, day.month, day.day, rng.randint(6, 21), rng.randint(0, 59), rng.randint(0, 59), tzinfo=timezone.utc)
        text = stamp.strftime("%Y-%m-%dT%H:%M:%S") + (".123Z" if category == "TRADING" else ".123456Z")
        rows.append([text, day.isoformat(), "DEFAULT", category, kind, asset, name, symbol, shares, price, amount, fee, tax, "EUR",
                     *original, description, str(uuid.UUID(int=rng.getrandbits(128), version=4)), counterparty, "", "", mcc])

    def price_of(base, day, drift=0.11, noise=0.015):
        years = (day - START).days / 365.25
        return round(base * (1 + drift) ** years * (1 + rng.uniform(-noise, noise)), 4)

    def buy(day, asset, euros, description, fee=""):
        name, symbol, klass, base = asset
        price = price_of(base, day, drift=0.3 if klass == "CRYPTO" else 0.11, noise=0.06 if klass == "CRYPTO" else 0.015)
        row(day, "TRADING", "BUY", klass, name, symbol, f"{euros / price:.10f}", f"{price:.10f}", f"{-euros:.2f}", fee,
            description=description)

    row(START, "CASH", "TRANSFER_INSTANT_INBOUND", name="Titular Ejemplo", amount="6000.000000", description="Instant transfer received",
        counterparty="Titular Ejemplo")
    row(START + timedelta(days=1), "CASH", "CARD_ORDERING_FEE", name="Trade Republic Card", amount="0.000000", fee="-5.00",
        description="Trade Republic Card")
    buy(START + timedelta(days=2), STOCK, 900.0, "Buy order", fee="-1.00")

    day = START
    while day <= END:
        if day.day == 1:
            row(day, "CASH", "TRANSFER_INSTANT_INBOUND", name="Titular Ejemplo", amount="600.000000",
                description="Instant transfer received", counterparty="Titular Ejemplo")
            cash = sum(float(r[10] or 0) + float(r[11] or 0) + float(r[12] or 0) for r in rows)
            interest = round(max(cash, 0) * 0.02 / 12, 2)
            if interest > 0:
                row(day, "CASH", "INTEREST_PAYMENT", amount=f"{interest:.6f}", tax=f"{-round(interest * 0.19, 2):.6f}",
                    description="Interest payment")
        if day.day == 2:
            buy(day, ETF_WORLD, 250.0, "Savings plan execution")
            buy(day, ETF_EM, 50.0, "Savings plan execution")
        if day.day == 3 and day.month % 3 == 0:
            buy(day, CRYPTO, 100.0, "Buy order", fee="-1.00")
        if day.day == 5:
            row(day, "CASH", "BENEFITS_SAVEBACK", amount="3.200000", tax="-0.610000", description="Your Saveback payment")
        if day.day == 8:
            row(day, "CASH", "TRANSFER_DIRECT_DEBIT_INBOUND", amount="-12.990000", description="Sepa Direct Debit transfer to SPOTIFY AB")
        if day.day == 15 and day.month in (3, 6, 9, 12):
            gross = round(0.12 * 7, 2)
            row(day, "CASH", "DIVIDEND", "STOCK", STOCK[0], STOCK[1], "7.0000000000", "", f"{gross:.6f}", "", f"{-round(gross * 0.15, 2):.6f}",
                description="Cash Dividend", original=(f"{gross * 1.1:.6f}", "USD", "1.100000"))
        if day.day == 20 and day.month in (2, 5, 8, 11):
            shares = round(rng.uniform(0.0003, 0.0009), 10)
            row(day, "DELIVERY", "FREE_RECEIPT", "CRYPTO", CRYPTO[0], CRYPTO[1], f"{shares:.10f}",
                f"{price_of(CRYPTO[3], day, drift=0.3):.10f}", description="Staking reward")
        if rng.random() < 0.18:
            name, mcc, low, high = rng.choice(CARD)
            row(day, "CASH", "CARD_TRANSACTION", name=name, amount=f"{-round(rng.uniform(low, high), 2):.6f}",
                description="Card payment", mcc=mcc)
        day += timedelta(days=1)

    # Una venta parcial: el coste debe salir por FIFO, de las compras más antiguas.
    sell_day = date(2026, 4, 14)
    price = price_of(ETF_WORLD[3], sell_day)
    shares = 6.0
    row(sell_day, "TRADING", "SELL", "FUND", ETF_WORLD[0], ETF_WORLD[1], f"{-shares:.10f}", f"{price:.10f}", f"{shares * price:.2f}", "-1.00",
        description="Sell order")
    row(date(2026, 1, 12), "CASH", "CARD_TRANSACTION", name="Hotel Ejemplo", amount="52.400000", description="Card refund", mcc="7011")
    row(date(2026, 5, 22), "CASH", "TRANSFER_INSTANT_OUTBOUND", name="Titular Ejemplo", amount="-800.000000",
        description="Instant transfer sent", counterparty="Titular Ejemplo")
    rows.sort(key=lambda r: r[0])
    return rows


def write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(HEADER)
        writer.writerows(build_rows())
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "traderepublic_ejemplo.csv"
    print(write(target))
