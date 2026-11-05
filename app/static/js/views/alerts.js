import { api, card, confirmButton, emptyState, eur, fmtDate, h, link, pageHead, segmented, statusChip, tile, toast } from "../ui.js";

export async function alertsView({ meta, refreshMeta, navigate, filters }) {
  const state = { status: "new", type: "", severity: "" };
  const list = h("div", null);
  const tiles = h("div", { class: "grid cols-4" });
  const bulkArea = h("div", { class: "row" });

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
      h("div", { class: "row" }, statusChip(alert.severity), h("span", { class: "tag" }, meta.alert_types[alert.type] ?? alert.type), h("span", { class: "faint small" }, fmtDate(alert.date))),
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
    if (filters.account) params.set("account", filters.account);
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
      const scope = state.type ? { type: state.type } : {};
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
  await load();

  return h("div", { class: "stack" },
    pageHead("Alertas", "Comisiones, posibles duplicados y movimientos que se salen de lo normal"),
    tiles,
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
