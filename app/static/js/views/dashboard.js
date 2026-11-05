import { chartCard, drawColumns, drawLines } from "../charts.js";
import {
  api, bars, card, delta, emptyState, eur, eur0, fmtDate, fmtDayMonth, fmtMonthLong, h, link, pct, qs, statusChip, tile,
} from "../ui.js";

const INCOME = "var(--series-1)";
const EXPENSE = "var(--series-2)";

export function monthlyCard(monthly, navigate) {
  return chartCard({
    title: "Ingresos y gastos por mes",
    subtitle: "Pulsa un mes para ver sus movimientos",
    legendItems: [{ color: INCOME, label: "Ingresos" }, { color: EXPENSE, label: "Gastos" }],
    draw: (el, width) => drawColumns(el, width, {
      labels: monthly.map((m) => m.month),
      titleLabel: fmtMonthLong,
      series: [
        { label: "Ingresos", color: INCOME, values: monthly.map((m) => m.income) },
        { label: "Gastos", color: EXPENSE, values: monthly.map((m) => m.expense) },
      ],
      onClick: navigate ? (month) => navigate(`#/movimientos?month=${month}`) : null,
    }),
    table: {
      columns: [{ label: "Mes" }, { label: "Ingresos", right: true }, { label: "Gastos", right: true }, { label: "Neto", right: true }],
      rows: monthly.map((m) => [fmtMonthLong(m.month), eur(m.income), eur(m.expense), eur(m.net)]),
    },
  });
}

export function balanceCard(series) {
  const xs = series.map((p) => new Date(`${p.date}T12:00:00`).getTime());
  const toIso = (t) => new Date(t).toISOString().slice(0, 10);
  return chartCard({
    title: "Evolución del saldo",
    subtitle: "Saldo al cierre de cada día con movimientos",
    draw: (el, width) => drawLines(el, width, {
      xs,
      xLabel: (t) => fmtDate(toIso(t)),
      tickLabel: (t) => fmtDayMonth(toIso(t)),
      series: [{ label: "Saldo", color: INCOME, values: series.map((p) => p.balance), area: true }],
      endLabel: true,
    }),
    table: {
      columns: [{ label: "Fecha" }, { label: "Saldo", right: true }],
      rows: series.slice(-60).reverse().map((p) => [fmtDate(p.date), eur(p.balance)]),
    },
  });
}

export async function dashboardView({ filters, navigate }) {
  const data = await api(`/api/dashboard${qs(filters)}`);
  if (data.empty) {
    return h("div", null, h("section", { class: "card" }, emptyState(
      "Empieza importando tus movimientos",
      "Descarga el Excel de movimientos de tu cuenta de BBVA y súbelo aquí. Los datos se quedan en tu ordenador.",
      link("#/importar", h("button", { type: "button", class: "primary" }, "Importar movimientos")),
    )));
  }
  const { totals, previous } = data;
  const compare = { label: "vs. periodo anterior" };
  const savingsNote = previous && previous.savings_rate !== null && totals.savings_rate !== null
    ? `${(totals.savings_rate - previous.savings_rate) >= 0 ? "▲" : "▼"} ${Math.abs((totals.savings_rate - previous.savings_rate) * 100).toFixed(0)} puntos vs. periodo anterior`
    : "Lo que queda de cada euro ingresado";

  const toMovements = (item) => navigate(`#/movimientos?category=${item.id}`);
  return h("div", { class: "stack" },
    h("div", { class: "summary-band", style: { marginBottom: 0 } },
      h("div", { class: "card" },
        h("div", { class: "tile-label" }, "Saldo actual"),
        h("div", { class: "hero-value" }, data.balance === null ? "—" : eur(data.balance)),
        h("div", { class: "tile-note" }, data.balance === null ? "El extracto no incluye saldo" : `A ${fmtDate(data.balance_date)}`),
      ),
      tile("Ingresos", eur0(totals.income), delta(totals.income, previous?.income, compare)),
      tile("Gastos", eur0(totals.expense), delta(totals.expense, previous?.expense, { ...compare, upIsGood: false })),
      tile("Balance del periodo", eur0(totals.net), `${totals.count} movimientos`),
      tile("Tasa de ahorro", pct(totals.savings_rate), savingsNote),
    ),
    data.alerts.length ? alertsCard(data.alerts) : null,
    h("div", { class: "grid cols-2" }, monthlyCard(data.monthly, navigate), balanceCard(data.balance_series)),
    h("div", { class: "grid cols-2" },
      card("Gastos por categoría", "Pulsa una categoría para ver el detalle",
        data.categories.length
          ? bars(data.categories.slice(0, 10), { onClick: toMovements, share: true })
          : h("p", { class: "muted" }, "Sin gastos en este periodo.")),
      card("Dónde se va más dinero", "Comercios con más gasto en el periodo",
        data.merchants.length
          ? bars(data.merchants.map((m) => ({ ...m, name: m.merchant })), { onClick: (m) => navigate(`#/movimientos?q=${encodeURIComponent(m.name)}`) })
          : h("p", { class: "muted" }, "Sin gastos en este periodo.")),
    ),
    data.upcoming.length ? upcomingCard(data.upcoming) : null,
  );
}

function alertsCard(alerts) {
  return card("Requiere tu atención", null,
    alerts.map((alert) => h("div", { class: "alert-item" },
      h("div", null,
        statusChip(alert.severity),
        h("div", { class: "alert-title" }, alert.title),
        h("div", { class: "alert-detail" }, `${fmtDate(alert.date)} · ${alert.detail}`)),
    )),
    h("div", { style: { marginTop: "10px" } }, link("#/alertas", "Ver todas las alertas →")),
  );
}

function upcomingCard(items) {
  return card("Próximos cargos e ingresos previstos", "Estimados a partir de lo que se repite en tu historial",
    h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "Fecha aprox."), h("th", null, "Concepto"), h("th", null, "Frecuencia"), h("th", { class: "right" }, "Importe"))),
      h("tbody", null, items.map((r) => h("tr", null,
        h("td", { class: "nowrap" }, fmtDate(r.next_date)),
        h("td", null, r.merchant),
        h("td", { class: "muted" }, r.frequency),
        h("td", { class: `right num ${r.direction === "in" ? "pos" : ""}` }, eur(r.direction === "in" ? r.avg_cents : -r.avg_cents))))))));
}
