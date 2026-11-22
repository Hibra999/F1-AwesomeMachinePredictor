"use strict";

const ui = Object.fromEntries([...document.querySelectorAll("[id]")].map((node) => [node.id, node]));
const periods = { walk_forward: "Validación 2024–25", final: "Final 2026 · rondas 01–20" };
const horizons = { fp1: "Pre-FP1", pre_race: "T−30", race: "Vueltas" };
const models = { reference: "Referencia", elo: "Elo", lightgbm: "LightGBM", ngboost: "NGBoost", catboost: "CatBoost", average: "Ensamble simple", learned: "Ensamble aprendido" };
const families = ["lightgbm", "ngboost", "catboost"];
const metrics = {
  brier: "Brier de podio", winner_log_loss: "Log loss del ganador", winner_hit: "Acierto de ganador",
  ordered_hit: "Podio exacto", map_members_hit: "Tres miembros MAP", podium_ece: "ECE de podio", winner_ece: "ECE del ganador",
};
const groups = {
  driver_form: "Forma del piloto", team_form: "Forma del equipo", circuit: "Circuito", pace: "Ritmo",
  qualifying: "Clasificación", grid: "Parrilla", sprint: "Sprint", position: "Posición", degradation: "Degradación",
  pit: "Paradas", weather: "Meteorología", neutralization: "Neutralización", penalties: "Sanciones",
};
const variables = {
  driver_form: "Forma del piloto", driver_podium: "Podios del piloto", driver_elo: "Elo del piloto", driver_starts: "Salidas del piloto",
  team_form: "Forma del equipo", team_podium: "Podios del equipo", team_elo: "Elo del equipo",
  driver_here: "Piloto en circuito", team_here: "Equipo en circuito", latitude: "Latitud", longitude: "Longitud",
  driver_pace: "Ritmo del piloto", team_pace: "Ritmo del equipo", practice_gap: "Brecha en libres", lap_pace: "Ritmo de vuelta", gap_same_lap: "Brecha misma vuelta",
  qual_position: "Posición en Q", qual_gap: "Brecha en Q", grid_position: "Posición de parrilla", sprint_position: "Posición en sprint",
  position: "Posición en carrera", laps_down: "Vueltas perdidas", progress: "Progreso de carrera", stint_slope: "Pendiente del stint", tyre_age: "Edad del neumático",
  stops: "Paradas", laps_since_stop: "Vueltas desde parada", air: "Temperatura del aire", track_temp: "Temperatura de pista",
  rain: "Lluvia", forecast_air: "Temperatura prevista", forecast_rain: "Lluvia prevista", neutralized: "Neutralización", track_yellow: "Bandera amarilla",
  announced_seconds: "Segundos de sanción",
};
const pctMetrics = new Set(["winner_hit", "ordered_hit", "map_members_hit", "podium_ece", "winner_ece"]);
const higher = new Set(["winner_hit", "ordered_hit", "map_members_hit"]);
const number = (options) => new Intl.NumberFormat("es-ES", options);
const decimals = number({ maximumFractionDigits: 6 });
const score = number({ minimumFractionDigits: 5, maximumFractionDigits: 5 });
const integer = number({ maximumFractionDigits: 0 });
const short = number({ maximumFractionDigits: 2 });
const pct = number({ style: "percent", maximumFractionDigits: 2 });
const signed = number({ signDisplay: "exceptZero", maximumFractionDigits: 6 });
const theme = getComputedStyle(document.querySelector(".lab"));
const token = (name) => theme.getPropertyValue(`--${name}`).trim();
const colors = { fg: token("fg"), muted: token("muted"), line: token("line"), accent: token("accent"), mint: token("mint"), gold: token("gold"), blue: token("lilac") };
const familyColors = { lightgbm: colors.blue, ngboost: colors.mint, catboost: colors.accent };
let report;

const fmt = (value, formatter = decimals) => Number.isFinite(value) ? formatter.format(value) : "—";
const metricText = (key, value) => fmt(value, pctMetrics.has(key) ? pct : score);
const familyName = (key) => models[key.split("/")[0]] ?? key;
const duration = (value) => `${fmt(value, short)} s`;
const missingRate = (feature) => feature.n > 0 ? feature.missing / feature.n : null;
const axis = { color: colors.muted, gridcolor: colors.line, zerolinecolor: colors.line, fixedrange: true, automargin: true };
const legend = { orientation: "h", x: 0, y: -.22, font: { size: 10 } };

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function set(id, text) {
  ui[id].textContent = text;
}

function kpis(id, rows) {
  ui[id].replaceChildren(...rows.map(([label, value, note, tone]) => {
    const card = el("div");
    card.className = "panel";
    if (tone) card.dataset.tone = tone;
    card.append(el("dt", label), el("dd", value));
    if (note) card.append(el("small", note));
    return card;
  }));
}

function row(label, values) {
  const tr = el("tr");
  const cell = el("th", label);
  cell.scope = "row";
  tr.append(cell, ...values.map((value) => el("td", value)));
  return tr;
}

function plot(id, traces, layout = {}) {
  if (!window.Plotly) {
    set(id, "Gráfico no disponible. Consulta las cifras del informe.");
    return;
  }
  const { xaxis, yaxis, margin, ...options } = layout;
  void Plotly.react(ui[id], traces, {
    paper_bgcolor: "transparent", plot_bgcolor: "transparent", showlegend: false, hovermode: "closest",
    margin: { l: 48, r: 15, t: 8, b: 45, ...margin },
    font: { color: colors.muted, family: token("mono"), size: 11 },
    xaxis: { ...axis, ...xaxis }, yaxis: { ...axis, ...yaxis },
    legend, ...options,
  }, { responsive: true, displayModeBar: false }).catch(() => set(id, "No se pudo mostrar el gráfico."));
}

function bars(x, y, color, name, extra = {}) {
  return { type: "bar", name, x, y, marker: { color }, ...extra };
}

function renderCalibration(block) {
  const target = ui["calibration-target"].value;
  const traces = [{ type: "scatter", mode: "lines", x: [0, 1], y: [0, 1], name: "Ideal", line: { color: colors.line, dash: "dot" }, hoverinfo: "skip" }];
  const names = block.selected === "reference" ? ["reference"] : ["reference", block.selected];
  for (const name of names) {
    const points = block.metrics[name][`${target}_calibration`];
    const color = name === block.selected ? colors.mint : colors.gold;
    traces.push({
      type: "scatter", mode: "lines+markers", name: models[name] ?? name,
      x: points.map((p) => p.p), y: points.map((p) => p.rate), customdata: points.map((p) => fmt(p.weight, pct)),
      line: { color, width: 2 }, marker: { color, size: 6 },
      hovertemplate: "Prevista %{x:.1%}<br>Observada %{y:.1%}<br>Peso del intervalo %{customdata}<extra>%{fullData.name}</extra>",
    });
  }
  plot("calibration-plot", traces, {
    xaxis: { title: { text: "Prevista" }, range: [0, 1], tickformat: ".0%" },
    yaxis: { title: { text: "Observada" }, range: [0, 1], tickformat: ".0%" }, showlegend: true, margin: { b: 70 },
  });
}

function renderEvaluation() {
  const period = ui["eval-period"].value;
  const horizon = ui["eval-horizon"].value;
  const metric = ui["eval-metric"].value;
  const block = report.evaluation[period][horizon];
  const selected = block.metrics[block.selected];
  const reference = block.metrics.reference;
  const name = models[block.selected] ?? block.selected;
  const gap = selected.brier - reference.brier;
  const coverage = block.coverage;
  const sources = Object.entries(coverage.reference_gps);
  kpis("eval-kpis", [
    ["GP y cortes", `${fmt(coverage.gps, integer)} GP`, `${fmt(coverage.cuts, integer)} cortes · ${horizons[horizon]}`],
    ["Brier de podio", metricText("brier", selected.brier), `${name} · Δ vs ref. ${fmt(gap, signed)}`, gap < 0 ? "better" : gap > 0 ? "worse" : ""],
    ["Log loss del ganador", metricText("winner_log_loss", selected.winner_log_loss), `Referencia: ${metricText("winner_log_loss", reference.winner_log_loss)}`],
    ["Podio exacto", fmt(selected.ordered_hit, pct), `Acierto de ganador: ${fmt(selected.winner_hit, pct)}`],
  ]);
  set("scores-caption", `${periods[period]} · ${metrics[metric]}. Cuanto ${higher.has(metric) ? "mayor" : "menor"}, mejor. Resaltado: ${name}.`);
  const ranked = Object.entries(block.metrics).sort((a, b) => (a[1][metric] - b[1][metric]) * (higher.has(metric) ? -1 : 1));
  plot("scores-plot", [bars(ranked.map(([, v]) => v[metric]), ranked.map(([id]) => models[id] ?? id),
    ranked.map(([id]) => id === block.selected ? colors.mint : id === "reference" ? colors.gold : colors.blue), "Resultado", {
      orientation: "h", customdata: ranked.map(([, v]) => metricText(metric, v[metric])), hovertemplate: "%{y}<br>%{customdata}<extra></extra>",
    })], { xaxis: pctMetrics.has(metric) ? { range: [0, 1], dtick: .25, tickformat: ".0%" } : { rangemode: "tozero" }, yaxis: { autorange: "reversed", showgrid: false }, margin: { l: 121, b: 35 } });
  renderCalibration(block);

  const folds = report.evaluation.walk_forward[horizon].folds;
  const windows = folds.map(({ start }) => `${start.slice(0, 4)} S${start.slice(5, 7) === "01" ? 1 : 2}`);
  plot("folds-plot", [
    bars(windows, folds.map((f) => f.inner_oos_gps), colors.blue, "Calibración previa"),
    bars(windows, folds.map((f) => f.test_gps), colors.accent, "Prueba"),
  ], { barmode: "group", showlegend: true, yaxis: { rangemode: "tozero", title: { text: "GP" } }, margin: { b: 64 } });

  ui["reference-coverage"].hidden = horizon === "race";
  ui["race-segments"].hidden = horizon !== "race";
  if (horizon === "race") {
    const thirds = ["early", "middle", "late"];
    const labels = ["Inicial", "Medio", "Final"];
    const cutCount = thirds.map((key) => fmt(selected.segments[key].cuts, integer)).join(" / ");
    set("segments-caption", `${periods[period]} · ${metrics[metric]}. Cortes por tercio: ${cutCount}.`);
    plot("segments-plot", [
      bars(labels, thirds.map((k) => reference.segments[k][metric]), colors.gold, "Referencia"),
      bars(labels, thirds.map((k) => selected.segments[k][metric]), colors.mint, name),
    ], { barmode: "group", showlegend: true, yaxis: pctMetrics.has(metric) ? { range: [0, 1], dtick: .25, tickformat: ".0%" } : { rangemode: "tozero" }, margin: { b: 64 } });
  } else {
    plot("reference-plot", [bars(sources.map(([k]) => k), sources.map(([, count]) => count), colors.blue, "Fuente")],
      { yaxis: { range: [0, coverage.gps * 1.12], title: { text: "GP" } } });
  }
  const recipe = block.recipe?.map(([id, temperature, weight]) => `${id} · T=${fmt(temperature, short)} · peso ${fmt(weight, pct)}`).join("; ");
  set("eval-coverage", `${coverage.roster_exact_cuts}/${coverage.cuts} cortes con alineación oficial exacta. Referencia por GP: ${sources.map(([k, n]) => `${k}: ${n}`).join(" · ")}.${recipe ? ` Receta sellada: ${recipe}.` : ""}`);
  ui["eval-table"].replaceChildren(...ranked.map(([id, value]) => {
    const entry = row(models[id] ?? id, Object.keys(metrics).map((key) => metricText(key, value[key])));
    entry.toggleAttribute("data-selected", id === block.selected);
    return entry;
  }));
}

function renderRankingStudy() {
  const { metrics, brier_contrasts: pairs, taf, corrected_clocks: clocks, decision } = report.ranking;
  const verdict = decision === "sombra" ? "El ranking pasa a prueba en sombra; aún se publica Elo." : "Se conserva Elo.";
  set("ranking-decision", `${verdict} Brier de podio en ${metrics.elo.gps} GP: ${fmt(metrics.elo.brier, score)} con Elo y ${fmt(metrics.rank_pl.brier, score)} con el ranking.`);
  set("ranking-context", `Se corrigieron ${clocks} horarios antes de medir. Hubo boletín de aeropuerto en ${taf.usable} GP y aviso de lluvia en ${taf.wet}.`
    + (taf.any_wet_train ? "" : " Sin avisos en entrenamiento no se pudo estimar su efecto."));
  const comparisons = [pairs.rank_pl_minus_elo, pairs.rank_pl_taf_minus_rank_pl];
  const labels = ["Ranking − Elo", "Con TAF − sin TAF"];
  const delta = comparisons.map((p) => p.delta);
  plot("ranking-plot", [{
    type: "scatter", mode: "markers", x: delta, y: labels, marker: { color: colors.mint, size: 10 },
    error_x: { type: "data", symmetric: false, arrayminus: comparisons.map((p) => p.delta - p.ci95[0]),
      array: comparisons.map((p) => p.ci95[1] - p.delta), color: colors.mint },
    hovertemplate: "%{y}: %{x:.5f}<extra></extra>",
  }], { xaxis: { title: { text: "Δ Brier · menor es mejor" }, zeroline: true, zerolinecolor: colors.gold, tickformat: ".3f" },
    yaxis: { type: "category", autorange: "reversed" }, height: 185 });
  set("ranking-interval", comparisons.map((p, i) => `${labels[i]}: ${fmt(p.delta, signed)} (IC 95 %: ${fmt(p.ci95[0], signed)} a ${fmt(p.ci95[1], signed)})`).join(" · "));
}

function renderDriverStudy() {
  const { metrics: m, coverage: c, contrasts, decision } = report.driver_performance;
  const labels = ["Referencia T−30", "CatBoost base", "CatBoost + dos señales"];
  const means = [m.reference.mae, m.base.mae, m.extended.mae];
  const places = number({ minimumFractionDigits: 3, maximumFractionDigits: 3 });
  const best = m.extended.mae < m.reference.mae ? "reduce" : "no reduce";
  set("driver-study-result", `En ${c.gps} GP de 2024–25, el candidato ${best} el error frente a la referencia: ${fmt(m.extended.mae, places)} frente a ${fmt(m.reference.mae, places)} puestos. No cambia el modelo publicado.`);
  plot("driver-study-plot", [bars(means, labels, [colors.gold, colors.blue, colors.mint], "MAE", {
    orientation: "h", text: means.map((v) => fmt(v, places)), textposition: "inside", insidetextanchor: "end",
    hovertemplate: "%{y}: %{x:.3f} puestos<extra></extra>",
  })], { xaxis: { title: { text: "Error medio en puestos" }, rangemode: "tozero" },
    yaxis: { type: "category", autorange: "reversed" }, height: 200 });
  const obs = c.feature_nonmissing;
  set("driver-study-coverage", `${c.matched}/${c.predicted} pilotos previstos con puesto oficial. Tandas largas de FP1: ${obs.fp1_run_field_pct}/${c.predicted}; ritmo contra el compañero en la misma fase de clasificación: ${obs.qual_teammate_pct}/${c.predicted}. Una señal ausente no equivale a cero.`);
  const delta = contrasts.mae.base;
  set("driver-study-interval", `Añadir las dos señales: ΔMAE ${fmt(delta.delta, signed)} puestos frente a CatBoost base (IC 95 % por GP: ${fmt(delta.ci95[0], signed)} a ${fmt(delta.ci95[1], signed)}). Decisión: ${decision}. Para el podio: Brier ${fmt(m.extended.brier, score)} frente a ${fmt(m.elo.brier, score)} de Elo.`);
}

const paramsText = (params) => Object.entries(params).map(([k, v]) => `${k}=${fmt(v)}`).join(" · ");

function renderTrials() {
  const { cycles } = report.rustuna;
  const index = Number(ui["trial-window"].value);
  const cycle = cycles[index];
  const testRanges = cycle.inner_folds.map((f) => `${f.cutoff.slice(0, 10)}: ${f.fit_gps} ajuste / ${f.oos_gps} OOS`).join("; ");
  set("trial-context", `${index === cycles.length - 1 ? "Ventana de refit antes de 2026. " : "Búsqueda dentro del entrenamiento. "}Bloques internos (GP): ${testRanges}. Cada punto es un ensayo; la línea es el mejor Brier interno acumulado.`);
  const traces = [];
  const bestRows = [];
  const allRows = [];
  for (const family of families) {
    const study = cycle.studies[`${family}/rustuna`];
    const name = familyName(family);
    const line = { color: familyColors[family], width: 2 };
    let best = Infinity;
    const x = study.trials.map((t) => t.trial + 1);
    const bestSoFar = study.trials.map((t) => (best = Math.min(best, t.brier)));
    traces.push({ type: "scatter", mode: "lines", x, y: bestSoFar, name, legendgroup: family, line, hoverinfo: "skip" });
    traces.push({
      type: "scatter", mode: "markers", x, y: study.trials.map((t) => t.brier), name, legendgroup: family,
      showlegend: false, marker: { color: line.color, size: 7, line: { color: colors.fg, width: 1 } },
      customdata: study.trials.map((t) => [duration(t.seconds), paramsText(t.params)]),
      hovertemplate: "Ensayo %{x} · %{y:.5f}<br>%{customdata[0]}<br>%{customdata[1]}<extra>%{fullData.name}</extra>",
    });
    const times = study.trials.map((t) => t.seconds).sort((a, b) => a - b);
    const median = (times[Math.floor((times.length - 1) / 2)] + times[Math.floor(times.length / 2)]) / 2;
    bestRows.push(row(name, [study.best_trial + 1, fmt(study.brier, score), duration(study.seconds), duration(median), paramsText(study.params)]));
    allRows.push(...study.trials.map((t) => row(name, [t.trial + 1, fmt(t.brier, score), duration(t.seconds), paramsText(t.params)])));
  }
  plot("trials-plot", traces, { xaxis: { title: { text: "Ensayo" }, dtick: 1 }, yaxis: { title: { text: "Brier interno" }, tickformat: ".3f" }, showlegend: true, margin: { b: 70 } });
  ui["trial-table"].replaceChildren(...bestRows);
  ui["all-trial-rows"].replaceChildren(...allRows);
}

function renderTraining() {
  const { summary, cycles } = report.rustuna;
  const search = summary.search;
  const environment = summary.environment;
  set("training-context", `${environment.os} · Python ${environment.python} · ${environment.estimator_device}. Rustuna ${environment.rustuna}, rueda ${environment.wheel_abi}.`);
  kpis("training-kpis", [
    ["Entrenamiento original", duration(report.model.wall_seconds), "Incluye selección y calibración"],
    ["Entrenamiento con ajuste", duration(search.wall_seconds_train_and_selection), "Incluye búsqueda y selección"],
    ["Búsqueda Rustuna", duration(search.wall_seconds_sum_of_studies), "Suma de tiempo por estudio"],
    ["Ensayos", `${search.trials_total}`, `${search.trials_per_family_per_window} por familia y ventana · ${search.sampler} · semilla ${search.seed}`],
  ]);
  const horizonKeys = Object.keys(horizons);
  plot("runtime-plot", families.map((family) => bars(Object.values(horizons), horizonKeys.map((h) =>
    ["small", "large"].reduce((sum, preset) => sum + report.model.model_seconds[`${h}:${family}/${preset}`], 0)), familyColors[family], familyName(family))),
  { barmode: "group", showlegend: true, yaxis: { title: { text: "Segundos" }, rangemode: "tozero" }, margin: { b: 64 } });

  const study = summary.walk_forward_2024_25;
  const result = study.podium_brier;
  set("rustuna-comparison-caption", `Mismos ${study.gp_count} GP de 2024–25. Línea dorada: Elo conservó el mejor Brier (${fmt(result.elo, score)}). No se usó 2026 para ajustar.`);
  const names = families.map(familyName);
  plot("rustuna-comparison-plot", [
    bars(names, families.map((f) => result[f].original_policy), colors.blue, "Política original"),
    bars(names, families.map((f) => result[f].best_fixed_preset_observed), colors.mint, "Mejor preset observado"),
    bars(names, families.map((f) => result[f].rustuna), colors.accent, "Rustuna"),
  ], { barmode: "group", showlegend: true, yaxis: { rangemode: "tozero", tickformat: ".3f" }, margin: { b: 82 },
    shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: result.elo, y1: result.elo, line: { color: colors.gold, width: 1, dash: "dot" } }],
  });
  ui["trial-window"].replaceChildren(...cycles.map(({ start }, i) => new Option(start.slice(0, 10), i)));
  ui["trial-window"].value = cycles.length - 1;
  renderTrials();
  renderTunedImportance();
}

function impactChart(id, impact) {
  const ranked = impact.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 9);
  plot(id, [bars(ranked.map(([, delta]) => 1000 * delta), ranked.map(([name]) => name),
    ranked.map(([, delta]) => delta < 0 ? colors.gold : colors.mint), "Δ Brier", {
      orientation: "h", customdata: ranked.map(([, delta]) => fmt(delta, signed)), hovertemplate: "%{y}<br>Δ Brier %{customdata}<extra></extra>",
    })], { xaxis: { title: { text: "Δ Brier × 1.000" }, zerolinewidth: 2 }, yaxis: { autorange: "reversed", showgrid: false }, margin: { l: 130, b: 50 } });
}

function renderTunedImportance() {
  const preset = ui["tuned-family"].value;
  const study = report.rustuna.importance[preset];
  set("tuned-importance-caption", `${familyName(preset)} · ${fmt(study.gps, integer)} GP de 2024–25 · Brier original ${fmt(study.baseline_brier, score)}. Δ positivo: al enmascarar la variable empeora el resultado. Temperatura y GP se mantienen fijos.`);
  impactChart("tuned-importance-plot", study.features.map((f) => [variables[f.name] ?? f.name, f.oos_delta_brier]));
  ui["tuned-feature-table"].replaceChildren(...[...study.features].sort((a, b) => b.oos_delta_brier - a.oos_delta_brier).map((f) =>
    row(variables[f.name] ?? f.name, [fmt(f.oos_delta_brier, signed), fmt(f.oos_evidence_gps, integer)])));
}

function renderImportance(horizon, data) {
  const byGroup = ui["importance-level"].value === "group";
  const impact = byGroup ? Object.entries(report.evaluation.walk_forward[horizon].group_ablation).map(([k, delta]) => [groups[k] ?? k, delta])
    : data.features.filter((f) => Number.isFinite(f.oos_delta_brier)).map((f) => [variables[f.name] ?? f.name, f.oos_delta_brier]);
  const coverage = data.importance_coverage;
  const suffix = coverage ? ` · ${fmt(coverage.gps, integer)} GP / ${fmt(coverage.cuts, integer)} cortes` : "";
  set("importance-caption", byGroup
    ? "LightGBM fijo reentrenado sin cada grupo. Δ Brier positivo: quitarlo empeora el resultado."
    : `${report.eda.importance.method}${suffix}. Δ Brier positivo: al enmascarar la variable empeora el resultado.`);
  impactChart("importance-plot", impact);
}

function renderEda() {
  const horizon = ui["eda-horizon"].value;
  const { eda } = report;
  const data = eda.horizons[horizon];
  const importance = eda.importance;
  set("eda-context", `${eda.period.start_year}–${eda.period.end_year} · corpus anterior al período final. Importancia evaluada en ${importance.period.start_year}–${importance.period.end_year}.`);
  set("importance-method", `Importancia individual · Brier original ${fmt(data.importance_coverage.baseline_brier, score)}. ${importance.notes.join(" ")}`);
  kpis("eda-kpis", [
    ["Grandes Premios", fmt(data.gps, integer), "Con cortes elegibles"], ["Cortes", fmt(data.cuts, integer), horizons[horizon]],
    ["Filas de piloto", fmt(data.driver_rows, integer), "Una fila por piloto y corte"], ["Variables", fmt(data.features.length, integer), "Incluye columnas sin evidencia"],
  ]);
  const year = data.by_year;
  plot("years-plot", [bars(year.map((y) => String(y.year)), year.map((y) => y.cuts), colors.blue, "Cortes", {
    customdata: year.map((y) => [fmt(y.gps, integer), fmt(y.driver_rows, integer)]),
    hovertemplate: "%{x}<br>%{y} cortes<br>%{customdata[0]} GP<br>%{customdata[1]} filas<extra></extra>",
  })], { xaxis: { type: "category" }, yaxis: { title: { text: "Cortes" }, rangemode: "tozero" } });
  plot("entrants-plot", [bars(data.entrants.map((g) => g.drivers), data.entrants.map((g) => g.cuts), colors.mint, "Cortes")],
    { xaxis: { title: { text: "Pilotos por corte" }, dtick: 1 }, yaxis: { title: { text: "Cortes" }, rangemode: "tozero" } });
  const missing = [...data.features].sort((a, b) => missingRate(b) - missingRate(a)).slice(0, 9);
  set("missing-caption", `Las ${missing.length} columnas con más faltantes. Los ceros observados no cuentan como faltantes.`);
  plot("missing-plot", [bars(missing.map(missingRate), missing.map((f) => variables[f.name] ?? f.name), colors.gold, "Faltantes", {
    orientation: "h", customdata: missing.map((f) => [fmt(f.missing, integer), fmt(f.n, integer)]),
    hovertemplate: "%{y}<br>%{x:.1%} faltante<br>%{customdata[0]} de %{customdata[1]} filas<extra></extra>",
  })], { xaxis: { range: [0, 1], tickformat: ".0%" }, yaxis: { autorange: "reversed", showgrid: false }, margin: { l: 130, b: 38 } });
  const ranked = [...data.features].sort((a, b) => (b.oos_delta_brier ?? -Infinity) - (a.oos_delta_brier ?? -Infinity));
  ui["feature-table"].replaceChildren(...ranked.map((f) => {
    const entry = row(variables[f.name] ?? f.name, [groups[f.group] ?? f.group, fmt(missingRate(f), pct), fmt(f.p10), fmt(f.median), fmt(f.p90), fmt(f.oos_delta_brier, signed)]);
    entry.firstChild.append(el("small", f.name));
    entry.lastChild.toggleAttribute("data-positive", f.oos_delta_brier > 0);
    entry.lastChild.toggleAttribute("data-negative", f.oos_delta_brier < 0);
    entry.append(el("td", fmt(f.oos_evidence_gps, integer)));
    return entry;
  }));
  renderImportance(horizon, data);
}

function renderProtocol() {
  const leakage = report.leakage;
  set("leakage-context", `GP ${leakage.event}, semilla ${leakage.seed}, tolerancia absoluta ${leakage.absolute_tolerance.toExponential()}.`);
  ui["leakage-results"].replaceChildren(...leakage.tests.map((test) => {
    const card = el("div");
    card.append(el("dt", test.lap ? `Vuelta ${test.lap}` : horizons[test.horizon]), el("dd", `Δ retirada ${test.max_delta_removed} · Δ alteración ${test.max_delta_poisoned}`));
    return card;
  }));
}

async function load() {
  try {
    const response = await fetch("./data/analysis.json", { cache: "no-cache", signal: AbortSignal.timeout(8_000) });
    if (!response.ok) throw new Error(`No se pudo cargar el informe (HTTP ${response.status}).`);
    report = await response.json();
    if (report.schema !== 1) throw new Error("Versión del informe no compatible.");
    set("lab-version", report.model.version);
    set("lab-limit", `${report.model.train_max_time_exclusive.slice(0, 10)} UTC`);
    ui["lab-content"].hidden = false;
    renderEvaluation();
    renderRankingStudy();
    renderDriverStudy();
    renderTraining();
    renderEda();
    renderProtocol();
    ui["lab-status"].hidden = true;
    for (const id of ["eval-period", "eval-horizon", "eval-metric", "calibration-target"]) ui[id].addEventListener("change", renderEvaluation);
    ui["trial-window"].addEventListener("change", renderTrials);
    ui["tuned-family"].addEventListener("change", renderTunedImportance);
    ui["eda-horizon"].addEventListener("change", renderEda);
    ui["importance-level"].addEventListener("change", () => {
      const horizon = ui["eda-horizon"].value;
      renderImportance(horizon, report.eda.horizons[horizon]);
    });
  } catch (error) {
    ui["lab-content"].hidden = true;
    ui["lab-status"].dataset.state = "error";
    set("lab-status", error instanceof TypeError || error instanceof SyntaxError ? "No se pudo leer el informe de análisis." : error.message);
  }
}

void load();
