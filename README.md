# Finanzas

Control de gastos personal, sin login y solo en tu ordenador. Importa los movimientos de tu banco, los clasifica
solo, te avisa de comisiones, duplicados y movimientos extraños, y ofrece análisis financiero.

## Arrancar y parar

```bash
podman-compose up -d --build   # primera vez o tras cambiar el código
podman-compose up -d           # el resto de veces (también tras reiniciar el ordenador o WSL)
podman-compose down            # parar (no borra tus datos)
```

Abre <http://localhost:8765>.

Se usa el 8765 porque en este Windows ya hay un servicio del sistema escuchando en `127.0.0.1:8080`, que tapa la
aplicación aunque desde WSL parezca funcionar. Si el 8765 también se ocupa, cambia el primer número en `compose.yaml`.

Si no tienes `podman-compose`: `pip3 install --user podman-compose` y asegúrate de tener `~/.local/bin` en el `PATH`.
El aviso `Error validating CNI config ... firewall` de Podman 3.4 es inofensivo.

No uses `podman restart` ni `podman-compose restart`: en Podman 3.4.4 sin root fallan con
`address already in use` y el contenedor se queda parado (tus datos no se pierden). Para reiniciar, usa
`podman-compose down && podman-compose up -d`. Si ya te ha pasado, basta con `podman-compose up -d`.

`restart: unless-stopped` no levanta el contenedor al arrancar WSL, porque hace falta systemd: ejecuta
`podman-compose up -d` en este directorio.

## Primeros pasos

1. Descarga los movimientos de tu cuenta desde la web de BBVA, en Excel.
2. Súbelos en **Importar**. Verás una vista previa antes de guardar nada.
3. Corrige en **Movimientos** lo que quede sin clasificar y pulsa «Aplicar siempre» para crear una regla.

¿Quieres probarla antes? Importa `samples/bbva_ejemplo.xlsx`, con datos ficticios, y después pulsa «Deshacer» en el
historial de importaciones. Se regenera con `python samples/generar_ejemplo.py`.

## Qué hace

| Sección | Contenido |
|---|---|
| **Inicio** | Saldo, ingresos, gastos, tasa de ahorro, alertas, categorías, comercios y próximos cargos |
| **Movimientos** | Búsqueda, filtros, cambio de categoría, reglas y exportación a CSV |
| **Análisis** | Salud financiera, regla 50/30/20, evolución, mapa de calor, variabilidad y tendencia, concentración del gasto, cargos fijos, comisiones, proyección de saldo y simulador de recortes |
| **Alertas** | Comisiones, duplicados, importes inusuales, comercios nuevos, subidas de precio, cargos o ingresos ausentes, nuevos cargos periódicos, saldo negativo o bajo, presupuestos y saldos que no cuadran |
| **Presupuestos** | Límite mensual por categoría, con sugerencia a partir de tu historial |
| **Reglas y ajustes** | Reglas de clasificación, categorías propias, umbrales de alertas y cuentas |

Criterios de cálculo:

- Los periodos cuentan desde el último movimiento importado, no desde hoy.
- Las medias mensuales usan solo meses completos.
- Los traspasos entre tus cuentas no cuentan como ingreso ni como gasto.
- Una devolución reduce el gasto de su categoría.
- Reimportar un fichero o solaparlo con otro no duplica nada, pero un cargo doble real sí se conserva y se avisa.

## Tus datos

- Todo vive en `data/finanzas.db` (SQLite). Para hacer una copia de seguridad, para el contenedor y copia esa carpeta.
- La aplicación no hace ninguna conexión a internet: no usa CDN, analítica ni servicios externos.
- **No tiene login**, por lo que solo se publica en `127.0.0.1`. No cambies eso en `compose.yaml` sin añadir antes
  autenticación: cualquiera que llegara al puerto vería tus movimientos. Además rechaza peticiones de otros orígenes
  y de otros `Host`, para que una web abierta en tu navegador no pueda leerla. Si la sirves con otro nombre, añádelo
  a la variable `ALLOWED_HOSTS`.

## Añadir otro banco

Sin escribir código: sube el Excel o CSV y la aplicación te preguntará qué es cada columna.

Para que se detecte solo, crea `app/ingest/mibanco.py`. Se registra automáticamente:

```python
from .base import BankParser, FormatError, ParseResult, RawFile, register
from .tabular import find_header, rows_to_transactions


@register
class MiBancoParser(BankParser):
    id = "mibanco"
    bank = "Mi Banco"

    def detect(self, raw: RawFile) -> float:
        """Confianza de 0 a 1 en que el fichero es de este banco."""
        ...

    def parse(self, raw: RawFile) -> ParseResult:
        """Devuelve los movimientos como ParsedTx. Ver bbva.py."""
        ...
```

`readers.py` ya lee `.xlsx`, `.xls`, CSV y los «falsos Excel» en HTML, y `tabular.py` entiende fechas e importes
en formato español y reconoce las columnas por su nombre. Lo demás no depende del banco.

## Desarrollo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
```

Backend en FastAPI y SQLite. Interfaz en JavaScript sin dependencias ni paso de compilación (`app/static`).
Los importes se guardan como enteros en céntimos.
