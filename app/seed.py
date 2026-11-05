"""Categorías, reglas de clasificación por defecto (comercios españoles) y ajustes."""
from __future__ import annotations

# (id, nombre, tipo, grupo). El grupo alimenta el análisis 50/30/20.
CATEGORIES = [
    ("nomina", "Nómina", "income", "income"),
    ("transfer_in", "Transferencias y Bizum recibidos", "income", "income"),
    ("intereses", "Intereses y dividendos", "income", "income"),
    ("devoluciones", "Devoluciones y reembolsos", "income", "income"),
    ("otros_ingresos", "Otros ingresos", "income", "income"),
    ("vivienda", "Hipoteca y alquiler", "expense", "needs"),
    ("recibos", "Recibos y suministros", "expense", "needs"),
    ("supermercado", "Supermercados", "expense", "needs"),
    ("seguros", "Seguros", "expense", "needs"),
    ("transporte", "Transporte y combustible", "expense", "needs"),
    ("salud", "Salud y farmacia", "expense", "needs"),
    ("educacion", "Educación", "expense", "needs"),
    ("impuestos", "Impuestos y tasas", "expense", "needs"),
    ("prestamos", "Préstamos y tarjeta de crédito", "expense", "needs"),
    ("comisiones", "Comisiones bancarias", "expense", "needs"),
    ("restaurantes", "Restaurantes y bares", "expense", "wants"),
    ("ocio", "Ocio y cultura", "expense", "wants"),
    ("suscripciones", "Suscripciones", "expense", "wants"),
    ("compras", "Compras y ropa", "expense", "wants"),
    ("hogar", "Hogar y bricolaje", "expense", "wants"),
    ("viajes", "Viajes", "expense", "wants"),
    ("deporte", "Deporte", "expense", "wants"),
    ("cuidado", "Cuidado personal", "expense", "wants"),
    ("mascotas", "Mascotas", "expense", "wants"),
    ("donaciones", "Regalos y donaciones", "expense", "wants"),
    ("efectivo", "Retiradas de efectivo", "expense", "wants"),
    ("transfer_out", "Transferencias y Bizum enviados", "expense", "wants"),
    ("ahorro", "Ahorro e inversión", "expense", "savings"),
    ("internos", "Traspasos entre mis cuentas", "transfer", "internal"),
    ("sin_clasificar", "Sin clasificar", "expense", "unknown"),
]

FALLBACK_INCOME = "otros_ingresos"
FALLBACK_EXPENSE = "sin_clasificar"

# Marcas: el patrón se busca como palabras completas en concepto + movimiento + observaciones.
# Una marca también fija el nombre del comercio, para unificar "MERCADONA 1234" y "MERCADONA VALENCIA".
BRANDS: dict[str, list] = {
    "supermercado": [
        "Mercadona", "Carrefour", "Lidl", "Aldi", "Alcampo", "Eroski", "Consum", "Ahorramas", "Hipercor",
        "Caprabo", "BonArea", "Gadis", "Froiz", "Condis", "Bon Preu", "Esclat", "Masymas", "Coviran",
        "Spar", "Alimerka", "Supersol", "Sanchez Romero", "Herbolario Navarro", "Veritas", "Ametller Origen",
        ("Supermercados Dia", "Dia"), ("Dia Retail", "Dia"), ("Dia SA", "Dia"), ("Distribuidora Internacional", "Dia"),
        ("Mas y Mas", "Masymas"), ("Ahorra Mas", "Ahorramas"), ("Carrefour Express", "Carrefour"),
        ("Getir", "Getir"), ("Supermercado", None), ("Fruteria", None), ("Carniceria", None), ("Pescaderia", None),
        ("Panaderia", None),
    ],
    "recibos": [
        "Iberdrola", "Endesa", "Naturgy", "Holaluz", "Repsol Luz", "TotalEnergies", "Octopus Energy",
        "Canal de Isabel II", ("Aguas de", None), ("Aigues de", None), "Agbar", "Emasesa", "Movistar", "Vodafone", "Orange",
        "Jazztel", "MasMovil", "Yoigo", "Digi", "Pepephone", "Lowi", "O2", "Simyo", "Finetwork", "Euskaltel",
        "Telefonica", "Gas Natural", "Comunidad de Propietarios", ("Comunidad Prop", None),
        ("Com Propietarios", None), ("Curenergia", "Curenergia"), ("Energia XXI", "Energía XXI"),
    ],
    "seguros": [
        "Mapfre", "Mutua Madrileña", "Linea Directa", "Allianz", "AXA", "Generali", "Sanitas", "Adeslas",
        "Asisa", "DKV", "Zurich", "Pelayo", "Reale", "Verti", "Caser", "Liberty Seguros", "Santalucia",
        "Ocaso", "Occident", "Helvetia", "Fiatc", "Aegon", "Direct Seguros", "Hilo Direct", "Genesis Seguros",
        "Catalana Occidente", "Mutua General", "Agrupacio", "Balumba", ("Seguro", None), ("Seguros", None),
        ("Poliza", None), ("Aseguradora", None),
    ],
    "transporte": [
        "Repsol", "Cepsa", "Moeve", "Galp", "Shell", "Petronor", "Plenoil", "Ballenoil", "Petroprix", "BP",
        "Renfe", "Metro de Madrid", "TMB", "Cabify", "Uber", "Bolt", "FreeNow", "Bicimad", "Blablacar",
        "Alsa", "Avanza", "EMT", "Metrovalencia", "Iryo", "Ouigo", "Parclick", "Telpark", "Saba", "Empark",
        "Autopista", "Autopistas", "Via-T", "Via T", "Itevebasa", "ITV", "Midas", "Norauto", "Feu Vert",
        ("Gasolinera", None), ("Estacion de Servicio", None), ("Parking", None), ("Aparcamiento", None),
        ("Taller", None), ("Consorcio Transportes", None),
    ],
    "salud": [
        "Druni Farma", "Quironsalud", "Vithas", "HM Hospitales", "Vivanta", "Vitaldent", "Multiopticas",
        "General Optica", "Alain Afflelou", "Opticalia", "Promofarma", "Atida", ("Farmacia", None),
        ("Farmaceutica", None), ("Dentista", None), ("Clinica", None), ("Hospital", None), ("Optica", None),
        ("Fisioterapia", None), ("Psicologo", None), ("Dental", None), ("Medico", None),
    ],
    "restaurantes": [
        "McDonalds", "Mcdonald", "Burger King", "KFC", "Telepizza", "Dominos", "Domino's Pizza", "Glovo",
        "Just Eat", "Uber Eats", "Foodhouse", "VIPS", "Ginos", "Foster's Hollywood", "Tagliatella", "100 Montaditos",
        "Starbucks", "Rodilla", "Pans Company", "Five Guys", "Goiko", "Taco Bell", "Popeyes", "Papa John", "Tierra Burrito",
        "Lizarran", ("Restaurante", None), ("Restaurant", None), ("Cafeteria", None), ("Bar", None),
        ("Cerveceria", None), ("Taberna", None), ("Pizzeria", None), ("Sushi", None), ("Kebab", None),
        ("Bodega", None), ("Tasca", None),
    ],
    "suscripciones": [
        "Netflix", "Spotify", "Disney Plus", "Disney+", "HBO", "Max.com", "DAZN", "Filmin", "Apple.com/bill",
        "Apple Bill", "Google One", "Google Storage", "Youtube Premium", "Amazon Prime", "Prime Video", "Audible",
        "iCloud", "Dropbox", "Microsoft 365", "Adobe", "Chatgpt", "Openai", "Anthropic", "Claude.ai", "Patreon",
        "Twitch", "Duolingo", "Tidal", "Deezer", "Movistar Plus", "Atresplayer", "RTVE Play", "SkyShowtime",
        "Crunchyroll", "Nintendo", "Playstation", "Xbox", "Steam",
    ],
    "ocio": [
        "Cinesa", "Yelmo", "Kinepolis", "Ocine", "Ticketmaster", "Entradas.com", "Eventbrite", "Fnac", "Casa del Libro",
        "Atrapalo", "Parque Warner", "PortAventura", ("Museo", None), ("Teatro", None), ("Cine", None), ("Cines", None),
        ("Concierto", None), ("Libreria", None), ("Loteria", None), ("Loterias", None), ("ONCE", None),
        ("Bet365", None), ("Codere", None),
    ],
    "compras": [
        ("Amazon", "Amazon"), ("AMZN", "Amazon"), "Aliexpress", "Temu", "Shein", "Ebay", "Vinted", "Wallapop", "Zara",
        "Bershka", "Pull&Bear", "Stradivarius", "Massimo Dutti", "Oysho", "Mango", "H&M", "Primark", "Uniqlo",
        "Lefties", "Springfield", "Cortefiel", "Tendam", "Sfera", "El Corte Ingles", "Mediamarkt", "PcComponentes",
        "Worten", "Apple Store", "Xiaomi", "Miravia", "Zalando", "Flying Tiger", "Pepco",
    ],
    "hogar": [
        "Ikea", "Leroy Merlin", "Bricomart", "Obramat", "Bauhaus", "Brico Depot", "Maisons du Monde", "Jysk",
        "Conforama", "Zara Home", "Kave Home", ("Ferreteria", None), ("Muebles", None), ("Tintoreria", None),
    ],
    "viajes": [
        "Ryanair", "Vueling", "Iberia", "Air Europa", "Easyjet", "Volotea", "Binter", "Booking", "Airbnb",
        "Expedia", "Trivago", "Edreams", "Skyscanner", "Hertz", "Europcar", "Sixt", "Avis", "Goldcar",
        "NH Hotels", "Melia", "Barcelo", "Riu", ("Hotel", None), ("Hostal", None),
    ],
    "deporte": [
        "Decathlon", "Basic-Fit", "Basic Fit", "McFit", "VivaGym", "Altafit", "Synergym", "Forus", "Metropolitan",
        "Sprinter", "Holmes Place", ("Gimnasio", None), ("Polideportivo", None),
    ],
    "cuidado": [
        "Sephora", "Primor", "Druni", "Douglas", "Marvimundo", ("Peluqueria", None), ("Barberia", None),
        ("Estetica", None),
    ],
    "mascotas": ["Kiwoko", "Tiendanimal", "Zooplus", "Maxi Zoo", ("Veterinario", None), ("Veterinaria", None)],
    "educacion": [
        "Udemy", "Coursera", ("Universidad", None), ("Colegio", None), ("Matricula", None), ("Academia", None),
        ("Guarderia", None), ("Escuela", None), ("Instituto", None),
    ],
    "donaciones": [
        "Cruz Roja", "Caritas", "UNICEF", "Medicos Sin Fronteras", "Greenpeace", "Save the Children", ("Donacion", None),
    ],
    "ahorro": [
        "MyInvestor", "Indexa Capital", "Finizens", "Trade Republic", "Degiro", "Interactive Brokers", "Openbank",
        ("Plan de Pensiones", None), ("Plan Pensiones", None), ("Fondo de Inversion", None), ("Aportacion", None),
    ],
}

# Conceptos genéricos: (categoría, signo, prioridad, patrones).
GENERIC = [
    ("comisiones", "out", 90, [
        "Comision", "Comisiones", "Com Mantenimiento", "Cuota Mantenimiento", "Mantenimiento Cuenta", "Gastos Mantenimiento",
        "Cuota Tarjeta", "Cuota Anual Tarjeta", "Cuota Tarjeta Credito", "Cuota Tarjeta Debito", "Cuota Anual",
        "Comision Descubierto", "Intereses Descubierto", "Interes Deudor", "Intereses Deudores",
        "Comision Emision", "Comision Transferencia", "Comision Cajero", "Comision Retirada", "Comision Reclamacion",
        "Gastos de Gestion", "Gastos Gestion", "Reclamacion Posiciones Deudoras", "Comision Administracion",
    ]),
    ("internos", "any", 85, [
        "Traspaso", "Traspasos", "Transferencia Interna", "Traspaso Cuentas", "Entre Cuentas", "Traspaso Propio",
    ]),
    ("nomina", "in", 80, ["Nomina", "Abono Nomina", "Pension", "Prestacion", "Paga Extra", "Salario", "SEPE"]),
    ("intereses", "in", 75, [
        "Intereses", "Interes Acreedor", "Dividendo", "Dividendos", "Liquidacion Intereses", "Rendimientos",
    ]),
    ("devoluciones", "in", 70, [
        "Devolucion", "Devoluciones", "Reembolso", "Abono Compra", "Anulacion", "Retrocesion", "Bonificacion",
    ]),
    ("vivienda", "out", 65, [
        "Hipoteca", "Prestamo Hipotecario", "Cuota Hipoteca", "Alquiler", "Arrendamiento", "Renta Vivienda",
    ]),
    ("prestamos", "out", 62, [
        "Prestamo", "Cuota Prestamo", "Amortizacion", "Liquidacion Tarjeta", "Liquidacion Tarjeta Credito", "Pago Tarjeta Credito",
        "Cargo Tarjeta Credito", "Cofidis", "Cetelem", "Younited",
    ]),
    ("impuestos", "any", 60, [
        "AEAT", "Agencia Tributaria", "Hacienda", "IRPF", "IBI", "Impuesto", "Impuestos", "Tasa", "Tasas",
        "Ayuntamiento", "Ajuntament", "Diputacion", "Tesoreria General", "DGT", "Multa", "Seguridad Social",
    ]),
    ("efectivo", "out", 55, [
        "Reintegro", "Retirada Efectivo", "Retirada Cajero", "Disposicion Efectivo", "Disposicion Cajero",
        "Cajero", "Reintegro Cajero", "Efectivo Cajero",
    ]),
    ("transfer_in", "in", 30, [
        "Bizum", "Transferencia", "Transferencia Recibida", "Transf Recibida", "Transf", "Ingreso", "Ingreso Efectivo",
    ]),
    ("transfer_out", "out", 30, ["Bizum", "Transferencia", "Transferencia Realizada", "Transf", "Transf Emitida"]),
    ("recibos", "out", 5, ["Recibo", "Adeudo", "Domiciliacion", "Adeudo Directo"]),
]

BRAND_PRIORITY = 50
BRAND_GENERIC_WORD_PRIORITY = 40

DEFAULT_SETTINGS = {
    "dup_window_days": 3,        # ventana para considerar dos cargos iguales como posible duplicado
    "dup_min_eur": 5,            # por debajo (café, bus) dos importes iguales son normales
    "outlier_z": 3.5,            # z-score robusto (mediana/MAD) a partir del cual un importe es raro
    "outlier_min_eur": 20,       # no avisar de anomalías pequeñas
    "new_merchant_eur": 200,     # primer movimiento con un comercio por encima de este importe
    "price_increase_pct": 5,     # subida en un cargo periódico estable
    "low_balance_eur": 100,      # aviso si el saldo actual cae por debajo
    "budget_warn_pct": 90,       # aviso cuando el gasto del mes se acerca al presupuesto
}


def iter_system_rules():
    """Filas (patrón, categoría, signo, prioridad, etiqueta) para la tabla de reglas."""
    from .categorize import normalize

    seen = set()
    for category, entries in BRANDS.items():
        for entry in entries:
            pattern, label = entry if isinstance(entry, tuple) else (entry, entry)
            norm = normalize(pattern)
            if (norm, "any") in seen or not norm:
                continue
            seen.add((norm, "any"))
            prio = BRAND_PRIORITY if label else BRAND_GENERIC_WORD_PRIORITY
            yield norm, category, "any", prio, label
    for category, sign, prio, patterns in GENERIC:
        for pattern in patterns:
            norm = normalize(pattern)
            if (norm, sign) in seen:
                continue
            seen.add((norm, sign))
            yield norm, category, sign, prio, None
