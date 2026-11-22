"use strict";

const ui = Object.fromEntries(Array.from(document.querySelectorAll("[id]"), (node) => [node.id, node]));
const modes = { forecast: "Pronóstico", live: "Directo", replay: "Repetición" };
const origins = { published: "Predicción publicada", backtest: "Reconstrucción histórica (backtest)" };
const horizons = { fp1: "Antes de Libres 1", pre_race: "Antes de la carrera", race: "Durante la carrera" };
const horizonOrder = Object.keys(horizons);
const metrics = { first: "1.º", second: "2.º", third: "3.º", podium: "Podio" };
const metricKeys = Object.keys(metrics);
const clock = new Intl.DateTimeFormat("es", {
  year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", timeZone: "UTC", hourCycle: "h23",
});
const percent = new Intl.NumberFormat("es", { style: "percent", maximumFractionDigits: 2 });
const compare = new Intl.Collator("es");
const pollMs = 65_000;
const staleMinutes = 4;
const entries = new Map();
const view = { key: null, doc: null, cursor: 0, follow: false, player: null, driver: null, metric: "podium" };
let catalog;
let catalogError;
let feed;
let userSelected = false;

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
  const milliseconds = timeNumber(value);
  return Number.isFinite(milliseconds) ? `${clock.format(milliseconds)} UTC` : "";
}

function renderDate(node, value, absent = "No anunciado") {
  const label = dateLabel(value);
  if (!label) {
    text(node, absent);
    return;
  }
  const time = html("time", label);
  time.dateTime = new Date(timeNumber(value)).toISOString();
  node.replaceChildren(time);
}

function isEvent(event) {
  return isText(event?.id) && isText(event?.name);
}

function isProbability(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1 + 1e-12;
}

function decodeDocument(payload) {
  if (!isEvent(payload?.event) || !Object.hasOwn(modes, payload.mode) || !isText(payload.source)
      || !Number.isFinite(timeNumber(payload.updated_utc)) || typeof payload.complete !== "boolean"
      || !Array.isArray(payload.predictions)) {
    throw new Error("El documento de predicciones tiene un formato no válido.");
  }
  for (const prediction of payload.predictions) {
    const podium = prediction?.podium;
    if (!Object.hasOwn(horizons, prediction?.horizon) || !Object.hasOwn(origins, prediction.origin)
        || !Number.isFinite(timeNumber(prediction.cutoff_utc)) || !isText(prediction.model_version)
        || (prediction.published_utc !== null && !Number.isFinite(timeNumber(prediction.published_utc)))
        || (prediction.lap !== null && (!Number.isInteger(prediction.lap) || prediction.lap < 0))
        || !Array.isArray(podium?.drivers) || podium.drivers.length !== 3 || !podium.drivers.every(isText)
        || !isProbability(podium.probability) || !Array.isArray(prediction.drivers) || !prediction.drivers.length
        || !prediction.drivers.every((driver) => isText(driver?.id) && isText(driver.name)
            && metricKeys.every((metric) => isProbability(driver[metric])))) {
      throw new Error("El documento contiene un corte de predicción no válido.");
    }
  }
  payload.predictions.sort((a, b) => timeNumber(a.cutoff_utc) - timeNumber(b.cutoff_utc)
    || horizonOrder.indexOf(a.horizon) - horizonOrder.indexOf(b.horizon)
    || (a.lap ?? -1) - (b.lap ?? -1)
    || (timeNumber(a.published_utc) || 0) - (timeNumber(b.published_utc) || 0));
  return payload;
}

async function fetchResource(url, options) {
  try {
    return await fetch(url, { signal: AbortSignal.timeout(15_000), ...options });
  } catch (error) {
    if (error.name === "TimeoutError") throw new Error("La consulta agotó el tiempo de espera.");
    throw new Error("No se pudo conectar. Comprueba la red o el acceso público al recurso.");
  }
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error("La respuesta no contiene JSON válido.");
    throw new Error("No se pudo leer la respuesta completa.");
  }
}

async function siteJson(path) {
  const response = await fetchResource(new URL(path, document.baseURI), { cache: "no-cache" });
  if (!response.ok) throw new Error(`No se pudo cargar el archivo (HTTP ${response.status}).`);
  return readJson(response);
}

function registerCatalog(payload) {
  if (!Array.isArray(payload?.replays) || !payload.replays.every((item) => isEvent(item) && isText(item.file))
      || (payload.next !== null && payload.next !== undefined && !isEvent(payload.next))
      || (payload.upcoming !== null && payload.upcoming !== undefined && !isText(payload.upcoming))) {
    throw new Error("El catálogo de eventos tiene un formato no válido.");
  }
  if (payload.next) {
    entries.set("upcoming", { key: "upcoming", label: `Próximo · ${payload.next.name}`, eventId: payload.next.id, event: payload.next, file: payload.upcoming });
  }
  if (payload.feed_url !== null && payload.feed_url !== undefined) {
    feed = { key: "feed", label: "Feed de publicación", nextPoll: 0 };
    try {
      feed.url = new URL(payload.feed_url);
      if (!/^https?:$/.test(feed.url.protocol)) throw new Error();
    } catch {
      feed.url = null;
      feed.error = "La URL del feed configurado no es válida.";
    }
    entries.set(feed.key, feed);
  }
  for (const replay of payload.replays) {
    const key = `archive:${replay.id}`;
    entries.set(key, { key, eventId: replay.id, label: `Archivo · ${replay.name} (${replay.id})`, file: replay.file, archive: true });
  }
  ui["event-select"].replaceChildren(...Array.from(entries.values(), ({ key, label }) => new Option(label, key)));
  ui["event-select"].disabled = !entries.size;
  if (!entries.size) ui["event-select"].add(new Option("No hay eventos disponibles"));
}

async function loadCatalog() {
  catalogError = null;
  renderHealth();
  try {
    const payload = await siteJson("data/index.json");
    registerCatalog(payload);
    catalog = payload;
    selectEvent(entries.keys().next().value);
    schedulePoll();
  } catch (error) {
    catalogError = error.message;
    renderHealth();
  }
}

async function loadEntry(entry) {
  entry.loading = true;
  entry.error = null;
  renderHealth();
  try {
    const doc = decodeDocument(await siteJson(entry.file));
    if (doc.event.id !== entry.eventId) throw new Error("El documento pertenece a otro evento.");
    entry.doc = doc;
    if (view.key === entry.key) syncView(entry);
  } catch (error) {
    entry.error = error.message;
    renderHealth();
  } finally {
    entry.loading = false;
  }
}

function health() {
  if (catalogError) return { status: "Catálogo no disponible", warning: true };
  if (!catalog) return { status: "Cargando el estado de la publicación…" };
  if (!feed) return { status: catalog.live_status || "No hay feed de publicación configurado." };
  if (feed.error) return { status: `Feed: ${feed.error}`, warning: true, stale: Boolean(feed.doc) };
  if (!feed.doc) return { status: catalog.live_status || "Esperando la primera publicación del feed…" };
  if (!feed.doc.complete && feed.doc.mode !== "forecast" && Date.now() - timeNumber(feed.doc.updated_utc) >= staleMinutes * 60_000) {
    return { status: `Feed sin publicaciones nuevas desde hace al menos ${staleMinutes} minutos.`, warning: true, stale: true };
  }
  if (!feed.doc.predictions.length) return { status: "Feed accesible · aún no hay predicciones publicadas." };
  return { status: feed.doc.complete ? "Feed operativo · documento completo." : "Feed operativo · publicación en curso.", ok: true };
}

function renderHealth() {
  const current = health();
  text(ui["live-status"], current.status);
  ui["live-status"].dataset.state = current.warning ? "warning" : current.ok ? "ok" : "waiting";
  const entry = entries.get(view.key);
  let message = "";
  let warning = false;
  let retry = false;
  if (!catalog) {
    message = catalogError ? `Catálogo no disponible: ${catalogError}` : "Cargando el catálogo…";
    warning = retry = Boolean(catalogError);
  } else if (!entry) {
    message = "El catálogo aún no contiene eventos.";
  } else if (entry === feed && current.warning) {
    message = current.stale && view.doc ? `${current.status} Se conserva el documento mostrado.` : current.status;
    warning = true;
  } else if (entry.error) {
    message = entry.doc ? `${entry.error} Se conserva el documento mostrado.` : entry.error;
    warning = retry = true;
  } else if (!view.doc) {
    message = entry === feed ? "Aún no se ha recibido un documento del feed."
      : entry.file ? "Cargando las predicciones del evento…" : "Todavía no hay predicciones para este evento.";
  } else if (!view.doc.predictions.length) {
    message = "Este documento todavía no contiene predicciones.";
  }
  const stale = Boolean(view.doc && (entry === feed ? current.stale : entry?.error));
  show(ui.freshness, stale);
  if (stale) {
    const minutes = Math.max(0, Math.floor((Date.now() - timeNumber(view.doc.updated_utc)) / 60_000));
    text(ui.freshness, `Datos anteriores · ${minutes || "<1"} min`);
  }
  show(ui.notice, Boolean(message));
  ui.notice.dataset.state = warning ? "warning" : "information";
  text(ui["notice-text"], message);
  show(ui.retry, retry);
}

function applyRateLimit(response) {
  const now = Date.now();
  const remaining = response.headers.get("x-ratelimit-remaining");
  const reset = response.headers.get("x-ratelimit-reset");
  if (remaining === "0" && reset !== null && Number.isFinite(Number(reset))) {
    feed.nextPoll = Math.max(feed.nextPoll, Number(reset) * 1_000 + 1_000);
  }
  const retry = response.headers.get("retry-after");
  if (retry !== null) {
    const deadline = Number.isFinite(Number(retry)) ? now + Number(retry) * 1_000 : Date.parse(retry);
    if (Number.isFinite(deadline)) feed.nextPoll = Math.max(feed.nextPoll, deadline);
  }
}

function gistError(response) {
  const status = response.status;
  if (status === 429 || (status === 403 && (response.headers.get("x-ratelimit-remaining") === "0"
      || response.headers.has("retry-after")))) return `Límite de consultas de GitHub alcanzado (HTTP ${status}).`;
  if (status === 401 || status === 403) return `GitHub denegó el acceso sin autenticación (HTTP ${status}).`;
  if (status === 404) return "No se encontró el Gist público (HTTP 404).";
  return `No se pudo consultar GitHub (HTTP ${status}).`;
}

function schedulePoll() {
  if (!feed?.url) return;
  window.clearTimeout(feed.timer);
  if (!document.hidden && !feed.busy && navigator.onLine) {
    feed.timer = window.setTimeout(pollFeed, Math.max(0, feed.nextPoll - Date.now()));
  }
}

async function pollFeed() {
  if (document.hidden || !navigator.onLine || feed.busy) return;
  if (Date.now() < feed.nextPoll) {
    schedulePoll();
    return;
  }
  feed.nextPoll = Date.now() + pollMs;
  feed.busy = true;
  try {
    const response = await fetchResource(feed.url, {
      cache: "no-store", credentials: "omit", headers: feed.etag ? { "If-None-Match": feed.etag } : {},
    });
    applyRateLimit(response);
    if (response.status === 304 && feed.doc) {
      feed.error = null;
      return;
    }
    if (!response.ok) throw new Error(gistError(response));
    const gist = await readJson(response);
    const file = gist?.files?.["feed.json"];
    if (!file) throw new Error("El Gist no tiene publicado el archivo feed.json.");
    if (file.truncated) throw new Error("GitHub devolvió feed.json truncado; no se puede usar esta publicación.");
    if (!isText(file.content)) throw new Error("El archivo feed.json está vacío o no está disponible.");
    let payload;
    try {
      payload = JSON.parse(file.content);
    } catch {
      throw new Error("El archivo feed.json no contiene JSON válido.");
    }
    feed.doc = decodeDocument(payload);
    feed.etag = response.headers.get("etag");
    feed.error = null;
    feed.label = `Feed de publicación · ${feed.doc.event.name}`;
    const option = Array.from(ui["event-select"].options).find(({ value }) => value === feed.key);
    if (option) text(option, feed.label);
    if (view.key === feed.key) {
      if (!view.doc || view.follow) syncView(feed);
    } else if (!userSelected && health().ok && (feed.doc.mode === "forecast" || (feed.doc.mode === "live" && !feed.doc.complete))) {
      selectEvent(feed.key);
    }
  } catch (error) {
    feed.error = error.message;
  } finally {
    feed.busy = false;
    renderHealth();
    schedulePoll();
  }
}

function stopPlayback() {
  window.clearInterval(view.player);
  view.player = null;
}

function syncView(entry) {
  if (!view.doc || view.follow) {
    view.doc = entry.doc ?? null;
    const count = view.doc?.predictions.length ?? 0;
    view.cursor = view.follow && count ? count - 1 : 0;
  }
  render();
}

function selectEvent(key) {
  stopPlayback();
  const entry = entries.get(key);
  view.key = entry?.key ?? null;
  view.doc = null;
  view.driver = null;
  view.follow = Boolean(entry && !entry.archive);
  if (entry) {
    ui["event-select"].value = key;
    syncView(entry);
    if (entry.file && !entry.doc && !entry.loading) void loadEntry(entry);
  } else {
    render();
  }
}

function checkpoint(prediction) {
  return prediction.lap === null ? horizons[prediction.horizon] : `Vuelta ${prediction.lap}`;
}

function stepTo(cursor) {
  userSelected = true;
  stopPlayback();
  view.follow = false;
  view.cursor = cursor;
  render();
}

function renderTimeline(prediction, count, entry) {
  const label = checkpoint(prediction);
  text(ui.checkpoint, label);
  text(ui["checkpoint-count"], `Corte ${view.cursor + 1} de ${count}`);
  text(ui["playback-status"], view.player !== null ? "En reproducción"
    : view.follow ? (entry === feed ? "Siguiendo actualizaciones" : "Último corte disponible") : "En pausa");
  ui.timeline.max = count - 1;
  ui.timeline.value = view.cursor;
  ui.timeline.setAttribute("aria-valuetext", `${label}, ${dateLabel(prediction.cutoff_utc)}`);
  ui.timeline.disabled = count < 2;
  ui.previous.disabled = view.cursor === 0;
  ui.next.disabled = view.cursor === count - 1;
  ui.play.disabled = count < 2 || view.player !== null;
  ui.pause.disabled = view.player === null && !view.follow;
  show(ui.follow, entry === feed);
  ui.follow.setAttribute("aria-pressed", String(view.follow));
  renderDate(ui.cutoff, prediction.cutoff_utc);
  renderDate(ui.published, prediction.published_utc, prediction.origin === "backtest" ? "No aplica" : "No registrada");
  text(ui.version, prediction.model_version);
}

function renderPodium(prediction) {
  const names = new Map(prediction.drivers.map(({ id, name }) => [id, name]));
  ui.podium.replaceChildren(...prediction.podium.drivers.map((id, position) => {
    const row = html("li");
    row.append(html("span", `${position + 1}.º`), html("strong", names.get(id) ?? id));
    return row;
  }));
  text(ui["joint-probability"], percent.format(prediction.podium.probability));
}

const sortButtons = Array.from(document.querySelectorAll("[data-metric]"));

function renderRanking(prediction) {
  for (const button of sortButtons) {
    button.parentElement.setAttribute("aria-sort", button.dataset.metric === view.metric ? "descending" : "none");
  }
  const drivers = [...prediction.drivers].sort((a, b) => b[view.metric] - a[view.metric]
    || compare.compare(a.name, b.name) || compare.compare(a.id, b.id));
  ui["ranking-body"].replaceChildren(...drivers.map((driver, rank) => {
    const row = html("tr");
    row.dataset.driver = driver.id;
    const name = html("th", driver.name);
    name.scope = "row";
    if (isText(driver.team)) name.append(html("span", driver.team, "driver-team"));
    row.append(html("td", String(rank + 1)), name);
    for (const metric of metricKeys) row.append(html("td", percent.format(driver[metric])));
    return row;
  }));
}

function svg(tag, attributes, value) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, attribute] of Object.entries(attributes)) node.setAttribute(name, attribute);
  if (value !== undefined) node.textContent = value;
  return node;
}

function renderChart(prediction) {
  const drivers = new Map();
  const laps = new Map();
  for (let index = 0; index <= view.cursor; index += 1) {
    const cut = view.doc.predictions[index];
    if (cut.horizon === "race" && cut.lap !== null) {
      laps.set(cut.lap, cut.drivers);
    } else if (index !== view.cursor) {
      continue;
    }
    for (const { id, name } of cut.drivers) drivers.set(id, name);
  }
  if (!drivers.has(view.driver)) view.driver = prediction.podium.drivers.find((id) => drivers.has(id)) ?? drivers.keys().next().value;
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
  show(ui["chart-legend"], Boolean(last));
  if (!last) {
    ui.chart.replaceChildren();
    ui["chart-legend"].replaceChildren();
    delete ui.chart.dataset.lastLap;
    text(ui["chart-empty"], laps.size ? "No hay datos de vuelta para este piloto hasta este corte."
      : "Aún no hay vueltas disponibles hasta este corte.");
    return;
  }
  drawChart(points, last, drivers.get(view.driver));
}

function drawChart(points, last, name) {
  const left = 56;
  const right = 458;
  const top = 28;
  const bottom = 184;
  const maxLap = points.at(-1).lap;
  const x = (lap) => left + (right - left) * lap / Math.max(1, maxLap);
  const y = (value) => bottom - (bottom - top) * value;
  const nodes = [
    svg("title", { id: "chart-svg-title" }, `Probabilidades por vuelta de ${name}`),
    svg("desc", { id: "chart-svg-desc" }, `Probabilidades del 0 al 100 %. Último dato del piloto: vuelta ${last.lap}.`),
    svg("text", { x: left, y: 17, class: "chart-label" }, "P (%)"),
  ];
  for (const value of [1, .75, .5, .25, 0]) {
    nodes.push(svg("line", { x1: left, x2: right, y1: y(value), y2: y(value), class: "chart-grid" }));
    nodes.push(svg("text", { x: left - 10, y: y(value) + 5, "text-anchor": "end", class: "chart-label" }, String(value * 100)));
  }
  const ticks = new Set(Array.from({ length: 6 }, (_, i) => Math.round(i * maxLap / 5)));
  for (const lap of ticks) {
    nodes.push(svg("line", { x1: x(lap), x2: x(lap), y1: bottom, y2: bottom + 6, class: "chart-grid" }));
    nodes.push(svg("text", { x: x(lap), y: bottom + 24, "text-anchor": "middle", class: "chart-label" }, String(lap)));
  }
  nodes.push(svg("text", { x: (left + right) / 2, y: 233, "text-anchor": "middle", class: "chart-label" }, "Vuelta"));
  const legend = [];
  for (const metric of metricKeys) {
    let connected = false;
    const path = [];
    const markers = [];
    for (const { lap, driver } of points) {
      if (!driver) {
        connected = false;
        continue;
      }
      const px = x(lap);
      const py = y(driver[metric]);
      path.push(`${connected ? "L" : "M"}${px} ${py}`);
      connected = true;
      markers.push(svg("circle", { cx: px, cy: py, r: 3, class: `chart-point series-${metric}`, "data-lap": lap }));
    }
    nodes.push(svg("path", { d: path.join(" "), class: `chart-line series-${metric}` }), ...markers);
    legend.push(html("li", `${metrics[metric]} · ${percent.format(last.driver[metric])}`, `series-${metric}`));
  }
  ui.chart.dataset.lastLap = last.lap;
  ui.chart.replaceChildren(...nodes);
  ui["chart-legend"].replaceChildren(...legend);
}

function render() {
  const entry = entries.get(view.key);
  const doc = view.doc;
  const event = doc?.event ?? entry?.event;
  const prediction = doc?.predictions[view.cursor];
  text(ui["event-name"], event?.name ?? entry?.label ?? "Probabilidades de podio");
  show(ui.badges, Boolean(doc));
  text(ui.mode, doc ? modes[doc.mode] : "");
  show(ui.origin, Boolean(prediction));
  text(ui.origin, prediction ? origins[prediction.origin] : "");
  show(ui.schedule, Boolean(event));
  if (event) {
    renderDate(ui["fp1-utc"], event.fp1_utc);
    renderDate(ui["pre-race-utc"], event.pre_race_utc);
    renderDate(ui["race-utc"], event.race_utc);
    text(ui["scheduled-laps"], Number.isInteger(event.scheduled_laps) ? String(event.scheduled_laps) : "No anunciadas");
  }
  show(ui.publication, Boolean(doc));
  if (doc) {
    text(ui.source, doc.source);
    renderDate(ui.updated, doc.updated_utc);
  }
  for (const panel of ["timeline-panel", "podium-panel", "analysis-panel"]) show(ui[panel], Boolean(prediction));
  if (prediction) {
    renderTimeline(prediction, doc.predictions.length, entry);
    renderPodium(prediction);
    renderRanking(prediction);
    renderChart(prediction);
  }
  renderHealth();
}

ui["event-select"].addEventListener("change", (event) => {
  userSelected = true;
  selectEvent(event.target.value);
});
ui.retry.addEventListener("click", () => {
  if (!catalog) void loadCatalog();
  else {
    const entry = entries.get(view.key);
    if (entry?.file && !entry.loading) void loadEntry(entry);
  }
});
ui.timeline.addEventListener("input", () => stepTo(ui.timeline.valueAsNumber));
ui.previous.addEventListener("click", () => stepTo(view.cursor - 1));
ui.next.addEventListener("click", () => stepTo(view.cursor + 1));
ui.pause.addEventListener("click", () => stepTo(view.cursor));
ui.play.addEventListener("click", () => {
  userSelected = true;
  const end = view.doc.predictions.length - 1;
  view.follow = false;
  if (view.cursor === end) view.cursor = 0;
  view.player = window.setInterval(() => {
    view.cursor += 1;
    if (view.cursor === end) stopPlayback();
    render();
  }, 1_000);
  render();
});
ui.follow.addEventListener("click", () => {
  if (view.follow) stepTo(view.cursor);
  else {
    stopPlayback();
    view.follow = true;
    syncView(feed);
  }
});
ui["driver-select"].addEventListener("change", (event) => {
  view.driver = event.target.value;
  renderChart(view.doc.predictions[view.cursor]);
});
for (const button of sortButtons) {
  button.addEventListener("click", () => {
    view.metric = button.dataset.metric;
    renderRanking(view.doc.predictions[view.cursor]);
  });
}

document.addEventListener("visibilitychange", () => {
  renderHealth();
  schedulePoll();
});
window.addEventListener("offline", () => {
  if (!feed?.url) return;
  feed.error = "No hay conexión a la red.";
  renderHealth();
  schedulePoll();
});
window.addEventListener("online", schedulePoll);
window.setInterval(renderHealth, 15_000);
void loadCatalog();
