import { chartCard, drawColumns, drawPie, pieSlices, sharePercent } from "../charts.js";
import {
  api, card, emptyState, eur, eur0, fmtDate, fmtMonth, fmtMonthLong, h, link, pageHead, pct, qs, signedPct, statusChip, tile, toast,
} from "../ui.js";

const CLASS_SLOTS = { FUND: 1, STOCK: 2, BOND: 3, CRYPTO: 4, SYNTHETIC: 5, DERIVATIVE: 7 };
const signedEur0 = (cents) => `${cents > 0 ? "+" : ""}${eur0(cents)}`;
const signClass = (cents) => (cents > 0 ? "pos" : cents < 0 ? "neg" : "");
const formatShares = (n) => new Intl.NumberFormat("es-ES", { maximumFractionDigits: n >= 100 ? 2 : 4 }).format(n);

function pie(title, subtitle, items, slots) {
  return chartCard({
    title,
    subtitle,
    height: 232,
    draw: (el, width) => drawPie(el, width, { slices: pieSlices(items, slots) }),
    table: {
      columns: [{ label: "Nombre" }, { label: "Valor", right: true }, { label: "Peso", right: true }],
      rows: items.map((i) => [i.name, eur(i.amount), sharePercent(i.share)]),
    },
  });
}

export async function investmentsView({ filters, refreshMeta }) {
  const root = h("div", { class: "stack" });

  const priceCell = (position, load) => {
    const input = h("input", {
      type: "number", min: 0, step: "any", value: position.price ? position.price.toFixed(position.price < 1 ? 6 : 2) : "", style: { width: "92px" },
      "aria-label": `Precio actual de ${position.name} en euros`,
      onchange: async (event) => {
        const price = Number(event.target.value);
        if (!(price > 0)) { toast("Escribe un precio mayor que cero.", { error: true }); return; }
        try {
          await api(`/api/investments/prices/${encodeURIComponent(position.isin)}`, { method: "PUT", json: { price_eur: price } });
          await refreshMeta();
          await load();
        } catch (error) { toast(error.message, { error: true }); }
      },
    });
    const source = { manual: "Tu precio", operation: "Última operación", none: "Sin precio" }[position.price_source];
    return h("div", null,
      input,
      h("div", { class: "faint small nowrap" }, position.price_date ? `${source}, ${fmtDate(position.price_date)}` : source,
        position.price_source === "manual"
          ? h("button", {
              type: "button", class: "mini ghost", title: "Volver al precio de la última operación",
              onclick: async () => {
                await api(`/api/investments/prices/${encodeURIComponent(position.isin)}`, { method: "DELETE" });
                await load();
              },
            }, "Quitar")
          : null));
  };

  const load = async () => {
    const data = await api(`/api/investments${qs({ accounts: filters.accounts })}`);
    if (data.empty) {
      root.replaceChildren(pageHead("Inversiones"), card(null, null, emptyState(
        "Aún no hay inversiones",
        "Importa la exportación de transacciones de Trade Republic para ver tus posiciones, intereses y rentabilidad.",
        link("#/importar", h("button", { type: "button", class: "primary" }, "Importar movimientos")))));
      return;
    }
    const s = data.summary;
    const sections = [
      pageHead("Inversiones", "Coste por FIFO. Sin precios en vivo: la valoración usa el último precio que se conoce de cada valor."),
      data.warnings.length ? h("div", { class: "notice" }, data.warnings.length === 1
        ? data.warnings[0]
        : h("ul", { class: "steps", style: { margin: 0, color: "inherit" } }, data.warnings.map((w) => h("li", null, w)))) : null,
      s.stale_prices ? h("div", { class: "notice info" },
        `${s.stale_prices} ${s.stale_prices === 1 ? "valor tiene" : "valores tienen"} un precio de hace más de dos semanas. Escribe el actual en la tabla de posiciones para que el valor sea realista.`) : null,
      h("div", { class: "summary-band", style: { marginBottom: 0 } },
        h("div", { class: "card" },
          h("div", { class: "tile-label" }, "Valor estimado"),
          h("div", { class: "hero-value" }, eur(s.value)),
          h("div", { class: "tile-note" }, s.valuation_day ? `Con precios hasta el ${fmtDate(s.valuation_day)}` : "")),
        tile("Coste de lo que tienes", eur0(s.cost), "Incluye comisiones de compra"),
        tile("Plusvalía sin realizar", signedEur0(s.unrealized), s.unrealized_pct === null ? "" : h("span", { class: signClass(s.unrealized) }, signedPct(s.unrealized_pct, 1))),
        tile("Ganancia ya realizada", signedEur0(s.realized), "En lo vendido, después de comisiones"),
        tile("Rentabilidad anual (TIR)", s.annual_return === null ? "—" : signedPct(s.annual_return, 1),
          s.annual_return === null ? "Hace falta más historial" : "Tiene en cuenta cuándo entró cada euro. Aproximada")),
      h("div", { class: "grid cols-4" },
        tile("Efectivo", data.cash ? (data.cash.complete ? eur(data.cash.balance) : "—") : "—",
          data.cash?.complete ? "Calculado. Compáralo con tu app" : "Falta historial"),
        tile("Intereses cobrados", eur(s.interest), data.cash_yield?.effective_rate != null
          ? `Rinde un ${pct(data.cash_yield.effective_rate, 2)} anual sobre el efectivo medio de ${eur0(data.cash_yield.average_cash)}`
          : "Sobre el efectivo de la cuenta"),
        tile("Dividendos cobrados", eur(s.dividends), "Antes de retenciones"),
        tile("Comisiones y retenciones", eur(s.order_fees + s.withholding), `${eur(s.order_fees)} en comisiones de operaciones · ${eur(s.withholding)} retenidos`)),
    ];

    if (data.positions.length) {
      sections.push(h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", null, h("h2", null, "Posiciones abiertas"),
          h("p", null, "Escribe el precio actual en euros para actualizar el valor. Se guarda hasta que lo cambies."))),
        h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null,
            h("th", null, "Valor"), h("th", { class: "right" }, "Títulos"), h("th", { class: "right" }, "Coste medio"), h("th", { class: "right" }, "Coste"),
            h("th", null, "Precio (€)"), h("th", { class: "right" }, "Valor"), h("th", { class: "right" }, "Plusvalía"), h("th", { class: "right" }, "Peso"))),
          h("tbody", null, data.positions.map((p) => h("tr", null,
            h("td", null,
              h("div", { class: "tx-name" }, p.name),
              h("div", { class: "faint small row" }, h("span", { class: "tag" }, p.asset_label), p.isin,
                p.price_stale ? statusChip("warning", "Precio antiguo") : null)),
            h("td", { class: "right num nowrap" }, formatShares(p.shares)),
            h("td", { class: "right num nowrap" }, new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: p.average_cost < 10 ? 4 : 2, useGrouping: "always" }).format(p.average_cost)),
            h("td", { class: "right num nowrap" }, eur(p.cost)),
            h("td", null, priceCell(p, load)),
            h("td", { class: "right num nowrap" }, eur(p.value)),
            h("td", { class: `right num nowrap ${signClass(p.unrealized ?? 0)}` },
              p.unrealized === null ? "—" : h("div", null, signedEur0(p.unrealized), h("div", { class: "small" }, signedPct(p.unrealized_pct, 1)))),
            h("td", { class: "right num nowrap" }, pct(p.weight)))))))));
      sections.push(h("div", { class: "grid cols-2" },
        pie("Por tipo de activo", `Valor estimado: ${eur(s.value)}`, data.allocation, CLASS_SLOTS),
        pie("Por posición", "Cuánto pesa cada valor en la cartera", data.positions.map((p) => ({ id: p.isin, name: p.name, amount: p.value, share: p.weight })), {})));
    }

    const panels = [];
    if (data.contributions.length) {
      const c = data.contributions;
      panels.push(chartCard({
        title: "Aportaciones netas por mes",
        subtitle: `Compras menos ventas. Por debajo de cero, retiraste más de lo que invertiste.${s.savings_plan_buys ? ` Incluye ${s.savings_plan_buys} compras por plan de inversión.` : ""}`,
        draw: (el, width) => drawColumns(el, width, {
          labels: c.map((r) => r.month), titleLabel: fmtMonthLong, tickLabel: fmtMonth,
          series: [{ label: "Aportado", color: "var(--series-1)", negativeColor: "var(--series-2)", values: c.map((r) => r.amount) }],
        }),
        table: { columns: [{ label: "Mes" }, { label: "Aportado", right: true }], rows: c.map((r) => [fmtMonthLong(r.month), eur(r.amount)]) },
      }));
    }
    if (data.interest_by_month.length) {
      const i = data.interest_by_month;
      panels.push(chartCard({
        title: "Intereses de la cuenta remunerada",
        subtitle: data.cash_yield ? `${eur(data.cash_yield.interest_12m)} en los últimos 12 meses, antes de retenciones` : null,
        draw: (el, width) => drawColumns(el, width, {
          labels: i.map((r) => r.month), titleLabel: fmtMonthLong, tickLabel: fmtMonth,
          series: [{ label: "Intereses", color: "var(--series-3)", values: i.map((r) => r.amount) }],
        }),
        table: { columns: [{ label: "Mes" }, { label: "Intereses", right: true }], rows: i.map((r) => [fmtMonthLong(r.month), eur(r.amount)]) },
      }));
    }
    if (panels.length) sections.push(h("div", { class: "grid cols-2" }, panels));

    if (data.by_year.length) {
      sections.push(card("Resumen por año", "Orientativo, para preparar la declaración: FIFO sin aplicar la regla de los dos meses. Contrástalo con el informe fiscal de Trade Republic.",
        h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, "Año"), h("th", { class: "right" }, "Ganancia realizada"), h("th", { class: "right" }, "Dividendos"),
            h("th", { class: "right" }, "Intereses"), h("th", { class: "right" }, "Retenciones"), h("th", { class: "right" }, "Comisiones de operaciones"))),
          h("tbody", null, data.by_year.map((y) => h("tr", null,
            h("td", null, y.year),
            h("td", { class: `right num nowrap ${signClass(y.realized)}` }, eur(y.realized)),
            h("td", { class: "right num nowrap" }, eur(y.dividends)),
            h("td", { class: "right num nowrap" }, eur(y.interest)),
            h("td", { class: "right num nowrap" }, eur(y.withholding)),
            h("td", { class: "right num nowrap" }, eur(y.fees)))))))));
    }
    if (data.closed.length) {
      sections.push(card("Posiciones cerradas", "Valores que ya no tienes",
        h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, "Valor"), h("th", null, "Tipo"), h("th", { class: "right" }, "Ganancia realizada"), h("th", { class: "right" }, "Dividendos"))),
          h("tbody", null, data.closed.map((p) => h("tr", null,
            h("td", null, p.name), h("td", { class: "muted" }, p.asset_label),
            h("td", { class: `right num nowrap ${signClass(p.realized)}` }, eur(p.realized)),
            h("td", { class: "right num nowrap" }, eur(p.dividends)))))))));
    }
    root.replaceChildren(...sections.filter(Boolean));
  };
  await load();
  return root;
}
