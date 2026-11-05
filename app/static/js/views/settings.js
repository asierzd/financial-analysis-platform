import { api, card, confirmButton, h, num, pageHead, segmented, toast } from "../ui.js";

const SETTING_FIELDS = [
  ["dup_window_days", "Duplicados: días de margen", "Dos cargos iguales del mismo comercio dentro de este plazo", "días"],
  ["dup_min_eur", "Duplicados: importe mínimo", "Por debajo es normal repetir (café, autobús)", "€"],
  ["outlier_z", "Sensibilidad a importes raros", "Más alto avisa menos. Lo razonable: entre 3 y 5", ""],
  ["outlier_min_eur", "Importes raros: importe mínimo", "No avisar por anomalías más pequeñas", "€"],
  ["new_merchant_eur", "Comercio nuevo: importe mínimo", "Primer movimiento con un nombre desconocido", "€"],
  ["price_increase_pct", "Subida de precio mínima", "En cargos periódicos con importe estable", "%"],
  ["low_balance_eur", "Saldo bajo", "Avisar si el saldo cae por debajo", "€"],
  ["transfer_window_days", "Traspasos: días de margen", "Entre la salida de una cuenta y la entrada en otra", "días"],
  ["budget_warn_pct", "Aviso de presupuesto", "Porcentaje gastado a partir del cual avisar", "%"],
];

export async function settingsView({ meta, refreshMeta, reload }) {
  const tabs = { rules: rulesTab, categories: categoriesTab, thresholds: thresholdsTab, accounts: accountsTab };
  const body = h("div", null);
  const context = { meta, refreshMeta, reload };
  const show = async (id) => body.replaceChildren(await tabs[id](context));
  await show("rules");
  return h("div", { class: "stack" },
    pageHead("Reglas y ajustes", "Cómo se clasifican tus movimientos y cuándo se te avisa"),
    h("div", null, segmented([["rules", "Reglas"], ["categories", "Categorías"], ["thresholds", "Umbrales de alertas"], ["accounts", "Cuentas"]], "rules", show, "Sección")),
    body,
  );
}

async function rulesTab({ meta, refreshMeta }) {
  const box = h("div", null);
  let filter = "";
  let items = [];
  let origin = "user";

  const reloadRules = async () => {
    items = (await api("/api/rules")).items;
    paint();
  };
  const paint = () => {
    const needle = filter.trim().toUpperCase();
    const visible = items.filter((r) => (origin === "all" || r.source === origin || (origin === "used" && r.matches > 0))
      && (!needle || r.pattern.includes(needle) || r.category_name.toUpperCase().includes(needle)));
    const shown = visible.slice(0, 200);
    const parts = [
      shown.length ? h("div", { class: "table-wrap" }, h("table", null,
        h("thead", null, h("tr", null, h("th", null, "Si el texto contiene"), h("th", null, "Categoría"), h("th", null, "Aplica a"), h("th", { class: "right" }, "Movimientos"), h("th", null, "Activa"), h("th", null, ""))),
        h("tbody", null, shown.map((rule) => h("tr", null,
          h("td", null, rule.pattern, rule.source === "user" ? h("span", { class: "tag", style: { marginLeft: "6px" } }, "Tuya") : null),
          h("td", null, rule.category_name),
          h("td", { class: "muted" }, { any: "Todos", in: "Entradas", out: "Salidas" }[rule.sign]),
          h("td", { class: "right num" }, num(rule.matches)),
          h("td", null, h("input", {
            type: "checkbox", checked: rule.enabled, "aria-label": `Activar regla ${rule.pattern}`,
            onchange: async (event) => {
              const done = await api(`/api/rules/${rule.id}`, { method: "PATCH", json: { enabled: event.target.checked } });
              await refreshMeta();
              toast(`Regla actualizada. ${num(done.changed)} movimientos reclasificados.`);
              await reloadRules();
            },
          })),
          h("td", { class: "right" }, rule.source === "user" ? confirmButton("Borrar", "¿Seguro?", async () => {
            const done = await api(`/api/rules/${rule.id}`, { method: "DELETE" });
            await refreshMeta();
            toast(`Regla borrada. ${num(done.changed)} movimientos reclasificados.`);
            await reloadRules();
          }) : null)))))) : h("p", { class: "muted" }, origin === "user"
        ? "Aún no tienes reglas propias. Crea una arriba, o cambia la categoría de un movimiento y pulsa «Aplicar siempre»."
        : "Ninguna regla coincide."),
      visible.length > shown.length ? h("p", { class: "muted small" }, `Se muestran 200 de ${num(visible.length)}. Busca para acotar.`) : null,
    ];
    box.replaceChildren(...parts.filter(Boolean));
  };

  const pattern = h("input", { type: "text", required: true, minlength: 2, maxlength: 100, placeholder: "Ej.: PANADERIA PEPE", style: { flex: "1 1 180px" }, "aria-label": "Texto a buscar" });
  const category = h("select", { "aria-label": "Categoría" }, meta.categories.map((c) => h("option", { value: c.id }, c.name)));
  const sign = h("select", { "aria-label": "Aplica a" }, h("option", { value: "any" }, "Entradas y salidas"), h("option", { value: "out" }, "Solo salidas"), h("option", { value: "in" }, "Solo entradas"));
  const create = async (event) => {
    event.preventDefault();
    try {
      const done = await api("/api/rules", { method: "POST", json: { pattern: pattern.value, category_id: category.value, sign: sign.value } });
      pattern.value = "";
      await refreshMeta();
      toast(`Regla guardada. ${num(done.changed)} movimientos reclasificados.`);
      await reloadRules();
    } catch (error) { toast(error.message, { error: true }); }
  };

  await reloadRules();
  return h("div", { class: "stack" },
    card("Nueva regla", "Tus reglas ganan a las del sistema y nunca pisan una categoría puesta a mano",
      h("form", { class: "row", onsubmit: create }, pattern, category, sign, h("button", { type: "submit", class: "primary" }, "Crear regla"))),
    h("section", { class: "card" },
      h("div", { class: "row spread", style: { marginBottom: "10px" } },
        segmented([["user", "Mías"], ["used", "En uso"], ["all", "Todas"]], origin, (value) => { origin = value; paint(); }, "Origen"),
        h("input", { type: "search", placeholder: "Buscar regla", "aria-label": "Buscar regla", oninput: (event) => { filter = event.target.value; paint(); } })),
      box),
  );
}

async function categoriesTab({ meta, reload }) {
  const name = h("input", { type: "text", required: true, minlength: 2, maxlength: 50, placeholder: "Nombre", style: { flex: "1 1 180px" }, "aria-label": "Nombre de la categoría" });
  const kind = h("select", { "aria-label": "Tipo" }, h("option", { value: "expense" }, "Gasto"), h("option", { value: "income" }, "Ingreso"));
  const group = h("select", { "aria-label": "Grupo 50/30/20" }, h("option", { value: "wants" }, "Caprichos"), h("option", { value: "needs" }, "Necesidades"), h("option", { value: "savings" }, "Ahorro"));
  const groupNames = { income: "Ingreso", needs: "Necesidad", wants: "Capricho", savings: "Ahorro", internal: "Traspaso", unknown: "Sin clasificar" };
  return h("div", { class: "stack" },
    card("Nueva categoría", "El grupo decide cómo cuenta en el análisis 50/30/20",
      h("form", {
        class: "row",
        onsubmit: async (event) => {
          event.preventDefault();
          try {
            await api("/api/categories", { method: "POST", json: { name: name.value, kind: kind.value, grp: group.value } });
            toast("Categoría creada.");
            reload();
          } catch (error) { toast(error.message, { error: true }); }
        },
      }, name, kind, group, h("button", { type: "submit", class: "primary" }, "Crear"))),
    card("Categorías", null, h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "Categoría"), h("th", null, "Grupo"), h("th", null, "Origen"))),
      h("tbody", null, meta.categories.map((c) => h("tr", null, h("td", null, c.name), h("td", { class: "muted" }, groupNames[c.grp]), h("td", { class: "muted" }, c.is_system ? "Sistema" : "Tuya"))))))),
  );
}

async function thresholdsTab({ refreshMeta }) {
  const current = await api("/api/settings");
  const inputs = {};
  return card("Umbrales de alertas", "Se recalculan al guardar. Lo que ya marcaste como revisado se conserva.",
    h("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        const body = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, Number(input.value)]));
        try {
          await api("/api/settings", { method: "PUT", json: body });
          await refreshMeta();
          toast("Umbrales guardados.");
        } catch (error) { toast(error.message, { error: true }); }
      },
    },
    h("div", { class: "grid cols-2" }, SETTING_FIELDS.map(([key, label, help, unit]) => {
      inputs[key] = h("input", { type: "number", min: 0, step: "any", required: true, value: current[key], style: { width: "100px" } });
      return h("label", { class: "field" },
        h("strong", { style: { color: "var(--ink)" } }, label),
        help,
        h("span", { class: "row" }, inputs[key], unit));
    })),
    h("div", { style: { marginTop: "14px" } }, h("button", { type: "submit", class: "primary" }, "Guardar"))));
}

async function accountsTab({ meta, reload }) {
  if (!meta.accounts.length) return card("Cuentas", null, h("p", { class: "muted" }, "Se crean al importar el primer extracto."));
  return card("Cuentas", "Cámbiales el nombre para reconocerlas mejor",
    h("div", { class: "stack" }, meta.accounts.map((account) => {
      const input = h("input", { type: "text", value: account.name, maxlength: 60, required: true, "aria-label": `Nombre de ${account.name}` });
      return h("form", {
        class: "row",
        onsubmit: async (event) => {
          event.preventDefault();
          try {
            await api(`/api/accounts/${account.id}`, { method: "PATCH", json: { name: input.value } });
            toast("Nombre guardado.");
            reload();
          } catch (error) { toast(error.message, { error: true }); }
        },
      }, input, h("span", { class: "tag" }, account.bank || "Banco"), account.iban ? h("span", { class: "muted small num" }, account.iban) : null, h("button", { type: "submit", class: "mini" }, "Guardar"));
    })));
}
