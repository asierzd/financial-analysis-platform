import { api, card, confirmButton, eur, fmtDate, h, num, pageHead, toast } from "../ui.js";

const MAP_FIELDS = [
  ["date", "Fecha *", true], ["concept", "Concepto *", true], ["amount", "Importe", false], ["debit", "Cargo (si va aparte)", false],
  ["credit", "Abono (si va aparte)", false], ["balance", "Saldo", false], ["value_date", "Fecha valor", false], ["notes", "Observaciones", false],
];

export async function importView({ meta, reload, refreshMeta, navigate }) {
  const stage = h("div", { class: "stack" });
  const history = h("div", null);
  let file = null;

  const reset = () => {
    file = null;
    stage.replaceChildren(dropZone());
  };

  const dropZone = () => {
    const input = h("input", {
      type: "file", accept: ".xlsx,.xls,.csv,.txt,.htm,.html",
      onchange: (event) => { if (event.target.files[0]) start(event.target.files[0]); },
    });
    const zone = h("label", {
      class: "drop",
      ondragover: (event) => { event.preventDefault(); zone.classList.add("over"); },
      ondragleave: () => zone.classList.remove("over"),
      ondrop: (event) => {
        event.preventDefault();
        zone.classList.remove("over");
        if (event.dataTransfer.files[0]) start(event.dataTransfer.files[0]);
      },
    }, input,
      h("strong", null, "Arrastra aquí el fichero o pulsa para elegirlo"),
      h("span", { class: "muted" }, "Excel (.xlsx, .xls) o CSV, hasta 25 MB. Se procesa solo en tu ordenador."));
    return zone;
  };

  const form = (extra = {}) => {
    const data = new FormData();
    data.append("file", file);
    for (const [key, value] of Object.entries(extra)) if (value !== null && value !== undefined && value !== "") data.append(key, value);
    return data;
  };

  const start = async (selected, mapping = null, accountId = null) => {
    file = selected;
    stage.replaceChildren(card(null, null, h("p", { class: "muted" }, `Leyendo ${file.name}…`)));
    try {
      const answer = await api("/api/import/preview", { method: "POST", body: form({ mapping, account_id: accountId }) });
      if (answer.status === "needs_mapping") stage.replaceChildren(mappingStep(answer.raw));
      else stage.replaceChildren(previewStep(answer.preview, answer.account_id, mapping));
    } catch (error) {
      stage.replaceChildren(h("div", { class: "notice error" }, error.message), dropZone());
    }
  };

  const previewStep = (p, suggestedAccount, mapping) => {
    const accountChoice = h("select", { "aria-label": "Cuenta de destino" },
      meta.accounts.map((a) => h("option", { value: a.id, selected: a.id === suggestedAccount }, a.name)),
      h("option", { value: "", selected: !suggestedAccount && !meta.accounts.length }, "Cuenta nueva"));
    const newName = h("input", { type: "text", value: `Cuenta ${p.bank}`, maxlength: 60, "aria-label": "Nombre de la cuenta nueva" });
    const syncName = () => { newName.hidden = accountChoice.value !== ""; };
    accountChoice.addEventListener("change", async () => {
      syncName();
      // Los movimientos ya importados dependen de la cuenta: se recalcula la vista previa.
      if (accountChoice.value) start(file, mapping, accountChoice.value);
    });
    syncName();

    const confirm = h("button", {
      type: "button", class: "primary", disabled: p.new === 0,
      onclick: async () => {
        confirm.disabled = true;
        confirm.textContent = "Importando…";
        try {
          const done = await api("/api/import/commit", {
            method: "POST",
            body: form({ mapping, account_id: accountChoice.value, account_name: accountChoice.value ? null : newName.value }),
          });
          await refreshMeta();
          toast(`${num(done.new)} movimientos importados${done.skipped ? `, ${num(done.skipped)} ya estaban` : ""}.`, {
            action: { label: "Ver el resumen", run: () => navigate("#/inicio") },
          });
          reload();
        } catch (error) {
          toast(error.message, { error: true });
          confirm.disabled = false;
          confirm.textContent = "Importar";
        }
      },
    }, p.new === 0 ? "Nada nuevo que importar" : `Importar ${num(p.new)} movimientos`);

    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", null,
        h("h2", null, `${file.name}: ${p.bank}`),
        h("p", null, `${fmtDate(p.date_from)} a ${fmtDate(p.date_to)}`))),
      h("div", { class: "grid cols-4", style: { marginBottom: "12px" } },
        miniStat("Movimientos en el fichero", num(p.total)),
        miniStat("Nuevos", num(p.new)),
        miniStat("Ya importados", num(p.existing)),
        miniStat("Clasificados solos", `${num(p.classified)} de ${num(p.total)}`)),
      p.warnings.map((w) => h("div", { class: "notice", style: { marginBottom: "8px" } }, w)),
      h("div", { class: "row", style: { marginBottom: "12px" } },
        h("label", { class: "field" }, "Guardar en", accountChoice),
        h("label", { class: "field" }, " ", newName)),
      h("h3", { style: { marginBottom: "4px" } }, "Últimos movimientos del fichero"),
      h("div", { class: "table-wrap", style: { marginBottom: "14px" } }, h("table", null,
        h("thead", null, h("tr", null, h("th", null, "Fecha"), h("th", null, "Comercio"), h("th", null, "Categoría"), h("th", { class: "right" }, "Importe"))),
        h("tbody", null, p.sample.slice(0, 10).map((t) => h("tr", null,
          h("td", { class: "num nowrap" }, fmtDate(t.date)),
          h("td", null, h("div", { class: "tx-name" }, t.merchant), h("div", { class: "tx-concept" }, t.concept)),
          h("td", null, t.category),
          h("td", { class: `right num nowrap ${t.amount_cents > 0 ? "pos" : ""}` }, eur(t.amount_cents))))))),
      h("div", { class: "row" }, confirm, h("button", { type: "button", onclick: reset }, "Cancelar")),
    );
  };

  const mappingStep = (raw) => {
    const state = { sheet: raw.sheet, header_row: raw.header_row };
    const selects = {};
    const tableBox = h("div", { class: "table-wrap" });
    const headerCells = () => raw.rows[state.header_row] ?? [];

    const fieldControls = h("div", { class: "map-grid" });
    const paintFields = () => {
      fieldControls.replaceChildren(...MAP_FIELDS.map(([key, label]) => {
        const previous = selects[key]?.value ?? raw.suggestion[key] ?? "";
        const select = h("select", null, h("option", { value: "" }, "—"),
          headerCells().map((cell, i) => h("option", { value: i, selected: String(previous) === String(i) }, cell || `Columna ${i + 1}`)));
        selects[key] = select;
        return h("label", { class: "field" }, label, select);
      }));
    };
    const paintTable = () => {
      tableBox.replaceChildren(h("table", { class: "small" }, h("tbody", null, raw.rows.slice(0, 15).map((row, index) =>
        h("tr", { class: `pick-row ${index === state.header_row ? "header-row" : ""}`, onclick: () => { state.header_row = index; paintTable(); paintFields(); } },
          row.map((cell) => h("td", { class: "nowrap" }, cell)))))));
    };
    paintFields();
    paintTable();

    const submit = () => {
      const mapping = { sheet: state.sheet, header_row: state.header_row };
      for (const [key] of MAP_FIELDS) {
        const value = selects[key].value;
        if (value !== "") mapping[key] = Number(value);
      }
      if (mapping.date === undefined || mapping.concept === undefined) {
        toast("Indica al menos la fecha y el concepto.", { error: true });
        return;
      }
      if (mapping.amount === undefined && mapping.debit === undefined && mapping.credit === undefined) {
        toast("Indica la columna de importe, o las de cargo y abono.", { error: true });
        return;
      }
      start(file, JSON.stringify(mapping));
    };

    return h("section", { class: "card" },
      h("div", { class: "card-head" }, h("div", null,
        h("h2", null, `No reconozco el formato de ${file.name}`),
        h("p", null, "Pulsa la fila con los títulos de las columnas y dime qué es cada una."))),
      tableBox,
      h("div", { style: { margin: "14px 0" } }, fieldControls),
      h("div", { class: "row" },
        h("button", { type: "button", class: "primary", onclick: submit }, "Continuar"),
        h("button", { type: "button", onclick: reset }, "Cancelar")),
    );
  };

  const loadHistory = async () => {
    const data = await api("/api/imports");
    history.replaceChildren(data.items.length
      ? h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, "Fichero"), h("th", null, "Cuenta"), h("th", null, "Periodo"), h("th", { class: "right" }, "Añadidos"), h("th", { class: "right" }, "Repetidos"), h("th", null, ""))),
          h("tbody", null, data.items.map((item) => h("tr", null,
            h("td", null, item.filename),
            h("td", { class: "muted" }, item.account_name),
            h("td", { class: "nowrap num" }, `${fmtDate(item.date_from)} – ${fmtDate(item.date_to)}`),
            h("td", { class: "right num" }, num(item.tx_count)),
            h("td", { class: "right num muted" }, num(item.rows_skipped)),
            h("td", { class: "right" }, confirmButton("Deshacer", `¿Borrar ${num(item.tx_count)} movimientos?`, async () => {
              const done = await api(`/api/imports/${item.id}`, { method: "DELETE" });
              toast(`${num(done.removed)} movimientos eliminados.`);
              reload();
            })))))))
      : h("p", { class: "muted" }, "Todavía no has importado nada."));
  };

  reset();
  await loadHistory();
  return h("div", { class: "stack" },
    pageHead("Importar movimientos", "Puedes repetir ficheros o solaparlos: los movimientos ya importados se ignoran"),
    stage,
    h("div", { class: "grid cols-2" },
      card("Cómo descargarlos de BBVA", "La opción puede cambiar un poco de sitio según la versión de la web",
        h("ol", { class: "steps" },
          h("li", null, "Entra en la web de BBVA desde el ordenador y abre tu cuenta."),
          h("li", null, "Ve a los movimientos y elige el rango de fechas más amplio que te deje."),
          h("li", null, "Usa la opción de descargar o exportar y elige Excel."),
          h("li", null, "Sube aquí ese fichero, sin abrirlo ni modificarlo."))),
      card("Otros bancos", "BBVA se detecta solo",
        h("p", { class: "muted" }, "Si subes un Excel o CSV de otro banco, te preguntaré qué es cada columna y lo importaré igual. Se puede añadir un importador automático por banco sin tocar el resto de la aplicación."))),
    card("Historial de importaciones", "Deshacer borra solo los movimientos que añadió esa importación", history),
  );
}

const miniStat = (label, value) => h("div", null, h("div", { class: "tile-label" }, label), h("div", { class: "tile-value" }, value));
