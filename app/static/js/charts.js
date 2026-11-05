// Gráficos SVG propios, sin dependencias externas (la aplicación funciona sin internet).
// Marcas finas, cuadrícula discreta, un solo eje Y, leyenda con 2 o más series, tooltip y vista de tabla.
import { eur, eur0, h, s, fmtMonth } from "./ui.js";

const tooltipEl = () => document.getElementById("tooltip");

function showTooltip(x, y, title, rows) {
  const el = tooltipEl();
  el.replaceChildren(
    h("div", { class: "t-title" }, title),
    ...rows.map((r) => h("div", { class: "t-row" },
      r.color ? h("i", { class: "t-key", style: { background: r.color } }) : null,
      h("b", null, r.value), r.label ? h("span", null, r.label) : null)),
  );
  el.hidden = false;
  const box = el.getBoundingClientRect();
  const left = Math.min(Math.max(8, x + 14), window.innerWidth - box.width - 8);
  const top = Math.min(Math.max(8, y - box.height - 10), window.innerHeight - box.height - 8);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
}

export const hideTooltip = () => { tooltipEl().hidden = true; };

function compact(cents) {
  const v = cents / 100;
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(1).replace(".", ",")} M`;
  if (a >= 1e4) return `${Math.round(v / 1e3)} k`;
  return new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0, useGrouping: "always" }).format(v);
}

function niceTicks(min, max, count = 4) {
  if (min === max) { max = min + 100; }
  const span = max - min;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((v) => v >= raw);
  const low = Math.floor(min / step) * step;
  const high = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = low; v <= high + step / 2; v += step) ticks.push(Math.round(v));
  return ticks;
}

const MARGIN = { top: 12, right: 12, bottom: 24 };

function frame(width, height, values, tickFormat = compact) {
  const min = Math.min(0, ...values);
  const max = Math.max(...values, 1);
  const ticks = niceTicks(min, max);
  const left = Math.max(...ticks.map((t) => tickFormat(t).length)) * 6.6 + 8;
  const plot = { x0: left, x1: width - MARGIN.right, y0: MARGIN.top, y1: height - MARGIN.bottom };
  const low = ticks[0];
  const high = ticks[ticks.length - 1];
  const y = (v) => plot.y1 - ((v - low) / (high - low)) * (plot.y1 - plot.y0);
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img" });
  for (const tick of ticks) {
    svg.append(
      s("line", { x1: plot.x0, x2: plot.x1, y1: y(tick), y2: y(tick), stroke: tick === 0 ? "var(--axis)" : "var(--grid)", "stroke-width": 1 }),
      s("text", { x: plot.x0 - 8, y: y(tick) + 4, "text-anchor": "end" }, tickFormat(tick)),
    );
  }
  return { svg, plot, y };
}

export function legend(items) {
  return h("div", { class: "legend" }, items.map((item) =>
    h("span", null, h("i", { class: item.type ?? "", style: item.type === "dash" ? {} : { background: item.color } }), item.label)));
}

function thin(labels, available, approx = 44) {
  const every = Math.max(1, Math.ceil((labels.length * approx) / available));
  return (i) => (labels.length - 1 - i) % every === 0;
}

/**
 * Líneas sobre eje temporal.
 * xs: marcas de tiempo; series: [{label, color, values, dashed, area}]; band: {low, high, color}
 */
export function drawLines(el, width, { xs, xLabel, tickLabel, series, band = null, height = 250, format = eur, endLabel = false, yFormat = compact, zeroFloor = false, tickEvery = null }) {
  el.replaceChildren();
  if (xs.length === 0) return;
  const values = series.flatMap((x) => x.values.filter((v) => v !== null));
  if (band) values.push(...band.low.filter((v) => v !== null), ...band.high.filter((v) => v !== null));
  const { svg, plot, y } = frame(width, height, zeroFloor ? [0, ...values] : values, yFormat);
  const tMin = xs[0];
  const tMax = xs[xs.length - 1];
  const x = (t) => (tMax === tMin ? (plot.x0 + plot.x1) / 2 : plot.x0 + ((t - tMin) / (tMax - tMin)) * (plot.x1 - plot.x0));

  // Etiquetas desde el final hacia atrás, saltando las que pisarían a la anterior.
  const labelIndexes = [];
  let previousX = Infinity;
  for (let i = xs.length - 1; i >= 0; i -= 1) {
    if (tickEvery ? i % tickEvery === 0 : previousX - x(xs[i]) >= 62) { labelIndexes.push(i); previousX = x(xs[i]); }
  }
  for (const i of labelIndexes) {
    const anchor = x(xs[i]) > plot.x1 - 24 ? "end" : x(xs[i]) < plot.x0 + 24 ? "start" : "middle";
    svg.append(s("text", { x: x(xs[i]), y: height - 6, "text-anchor": anchor }, tickLabel(xs[i], i)));
  }

  if (band) {
    const idx = xs.map((_, i) => i).filter((i) => band.low[i] !== null);
    if (idx.length > 1) {
      const top = idx.map((i) => `${x(xs[i])},${y(band.high[i])}`);
      const bottom = idx.slice().reverse().map((i) => `${x(xs[i])},${y(band.low[i])}`);
      svg.append(s("polygon", { points: [...top, ...bottom].join(" "), fill: band.color, "fill-opacity": 0.14 }));
    }
  }
  for (const line of series) {
    const segments = [];
    let current = [];
    line.values.forEach((v, i) => {
      if (v === null) { if (current.length) segments.push(current); current = []; } else current.push([x(xs[i]), y(v)]);
    });
    if (current.length) segments.push(current);
    for (const seg of segments) {
      const points = seg.map((p) => p.join(",")).join(" ");
      if (line.area && seg.length > 1) {
        svg.append(s("polygon", {
          points: `${seg[0][0]},${plot.y1} ${points} ${seg[seg.length - 1][0]},${plot.y1}`,
          fill: line.color, "fill-opacity": 0.1,
        }));
      }
      svg.append(s("polyline", {
        points, fill: "none", stroke: line.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
        "stroke-dasharray": line.dashed ? "2 5" : null,
      }));
    }
  }
  if (endLabel) {
    for (const line of series) {
      const i = line.values.length - 1;
      if (line.values[i] === null || line.values[i] === undefined) continue;
      svg.append(s("circle", { cx: x(xs[i]), cy: y(line.values[i]), r: 4, fill: line.color, stroke: "var(--surface)", "stroke-width": 2 }));
    }
  }

  const hair = s("line", { y1: plot.y0, y2: plot.y1, stroke: "var(--axis)", "stroke-width": 1, visibility: "hidden" });
  const dots = series.map((line) => s("circle", { r: 4, fill: line.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }));
  svg.append(hair, ...dots);

  const overlay = s("rect", {
    class: "overlay", x: plot.x0, y: plot.y0, width: plot.x1 - plot.x0, height: plot.y1 - plot.y0, fill: "transparent", tabindex: 0,
    "aria-label": "Usa las flechas para recorrer los datos",
  });
  let active = xs.length - 1;
  const show = (i, clientX, clientY) => {
    active = i;
    const px = x(xs[i]);
    hair.setAttribute("x1", px);
    hair.setAttribute("x2", px);
    hair.setAttribute("visibility", "visible");
    series.forEach((line, n) => {
      const v = line.values[i];
      if (v === null || v === undefined) { dots[n].setAttribute("visibility", "hidden"); return; }
      dots[n].setAttribute("cx", px);
      dots[n].setAttribute("cy", y(v));
      dots[n].setAttribute("visibility", "visible");
    });
    const rows = series.filter((line) => line.values[i] !== null && line.values[i] !== undefined)
      .map((line) => ({ color: line.color, value: format(line.values[i]), label: series.length > 1 ? line.label : "" }));
    if (band && band.low[i] !== null) rows.push({ value: `${format(band.low[i])} a ${format(band.high[i])}`, label: band.label });
    showTooltip(clientX, clientY, xLabel(xs[i], i), rows);
  };
  const hide = () => {
    hair.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    hideTooltip();
  };
  overlay.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * width;
    let best = 0;
    let bestDistance = Infinity;
    xs.forEach((t, i) => {
      const distance = Math.abs(x(t) - px);
      if (distance < bestDistance) { best = i; bestDistance = distance; }
    });
    show(best, event.clientX, event.clientY);
  });
  overlay.addEventListener("pointerleave", hide);
  overlay.addEventListener("blur", hide);
  const focusAt = (i) => {
    const rect = svg.getBoundingClientRect();
    show(i, rect.left + (x(xs[i]) / width) * rect.width, rect.top + 30);
  };
  overlay.addEventListener("focus", () => focusAt(active));
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      focusAt(Math.min(xs.length - 1, Math.max(0, active + (event.key === "ArrowLeft" ? -1 : 1))));
    }
  });
  svg.append(overlay);
  el.append(svg);
}

function barPath(x, y0, y1, w, r) {
  // Extremo de datos redondeado (4px) y lado de la base recto.
  const height = Math.abs(y1 - y0);
  const radius = Math.min(r, height, w / 2);
  if (y1 <= y0) {
    return `M${x},${y0}V${y1 + radius}Q${x},${y1} ${x + radius},${y1}H${x + w - radius}Q${x + w},${y1} ${x + w},${y1 + radius}V${y0}Z`;
  }
  return `M${x},${y0}V${y1 - radius}Q${x},${y1} ${x + radius},${y1}H${x + w - radius}Q${x + w},${y1} ${x + w},${y1 - radius}V${y0}Z`;
}

/** Columnas agrupadas o apiladas. labels: claves; series: [{label, color, values}] */
export function drawColumns(el, width, { labels, tickLabel = fmtMonth, titleLabel = tickLabel, series, stacked = false, height = 250, format = eur, onClick = null }) {
  el.replaceChildren();
  if (labels.length === 0) return;
  const totals = labels.map((_, i) => series.reduce((sum, line) => sum + Math.max(line.values[i], 0), 0));
  const values = stacked ? totals : series.flatMap((line) => line.values);
  const { svg, plot, y } = frame(width, height, values);
  const bandWidth = (plot.x1 - plot.x0) / labels.length;
  const showLabel = thin(labels, plot.x1 - plot.x0);
  labels.forEach((label, i) => {
    const bandX = plot.x0 + i * bandWidth;
    const group = s("g", { class: "band-hit", tabindex: 0, role: onClick ? "button" : null });
    group.append(s("rect", { class: "band-wash", x: bandX, y: plot.y0, width: bandWidth, height: plot.y1 - plot.y0, rx: 4, fill: "transparent" }));
    if (stacked) {
      const barWidth = Math.min(24, bandWidth * 0.68);
      const barX = bandX + (bandWidth - barWidth) / 2;
      let base = 0;
      const visible = series.filter((line) => line.values[i] > 0);
      visible.forEach((line, n) => {
        const top = base + line.values[i];
        const y0 = y(base) - (n ? 1 : 0);
        const y1 = y(top) + (n < visible.length - 1 ? 1 : 0);
        if (y0 - y1 > 0.5) {
          group.append(s("path", { d: barPath(barX, y0, y1, barWidth, n === visible.length - 1 ? 4 : 0), fill: line.color }));
        }
        base = top;
      });
    } else {
      const gap = series.length > 1 ? 2 : 0;
      const barWidth = Math.min(24, (bandWidth * 0.72 - gap * (series.length - 1)) / series.length);
      const groupWidth = barWidth * series.length + gap * (series.length - 1);
      const start = bandX + (bandWidth - groupWidth) / 2;
      series.forEach((line, n) => {
        const value = line.values[i];
        if (!value) return;
        group.append(s("path", { d: barPath(start + n * (barWidth + gap), y(0), y(value), barWidth, 4), fill: line.negativeColor && value < 0 ? line.negativeColor : line.color }));
      });
    }
    const tip = (x, top) => {
      const rows = series.map((line) => ({ color: line.color, value: format(line.values[i]), label: series.length > 1 ? line.label : "" })).reverse();
      if (stacked) rows.unshift({ value: format(totals[i]), label: "Total" });
      showTooltip(x, top, titleLabel(label), rows);
    };
    group.addEventListener("pointermove", (event) => tip(event.clientX, event.clientY));
    group.addEventListener("pointerleave", hideTooltip);
    group.addEventListener("focus", () => {
      const box = group.getBoundingClientRect();
      tip(box.left + box.width / 2, box.top + 30);
    });
    group.addEventListener("blur", hideTooltip);
    if (onClick) {
      group.style.cursor = "pointer";
      group.addEventListener("click", () => onClick(label));
      group.addEventListener("keydown", (event) => { if (event.key === "Enter") onClick(label); });
    }
    svg.append(group);
    if (showLabel(i)) svg.append(s("text", { x: bandX + bandWidth / 2, y: height - 6, "text-anchor": "middle" }, tickLabel(label)));
  });
  el.append(svg);
}

const SLOTS = 8;
const OTHER = { color: "var(--series-other)", id: "__other" };
const isDarkTheme = () => document.documentElement.dataset.theme === "dark"
  || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);

/**
 * Prepara las porciones: las `max` mayores y el resto en "Otros".
 * El color sigue a la categoría, no a su posición: si cambias de periodo, Supermercados sigue del mismo color.
 * preferred: {idCategoría: hueco de la paleta}. Si dos coinciden, la segunda toma el siguiente libre.
 */
export function pieSlices(items, preferred = {}, max = 6) {
  const ranked = items.slice().sort((a, b) => b.amount - a.amount);
  const shown = ranked.length > max + 1 ? ranked.slice(0, max) : ranked;
  const rest = ranked.slice(shown.length);
  const used = new Set();
  const slotOf = {};
  const stable = shown.slice().sort((a, b) => (a.id < b.id ? -1 : 1));
  for (const item of stable.filter((i) => preferred[i.id])) {
    if (!used.has(preferred[item.id])) { slotOf[item.id] = preferred[item.id]; used.add(preferred[item.id]); }
  }
  for (const item of stable) {
    if (slotOf[item.id]) continue;
    let slot = 1;
    while (used.has(slot) && slot < SLOTS) slot += 1;
    slotOf[item.id] = slot;
    used.add(slot);
  }
  const total = items.reduce((sum, i) => sum + i.amount, 0);
  const slices = shown.map((item) => ({ ...item, slot: slotOf[item.id], color: `var(--series-${slotOf[item.id]})`, share: item.amount / total }));
  if (rest.length) {
    const amount = rest.reduce((sum, i) => sum + i.amount, 0);
    slices.push({ ...OTHER, name: `Otras ${rest.length} categorías`, amount, share: amount / total, includes: rest.map((i) => i.name) });
  }
  return slices;
}

export function sharePercent(share) {
  if (share > 0 && share < 0.001) return "<0,1 %";
  return `${(share * 100).toFixed(share < 0.1 ? 1 : 0).replace(".", ",")} %`;
}

function pieLabelColor(slice) {
  const dark = isDarkTheme();
  if (slice.slot === 4 && !dark) return "#0b0b0b"; // el amarillo claro no admite texto blanco
  if (slice.id === OTHER.id && !dark) return "#0b0b0b";
  return "#ffffff";
}

function piePath(cx, cy, r, from, to) {
  const point = (angle) => `${cx + r * Math.cos(angle)} ${cy + r * Math.sin(angle)}`;
  return `M${cx} ${cy}L${point(from)}A${r} ${r} 0 ${to - from > Math.PI ? 1 : 0} 1 ${point(to)}Z`;
}

/** Gráfico de pastel con porcentaje en las porciones grandes y leyenda con nombre, importe y peso. */
export function drawPie(el, width, { slices, format = eur, onClick = null }) {
  el.replaceChildren();
  const narrow = width < 430;
  const size = Math.min(narrow ? width : width * 0.42, 232);
  const center = size / 2;
  const radius = center - 2;
  const svg = s("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: "group", style: { width: `${size}px` } });
  const percent = sharePercent;
  const describe = (slice) => `${slice.name}: ${format(slice.amount)} (${percent(slice.share)})`;
  const clickable = (slice) => onClick && slice.id !== OTHER.id;

  let angle = -Math.PI / 2;
  const labels = [];
  for (const slice of slices) {
    const sweep = slice.share * Math.PI * 2;
    const attributes = {
      class: "pie-slice", fill: slice.color, stroke: "var(--surface)", "stroke-width": 2, "stroke-linejoin": "round",
      tabindex: 0, role: clickable(slice) ? "button" : "img", "aria-label": describe(slice),
    };
    const mark = slices.length === 1
      ? s("circle", { ...attributes, cx: center, cy: center, r: radius })
      : s("path", { ...attributes, d: piePath(center, center, radius, angle, angle + sweep) });
    const tip = (x, y) => {
      const rows = [{ color: slice.color, value: format(slice.amount), label: percent(slice.share) }];
      if (slice.includes) rows.push({ value: "", label: slice.includes.join(", ") });
      showTooltip(x, y, slice.name, rows);
    };
    mark.addEventListener("pointermove", (event) => tip(event.clientX, event.clientY));
    mark.addEventListener("pointerleave", hideTooltip);
    mark.addEventListener("focus", () => {
      const box = mark.getBoundingClientRect();
      tip(box.left + box.width / 2, box.top + box.height / 2);
    });
    mark.addEventListener("blur", hideTooltip);
    if (clickable(slice)) {
      mark.style.cursor = "pointer";
      mark.addEventListener("click", () => onClick(slice));
      mark.addEventListener("keydown", (event) => { if (event.key === "Enter") onClick(slice); });
    }
    svg.append(mark);
    // Solo se rotula lo que cabe con holgura; el resto queda en la leyenda.
    if (slice.share >= 0.07) {
      const middle = angle + sweep / 2;
      const distance = slices.length === 1 ? 0 : radius * 0.64;
      labels.push(s("text", {
        x: center + distance * Math.cos(middle), y: center + distance * Math.sin(middle) + 4, "text-anchor": "middle",
        "pointer-events": "none", style: { fill: pieLabelColor(slice), fontWeight: 620, fontSize: "12px" },
      }, percent(slice.share)));
    }
    angle += sweep;
  }
  svg.append(...labels);
  const legendRows = slices.map((slice) => {
    const content = [
      h("i", { style: { background: slice.color } }),
      h("span", { class: "pie-name", title: slice.includes ? slice.includes.join(", ") : null }, slice.name),
      h("span", { class: "num small" }, eur0(slice.amount)),
      h("span", { class: "num small faint" }, percent(slice.share)),
    ];
    return clickable(slice)
      ? h("button", { type: "button", class: "pie-legend-row", title: `Ver movimientos de ${slice.name}`, onclick: () => onClick(slice) }, content)
      : h("div", { class: "pie-legend-row" }, content);
  });
  el.append(h("div", { class: `pie${narrow ? " narrow" : ""}` }, svg, h("div", { class: "pie-legend" }, legendRows)));
}

/** Tarjeta con gráfico responsive y alternativa en tabla. draw(el, width) repinta al cambiar el ancho. */
export function chartCard({ title, subtitle, legendItems, draw, table, height = 250, footer = null }) {
  const chart = h("div", { class: "chart", style: { minHeight: `${height}px` } });
  const tableBox = h("div", { class: "table-wrap", hidden: true });
  let showing = false;
  const toggle = h("button", { type: "button", class: "ghost mini" }, "Ver tabla");
  toggle.addEventListener("click", () => {
    showing = !showing;
    toggle.textContent = showing ? "Ver gráfico" : "Ver tabla";
    chart.hidden = showing;
    tableBox.hidden = !showing;
    if (showing && !tableBox.firstChild) tableBox.append(renderTable(table));
    if (!showing) paint();
  });
  let lastWidth = 0;
  const paint = () => {
    const width = Math.round(chart.clientWidth);
    if (width > 0) { lastWidth = width; draw(chart, width); }
  };
  new ResizeObserver(() => {
    if (Math.round(chart.clientWidth) !== lastWidth) paint();
  }).observe(chart);
  return h("section", { class: "card" },
    h("div", { class: "card-head" },
      h("div", null, h("h2", null, title), subtitle ? h("p", null, subtitle) : null),
      table ? toggle : null),
    legendItems ? legend(legendItems) : null,
    chart,
    table ? tableBox : null,
    footer,
  );
}

export function renderTable({ columns, rows }) {
  return h("table", null,
    h("thead", null, h("tr", null, columns.map((c) => h("th", { class: c.right ? "right" : "" }, c.label)))),
    h("tbody", null, rows.map((row) => h("tr", null, row.map((cell, i) => h("td", { class: columns[i].right ? "right num" : "" }, cell))))),
  );
}

export function sparkline(values, { width = 90, height = 22 } = {}) {
  const max = Math.max(...values, 1);
  const stepX = values.length > 1 ? (width - 6) / (values.length - 1) : 0;
  const points = values.map((v, i) => `${3 + i * stepX},${height - 3 - (Math.max(v, 0) / max) * (height - 6)}`).join(" ");
  const last = values.length - 1;
  return s("svg", { viewBox: `0 0 ${width} ${height}`, width, height, "aria-hidden": "true" },
    s("polyline", { points, fill: "none", stroke: "var(--ink-3)", "stroke-width": 1.5, "stroke-linejoin": "round" }),
    values.length ? s("circle", { cx: 3 + last * stepX, cy: height - 3 - (Math.max(values[last], 0) / max) * (height - 6), r: 2.5, fill: "var(--series-1)" }) : null);
}

/** Color de un paso de la rampa azul para una magnitud entre 0 y 1. Texto oscuro o claro según el fondo. */
export function heatColor(t) {
  const ramp = ["#e6effa", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"];
  const dark = document.documentElement.dataset.theme === "dark"
    || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  if (t <= 0) return { background: "transparent", color: "var(--ink-3)" };
  const index = Math.min(ramp.length - 1, Math.floor(Math.sqrt(t) * ramp.length));
  const colors = dark ? ["#1a2a40", "#1d3a60", "#1c5cab", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"] : ramp;
  const lightText = dark ? index <= 4 : index >= 4;
  return { background: colors[index], color: lightText ? "#ffffff" : "#0b0b0b" };
}
