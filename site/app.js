"use strict";

const ui = Object.fromEntries([...document.querySelectorAll("[id]")].map((node) => [node.id, node]));
const metrics = { first: "1.º", second: "2.º", third: "3.º", podium: "Podio" };
const positions = ["first", "second", "third"];
const horizons = { fp1: "Antes de Libres 1", pre_race: "Previa de carrera · T−30" };
const clock = new Intl.DateTimeFormat("es-ES", {
  day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  second: "2-digit", timeZone: "UTC", hourCycle: "h23",
});
const percent = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 2 });
const compare = new Intl.Collator("es-ES");
const sortButtons = [...document.querySelectorAll("[data-metric]")];
const view = { doc: null, cut: null, metric: "first" };

function text(id, value) {
  ui[id].textContent = value;
}

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function utcLabel(value) {
  return `${clock.format(new Date(value))} UTC`;
}

function showTime(id, value) {
  ui[id].dateTime = value;
  text(id, utcLabel(value));
}

function isText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isProbability(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

function selectCut(doc) {
  const event = doc?.event;
  const fp1 = Date.parse(event?.fp1_utc);
  const t30 = Date.parse(event?.pre_race_utc);
  const race = Date.parse(event?.race_utc);
  if (doc?.mode !== "forecast" || !isText(event?.name) || !isText(event.id) || !isText(doc.source)
      || !(fp1 < t30 && race - t30 === 1_800_000) || !Array.isArray(doc.predictions)) {
    throw new Error("No hay un documento de proyección previa válido.");
  }
  const cuts = new Map();
  for (const cut of doc.predictions) {
    const at = Date.parse(cut.cutoff_utc);
    const issued = Date.parse(cut.published_utc);
    const validTime = cut.horizon === "fp1" ? issued < fp1 : at === t30 && issued < race;
    const drivers = cut.drivers;
    const order = cut.podium?.drivers;
    if (!Object.hasOwn(horizons, cut.horizon) || cut.origin !== "published" || cut.lap !== null
        || !(at <= issued && validTime) || cuts.has(cut.horizon)
        || !isText(cut.model_version) || !isText(cut.method)
        || !Array.isArray(drivers) || !drivers.every((d) => isText(d?.id) && isText(d.name)
          && Object.keys(metrics).every((k) => isProbability(d[k])))
        || !Array.isArray(order) || order.length !== 3 || new Set(order).size !== 3
        || !isProbability(cut.podium.probability)) {
      throw new Error("El documento contiene un corte inválido o ajeno a la proyección previa.");
    }
    const ids = new Set(drivers.map((d) => d.id));
    if (ids.size !== drivers.length || !order.every((id) => ids.has(id))) {
      throw new Error("El documento tiene pilotos duplicados o un podio inválido.");
    }
    cuts.set(cut.horizon, cut);
  }
  const cut = cuts.get("pre_race") ?? cuts.get("fp1");
  if (!cut) throw new Error("Aún no hay una proyección publicada.");
  return cut;
}

function sortedDrivers(drivers, metric) {
  return [...drivers].sort((a, b) => b[metric] - a[metric] || compare.compare(a.id, b.id));
}

function setNotice(message, state) {
  ui.notice.hidden = !message;
  ui.notice.dataset.state = state;
  text("notice-text", message);
  ui.retry.hidden = state !== "error";
}

function renderTiming() {
  const event = view.doc.event;
  const now = Date.now();
  if (now >= Date.parse(event.race_utc)) {
    setNotice("Proyección previa conservada. Ya pasó la hora de salida programada; esta página no sigue la carrera.", "info");
  } else if (view.cut.horizon === "fp1") {
    setNotice(now < Date.parse(event.pre_race_utc)
      ? `Corte T−30 previsto: ${utcLabel(event.pre_race_utc)}. Hasta entonces se muestra la proyección anticipada.`
      : "Ya pasó la hora del corte T−30, pero aún no está publicado. Se conserva la proyección anticipada.", "info");
  } else {
    setNotice("", "info");
  }
}

function renderPodium(cut) {
  const drivers = new Map(cut.drivers.map((d) => [d.id, d]));
  ui.podium.replaceChildren(...cut.podium.drivers.map((id, index) => {
    const d = drivers.get(id);
    const row = element("li");
    const driver = element("div");
    driver.append(element("strong", d.name));
    if (isText(d.team)) driver.append(element("span", d.team, "muted"));
    row.append(
      element("span", metrics[positions[index]], `podium-position series-${positions[index]}`),
      driver, element("span", id, "badge"),
    );
    return row;
  }));
  text("joint-probability", percent.format(cut.podium.probability));
}

function renderRanking() {
  for (const b of sortButtons) {
    b.parentElement.setAttribute("aria-sort", b.dataset.metric === view.metric ? "descending" : "none");
  }
  ui["ranking-body"].replaceChildren(...sortedDrivers(view.cut.drivers, view.metric).map((d, index) => {
    const row = element("tr");
    row.dataset.driver = d.id;
    row.style.setProperty("--car", raceInsights.teamColor(d.team));
    const name = element("th");
    name.scope = "row";
    const driver = element("div", undefined, "table-driver");
    const details = element("div", undefined, "full-name");
    details.append(element("span", d.name));
    if (isText(d.team)) details.append(element("span", d.team, "muted"));
    driver.append(element("span", d.id, "badge"), details);
    name.append(driver);
    row.append(element("td", index + 1, "table-rank"), name);
    for (const k of Object.keys(metrics)) {
      const cell = element("td", percent.format(d[k]), "table-percent");
      if (k === view.metric) cell.toggleAttribute("data-selected", true);
      row.append(cell);
    }
    return row;
  }));
}

async function drawChart(drivers) {
  if (!window.Plotly) {
    ui.chart.hidden = true;
    ui["chart-note"].hidden = false;
    return;
  }
  const top = sortedDrivers(drivers, "podium").slice(0, 6).reverse();
  const tokens = getComputedStyle(ui.chart);
  const color = (name) => tokens.getPropertyValue(`--${name}`).trim();
  const traces = positions.map((k, index) => ({
    type: "bar", orientation: "h", name: metrics[k],
    x: top.map((d) => d[k]), y: top.map((d) => d.id),
    customdata: top.map((d) => [d.name, percent.format(d[k])]),
    marker: { color: color(["accent", "mint", "gold"][index]) },
    hovertemplate: "%{customdata[0]} · %{customdata[1]}<extra>%{fullData.name}</extra>",
  }));
  try {
    await Plotly.newPlot(ui.chart, traces, {
      barmode: "stack", bargap: .42, showlegend: false,
      paper_bgcolor: "transparent", plot_bgcolor: "transparent",
      margin: { t: 10, r: 12, b: 35, l: 46 },
      font: { color: color("muted"), family: color("mono"), size: 12 },
      hoverlabel: { bgcolor: color("panel-raised"), font: { color: color("fg") } },
      xaxis: { range: [0, 1], dtick: .25, tickformat: ".0%", gridcolor: color("line"), zeroline: false, fixedrange: true },
      yaxis: { fixedrange: true },
    }, { responsive: true, displayModeBar: false });
  } catch {
    ui.chart.hidden = true;
    ui["chart-note"].hidden = false;
  }
}

function renderForecast(doc, cut) {
  const event = doc.event;
  const name = event.name.replace(/\s+\d{4}$/, "");
  const [season, round] = event.id.split("-");
  const favorite = sortedDrivers(cut.drivers, "first")[0];
  document.title = `${name} · Podio probable`;
  text("round", `Temporada ${season} / Ronda ${round}`);
  text("event-name", name);
  text("summary", "Pronóstico del ganador y del podio oficial del Gran Premio.");
  text("cut-label", cut.horizon === "fp1" ? "Proyección anticipada" : "Corte T−30");
  showTime("cut-time", cut.cutoff_utc);
  text("favorite-code", favorite.id);
  text("favorite-driver", favorite.name);
  text("favorite-team", favorite.team ?? "");
  text("favorite-probability", percent.format(favorite.first));
  showTime("race-time", event.race_utc);
  text("race-distance", Number.isInteger(event.scheduled_laps) ? `${event.scheduled_laps} vueltas` : "Por anunciar");
  text("roster-count", `${cut.drivers.length} pilotos${doc.entries_status?.startsWith("provisional") ? " · provisional" : ""}`);
  text("horizon", horizons[cut.horizon]);
  text("source", doc.source);
  showTime("model-cutoff", cut.cutoff_utc);
  showTime("model-published", cut.published_utc);
  text("version", cut.model_version);
  text("method", ({ average: "Promedio de modelos", elo: "Elo + señal previa", baseline: "Referencia" })[cut.method] ?? cut.method);
  const evidence = Array.isArray(doc.evidence_urls) ? doc.evidence_urls : [];
  ui.evidence.replaceChildren(...evidence.flatMap((value) => {
    const url = URL.canParse(value) ? new URL(value) : null;
    if (url?.protocol !== "https:") return [];
    const item = element("li");
    const link = element("a", `${url.hostname.replace(/^www\./, "")} ↗`);
    link.href = url.href;
    item.append(link);
    return [item];
  }));
  renderPodium(cut);
  renderRanking();
  renderTiming();
  for (const id of ["forecast", "favorite", "cut-note", "see-forecast"]) ui[id].hidden = false;
  raceInsights.showChances(cut, cut.horizon === "pre_race" ? doc.predictions.find((p) => p.horizon === "fp1") : null);
  void drawChart(cut.drivers);
}

async function loadForecast() {
  setNotice("Cargando la proyección publicada…", "info");
  try {
    const response = await fetch("data/upcoming.json", { cache: "no-cache", signal: AbortSignal.timeout(8_000) });
    if (!response.ok) throw new Error(`No se pudo cargar la proyección (HTTP ${response.status}).`);
    const doc = await response.json();
    const cut = selectCut(doc);
    view.doc = doc;
    view.cut = cut;
    renderForecast(doc, cut);
  } catch (error) {
    setNotice(error instanceof SyntaxError ? "La proyección no contiene JSON válido." : error.message, "error");
  }
}

for (const b of sortButtons) {
  b.addEventListener("click", () => {
    view.metric = b.dataset.metric;
    renderRanking();
  });
}
ui.retry.addEventListener("click", loadForecast);
window.setInterval(() => { if (view.cut) renderTiming(); }, 60_000);
void loadForecast();
