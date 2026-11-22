"use strict";

const raceInsights = (() => {
  const byId = (id) => document.getElementById(id);
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const pct = new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 2 });
  const signed = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 2, signDisplay: "exceptZero" });
  const slots = { first: "Victoria", second: "Segundo puesto", third: "Tercer puesto" };
  const color = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  // DriverList.TeamColour, Las Vegas 2026. Keys use the published roster's canonical team names.
  const teams = { alpine: "#00A1E8", "aston martin": "#229971", audi: "#F50537", cadillac: "#909090",
    ferrari: "#ED1131", haas: "#9C9FA2", mclaren: "#F47600", mercedes: "#00D7B6", rb: "#6C98FF",
    "red bull": "#4781D7", williams: "#1868DB" };
  const teamColor = (name) => typeof name === "string" && Object.hasOwn(teams, name.toLowerCase()) ? teams[name.toLowerCase()] : color("muted");
  const cutName = (cut) => cut.horizon === "race" ? `Vuelta ${cut.lap}` : cut.horizon === "pre_race" ? "T−30" : "Pre-FP1";
  let shown, prior;

  const desk = byId("probability-insights");
  if (desk) {
    desk.innerHTML = `<figure class="panel insight-card" aria-labelledby="share-title">
      <figcaption class="insight-head"><div><p class="eyebrow">Distribución del puesto</p><h2 id="share-title">¿Quién se lleva el puesto?</h2></div>
      <label>Puesto<select id="share-position"><option value="first">Victoria</option><option value="second">Segundo puesto</option><option value="third">Tercer puesto</option></select></label></figcaption>
      <div class="share-body"><div id="share-plot" role="img" aria-label="Reparto del puesto entre los pilotos"></div><ol id="share-key" aria-label="Porcentajes del gráfico de pastel"></ol></div>
      <p class="muted insight-note" id="share-note"></p></figure>
      <figure class="panel insight-card" id="swing-card" hidden aria-labelledby="swing-title">
      <figcaption><p class="eyebrow">Cambio de probabilidad</p><h2 id="swing-title">Quién gana y pierde opciones de podio</h2></figcaption>
      <p id="swing-cuts" class="muted insight-note"></p><ol id="swing-rows" aria-label="Mayores cambios en puntos porcentuales"></ol>
      <p class="muted insight-note">Las barras muestran cambios en puntos porcentuales, no adelantamientos en pista.</p></figure>`;
    byId("share-position").addEventListener("change", drawShare);
  }

  function drawShare() {
    if (!shown) return;
    const slot = byId("share-position").value;
    const drivers = [...shown.drivers].sort((a, b) => b[slot] - a[slot] || a.id.localeCompare(b.id));
    const parts = drivers.slice(0, 4).map((d) => ({ code: d.id, name: d.name, p: d[slot], color: teamColor(d.team) }));
    const rest = drivers.slice(parts.length);
    if (rest.length) parts.push({ code: "Resto", name: `Otros ${rest.length} pilotos`, p: rest.reduce((sum, d) => sum + d[slot], 0), color: color("muted") });
    byId("share-key").replaceChildren(...parts.map((part) => {
      const li = make("li");
      li.style.setProperty("--slice", part.color);
      li.append(make("span", part.name), make("strong", pct.format(part.p)));
      return li;
    }));
    byId("share-note").textContent = `${cutName(shown)} · ${drivers.length} pilotos · Color por equipo. Cada puesto reparte el 100 % antes de redondear.`;
    const plot = byId("share-plot");
    if (!window.Plotly) { plot.hidden = true; return; }
    plot.hidden = false;
    const leader = parts[0];
    void Plotly.react(plot, [{ type: "pie", hole: .7, sort: false, direction: "clockwise", rotation: -90,
      labels: parts.map((p) => p.code), values: parts.map((p) => p.p), customdata: parts.map((p) => [p.name, pct.format(p.p)]),
      marker: { colors: parts.map((p) => p.color), line: { color: color("panel"), width: 2 } },
      textinfo: "none", hovertemplate: "%{customdata[0]}<br>%{customdata[1]}<extra></extra>",
    }], { margin: { l: 4, r: 4, t: 4, b: 4 }, paper_bgcolor: "transparent", showlegend: false,
      font: { color: color("fg") }, annotations: [
        { x: .5, y: .55, text: leader.code, showarrow: false, font: { size: 24, color: color("fg") } },
        { x: .5, y: .40, text: pct.format(leader.p), showarrow: false, font: { size: 14, color: color("fg") } },
      ] }, { responsive: true, displayModeBar: false }).catch(() => { plot.hidden = true; });
    plot.setAttribute("aria-label", `${slots[slot]}: ${leader.name} encabeza el pronóstico con ${pct.format(leader.p)}.`);
  }

  function showChances(cut, previous) {
    if (!desk || (shown === cut && prior === previous)) return;
    shown = cut;
    prior = previous;
    desk.hidden = !cut;
    if (!cut) return;
    drawShare();
    const card = byId("swing-card");
    card.hidden = !previous;
    if (!previous) return;
    const baseline = new Map(previous.drivers.map((d) => [d.id, d.podium]));
    const deltas = cut.drivers.filter((d) => baseline.has(d.id)).map((d) => ({ id: d.id, now: d.podium,
      before: baseline.get(d.id), change: 100 * (d.podium - baseline.get(d.id)) }));
    const ordered = deltas.sort((a, b) => b.change - a.change || a.id.localeCompare(b.id));
    const rows = [...ordered.filter((p) => p.change >= .005).slice(0, 3), ...ordered.filter((p) => p.change <= -.005).slice(-3)];
    const span = Math.max(.005, ...rows.map((p) => Math.abs(p.change)));
    byId("swing-cuts").textContent = `${cutName(previous)} → ${cutName(cut)} · ${rows.length ? "Mayores subidas y bajadas entre cortes observados." : "Ningún cambio llega a 0,01 puntos."}`;
    byId("swing-rows").replaceChildren(...rows.map(({ id, before, now, change }) => {
      const li = make("li");
      const bar = make("span", undefined, "swing-track");
      bar.setAttribute("aria-hidden", "true");
      bar.append(make("i"));
      li.dataset.direction = change > 0 ? "up" : "down";
      li.style.setProperty("--magnitude", `${50 * Math.abs(change) / span}%`);
      li.append(make("b", id), bar, make("strong", `${signed.format(change)} pp`), make("small", `${pct.format(before)} → ${pct.format(now)}`));
      return li;
    }));
  }

  const results = byId("resultados");
  function resultCard(race) {
    const card = make("article", undefined, "panel result-card");
    const top = make("div", undefined, "insight-head");
    const heading = make("div");
    heading.append(make("p", `GP ${race.gp} · Fecha local: ${race.date}`, "micro"), make("h3", race.name));
    const status = { final: "Clasificación final", provisional: "Provisional", pending: "Pendiente" }[race.status];
    top.append(heading, make("span", status, "badge"));
    card.append(top);
    const predicted = race.forecast;
    const official = race.official;
    const comparable = predicted && official;
    const scored = race.status === "final" && comparable;
    if (comparable) {
      const exact = predicted.filter((code, i) => code === official[i]).length;
      const members = predicted.filter((code) => official.includes(code)).length;
      const hits = make("p", undefined, "result-verdict");
      hits.append(make("strong", `${exact}/3`), document.createTextNode(scored
        ? ` puestos exactos · ${members}/3 pilotos en el podio`
        : ` puestos coinciden por ahora · ${members}/3 pilotos previstos. Sin veredicto definitivo.`));
      card.append(hits);
    } else {
      card.append(make("p", !predicted ? "Sin pronóstico T−30 verificable; no se puntúa."
        : "Sin clasificación final confirmada; no se puntúa.", "result-verdict"));
    }
    const table = make("table", undefined, "result-table");
    table.setAttribute("aria-label", `Pronóstico y clasificación de ${race.name}`);
    const tr = table.createTHead().insertRow();
    for (const title of ["Puesto", "Previsto", "Resultado", "Acierto"]) { const th = make("th", title); th.scope = "col"; tr.append(th); }
    const body = table.createTBody();
    for (let i = 0; i < 3; i += 1) {
      const row = body.insertRow();
      const hit = comparable && predicted[i] === official[i];
      const th = make("th", `${i + 1}.º`); th.scope = "row";
      const verdict = make("td", scored ? (hit ? "Sí" : "No") : comparable ? (hit ? "Coincide" : "Difiere") : "—");
      if (scored) verdict.dataset.hit = String(hit);
      row.append(th);
      for (const code of [predicted?.[i], official?.[i]]) {
        const cell = make("td", code ?? "—");
        if (race.names?.[code]) cell.append(make("small", race.names[code]));
        row.append(cell);
      }
      row.append(verdict);
    }
    const evidence = make("div", undefined, "result-source");
    if (predicted) {
      const origin = race.origin === "published" ? "Pronóstico publicado" : "Backtest · no publicado entonces";
      evidence.append(make("span", `${origin} · T−30: ${race.cutoff_utc.replace("T", " ").slice(0, 16)} UTC · ${race.model}`));
    }
    const url = new URL(race.source_url);
    if (url.protocol === "https:") {
      const link = make("a", `${race.status === "pending" ? "Consultar el estado" : "Ver clasificación"} ↗`);
      link.href = url.href;
      evidence.append(link);
    }
    if (race.result_url && race.result_url !== race.source_url) {
      const link = make("a", "Tabla de Fórmula 1 ↗");
      link.href = race.result_url;
      evidence.append(link);
    }
    if (race.forecast_urls.length) {
      const details = make("details");
      details.append(make("summary", "Fuentes de la previa"));
      for (const url of race.forecast_urls) {
        const link = make("a", `${new URL(url).hostname} ↗`);
        link.href = url;
        details.append(link);
      }
      evidence.append(details);
    }
    card.append(table, evidence);
    return card;
  }

  async function loadResults() {
    const cards = byId("recent-cards");
    try {
      const res = await fetch("./data/recent-podiums.json", { cache: "no-cache", signal: AbortSignal.timeout(8000) });
      if (!res.ok) throw new Error();
      const report = await res.json();
      const stamp = document.createElement("p");
      stamp.className = "micro result-updated";
      stamp.textContent = `Fuentes consultadas: ${report.as_of_utc.replace("T", " ").slice(0, 16)} UTC`;
      const latest = [...report.pending, ...report.races].sort((a, b) => b.gp.localeCompare(a.gp)).slice(0, 2);
      cards.replaceChildren(...latest.map(resultCard), stamp);
    } catch { cards.textContent = "No se pudo leer la comparación de podios."; }
  }
  if (results) void loadResults();
  return { showChances, teamColor };
})();
