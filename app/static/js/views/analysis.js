import { chartCard, drawColumns, drawLines, heatColor, sparkline } from "../charts.js";
import {
  api, bars, card, emptyState, eur, eur0, fmtDate, fmtMonth, fmtMonthLong, h, link, pageHead, pct, qs, segmented, signedPct, statusChip, tile,
} from "../ui.js";
import { monthlyCard } from "./dashboard.js";

const COLORS = { needs: "var(--series-1)", wants: "var(--series-2)", savings: "var(--series-3)", unknown: "var(--series-4)" };
const GROUP_LABELS = { needs: "Necesidades", wants: "Caprichos", savings: "Ahorro e inversión", unknown: "Sin clasificar" };

export async function analysisView({ filters, navigate }) {
  const data = await api(`/api/analysis${qs(filters)}`);
  if (data.empty) {
    return card(null, null, emptyState("Sin datos para analizar", "Importa un extracto para ver tu análisis financiero.",
      link("#/importar", h("button", { type: "button", class: "primary" }, "Importar movimientos"))));
  }
  const tabs = {
    salud: ["Salud financiera", healthTab],
    evolucion: ["Evolución", evolutionTab],
    categorias: ["Categorías", categoriesTab],
    fijos: ["Cargos fijos", recurringTab],
    comisiones: ["Comisiones", feesTab],
    proyeccion: ["Proyección y simulador", projectionTab],
  };
  const body = h("div", { class: "stack" });
  const show = (id) => {
    try { localStorage.setItem("analysisTab", id); } catch { /* opcional */ }
    body.replaceChildren(...[tabs[id][1](data, navigate)].flat().filter(Boolean));
  };
  let first = "salud";
  try { const saved = localStorage.getItem("analysisTab"); if (tabs[saved]) first = saved; } catch { /* opcional */ }
  show(first);
  return h("div", { class: "stack" },
    pageHead("Análisis", data.complete
      ? `Medias sobre ${data.months_used} meses completos del periodo elegido`
      : "El periodo no contiene ningún mes completo: las medias son orientativas"),
    h("div", null, segmented(Object.entries(tabs).map(([id, [label]]) => [id, label]), first, show, "Sección de análisis")),
    body,
  );
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function level(value, good, fair, higherIsBetter = true) {
  if (value === null || value === undefined) return null;
  if (higherIsBetter ? value >= good : value <= good) return "good";
  return (higherIsBetter ? value >= fair : value <= fair) ? "warning" : "critical";
}

const LEVEL_TEXT = { good: "Bien", warning: "Mejorable", critical: "Atención" };

function indicator(label, value, lvl, explanation) {
  return h("div", { class: "card" },
    h("div", { class: "row spread" }, h("div", { class: "tile-label" }, label), lvl ? statusChip(lvl, LEVEL_TEXT[lvl]) : null),
    h("div", { class: "tile-value" }, value),
    h("div", { class: "tile-note" }, explanation));
}

function healthTab(data) {
  const i = data.indicators;
  const rule = data.rule_50_30_20;
  const months = i.runway_months === null ? "—" : `${i.runway_months.toFixed(1).replace(".", ",")} meses`;
  const meter = (label, amount, target, note, lowerIsBetter) => {
    const share = rule.income > 0 ? amount / rule.income : 0;
    const ok = lowerIsBetter ? share <= target : share >= target;
    return h("div", null,
      h("div", { class: "row spread" },
        h("div", { class: "row" }, h("strong", null, label), statusChip(ok ? "good" : "warning", ok ? "En objetivo" : lowerIsBetter ? "Por encima" : "Por debajo")),
        h("span", { class: "num" }, `${pct(share)} `, h("span", { class: "muted small" }, `(${eur0(amount)}/mes · objetivo ${pct(target)})`))),
      h("div", { class: "meter", style: { marginTop: "6px" } },
        h("div", { class: "meter-fill", style: { width: `${Math.min(Math.max(share, 0), 1) * 100}%` } }),
        h("div", { class: "meter-mark", style: { left: `${target * 100}%` }, title: `Objetivo: ${pct(target)}` })),
      note ? h("p", { class: "muted small", style: { marginTop: "4px" } }, note) : null);
  };
  const rolling = data.rolling_savings;
  return [
    h("div", { class: "grid cols-3" },
      indicator("Tasa de ahorro media", pct(i.savings_rate, 1), level(i.savings_rate, 0.2, 0.1), `Guardas ${eur0(i.avg_net)} de ${eur0(i.avg_income)} al mes. Referencia: 20 % o más.`),
      indicator("Colchón de seguridad", months, level(i.runway_months, 6, 3), "Meses que aguantarías sin ingresos con tu gasto medio. Referencia: 6 o más."),
      indicator("Peso de los gastos fijos", pct(i.fixed_ratio), level(i.fixed_ratio, 0.5, 0.7, false), `${eur0(i.fixed_monthly)}/mes en cargos periódicos. Te quedan ${eur0(i.free_after_fixed)} libres.`),
      tile("Ingreso medio mensual", eur0(i.avg_income), `Variación mensual típica: ${pct(i.income_volatility)}`),
      tile("Gasto medio mensual", eur0(i.avg_expense), `Variación mensual típica: ${pct(i.expense_volatility)}`),
      tile("Mejor y peor mes", i.best_month ? `${fmtMonth(i.best_month)} / ${fmtMonth(i.worst_month)}` : "—", "Según lo que quedó tras los gastos"),
    ),
    h("div", { class: "grid cols-2" },
      card("Regla 50/30/20", "Reparto de tus ingresos medios. La marca negra es el objetivo.",
        h("div", { class: "stack" },
          meter("Necesidades", rule.needs, 0.5, "Vivienda, recibos, supermercado, seguros, transporte…", true),
          meter("Caprichos", rule.wants, 0.3, "Restaurantes, ocio, compras, viajes…", true),
          meter("Ahorro", rule.saved, 0.2, "Lo que queda sin gastar, más lo destinado a inversión.", false),
          rule.unknown > 0 ? h("p", { class: "muted small" }, `Hay ${eur0(rule.unknown)}/mes sin clasificar. Clasificarlos afinará este reparto.`) : null)),
      chartCard({
        title: "Tasa de ahorro (media móvil de 3 meses)",
        subtitle: "Suaviza meses puntuales para ver la tendencia real",
        draw: (el, width) => drawLines(el, width, {
          xs: rolling.map((_, i) => i),
          xLabel: (_, n) => fmtMonthLong(rolling[n].month),
          tickLabel: (_, n) => fmtMonth(rolling[n].month),
          series: [{ label: "Tasa de ahorro", color: "var(--series-1)", values: rolling.map((r) => r.savings_rate === null ? null : Math.round(r.savings_rate * 1000) / 10) }],
          format: (v) => `${String(v).replace(".", ",")} %`,
          yFormat: (v) => `${v} %`,
          endLabel: true,
        }),
        table: {
          columns: [{ label: "Mes" }, { label: "Tasa móvil", right: true }],
          rows: rolling.map((r) => [fmtMonthLong(r.month), pct(r.savings_rate, 1)]),
        },
      })),
  ];
}

function evolutionTab(data, navigate) {
  const m = data.monthly;
  const groups = Object.keys(COLORS);
  const heat = data.heatmap;
  const heatMax = Math.max(...heat.rows.flatMap((r) => r.values), 1);
  const compact = (cents) => (cents >= 100000 ? `${(cents / 100000).toFixed(1).replace(".", ",")}k` : String(Math.round(cents / 100)));
  return [
    h("div", { class: "grid cols-2" },
      monthlyCard(m, navigate),
      chartCard({
        title: "Lo que queda cada mes",
        subtitle: "Ingresos menos gastos. Por debajo de cero, gastas más de lo que entra.",
        draw: (el, width) => drawColumns(el, width, {
          labels: m.map((r) => r.month),
          titleLabel: fmtMonthLong,
          series: [{ label: "Neto", color: "var(--series-1)", negativeColor: "var(--series-2)", values: m.map((r) => r.net) }],
        }),
        table: { columns: [{ label: "Mes" }, { label: "Neto", right: true }, { label: "Ahorro", right: true }], rows: m.map((r) => [fmtMonthLong(r.month), eur(r.net), pct(r.savings_rate)]) },
      })),
    chartCard({
      title: "Composición del gasto",
      subtitle: "Por tipo de gasto, mes a mes",
      legendItems: groups.map((g) => ({ color: COLORS[g], label: GROUP_LABELS[g] })),
      draw: (el, width) => drawColumns(el, width, {
        labels: m.map((r) => r.month), titleLabel: fmtMonthLong, stacked: true,
        series: groups.map((g) => ({ label: GROUP_LABELS[g], color: COLORS[g], values: m.map((r) => r[g]) })),
      }),
      table: {
        columns: [{ label: "Mes" }, ...groups.map((g) => ({ label: GROUP_LABELS[g], right: true }))],
        rows: m.map((r) => [fmtMonthLong(r.month), ...groups.map((g) => eur(r[g]))]),
      },
    }),
    card("Mapa de calor por categoría", "Euros gastados por mes. Más oscuro, más gasto. Sirve para localizar picos y estacionalidad.",
      h("div", { class: "table-wrap" }, h("table", { class: "heat" },
        h("thead", null, h("tr", null, h("th", null, "Categoría"), heat.months.map((k) => h("th", null, fmtMonth(k))))),
        h("tbody", null, heat.rows.map((row) => h("tr", null,
          h("td", null, row.name),
          row.values.map((v, n) => {
            const colors = heatColor(v / heatMax);
            return h("td", { class: "cell", title: `${row.name}, ${fmtMonthLong(heat.months[n])}: ${eur(v)}`, style: { background: colors.background, color: colors.color } }, v > 0 ? compact(v) : "·");
          }))))))),
  ];
}

function categoriesTab(data, navigate) {
  const state = { sort: "total", direction: -1 };
  const tableBox = h("div", { class: "table-wrap" });
  const columns = [
    ["name", "Categoría", false], ["total", "Total", true], ["avg", "Media/mes", true], ["median", "Mediana", true],
    ["volatility", "Variabilidad", true], ["trend", "Tendencia", true], ["yoy", "Vs. año anterior", true],
  ];
  const trendText = (t) => {
    if (t === null || t === undefined) return "—";
    if (Math.abs(t) < 0.02) return "Estable";
    return `${t > 0 ? "▲" : "▼"} ${pct(Math.abs(t))}/mes`;
  };
  const volText = (v) => {
    if (v === null || v === undefined) return "—";
    return `${v < 0.25 ? "Baja" : v < 0.75 ? "Media" : "Alta"} (${pct(v)})`;
  };
  const paint = () => {
    const rows = data.category_stats.slice().sort((a, b) => {
      const x = a[state.sort];
      const y = b[state.sort];
      if (typeof x === "string") return state.direction * x.localeCompare(y);
      return state.direction * ((x ?? -Infinity) - (y ?? -Infinity));
    });
    tableBox.replaceChildren(h("table", null,
      h("thead", null, h("tr", null,
        columns.map(([key, label, right]) => h("th", { class: right ? "right" : "" }, h("button", {
          type: "button",
          onclick: () => {
            if (state.sort === key) state.direction *= -1;
            else { state.sort = key; state.direction = key === "name" ? 1 : -1; }
            paint();
          },
        }, label, state.sort === key ? (state.direction > 0 ? " ↑" : " ↓") : ""))),
        h("th", null, "Meses"))),
      h("tbody", null, rows.map((c) => h("tr", null,
        h("td", null, h("a", { href: `#/movimientos?category=${c.id}` }, c.name)),
        h("td", { class: "right num nowrap" }, eur0(c.total)),
        h("td", { class: "right num nowrap" }, eur0(c.avg)),
        h("td", { class: "right num nowrap" }, eur0(c.median)),
        h("td", { class: "right num nowrap" }, volText(c.volatility)),
        h("td", { class: "right num nowrap" }, trendText(c.trend)),
        h("td", { class: "right num nowrap" }, signedPct(c.yoy)),
        h("td", null, sparkline(c.series)))))));
  };
  paint();
  const summary = data.pareto_summary;
  return [
    card("Estadísticas por categoría", "Variabilidad alta: difícil de presupuestar. Tendencia: cuánto crece o baja al mes respecto a su media.", tableBox),
    h("div", { class: "grid cols-2" },
      card("Concentración del gasto", summary.merchants
        ? `${summary.for_80pct} de tus ${summary.merchants} comercios concentran el 80 % del gasto`
        : "Sin gastos en este periodo",
        h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, "Comercio"), h("th", { class: "right" }, "Veces"), h("th", { class: "right" }, "Total"), h("th", { class: "right" }, "Acumulado"))),
          h("tbody", null, data.pareto.map((m) => h("tr", null,
            h("td", null, h("a", { href: `#/movimientos?q=${encodeURIComponent(m.merchant)}` }, m.merchant)),
            h("td", { class: "right num" }, m.count),
            h("td", { class: "right num nowrap" }, eur0(m.amount)),
            h("td", { class: "right num" }, pct(m.cumulative))))))),),
      card("Gasto por día de la semana", "Media diaria de compras y ocio, sin recibos domiciliados",
        bars(data.weekday.map((d) => ({ name: d.day, amount: d.avg }))))),
  ];
}

function recurringTable(items) {
  return h("div", { class: "table-wrap" }, h("table", null,
    h("thead", null, h("tr", null, h("th", null, "Concepto"), h("th", null, "Categoría"), h("th", null, "Frecuencia"), h("th", { class: "right" }, "Último"), h("th", { class: "right" }, "Al año"), h("th", null, "Próximo"), h("th", null, "Historial"))),
    h("tbody", null, items.map((r) => h("tr", null,
      h("td", null, h("a", { href: `#/movimientos?q=${encodeURIComponent(r.merchant)}` }, r.merchant)),
      h("td", { class: "muted" }, r.category),
      h("td", { class: "muted" }, r.frequency),
      h("td", { class: "right num nowrap" }, eur(r.last_cents)),
      h("td", { class: "right num nowrap" }, eur0(r.annual_cents)),
      h("td", { class: "num nowrap" }, fmtDate(r.next_date)),
      h("td", null, sparkline(r.amounts)))))));
}

function recurringTab(data) {
  const r = data.recurring;
  const subscriptions = r.out.filter((x) => x.category_id === "suscripciones");
  const subscriptionsYear = subscriptions.reduce((sum, x) => sum + x.annual_cents, 0);
  return [
    h("div", { class: "grid cols-3" },
      tile("Cargos fijos al mes", eur0(r.monthly_out), plural(r.out.length, "cargo que se repite", "cargos que se repiten")),
      tile("Ingresos periódicos al mes", eur0(r.monthly_in), plural(r.in.length, "ingreso que se repite", "ingresos que se repiten")),
      tile("Suscripciones al año", eur0(subscriptionsYear), plural(subscriptions.length, "activa", "activas"))),
    card("Cargos periódicos activos", "Detectados solos por su regularidad de fecha e importe",
      r.out.length ? recurringTable(r.out) : h("p", { class: "muted" }, "Se necesitan al menos tres repeticiones para detectar un cargo periódico.")),
    r.in.length ? card("Ingresos periódicos", null, recurringTable(r.in)) : null,
    r.inactive.length ? card("Dejaron de cobrarse", "Ya no aparecen con su frecuencia habitual", recurringTable(r.inactive)) : null,
  ];
}

function feesTab(data) {
  const fees = data.fees;
  if (!fees.count) return [card(null, null, emptyState("Sin comisiones en este periodo", "Ningún movimiento se ha clasificado como comisión bancaria."))];
  const years = fees.by_year;
  return [
    h("div", { class: "grid cols-3" },
      tile("Comisiones pagadas", eur(fees.total), `${fees.count} cargos en el periodo`),
      tile("Devueltas por el banco", eur(fees.refunded), "Devoluciones clasificadas como comisión"),
      tile("Por año, de media", eur0(fees.total / Math.max(years.length, 1)), `${years.length} ${years.length === 1 ? "año" : "años"} con comisiones`)),
    h("div", { class: "grid cols-2" },
      chartCard({
        title: "Comisiones por año",
        draw: (el, width) => drawColumns(el, width, {
          labels: years.map((y) => y.year), tickLabel: (y) => y,
          series: [{ label: "Comisiones", color: "var(--series-1)", values: years.map((y) => y.amount) }],
        }),
        table: { columns: [{ label: "Año" }, { label: "Total", right: true }], rows: years.map((y) => [y.year, eur(y.amount)]) },
      }),
      card("Por concepto", "Muchas se pueden evitar: pregunta a tu banco por las condiciones para quedar exento",
        h("div", { class: "table-wrap" }, h("table", null,
          h("thead", null, h("tr", null, h("th", null, "Concepto"), h("th", { class: "right" }, "Veces"), h("th", { class: "right" }, "Total"), h("th", null, "Última"))),
          h("tbody", null, fees.by_concept.map((f) => h("tr", null,
            h("td", null, f.concept), h("td", { class: "right num" }, f.count), h("td", { class: "right num nowrap" }, eur(f.amount)), h("td", { class: "num nowrap" }, fmtDate(f.last))))))))),
  ];
}

function projectionTab(data) {
  const f = data.forecast;
  return [forecastCard(f, data), simulatorCard(data)].filter(Boolean);
}

function forecastCard(f, data) {
  if (!f) return card("Proyección de saldo", null, h("p", { class: "muted" }, "Hacen falta al menos 3 meses completos con saldo para proyectar."));
  const past = f.history;
  const months = [...past.map((p) => p.month), ...f.points.map((p) => p.month)];
  const n = past.length;
  const hist = [...past.map((p) => p.balance), ...f.points.map(() => null)];
  const expected = [...past.map((_, i) => (i === n - 1 ? past[i].balance : null)), ...f.points.map((p) => p.expected)];
  const low = [...past.map((_, i) => (i === n - 1 ? past[i].balance : null)), ...f.points.map((p) => p.low)];
  const high = [...past.map((_, i) => (i === n - 1 ? past[i].balance : null)), ...f.points.map((p) => p.high)];
  const end = f.points[f.points.length - 1];
  return chartCard({
    title: "Proyección de saldo a 6 meses",
    subtitle: `Extrapola tu resultado medio (${eur0(f.avg_net)}/mes en ${f.months_used} meses). La banda es el margen habitual: no es una predicción exacta.`,
    legendItems: [{ color: "var(--series-1)", label: "Histórico", type: "line" }, { label: "Previsión", type: "dash" }, { color: "var(--series-1)", label: "Margen habitual" }],
    draw: (el, width) => drawLines(el, width, {
      xs: months.map((_, i) => i),
      xLabel: (_, i) => fmtMonthLong(months[i]),
      tickLabel: (_, i) => fmtMonth(months[i]),
      series: [
        { label: "Histórico", color: "var(--series-1)", values: hist },
        { label: "Previsión", color: "var(--ink-3)", values: expected, dashed: true },
      ],
      band: { low, high, color: "var(--series-1)", label: "Margen" },
      zeroFloor: true,
    }),
    table: {
      columns: [{ label: "Mes" }, { label: "Previsión", right: true }, { label: "Mínimo", right: true }, { label: "Máximo", right: true }],
      rows: f.points.map((p) => [fmtMonthLong(p.month), eur(p.expected), eur(p.low), eur(p.high)]),
    },
    footer: h("p", { class: "muted small", style: { marginTop: "6px" } }, `En ${fmtMonthLong(end.month)}: unos ${eur0(end.expected)}, entre ${eur0(end.low)} y ${eur0(end.high)}.`),
  });
}

function simulatorCard(data) {
  const ind = data.indicators;
  const categories = data.category_stats.filter((c) => c.avg > 0 && !["vivienda", "impuestos", "sin_clasificar"].includes(c.id)).slice(0, 8);
  const cuts = new Map(categories.map((c) => [c.id, 0]));
  const result = h("div", { class: "stack" });
  const horizon = 36;
  const goal = h("input", { type: "number", min: 0, step: 100, value: Math.max(Math.round((ind.avg_expense * 6) / 100), 1000), style: { width: "110px" }, "aria-label": "Objetivo de ahorro en euros", oninput: () => paint() });
  const yieldInput = h("input", { type: "number", min: 0, max: 30, step: 0.5, value: 2, style: { width: "80px" }, "aria-label": "Rentabilidad anual en porcentaje", oninput: () => paint() });

  const projection = (monthly) => {
    const rate = Math.max(Number(yieldInput.value) || 0, 0) / 100 / 12;
    const points = [];
    let value = ind.balance ?? 0;
    for (let i = 0; i <= horizon; i += 1) {
      points.push(Math.round(value));
      value = (value > 0 ? value * (1 + rate) : value) + monthly;
    }
    return points;
  };
  const monthsToGoal = (monthly) => {
    const rate = Math.max(Number(yieldInput.value) || 0, 0) / 100 / 12;
    const target = (Number(goal.value) || 0) * 100;
    let value = ind.balance ?? 0;
    if (value >= target) return 0;
    if (monthly <= 0 && rate === 0) return null;
    for (let i = 1; i <= 1200; i += 1) {
      value = (value > 0 ? value * (1 + rate) : value) + monthly;
      if (value >= target) return i;
    }
    return null;
  };
  const describeMonths = (m) => {
    if (m === null) return "No se alcanza a este ritmo";
    if (m === 0) return "Ya lo tienes";
    if (m < 12) return `${m} ${m === 1 ? "mes" : "meses"}`;
    const years = Math.floor(m / 12);
    const rest = m % 12;
    return `${years} ${years === 1 ? "año" : "años"}${rest ? ` y ${rest} ${rest === 1 ? "mes" : "meses"}` : ""}`;
  };

  const chartBox = h("div", { class: "chart", style: { minHeight: "220px" } });
  let lastBase = [];
  let lastScenario = [];
  const drawChart = () => {
    const width = Math.round(chartBox.clientWidth);
    if (!width) return;
    const xs = lastBase.map((_, i) => i);
    drawLines(chartBox, width, {
      xs,
      xLabel: (_, i) => (i === 0 ? "Hoy" : `Dentro de ${i} ${i === 1 ? "mes" : "meses"}`),
      tickLabel: (_, i) => (i === 0 ? "Hoy" : `${i} m`),
      series: [
        { label: "Como ahora", color: "var(--series-2)", values: lastBase },
        { label: "Con los recortes", color: "var(--series-1)", values: lastScenario },
      ],
      height: 220,
      zeroFloor: true,
      tickEvery: 6,
    });
  };
  new ResizeObserver(drawChart).observe(chartBox);

  const paint = () => {
    const saving = categories.reduce((sum, c) => sum + (c.avg * cuts.get(c.id)) / 100, 0);
    const scenarioNet = ind.avg_net + saving;
    const rate = ind.avg_income > 0 ? scenarioNet / ind.avg_income : null;
    const baseMonths = monthsToGoal(ind.avg_net);
    const newMonths = monthsToGoal(scenarioNet);
    lastBase = projection(ind.avg_net);
    lastScenario = projection(scenarioNet);
    result.replaceChildren(h("div", { class: "grid cols-3" },
      miniStat("Ahorro extra", `${eur0(saving)}/mes`, `${eur0(saving * 12)} al año`),
      miniStat("Nueva tasa de ahorro", pct(rate), `Ahora: ${pct(ind.savings_rate)}`),
      miniStat("Tiempo hasta el objetivo", describeMonths(newMonths), `Ahora: ${describeMonths(baseMonths)}`)));
    drawChart();
  };

  const sliders = categories.map((c) => {
    const value = h("span", { class: "num right" }, "0 %");
    const saved = h("span", { class: "num right muted small" }, eur0(0));
    return h("div", { class: "slider-row" },
      h("span", { class: "bar-name", title: c.name }, c.name),
      h("input", {
        type: "range", min: 0, max: 100, step: 5, value: 0, "aria-label": `Recorte en ${c.name}`,
        oninput: (event) => {
          const v = Number(event.target.value);
          cuts.set(c.id, v);
          value.textContent = `${v} %`;
          saved.textContent = eur0((c.avg * v) / 100);
          paint();
        },
      }),
      value, saved);
  });
  paint();

  return h("section", { class: "card" },
    h("div", { class: "card-head" }, h("div", null, h("h2", null, "Simulador: ¿y si recorto?"),
      h("p", null, "Mueve las barras para ver cuánto antes llegarías a tu objetivo. Nada de esto cambia tus datos."))),
    h("div", { class: "grid cols-2" },
      h("div", { class: "stack" },
        h("div", { class: "row" },
          h("label", { class: "field" }, "Objetivo de ahorro (€)", goal),
          h("label", { class: "field" }, "Rentabilidad anual (%)", yieldInput)),
        h("div", { class: "stack", style: { gap: "8px" } }, sliders)),
      h("div", { class: "stack" }, result,
        h("div", null, h("div", { class: "legend" },
          h("span", null, h("i", { class: "line", style: { background: "var(--series-2)" } }), "Como ahora"),
          h("span", null, h("i", { class: "line", style: { background: "var(--series-1)" } }), "Con los recortes")),
        chartBox))),
  );
}

const miniStat = (label, value, note) => h("div", null,
  h("div", { class: "tile-label" }, label), h("div", { class: "tile-value", style: { fontSize: "20px" } }, value), h("div", { class: "tile-note" }, note));
