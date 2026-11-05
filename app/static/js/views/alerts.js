import { accountLabel, accountTag, api, card, confirmButton, emptyState, eur, fmtDate, h, link, pageHead, qs, segmented, statusChip, tile, toast } from "../ui.js";

export async function alertsView({ meta, refreshMeta, navigate, filters }) {
  const state = { status: "new", type: "", severity: "" };
  const list = h("div", null);
  const tiles = h("div", { class: "grid cols-4" });
  const bulkArea = h("div", { class: "row" });
  const transfersBox = h("div", null);
  const accountName = (id) => {
    const account = meta.accounts.find((a) => a.id === id);
    return account ? accountLabel(account) : "Cuenta";
  };

  const afterTransferChange = async () => {
    await refreshMeta();
    await Promise.all([loadTransfers(), load()]);
  };
  const decide = async (pairs, status, message) => {
    const body = pairs.map((p) => ({ out_id: p.out.id, in_id: p.in.id }));
    await api("/api/transfers/decide", { method: "POST", json: { pairs: body, status } });
    await afterTransferChange();
    toast(message, {
      action: { label: "Deshacer", run: async () => {
        await Promise.all(body.map((pair) => api("/api/transfers/undo", { method: "POST", json: pair })));
        await afterTransferChange();
      } },
    });
  };
  const guarded = (run) => async () => { try { await run(); } catch (error) { toast(error.message, { error: true }); } };

  const pendingRow = (pair) => h("article", { class: "alert-item" },
    h("div", null,
      h("div", { class: "row" },
        h("strong", { class: "num" }, eur(pair.amount_cents)),
        h("span", { class: "muted small" }, `${accountName(pair.out.account_id)} → ${accountName(pair.in.account_id)}`),
        h("span", { class: "faint small" }, pair.gap_days === 0 ? "el mismo día" : `con ${pair.gap_days} ${pair.gap_days === 1 ? "día" : "días"} de diferencia`)),
      h("div", { class: "alert-detail" }, `${fmtDate(pair.out.date)} · Sale: ${pair.out.concept}`),
      h("div", { class: "alert-detail" }, `${fmtDate(pair.in.date)} · Entra: ${pair.in.concept}`)),
    h("div", { class: "alert-actions" },
      h("button", { type: "button", class: "mini", onclick: guarded(() => decide([pair], "confirmed", "Marcado como traspaso: ya no cuenta como gasto ni como ingreso.")) }, "Es un traspaso"),
      h("button", { type: "button", class: "mini ghost", onclick: guarded(() => decide([pair], "rejected", "De acuerdo, no se volverá a proponer.")) }, "No lo es")));

  const decidedRow = (item) => h("tr", null,
    h("td", { class: "nowrap num" }, fmtDate(item.out_date)),
    h("td", { class: "right num nowrap" }, eur(item.amount_cents)),
    h("td", null, `${accountName(item.out_account)} → ${accountName(item.in_account)}`),
    h("td", null, h("span", { class: "tag" }, item.status === "confirmed" ? "Traspaso" : "No es traspaso")),
    h("td", { class: "right" }, h("button", {
      type: "button", class: "mini ghost",
      onclick: guarded(async () => {
        await api("/api/transfers/undo", { method: "POST", json: { out_id: item.out_id, in_id: item.in_id } });
        await afterTransferChange();
      }),
    }, "Deshacer")));

  const loadTransfers = async () => {
    const { pending, decided } = await api(`/api/transfers${qs({ accounts: filters.accounts })}`);
    transfersBox.replaceChildren();
    if (!pending.length && !decided.length) return;
    transfersBox.append(h("section", { class: "card" },
      h("div", { class: "card-head" },
        h("div", null,
          h("h2", null, pending.length ? `Posibles traspasos entre tus cuentas (${pending.length})` : "Traspasos entre tus cuentas"),
          h("p", null, "Cada extracto solo ve su lado. Hasta que confirmes, estos movimientos cuentan como gasto en una cuenta y como ingreso en la otra.")),
        pending.length > 1
          ? confirmButton(`Confirmar los ${pending.length}`, "¿Todos? Pulsa otra vez", () => decide(pending, "confirmed", `${pending.length} traspasos confirmados.`), { danger: false })
          : null),
      pending.length ? h("div", null, pending.map(pendingRow)) : h("p", { class: "muted" }, "No hay ninguno pendiente de revisar."),
      decided.length ? h("details", { class: "decided" },
        h("summary", null, `Ya decididos (${decided.length})`),
        h("div", { class: "table-wrap" }, h("table", null, h("tbody", null, decided.map(decidedRow))))) : null,
    ));
  };

  const setStatus = async (alert, status, message) => {
    await api(`/api/alerts/${alert.id}`, { method: "PATCH", json: { status } });
    await refreshMeta();
    await load();
    toast(message, {
      action: { label: "Deshacer", run: async () => {
        await api(`/api/alerts/${alert.id}`, { method: "PATCH", json: { status: alert.status } });
        await refreshMeta();
        await load();
      } },
    });
  };

  const row = (alert) => h("article", { class: "alert-item" },
    h("div", null,
      h("div", { class: "row" }, statusChip(alert.severity), h("span", { class: "tag" }, meta.alert_types[alert.type] ?? alert.type), accountTag(meta, alert.account_id), h("span", { class: "faint small" }, fmtDate(alert.date))),
      h("div", { class: "alert-title" }, alert.title),
      h("div", { class: "alert-detail" }, alert.detail)),
    h("div", { class: "alert-actions" },
      alert.tx_ids.length
        ? h("button", { type: "button", class: "mini ghost", onclick: () => navigate(`#/movimientos?ids=${alert.tx_ids.join(",")}`) }, "Ver movimientos")
        : null,
      alert.status !== "reviewed" ? h("button", { type: "button", class: "mini", onclick: () => setStatus(alert, "reviewed", "Marcada como revisada.") }, "Revisada") : null,
      alert.status !== "dismissed" ? h("button", { type: "button", class: "mini ghost", onclick: () => setStatus(alert, "dismissed", "Alerta descartada.") }, "Descartar") : null,
      alert.status !== "new" ? h("button", { type: "button", class: "mini ghost", onclick: () => setStatus(alert, "new", "Alerta recuperada.") }, "Recuperar") : null,
    ));

  const load = async () => {
    const params = new URLSearchParams({ status: state.status });
    if (state.type) params.set("type", state.type);
    if (state.severity) params.set("severity", state.severity);
    if (filters.accounts) params.set("accounts", filters.accounts);
    const data = await api(`/api/alerts?${params}`);

    const sum = (predicate) => data.counts.filter(predicate).reduce((acc, c) => acc + c.n, 0);
    const money = (type) => data.counts.filter((c) => c.type === type).reduce((acc, c) => acc + Math.abs(c.total), 0);
    tiles.replaceChildren(
      tile("Importantes", String(sum((c) => c.severity === "critical")), "Requieren acción pronto"),
      tile("Comisiones pendientes de revisar", eur(money("fee")), `${sum((c) => c.type === "fee")} cargos`),
      tile("Posibles duplicados", String(sum((c) => c.type === "duplicate")), `${eur(money("duplicate"))} implicados`),
      tile("Movimientos extraños", String(sum((c) => c.type === "outlier" || c.type === "new_merchant")), "Importes raros o comercios nuevos"),
    );

    bulkArea.replaceChildren();
    if (state.status === "new" && data.items.length > 1) {
      const scope = { ...(state.type ? { type: state.type } : {}), ...(filters.accounts ? { accounts: filters.accounts } : {}) };
      bulkArea.append(confirmButton("Marcar todas como revisadas", "¿Seguro? Pulsa otra vez", async () => {
        const done = await api("/api/alerts/bulk", { method: "POST", json: { status: "reviewed", ...scope } });
        await refreshMeta();
        await load();
        toast(`${done.updated} alertas marcadas como revisadas.`);
      }));
    }

    const empties = {
      new: ["Todo en orden", "No hay alertas pendientes con estos filtros."],
      reviewed: ["Sin alertas revisadas", "Las que marques como revisadas aparecerán aquí."],
      dismissed: ["Sin alertas descartadas", "Las que descartes aparecerán aquí, por si quieres recuperarlas."],
    };
    list.replaceChildren(data.items.length ? h("div", null, data.items.map(row)) : emptyState(...empties[state.status]));
  };

  if (!meta.count) {
    return card(null, null, emptyState("Aún no hay alertas", "Importa un extracto y aquí verás comisiones, duplicados y movimientos extraños.",
      link("#/importar", h("button", { type: "button", class: "primary" }, "Importar movimientos"))));
  }
  await Promise.all([load(), loadTransfers()]);

  return h("div", { class: "stack" },
    pageHead("Alertas", "Comisiones, posibles duplicados y movimientos que se salen de lo normal"),
    tiles,
    transfersBox,
    h("section", { class: "card" },
      h("div", { class: "row spread", style: { marginBottom: "14px" } },
        h("div", { class: "row" },
          segmented([["new", "Pendientes"], ["reviewed", "Revisadas"], ["dismissed", "Descartadas"]], state.status, (value) => { state.status = value; load(); }, "Estado"),
          h("select", { "aria-label": "Tipo de alerta", onchange: (event) => { state.type = event.target.value; load(); } },
            h("option", { value: "" }, "Todos los tipos"),
            Object.entries(meta.alert_types).map(([id, label]) => h("option", { value: id }, label))),
          h("select", { "aria-label": "Importancia", onchange: (event) => { state.severity = event.target.value; load(); } },
            h("option", { value: "" }, "Toda importancia"),
            h("option", { value: "critical" }, "Importante"), h("option", { value: "warning" }, "Revisar"), h("option", { value: "info" }, "Información"))),
        bulkArea),
      list),
  );
}
