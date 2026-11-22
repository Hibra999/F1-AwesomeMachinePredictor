"use strict";

const ui = Object.fromEntries(Array.from(document.querySelectorAll("[id]"), (node) => [node.id, node]));
const modes = { live: "Carrera actual", replay: "Backtest" };
const origins = { published: "Predicción publicada", backtest: "Reconstrucción histórica (backtest)" };
const horizons = { fp1: "Antes de Libres 1", pre_race: "Antes de la carrera", race: "Durante la carrera" };
const horizonOrder = Object.keys(horizons);
const metrics = { first: "1.º", second: "2.º", third: "3.º", podium: "Podio" };
const metricKeys = Object.keys(metrics);
const sortButtons = [...document.querySelectorAll("[data-metric]")];
const cutPanels = [ui["timing-tower"], ui["timeline-panel"], ui["probability-panel"], ui["podium-panel"]];
const clock = new Intl.DateTimeFormat("es", {
  year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", timeZone: "UTC", hourCycle: "h23",
});
const percent = new Intl.NumberFormat("es", { style: "percent", maximumFractionDigits: 2 });
const compare = new Intl.Collator("es");
const entries = { live: {}, replay: {} };
const view = { mode: null, doc: null, cursor: 0, follow: true, player: null, driver: null, metric: "podium" };

function text(node, value) {
  if (node.textContent !== value) node.textContent = value;
}

function show(node, visible) {
  node.toggleAttribute("hidden", !visible);
}

function html(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function isText(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function timeNumber(value) {
  return typeof value === "string" ? Date.parse(value) : NaN;
}

function dateLabel(value) {
  return `${clock.format(timeNumber(value))} UTC`;
}

function renderDate(node, value, absent = "No registrada") {
  if (value === null) {
    text(node, absent);
    return;
  }
  const time = html("time", dateLabel(value));
  time.dateTime = new Date(timeNumber(value)).toISOString();
  node.replaceChildren(time);
}

function isProbability(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1 + 1e-12;
}

function decodeDocument(payload, mode) {
  if (payload?.mode !== mode) throw new Error(`El archivo no corresponde al modo ${modes[mode]}.`);
  if (!isText(payload.event?.id) || !isText(payload.event?.name) || !isText(payload.source)
      || !Number.isFinite(timeNumber(payload.updated_utc)) || typeof payload.complete !== "boolean"
      || !Array.isArray(payload.predictions)) {
    throw new Error("El documento de predicciones tiene un formato no válido.");
  }
  const keys = new Set();
  for (const prediction of payload.predictions) {
    const podium = prediction?.podium;
    if (!Object.hasOwn(horizons, prediction?.horizon) || !Object.hasOwn(origins, prediction.origin)
        || !Number.isFinite(timeNumber(prediction.cutoff_utc)) || !isText(prediction.model_version)
        || (prediction.published_utc !== null && !Number.isFinite(timeNumber(prediction.published_utc)))
        || (prediction.horizon === "race" ? !Number.isInteger(prediction.lap) || prediction.lap < 0 : prediction.lap !== null)
        || !Array.isArray(podium?.drivers) || podium.drivers.length !== 3 || new Set(podium.drivers).size !== 3
        || !isProbability(podium.probability) || !Array.isArray(prediction.drivers)
        || !prediction.drivers.every((driver) => isText(driver?.id) && isText(driver.name)
            && metricKeys.every((metric) => isProbability(driver[metric])))) {
      throw new Error("El documento contiene un corte de predicción no válido.");
    }
    const drivers = new Set(prediction.drivers.map(({ id }) => id));
    const key = cutKey(prediction);
    if (drivers.size !== prediction.drivers.length || !podium.drivers.every((id) => drivers.has(id)) || keys.has(key)) {
      throw new Error("El documento contiene pilotos o cortes incoherentes.");
    }
    keys.add(key);
    const ctx = prediction.race_context;
    if (ctx != null && (typeof ctx.partial !== "boolean"
        || !(ctx.total_laps === null || Number.isInteger(ctx.total_laps) && ctx.total_laps > 0)
        || ![ctx.safety_cars, ctx.virtual_safety_cars].every((n) => n === null || Number.isInteger(n) && n >= 0)
        || !ctx.markers || Array.isArray(ctx.markers) || typeof ctx.markers !== "object"
        || Object.entries(ctx.markers).some(([id, p]) => !drivers.has(id) || !Number.isFinite(p) || p < 0 || p > 1))) {
      throw new Error("El contexto de carrera contiene distancias o posiciones no válidas.");
    }
  }
  payload.predictions.sort((a, b) => timeNumber(a.cutoff_utc) - timeNumber(b.cutoff_utc)
    || horizonOrder.indexOf(a.horizon) - horizonOrder.indexOf(b.horizon)
    || (a.lap ?? -1) - (b.lap ?? -1));
  return payload;
}

function cutKey(prediction) {
  return `${prediction.horizon}:${prediction.lap}`;
}

async function loadEntry(mode) {
  const entry = entries[mode];
  if (entry.loading) return;
  entry.loading = true;
  try {
    const response = await fetch(`/data/${mode}.json`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error(`No se pudo cargar el archivo (HTTP ${response.status}).`);
    const revision = await response.text();
    if (entry.revision !== revision) {
      const doc = decodeDocument(JSON.parse(revision), mode);
      if (mode === "live" && entry.doc && timeNumber(doc.updated_utc) < timeNumber(entry.doc.updated_utc)) {
        throw new Error("El archivo recibido es anterior al último corte guardado.");
      }
      entry.doc = doc;
      entry.revision = revision;
      if (view.mode === mode) syncView(doc);
    }
    entry.error = null;
  } catch (error) {
    entry.error = error instanceof SyntaxError ? "El archivo no contiene JSON válido." : error.message;
  } finally {
    entry.loading = false;
    if (view.mode === mode) renderHealth();
  }
}

function renderHealth() {
  const { error } = entries[view.mode];
  const doc = view.doc;
  const age = doc ? Math.max(0, Math.floor((Date.now() - timeNumber(doc.updated_utc)) / 1000)) : 0;
  const stale = view.mode === "live" && doc && !doc.complete && age >= 240;
  const warning = Boolean(error || stale);
  text(ui["live-status"], error ? "Archivo no disponible"
    : !doc ? "Esperando datos…" : stale ? "Directo sin publicaciones recientes"
      : view.mode === "replay" ? "Repetición histórica" : doc.complete ? "Directo finalizado" : "Recibiendo cortes");
  ui["live-status"].dataset.state = warning ? "warning" : doc ? "ok" : "waiting";
  if (doc) text(ui.freshness, `Edad del documento · ${age < 60 ? `${age} s` : `${Math.floor(age / 60)} min`}`);
  ui.freshness.dataset.state = warning ? "warning" : "ok";
  show(ui.freshness, Boolean(doc));
  const message = error ? `${error}${doc ? " Se conserva la última revisión válida."
    : view.mode === "live" ? " Puedes elegir el backtest en el selector de sesión." : ""}`
    : !doc ? "Cargando las predicciones…" : !doc.predictions.length ? "Aún no hay cortes de predicción." : "";
  text(ui["notice-text"], message);
  show(ui.notice, Boolean(message));
  show(ui.retry, Boolean(error));
}

function stopPlayback() {
  window.clearInterval(view.player);
  view.player = null;
}

function syncView(doc) {
  const previous = view.doc?.predictions[view.cursor];
  const sameEvent = view.doc?.event.id === doc.event.id;
  const index = sameEvent && previous ? doc.predictions.findIndex((cut) => cutKey(cut) === cutKey(previous)) : -1;
  if (!sameEvent) {
    stopPlayback();
    view.driver = null;
  }
  view.doc = doc;
  if (view.follow) view.cursor = Math.max(0, doc.predictions.length - 1);
  else if (!sameEvent && view.mode === "replay") {
    const cuts = doc.predictions;
    const hasField = (p) => Object.keys(p.race_context?.markers ?? {}).length >= 3;
    const halfway = cuts.findIndex((p) => hasField(p) && p.race_context.total_laps && 2 * p.lap >= p.race_context.total_laps);
    view.cursor = doc.complete ? Math.max(0, halfway >= 0 ? halfway : cuts.findIndex(hasField)) : Math.max(0, cuts.length - 1);
  } else view.cursor = Math.max(0, index);
  render();
}

function selectMode(mode) {
  stopPlayback();
  view.mode = mode;
  ui["mode-select"].value = mode;
  view.doc = null;
  view.driver = null;
  view.cursor = 0;
  view.follow = mode === "live";
  if (entries[mode].doc) syncView(entries[mode].doc);
  else render();
  renderHealth();
  void loadEntry(mode);
}

function checkpoint(prediction) {
  return prediction.lap === null ? horizons[prediction.horizon] : `Vuelta ${prediction.lap}`;
}

function stepTo(cursor) {
  stopPlayback();
  view.follow = false;
  view.cursor = cursor;
  renderCut();
}

function renderTimeline(prediction, count) {
  const label = checkpoint(prediction);
  text(ui.checkpoint, label);
  text(ui["checkpoint-count"], `Corte ${view.cursor + 1} de ${count}`);
  text(ui["playback-status"], view.player !== null ? "En reproducción" : !view.follow ? "En pausa"
    : view.doc.complete ? "Último corte recibido" : "Siguiendo actualizaciones");
  ui.timeline.max = count - 1;
  ui.timeline.value = view.cursor;
  ui.timeline.setAttribute("aria-valuetext", `${label}, ${dateLabel(prediction.cutoff_utc)}`);
  ui.timeline.disabled = count < 2;
  ui["first-cut"].disabled = view.cursor === 0;
  ui.previous.disabled = view.cursor === 0;
  ui.next.disabled = view.cursor === count - 1;
  ui.play.disabled = count < 2 || view.player !== null;
  ui.pause.disabled = view.player === null && (!view.follow || view.doc.complete);
  show(ui.follow, view.mode === "live" && !view.doc.complete);
  ui.follow.setAttribute("aria-pressed", String(view.follow));
  renderDate(ui.cutoff, prediction.cutoff_utc);
  renderDate(ui.published, prediction.published_utc, prediction.origin === "backtest" ? "No aplica" : "No registrada");
  text(ui.version, prediction.model_version);
}

function renderPodium(prediction) {
  const names = new Map(prediction.drivers.map(({ id, name }) => [id, name]));
  ui.podium.replaceChildren(...prediction.podium.drivers.map((id) => html("li", names.get(id))));
  text(ui["joint-probability"], percent.format(prediction.podium.probability));
}

function renderRanking(prediction) {
  for (const button of sortButtons) {
    button.parentElement.setAttribute("aria-sort", button.dataset.metric === view.metric ? "descending" : "none");
  }
  const drivers = [...prediction.drivers].sort((a, b) => b[view.metric] - a[view.metric]
    || compare.compare(a.name, b.name) || compare.compare(a.id, b.id));
  ui["ranking-body"].replaceChildren(...drivers.map((driver, rank) => {
    const row = html("tr");
    row.dataset.driver = driver.id;
    const carColor = raceInsights.teamColor(driver.team);
    row.style.setProperty("--car", carColor);
    ui["circuit-panel"].style.setProperty(`--driver-${driver.id}`, carColor);
    const name = html("th");
    name.scope = "row";
    const pick = html("button", driver.name, "driver-pick");
    pick.type = "button";
    pick.setAttribute("aria-label", `Seguir a ${driver.name}`);
    pick.setAttribute("aria-pressed", String(driver.id === view.driver));
    name.append(pick);
    if (isText(driver.team)) name.append(html("span", driver.team, "driver-team"));
    row.append(html("td", String(rank + 1)), name);
    for (const metric of metricKeys) row.append(html("td", percent.format(driver[metric])));
    return row;
  }));
}

const chartColors = ["--accent", "--mint", "--gold", "--fg"];

function chartColor(value) {
  return getComputedStyle(ui.chart).getPropertyValue(value).trim();
}

function renderChart(prediction) {
  const drivers = new Map();
  const laps = new Map();
  for (let index = 0; index <= view.cursor; index += 1) {
    const cut = view.doc.predictions[index];
    if (cut.horizon === "race") laps.set(cut.lap, cut.drivers);
    else if (index !== view.cursor) continue;
    for (const { id, name } of cut.drivers) drivers.set(id, name);
  }
  if (!drivers.has(view.driver)) view.driver = prediction.podium.drivers[0];
  const options = [...drivers].sort((a, b) => compare.compare(a[1], b[1]) || compare.compare(a[0], b[0]));
  const select = ui["driver-select"];
  if (select.length !== options.length || options.some(([id, name], i) => select.options[i].value !== id || select.options[i].text !== name)) {
    select.replaceChildren(...options.map(([id, name]) => new Option(name, id)));
  }
  select.value = view.driver;
  const points = Array.from(laps, ([lap, roster]) => ({ lap, driver: roster.find(({ id }) => id === view.driver) }))
    .sort((a, b) => a.lap - b.lap);
  const last = points.findLast(({ driver }) => driver);
  show(ui["chart-empty"], !last);
  show(ui.chart, Boolean(last));
  if (!last) {
    if (ui.chart.data) Plotly.purge(ui.chart);
    ui["chart-legend"].replaceChildren();
    return;
  }
  const series = points.flatMap((point, index) => {
    const prior = points[index - 1];
    return prior && point.lap > prior.lap + 1 ? [{ lap: prior.lap + 1 }, point] : [point];
  });
  const x = series.map(({ lap }) => lap);
  const traces = metricKeys.map((metric, index) => ({
    type: "scatter", mode: x.length <= 12 ? "lines+markers" : "lines", name: metrics[metric], x,
    y: series.map(({ driver }) => driver?.[metric] ?? null),
    line: { color: chartColor(chartColors[index]), width: metric === "podium" ? 3 : 1.5 },
    hovertemplate: "Vuelta %{x}<br>%{y:.1%}",
  }));
  const line = chartColor("--line");
  Plotly.react(ui.chart, traces, {
    autosize: true, height: 320, margin: { t: 20, r: 16, b: 48, l: 52 },
    paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: { color: chartColor("--fg") },
    xaxis: { title: { text: "Vuelta" }, gridcolor: line, dtick: x.length < 10 ? 1 : undefined, fixedrange: true },
    yaxis: { range: [0, 1], dtick: .25, tickformat: ".0%", gridcolor: line, zeroline: false, fixedrange: true },
    showlegend: false, hovermode: "x unified",
  }, { responsive: true, displayModeBar: false });
  ui["chart-legend"].replaceChildren(...metricKeys.map((metric, index) => {
    const item = html("li", `${metrics[metric]} · ${percent.format(last.driver[metric])}`);
    item.style.color = `var(${chartColors[index]})`;
    return item;
  }));
}

function currentCut() {
  return view.doc?.predictions[view.cursor];
}

function renderFacts(cut) {
  show(ui["race-facts"], Boolean(cut));
  if (!cut) return;
  const ctx = cut.race_context;
  // Legacy feeds have only the latest distance. Do not leak it into an earlier cut.
  const lastLegacy = !ctx && cut === view.doc.predictions.at(-1);
  const total = ctx ? ctx.total_laps : lastLegacy ? view.doc.event.scheduled_laps : null;
  const racing = cut.horizon === "race";
  const known = Number.isInteger(total) && total > 0;
  const lap = racing ? cut.lap : 0;
  text(ui["race-lap"], racing ? `${lap} / ${known ? total : "?"}` : known ? `0 / ${total}` : "Sin salida");
  const progress = ui["lap-progress"];
  show(progress, known);
  if (known) { progress.max = total; progress.value = lap; progress.setAttribute("aria-valuetext", `${lap} de ${total} vueltas`); }
  const count = (n) => !racing ? "—" : !Number.isInteger(n) ? "Sin dato" : ctx.partial ? `≥ ${n}` : String(n);
  text(ui["race-sc"], count(ctx?.safety_cars));
  text(ui["race-vsc"], count(ctx?.virtual_safety_cars));
  text(ui["fact-note"], !racing ? "La carrera todavía no había empezado en este corte."
    : !ctx ? "Este corte antiguo no guardó estado de pista ni posiciones de los pilotos."
      : ctx.partial ? "Cobertura parcial. Los recuentos indican el mínimo observado, no el total de la carrera."
        : "Recuento acumulado hasta esta vuelta. Un safety car virtual no cuenta como salida del coche de seguridad.");
}

function updateMap() {
  const cut = currentCut();
  const total = cut?.race_context?.total_laps;
  const label = cut ? `${checkpoint(cut)}${cut.lap != null && total ? ` de ${total}` : ""}` : null;
  circuitMap.render(view.doc?.event, label, cut, view.driver, chooseDriver);
}

function chooseDriver(id) {
  const cut = currentCut();
  if (!cut || ![...ui["driver-select"].options].some((option) => option.value === id)) return;
  view.driver = id;
  renderChart(cut);
  for (const row of ui["ranking-body"].rows) {
    row.querySelector("button").setAttribute("aria-pressed", String(row.dataset.driver === id));
  }
  updateMap();
}

function renderCut() {
  const prediction = currentCut();
  renderFacts(prediction);
  show(ui.origin, Boolean(prediction));
  show(ui["analysis-panel"], Boolean(view.doc));
  for (const panel of cutPanels) show(panel, Boolean(prediction));
  raceInsights.showChances(prediction, view.doc?.predictions[view.cursor - 1]);
  if (!prediction) {
    if (ui.chart.data) Plotly.purge(ui.chart);
    updateMap();
    return;
  }
  text(ui.origin, origins[prediction.origin]);
  renderTimeline(prediction, view.doc.predictions.length);
  renderPodium(prediction);
  renderChart(prediction);
  renderRanking(prediction);
  updateMap();
}

function render() {
  const doc = view.doc;
  text(ui["event-name"], doc?.event.name ?? "Mesa de carrera");
  text(ui.mode, view.mode === "live" && doc?.complete ? "Captura del directo" : modes[view.mode]);
  show(ui.publication, Boolean(doc));
  if (doc) {
    text(ui.source, doc.source);
    renderDate(ui.updated, doc.updated_utc);
  }
  renderCut();
}

function play() {
  view.follow = false;
  if (view.cursor === view.doc.predictions.length - 1) view.cursor = 0;
  view.player = window.setInterval(() => {
    const last = view.doc.predictions.length - 1;
    view.cursor = Math.min(view.cursor + 1, last);
    if (view.cursor >= last) stopPlayback();
    renderCut();
  }, 900);
  renderCut();
}

ui["mode-select"].addEventListener("change", (event) => {
  const mode = event.target.value;
  history.replaceState(null, "", mode === "replay" ? "#backtest" : location.pathname);
  selectMode(mode);
});
ui.retry.addEventListener("click", () => void loadEntry(view.mode));
ui.timeline.addEventListener("input", (event) => stepTo(Number(event.target.value)));
ui["first-cut"].addEventListener("click", () => stepTo(0));
ui.previous.addEventListener("click", () => stepTo(view.cursor - 1));
ui.next.addEventListener("click", () => stepTo(view.cursor + 1));
ui.play.addEventListener("click", play);
ui.pause.addEventListener("click", () => stepTo(view.cursor));
ui.follow.addEventListener("click", () => {
  if (view.follow) {
    stepTo(view.cursor);
    return;
  }
  stopPlayback();
  view.follow = true;
  view.cursor = view.doc.predictions.length - 1;
  renderCut();
});
ui["driver-select"].addEventListener("change", (event) => chooseDriver(event.target.value));
ui["ranking-body"].addEventListener("click", (event) => {
  if (event.target.closest("button")) chooseDriver(event.target.closest("tr").dataset.driver);
});
for (const button of sortButtons) {
  button.addEventListener("click", () => {
    view.metric = button.dataset.metric;
    renderRanking(currentCut());
  });
}

selectMode(location.hash === "#backtest" ? "replay" : "live");
window.setInterval(() => {
  if (view.mode) renderHealth();
  if (view.mode === "live") void loadEntry("live");
}, 3000);
