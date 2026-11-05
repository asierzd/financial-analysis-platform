import { accountLabel, api, eur, fmtDate, h, qs, s, segmented, toast } from "./ui.js";
import { hideTooltip } from "./charts.js";
import { dashboardView } from "./views/dashboard.js";
import { transactionsView } from "./views/transactions.js";
import { analysisView } from "./views/analysis.js";
import { investmentsView } from "./views/investments.js";
import { alertsView } from "./views/alerts.js";
import { budgetsView } from "./views/budgets.js";
import { importView } from "./views/import.js";
import { settingsView } from "./views/settings.js";

const ROUTES = [
  { id: "inicio", label: "Inicio", view: dashboardView, filters: true, accounts: true, icon: "M4 11.5 12 4l8 7.5V20h-5.5v-5.5h-5V20H4Z" },
  { id: "movimientos", label: "Movimientos", view: transactionsView, filters: true, accounts: true, icon: "M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3" },
  { id: "analisis", label: "Análisis", view: analysisView, filters: true, accounts: true, icon: "M4 20V10M10 20V4M16 20v-7M21 20H3" },
  { id: "inversiones", label: "Inversiones", view: investmentsView, accounts: true, icon: "M4 19 9.5 13l4 3.5L20 8M20 8h-5M20 8v5" },
  { id: "alertas", label: "Alertas", view: alertsView, accounts: true, icon: "M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15ZM10 21h4" },
  { id: "presupuestos", label: "Presupuestos", view: budgetsView, accounts: true, icon: "M4 7h16v13H4ZM4 7l2-3h12l2 3M15 13.5h2" },
  { id: "importar", label: "Importar", view: importView, icon: "M12 15V4M8 8l4-4 4 4M4 15v5h16v-5" },
  { id: "ajustes", label: "Reglas y ajustes", view: settingsView, icon: "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4" },
];

const PERIODS = [["month", "Este mes"], ["prev", "Mes pasado"], ["3m", "3 meses"], ["12m", "12 meses"], ["ytd", "Este año"], ["all", "Todo"]];

function storageGet(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* el navegador no guarda preferencias: no pasa nada */ }
}

const state = {
  meta: null,
  period: storageGet("period", "12m"),
  custom: { from: "", to: "" },
  hidden: loadHiddenAccounts(),
  focusAccount: null,
  token: 0,
};

// Se guardan las cuentas desmarcadas, no las marcadas: así una cuenta recién importada aparece incluida.
function loadHiddenAccounts() {
  try { return new Set(JSON.parse(storageGet("hiddenAccounts", "[]")).map(Number)); } catch { return new Set(); }
}

const selectedAccounts = () => (state.meta?.accounts ?? []).filter((a) => !state.hidden.has(a.id));

function accountsParam() {
  const all = state.meta?.accounts ?? [];
  const selected = selectedAccounts();
  return selected.length && selected.length < all.length ? selected.map((a) => a.id).join(",") : null;
}

function setHidden(ids) {
  state.hidden = new Set(ids);
  storageSet("hiddenAccounts", JSON.stringify([...state.hidden]));
}

const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// Los periodos cuentan desde el último movimiento importado, no desde hoy: el extracto puede ser de hace semanas.
function currentRange() {
  const { meta, period, custom } = state;
  if (period === "custom") return { from: custom.from || null, to: custom.to || null };
  if (!meta?.last || period === "all") return { from: null, to: null };
  const last = new Date(`${meta.last}T12:00:00`);
  const y = last.getFullYear();
  const m = last.getMonth();
  switch (period) {
    case "month": return { from: iso(new Date(y, m, 1)), to: meta.last };
    case "prev": return { from: iso(new Date(y, m - 1, 1)), to: iso(new Date(y, m, 0)) };
    case "3m": return { from: iso(new Date(y, m - 2, 1)), to: meta.last };
    case "12m": return { from: iso(new Date(y, m - 11, 1)), to: meta.last };
    case "ytd": return { from: `${y}-01-01`, to: meta.last };
    default: return { from: null, to: null };
  }
}

const filterQuery = () => ({ ...currentRange(), accounts: accountsParam() });

function accountBar() {
  const accounts = state.meta.accounts;
  const selected = selectedAccounts();
  const chips = accounts.map((account) => {
    const checked = !state.hidden.has(account.id);
    const isLast = checked && selected.length === 1;
    const showBank = account.bank && !account.name.toLowerCase().includes(account.bank.toLowerCase());
    const period = account.first ? `Datos del ${fmtDate(account.first)} al ${fmtDate(account.last)}` : "Sin movimientos";
    return h("label", {
      class: `acct-chip${checked ? " on" : ""}`,
      title: isLast ? "Tiene que quedar al menos una cuenta marcada" : `${period} · ${account.count} movimientos`,
    },
      h("input", {
        type: "checkbox", checked, disabled: isLast, "data-account": account.id,
        onchange: (event) => {
          state.focusAccount = account.id;
          const hidden = new Set(state.hidden);
          if (event.target.checked) hidden.delete(account.id); else hidden.add(account.id);
          setHidden(hidden);
          render({ refresh: true });
        },
      }),
      showBank ? h("span", { class: "acct-bank" }, account.bank) : null,
      h("span", { class: "acct-name" }, accountLabel(account)),
      account.balance === null ? null : h("span", { class: "acct-balance num" }, eur(account.balance)),
    );
  });
  const partial = selected.length < accounts.length;
  return h("div", { class: "account-bar", role: "group", "aria-label": "Cuentas incluidas en la vista" },
    h("span", { class: "account-bar-label" }, "Cuentas"),
    chips,
    partial ? h("button", { type: "button", class: "ghost mini", onclick: () => { setHidden([]); render({ refresh: true }); } }, "Ver todas") : null,
  );
}

function renderFilters(route) {
  const root = document.getElementById("filters");
  root.replaceChildren();
  if (!state.meta?.count) return;
  if (route.accounts && state.meta.accounts.length > 1) root.append(accountBar());
  if (state.focusAccount !== null) {
    root.querySelector(`input[data-account="${state.focusAccount}"]`)?.focus();
    state.focusAccount = null;
  }
  if (!route.filters) return;
  const custom = h("span", { class: "row" });
  const paintCustom = () => {
    custom.replaceChildren();
    if (state.period !== "custom") return;
    const onDate = (key) => (event) => { state.custom[key] = event.target.value; render(); };
    custom.append(
      h("input", { type: "date", "aria-label": "Desde", value: state.custom.from, min: state.meta.first, max: state.meta.last, onchange: onDate("from") }),
      h("input", { type: "date", "aria-label": "Hasta", value: state.custom.to, min: state.meta.first, max: state.meta.last, onchange: onDate("to") }),
    );
  };
  paintCustom();
  root.append(h("div", { class: "filterbar" },
    segmented([...PERIODS, ["custom", "Fechas"]], state.period, (value) => {
      state.period = value;
      if (value === "custom" && !state.custom.from) Object.assign(state.custom, { from: state.meta.first, to: state.meta.last });
      if (value !== "custom") storageSet("period", value);
      paintCustom();
      render();
    }, "Periodo"),
    custom,
    h("span", { class: "range-note" }, `Datos hasta el ${fmtDate(state.meta.last)}`),
  ));
}

const pendingCount = () => (state.meta?.alerts_new ?? 0) + (state.meta?.transfers?.count ?? 0);

function renderNav(active) {
  const nav = document.getElementById("nav");
  nav.replaceChildren(...ROUTES.map((route) =>
    h("a", { href: `#/${route.id}`, class: route.id === active ? "active" : "", "aria-current": route.id === active ? "page" : null },
      s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, s("path", { d: route.icon })),
      route.label,
      route.id === "alertas" && pendingCount()
        ? h("span", { class: "badge", "aria-label": `${pendingCount()} alertas pendientes` }, pendingCount() > 99 ? "99+" : pendingCount())
        : null)));
}

function parseHash() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const route = ROUTES.find((r) => r.id === path) ?? ROUTES[0];
  return { route, params: Object.fromEntries(new URLSearchParams(query ?? "")) };
}

async function refreshMeta() {
  state.meta = await api("/api/meta");
  // Olvida cuentas que ya no existen y evita quedarse con todas desmarcadas.
  const existing = state.meta.accounts.map((a) => a.id);
  const hidden = [...state.hidden].filter((id) => existing.includes(id));
  setHidden(hidden.length >= existing.length ? [] : hidden);
  const accounts = accountsParam();
  if (accounts) state.meta = await api(`/api/meta${qs({ accounts })}`);
  renderNav(parseHash().route.id);
}

async function render({ refresh = false } = {}) {
  const token = ++state.token;
  const { route, params } = parseHash();
  hideTooltip();
  const viewRoot = document.getElementById("view");
  viewRoot.classList.add("loading");
  try {
    if (refresh || !state.meta) await refreshMeta();
    if (token !== state.token) return;
    renderNav(route.id);
    renderFilters(route);
    const node = await route.view({
      params,
      meta: state.meta,
      filters: filterQuery(),
      navigate: (hash) => { location.hash = hash; },
      reload: () => render({ refresh: true }),
      refreshMeta,
    });
    if (token !== state.token) return;
    viewRoot.replaceChildren(node);
  } catch (error) {
    if (token !== state.token) return;
    viewRoot.replaceChildren(h("div", { class: "notice error" }, error.message));
  } finally {
    if (token === state.token) viewRoot.classList.remove("loading");
  }
}

function setupTheme() {
  const button = document.getElementById("theme");
  const labels = { system: "Tema: del sistema", light: "Tema: claro", dark: "Tema: oscuro" };
  let theme = storageGet("theme", "system");
  const apply = () => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    button.textContent = labels[theme];
  };
  button.addEventListener("click", () => {
    theme = { system: "light", light: "dark", dark: "system" }[theme];
    storageSet("theme", theme);
    apply();
    render();
  });
  apply();
}

setupTheme();
window.addEventListener("hashchange", () => {
  render();
  scrollTo(0, 0);
});
window.addEventListener("unhandledrejection", (event) => toast(event.reason?.message ?? "Error inesperado", { error: true }));
render();
