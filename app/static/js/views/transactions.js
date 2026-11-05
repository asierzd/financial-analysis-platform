import { api, card, emptyState, eur, fmtDate, fmtMonthLong, h, link, num, pageHead, qs, toast } from "../ui.js";

const PAGE_SIZE = 50;

function monthRange(key) {
  const [year, month] = key.split("-").map(Number);
  const last = new Date(year, month, 0).getDate();
  return { from: `${key}-01`, to: `${key}-${String(last).padStart(2, "0")}` };
}

export async function transactionsView({ params, meta, filters, refreshMeta }) {
  if (!meta.count) {
    return card(null, null, emptyState("Todavía no hay movimientos", "Importa un extracto para empezar.",
      link("#/importar", h("button", { type: "button", class: "primary" }, "Importar movimientos"))));
  }
  const state = {
    q: params.q ?? "",
    category: params.category ?? "",
    kind: "",
    min: "",
    max: "",
    ids: params.ids ?? "",
    sort: "date",
    direction: "desc",
    page: 1,
  };
  const scope = params.month ? { ...filters, ...monthRange(params.month) } : params.ids ? { account: filters.account } : filters;
  const results = h("div", null);
  const summary = h("p", { class: "muted" }, "");
  const query = () => ({
    ...scope, q: state.q, category: state.category, kind: state.kind, min_amount: state.min, max_amount: state.max,
  });

  let loadToken = 0;
  const load = async () => {
    const token = ++loadToken;
    results.style.opacity = 0.55;
    const data = await api(`/api/transactions${qs({ ...query(), ids: state.ids, sort: state.sort, direction: state.direction, page: state.page, size: PAGE_SIZE })}`);
    if (token !== loadToken) return;
    results.style.opacity = 1;
    summary.replaceChildren(
      `${num(data.total)} movimientos · entradas `, h("span", { class: "num pos" }, eur(data.money_in)),
      " · salidas ", h("span", { class: "num" }, eur(data.money_out)),
    );
    results.replaceChildren(renderTable(data));
  };

  const header = (key, label, right) => h("th", { class: right ? "right" : "", "aria-sort": state.sort === key ? (state.direction === "asc" ? "ascending" : "descending") : null },
    h("button", {
      type: "button",
      onclick: () => {
        if (state.sort === key) state.direction = state.direction === "asc" ? "desc" : "asc";
        else { state.sort = key; state.direction = key === "date" || key === "amount" ? "desc" : "asc"; }
        state.page = 1;
        load();
      },
    }, label, state.sort === key ? (state.direction === "asc" ? " ↑" : " ↓") : ""));

  const categorySelect = (tx) => {
    const select = h("select", {
      class: `table-select${tx.category_id === "sin_clasificar" ? " unclassified" : ""}`,
      "aria-label": `Categoría de ${tx.merchant}`,
      onchange: async (event) => {
        const categoryId = event.target.value;
        const name = event.target.selectedOptions[0].textContent;
        try {
          await api(`/api/transactions/${tx.id}`, { method: "PATCH", json: { category_id: categoryId } });
          await refreshMeta();
          select.classList.toggle("unclassified", categoryId === "sin_clasificar");
          toast(`Categoría cambiada a ${name}.`, {
            action: {
              label: `Aplicar siempre a ${tx.merchant}`,
              run: async () => {
                const done = await api(`/api/transactions/${tx.id}`, { method: "PATCH", json: { category_id: categoryId, create_rule: true } });
                await refreshMeta();
                toast(`Regla creada: ${num(done.affected)} movimientos en ${name}.`);
                load();
              },
            },
          });
        } catch (error) {
          toast(error.message, { error: true });
          select.value = tx.category_id;
        }
      },
    });
    const groups = { income: "Ingresos", expense: "Gastos", transfer: "Traspasos" };
    for (const [kind, label] of Object.entries(groups)) {
      select.append(h("optgroup", { label }, meta.categories.filter((c) => c.kind === kind).map((c) =>
        h("option", { value: c.id, selected: c.id === tx.category_id }, c.name))));
    }
    return select;
  };

  const renderTable = (data) => {
    if (!data.items.length) {
      return emptyState("Nada coincide con estos filtros", "Prueba a quitar algún filtro o a ampliar el periodo.");
    }
    const pages = Math.max(1, Math.ceil(data.total / data.size));
    return h("div", null,
      h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null,
          header("date", "Fecha"), header("merchant", "Comercio y concepto"), header("category", "Categoría"),
          header("amount", "Importe", true), h("th", { class: "right" }, "Saldo"))),
        h("tbody", null, data.items.map((tx) => h("tr", null,
          h("td", { class: "nowrap num" }, fmtDate(tx.date)),
          h("td", null,
            h("div", { class: "tx-name" }, tx.merchant),
            h("div", { class: "tx-concept", title: [tx.concept, tx.notes].filter(Boolean).join(" · ") },
              [tx.concept, tx.notes].filter(Boolean).join(" · "))),
          h("td", null, categorySelect(tx)),
          h("td", { class: `right num nowrap ${tx.amount_cents > 0 ? "pos" : ""}` }, `${tx.amount_cents > 0 ? "+" : ""}${eur(tx.amount_cents)}`),
          h("td", { class: "right num nowrap muted" }, tx.balance_cents === null ? "—" : eur(tx.balance_cents))))),
      )),
      h("div", { class: "pager" },
        h("span", { class: "muted small" }, `Página ${state.page} de ${pages}`),
        h("div", { class: "row" },
          h("button", { type: "button", disabled: state.page <= 1, onclick: () => { state.page -= 1; load(); scrollTo(0, 0); } }, "← Anterior"),
          h("button", { type: "button", disabled: state.page >= pages, onclick: () => { state.page += 1; load(); scrollTo(0, 0); } }, "Siguiente →"))),
    );
  };

  let searchTimer = null;
  const onFilter = (key) => (event) => {
    state[key] = event.target.value;
    state.page = 1;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, event.type === "input" ? 220 : 0);
  };

  const groups = { Gastos: "expense", Ingresos: "income", Traspasos: "transfer" };
  const notes = [];
  if (params.month) notes.push(`Solo ${fmtMonthLong(params.month)}`);
  if (params.ids) notes.push("Solo los movimientos de una alerta");

  const exportLink = h("a", { class: "nowrap", href: "#", onclick: (event) => { event.currentTarget.href = `/api/transactions.csv${qs(query())}`; } }, "Exportar CSV");

  await load();
  return h("div", { class: "stack" },
    pageHead("Movimientos", null, exportLink),
    h("section", { class: "card" },
      h("div", { class: "row", style: { marginBottom: "12px" } },
        h("input", { type: "search", placeholder: "Buscar comercio o concepto", "aria-label": "Buscar", value: state.q, style: { flex: "1 1 200px" }, oninput: onFilter("q") }),
        h("select", { "aria-label": "Categoría", onchange: onFilter("category") },
          h("option", { value: "" }, "Todas las categorías"),
          Object.entries(groups).map(([label, kind]) => h("optgroup", { label },
            meta.categories.filter((c) => c.kind === kind).map((c) => h("option", { value: c.id, selected: c.id === state.category }, c.name))))),
        h("select", { "aria-label": "Tipo", onchange: onFilter("kind") },
          h("option", { value: "" }, "Entradas y salidas"), h("option", { value: "expense" }, "Solo salidas"), h("option", { value: "income" }, "Solo entradas")),
        h("input", { type: "number", min: 0, step: "any", placeholder: "Mín. €", "aria-label": "Importe mínimo", style: { width: "84px" }, oninput: onFilter("min") }),
        h("input", { type: "number", min: 0, step: "any", placeholder: "Máx. €", "aria-label": "Importe máximo", style: { width: "84px" }, oninput: onFilter("max") }),
      ),
      h("div", { class: "row spread", style: { marginBottom: "8px" } }, summary,
        notes.length ? h("span", { class: "row" }, notes.map((n) => h("span", { class: "tag" }, n)), link("#/movimientos", "Quitar", { class: "small" })) : null),
      results,
    ),
  );
}
