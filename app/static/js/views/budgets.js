import { sparkline } from "../charts.js";
import { api, card, emptyState, eur, eur0, fmtMonthLong, h, link, pageHead, pct, statusChip, toast } from "../ui.js";

export async function budgetsView({ meta, refreshMeta, filters }) {
  if (!meta.count) {
    return card(null, null, emptyState("Sin datos todavía", "Con unos meses de movimientos, aquí te sugeriré un presupuesto por categoría.",
      link("#/importar", h("button", { type: "button", class: "primary" }, "Importar movimientos"))));
  }
  const root = h("div", { class: "stack" });

  const load = async () => {
    const data = await api(`/api/budgets${filters.accounts ? `?accounts=${filters.accounts}` : ""}`);
    const active = data.items.filter((i) => i.budget);
    const rest = data.items.filter((i) => !i.budget && (i.suggested || i.spent > 0));
    const totalBudget = active.reduce((sum, i) => sum + i.budget, 0);
    const totalSpent = active.reduce((sum, i) => sum + Math.max(i.spent, 0), 0);

    const save = async (id, euros) => {
      await api(`/api/budgets/${id}`, { method: "PUT", json: { monthly_eur: euros } });
      await refreshMeta();
      await load();
    };

    const editor = (item, value) => {
      const input = h("input", {
        type: "number", min: 1, step: "any", value: value ? (value / 100).toFixed(0) : "", placeholder: "€/mes",
        "aria-label": `Presupuesto mensual de ${item.name}`, style: { width: "86px" },
      });
      const submit = async (event) => {
        event.preventDefault();
        const euros = Number(input.value);
        if (!(euros > 0)) { toast("Escribe un importe mayor que cero.", { error: true }); return; }
        try { await save(item.id, euros); } catch (error) { toast(error.message, { error: true }); }
      };
      return h("form", { class: "row", onsubmit: submit }, input, h("button", { type: "submit", class: "mini" }, item.budget ? "Guardar" : "Fijar"));
    };

    const sections = [
      pageHead("Presupuestos", `Gasto de ${fmtMonthLong(data.month)} frente a tu límite mensual`),
      filters.accounts ? h("div", { class: "notice info" }, "Esta página suma solo las cuentas marcadas, pero los avisos de presupuesto siempre cuentan todas tus cuentas.") : null,
      active.length
        ? h("section", { class: "card" },
            h("div", { class: "card-head" },
              h("div", null, h("h2", null, "Este mes"), h("p", null, `${eur0(totalSpent)} de ${eur0(totalBudget)} presupuestados`))),
            h("div", { class: "stack" }, active.map((item) => {
              const ratio = item.spent / item.budget;
              const level = ratio > 1 ? "critical" : ratio >= 0.9 ? "warning" : "good";
              const labels = { critical: "Superado", warning: "Al límite", good: "Dentro" };
              return h("div", null,
                h("div", { class: "row spread" },
                  h("div", { class: "row" }, h("strong", null, item.name), statusChip(level, labels[level])),
                  h("div", { class: "row" },
                    h("span", { class: "num" }, `${eur(Math.max(item.spent, 0))} de ${eur(item.budget)}`),
                    h("span", { class: "muted small num" }, pct(ratio)),
                    editor(item, item.budget),
                    h("button", {
                      type: "button", class: "mini ghost", onclick: async () => {
                        await api(`/api/budgets/${item.id}`, { method: "DELETE" });
                        await refreshMeta();
                        await load();
                      },
                    }, "Quitar"))),
                h("div", { class: "meter", style: { marginTop: "6px" } },
                  h("div", { class: `meter-fill ${ratio > 1 ? "over" : ratio >= 0.9 ? "warn" : ""}`, style: { width: `${Math.min(ratio, 1) * 100}%` } })),
              );
            })))
        : null,
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", null, h("h2", null, active.length ? "Añadir más categorías" : "Fija tu primer presupuesto"),
          h("p", null, `La sugerencia es la mediana de tus últimos ${data.months.length} meses completos. Te avisaré al llegar al límite.`))),
        rest.length ? h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, "Categoría"), h("th", null, "Historial"), h("th", { class: "right" }, "Este mes"), h("th", { class: "right" }, "Sugerido"), h("th", null, "Límite mensual"))),
          h("tbody", null, rest.map((item) => h("tr", null,
            h("td", null, item.name),
            h("td", null, item.history.length ? sparkline(item.history) : "—"),
            h("td", { class: "right num" }, eur(Math.max(item.spent, 0))),
            h("td", { class: "right num" }, item.suggested ? eur0(item.suggested) : "—"),
            h("td", null, editor(item, item.suggested))))))) : h("p", { class: "muted" }, "Todas las categorías con gasto ya tienen presupuesto."),
      ),
    ];
    root.replaceChildren(...sections.filter(Boolean));
  };
  await load();
  return root;
}
