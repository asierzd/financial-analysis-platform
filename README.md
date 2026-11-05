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

1. Descarga los movimientos de tu cuenta desde la web de BBVA o Santander, en Excel.
2. Súbelos en **Importar**. Verás una vista previa antes de guardar nada.
3. Corrige en **Movimientos** lo que quede sin clasificar y pulsa «Aplicar siempre» para crear una regla.

¿Quieres probarla antes? Importa `samples/bbva_ejemplo.xlsx` o `samples/santander_ejemplo.xlsx`, con datos ficticios,
y después pulsa «Deshacer» en el historial de importaciones. Se regeneran con `samples/generar_ejemplo.py` y
`samples/generar_ejemplo_santander.py`.

## Qué hace

| Sección | Contenido |
|---|---|
| **Inicio** | Saldo, ingresos, gastos, tasa de ahorro, alertas, gráficos de pastel de gastos e ingresos, categorías, comercios y próximos cargos |
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

## Varias cuentas

Cada banco va en su propia cuenta y puedes filtrar por ella en la barra superior. Si no hay una coincidencia clara por
IBAN o por banco, la importación propone una cuenta nueva.

Una transferencia entre tus propias cuentas de distintos bancos cuenta como gasto en una y como ingreso en la otra,
porque cada extracto solo ve su lado. Para excluirlas, crea una regla con un texto que las identifique (por ejemplo,
tu nombre) y la categoría «Traspasos entre mis cuentas».

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
from .base import RawFile, register
from .tabular import TableBankParser, above_header_words, find_header


@register
class MiBancoParser(TableBankParser):
    id = "mibanco"
    bank = "Mi Banco"

    def detect(self, raw: RawFile) -> float:
        """Confianza de 0 a 1 en que el fichero es de este banco. Ver santander.py."""
        for sheet in raw.sheets:
            found = find_header(sheet)
            if found and "MIBANCO" in above_header_words(sheet.rows, found[0], raw.filename):
                return 1.0
        return 0.0
```

Si el extracto es una tabla con títulos de columna, `TableBankParser` ya lo lee todo. `readers.py` abre `.xlsx`, `.xls`,
CSV y los «falsos Excel» en HTML, y `tabular.py` entiende fechas e importes en formato español y reconoce las columnas
por su nombre; añade allí los títulos nuevos. Para otro formato, hereda de `BankParser` e implementa también `parse`.

## Desarrollo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests
```

Backend en FastAPI y SQLite. Interfaz en JavaScript sin dependencias ni paso de compilación (`app/static`).
Los importes se guardan como enteros en céntimos.
