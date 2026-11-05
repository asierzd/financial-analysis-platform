// Utilidades de DOM, formato y API. Los textos del banco no son de fiar: todo entra por textContent.

const SVG_NS = "http://www.w3.org/2000/svg";

function assign(el, props, isSvg) {
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.setAttribute("class", value);
    else if (key === "style") Object.assign(el.style, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "value" && !isSvg) el.value = value;
    else if (key === "checked" || key === "selected" || key === "disabled") el[key] = Boolean(value);
    else el.setAttribute(key, value === true ? "" : value);
  }
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  assign(el, props, false);
  append(el, children);
  return el;
}

export function s(tag, props, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  assign(el, props, true);
  append(el, children);
  return el;
}

const eurFormat = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", useGrouping: "always" });
const eur0Format = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0, useGrouping: "always" });
const numFormat = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" });

export const eur = (cents) => (cents === null || cents === undefined ? "—" : eurFormat.format(cents / 100));
export const eur0 = (cents) => (cents === null || cents === undefined ? "—" : eur0Format.format(Math.round(cents / 100)));
export const signedEur = (cents) => (cents > 0 ? "+" : "") + eur(cents);
export const num = (v) => numFormat.format(v);
export const pct = (ratio, digits = 0) => {
  if (ratio === null || ratio === undefined) return "—";
  const text = (ratio * 100).toFixed(digits);
  return `${Number(text) === 0 ? text.replace("-", "") : text}`.replace(".", ",") + " %";
};
export const signedPct = (ratio, digits = 0) =>
  ratio === null || ratio === undefined ? "—" : `${ratio > 0 ? "+" : ""}${pct(ratio, digits)}`;

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const MONTHS_LONG = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];

export const fmtDate = (iso) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}` : "—");
export const fmtDayMonth = (iso) => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;
export const fmtMonth = (key) => `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(2, 4)}`;
export const fmtMonthLong = (key) => `${MONTHS_LONG[Number(key.slice(5, 7)) - 1]} de ${key.slice(0, 4)}`;

export async function api(path, options = {}) {
  const init = { ...options };
  if (options.json !== undefined) {
    init.body = JSON.stringify(options.json);
    init.headers = { "Content-Type": "application/json" };
    delete init.json;
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new Error("No se puede conectar con la aplicación. ¿Sigue en marcha el contenedor?");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : "La operación no se ha podido completar.";
    throw new Error(detail);
  }
  return data;
}

export function qs(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export function toast(message, { error = false, action = null, duration = null } = {}) {
  const el = h("div", { class: `toast${error ? " error" : ""}` }, h("span", null, message));
  let timer = null;
  const close = () => { clearTimeout(timer); el.remove(); };
  if (action) {
    el.append(h("button", {
      type: "button",
      onclick: async () => { close(); try { await action.run(); } catch (e) { toast(e.message, { error: true }); } },
    }, action.label));
  }
  document.getElementById("toasts").append(el);
  timer = setTimeout(close, duration ?? (error || action ? 10000 : 4000));
  return el;
}

const ICONS = {
  critical: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/>',
  warning: '<path d="M12 4 2.8 20h18.4Z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5v.01"/>',
  good: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
};

function icon(name) {
  const svg = s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  const parsed = new DOMParser().parseFromString(`<svg xmlns="${SVG_NS}">${ICONS[name]}</svg>`, "image/svg+xml");
  svg.append(...parsed.documentElement.childNodes);
  return svg;
}

const STATUS_LABELS = { critical: "Importante", warning: "Revisar", info: "Información", good: "Bien" };

// Un estado siempre lleva icono y texto, nunca solo color.
export const statusChip = (level, label) => h("span", { class: `status ${level}` }, icon(level), label ?? STATUS_LABELS[level]);

export function tile(label, value, note, props) {
  return h("div", { class: "card", ...props },
    h("div", { class: "tile-label" }, label),
    h("div", { class: "tile-value" }, value),
    h("div", { class: "tile-note" }, note ?? ""),
  );
}

export function delta(current, previous, { upIsGood = true, label = "" } = {}) {
  if (previous === null || previous === undefined || previous === 0) return h("span", { class: "faint" }, "Sin periodo anterior");
  const change = (current - previous) / Math.abs(previous);
  if (Math.abs(change) < 0.005) return h("span", { class: "faint" }, `Igual ${label}`.trim());
  const good = change > 0 === upIsGood;
  return h("span", null,
    h("span", { class: good ? "delta-good" : "delta-bad" }, `${change > 0 ? "▲" : "▼"} ${pct(Math.abs(change))}`),
    h("span", { class: "faint" }, ` ${label}`),
  );
}

export function card(title, subtitle, ...content) {
  return h("section", { class: "card" },
    title ? h("div", { class: "card-head" }, h("div", null, h("h2", null, title), subtitle ? h("p", null, subtitle) : null)) : null,
    ...content,
  );
}

export const pageHead = (title, subtitle, ...actions) =>
  h("header", { class: "page-head" },
    h("div", null, h("h1", null, title), subtitle ? h("p", null, subtitle) : null),
    actions.length ? h("div", { class: "row" }, actions) : null,
  );

export function bars(items, { onClick = null, value = eur0, share = false } = {}) {
  const max = Math.max(...items.map((i) => i.amount), 1);
  return h("div", { class: "bars" }, items.map((item) => {
    const inner = [
      h("span", { class: "bar-name", title: item.name }, item.name),
      h("span", { class: "bar-track" }, h("span", { class: "bar-fill", style: { display: "block", width: `${(item.amount / max) * 100}%` } })),
      h("span", { class: "bar-value" }, value(item.amount), share ? h("span", { class: "bar-share" }, pct(item.share)) : null),
    ];
    return onClick
      ? h("button", { type: "button", class: "bar-row", onclick: () => onClick(item), title: `Ver movimientos de ${item.name}` }, inner)
      : h("div", { class: "bar-row" }, inner);
  }));
}

// Botón destructivo de dos pasos: el primer clic arma, el segundo ejecuta.
export function confirmButton(label, confirmLabel, run, props = {}) {
  let armed = false;
  let timer = null;
  const button = h("button", {
    type: "button",
    class: `mini danger ${props.class ?? ""}`,
    onclick: async () => {
      if (!armed) {
        armed = true;
        button.textContent = confirmLabel;
        timer = setTimeout(() => { armed = false; button.textContent = label; }, 4000);
        return;
      }
      clearTimeout(timer);
      button.disabled = true;
      try { await run(); } catch (e) { toast(e.message, { error: true }); button.disabled = false; }
    },
  }, label);
  return button;
}

export function segmented(options, selected, onChange, ariaLabel) {
  const root = h("div", { class: "segmented", role: "group", "aria-label": ariaLabel });
  const paint = (value) => {
    for (const button of root.children) button.setAttribute("aria-pressed", String(button.dataset.value === value));
  };
  for (const [value, label] of options) {
    root.append(h("button", {
      type: "button",
      "data-value": value,
      onclick: () => { paint(value); onChange(value); },
    }, label));
  }
  paint(selected);
  return root;
}

export const emptyState = (title, text, ...actions) =>
  h("div", { class: "empty" }, h("h2", null, title), h("p", null, text), actions.length ? h("div", { class: "row" }, actions) : null);

export const link = (href, text, props) => h("a", { href, ...props }, text);
