"use strict";

const circuitMap = (() => {
  if (typeof module === "object" && module.exports) {
    module.exports = { project, pointAtLap };
    return;
  }
  const el = (id) => document.getElementById(`circuit-${id}`);
  const panel = el("panel");
  const stage = el("stage");
  const canvas = el("3d");
  const svg = el("2d");
  const dialog = el("dialog");
  const expand = el("expand");
  const home = panel.parentNode;
  const makeSvg = (tag) => document.createElementNS("http://www.w3.org/2000/svg", tag);
  const overlay = makeSvg("svg");
  overlay.classList.add("circuit-marks");
  overlay.setAttribute("aria-hidden", "true");
  stage.append(overlay);
  const finish = makeSvg("g");
  finish.classList.add("circuit-finish");
  const gate = makeSvg("line");
  gate.setAttribute("y1", "-6");
  gate.setAttribute("y2", "6");
  const arrow = makeSvg("polygon");
  arrow.setAttribute("points", "4,-3 10,0 4,3");
  finish.append(gate, arrow);
  stage.after(el("mode"));
  const picker = document.createElement("label");
  picker.className = "circuit-picker";
  picker.append("Piloto en el mapa ");
  const select = document.createElement("select");
  picker.append(select);
  el("status").before(picker);
  const aliases = [
    ["interlagos", /-(?:2026-20|interlagos|sao-paulo|brazil|brazilian|brasil|jose-carlos-pace)-/],
    ["las-vegas", /-(?:2026-21|las-vegas)-/],
    ["lusail", /-(?:2026-22|lusail|losail|qatar|catar)-/],
    ["yas-marina", /-(?:2026-23|yas-marina|abu-dhabi|united-arab-emirates|emiratos-arabes)-/],
  ];
  const defaultCamera = { azimuth: -.38, elevation: .85, zoom: 1 };
  const camera = { ...defaultCamera };
  let tracks;
  let track;
  let prediction;
  let selected;
  let onPick;
  let marks = [];
  let key;
  let graphics;
  let graphicsTried = false;
  let prefer3d = true;
  let drag;

  function circuitKey(event) {
    const { circuit } = event;
    for (const value of [circuit?.id, circuit?.name, circuit, event.id, event.name]) {
      if (typeof value !== "string") continue;
      const token = value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
      const known = aliases.find(([, pattern]) => pattern.test(`-${token}-`));
      if (known) return known[0];
    }
    return null;
  }

  function project({ properties, geometry }) {
    const coords = geometry?.coordinates;
    if (geometry?.type !== "LineString" || !Array.isArray(coords) || coords.length < 4
        || coords.some((pair) => !Array.isArray(pair) || pair.length !== 2 || !pair.every(Number.isFinite)
          || Math.abs(pair[0]) > 180 || Math.abs(pair[1]) > 90)
        || coords[0].some((value, i) => value !== coords.at(-1)[i])
        || !/^(way|relation)\/\d+$/.test(properties?.osm)) {
      throw new Error("Eje local no válido");
    }
    const lon = coords.map(([x]) => x);
    const lat = coords.map(([, y]) => y);
    const cx = (Math.max(...lon) + Math.min(...lon)) / 2;
    const cy = (Math.max(...lat) + Math.min(...lat)) / 2;
    const cos = Math.cos(cy * Math.PI / 180);
    const scale = Math.max((Math.max(...lon) - Math.min(...lon)) * cos, Math.max(...lat) - Math.min(...lat)) / 2;
    if (!scale) throw new Error("Eje local degenerado");
    const points = coords.map(([x, y]) => [(x - cx) * cos / scale, (y - cy) / scale]);
    const distances = [0];
    for (let i = 1; i < points.length; i++) {
      distances.push(distances[i - 1] + Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]));
    }
    const [segment, part] = properties.timing_line ?? [];
    if (!Number.isInteger(segment) || segment < 0 || segment >= points.length - 1
        || !Number.isFinite(part) || part < 0 || part > 1 || ![1, -1].includes(properties.direction)) {
      throw new Error("Línea de meta o sentido no válidos");
    }
    const origin = distances[segment] + part * (distances[segment + 1] - distances[segment]);
    return { ...properties, points, distances, origin };
  }

  function pointAtLap({ points, distances, origin, direction }, fraction) {
    const length = distances.at(-1);
    const target = ((origin + direction * (fraction % 1) * length) % length + length) % length;
    const i = distances.findIndex((distance) => distance > target);
    const t = (target - distances[i - 1]) / (distances[i] - distances[i - 1]);
    return points[i].map((value, axis) => points[i - 1][axis] + t * (value - points[i - 1][axis]));
  }

  function makeGraphics() {
    const gl = canvas.getContext("webgl", { antialias: true });
    if (!gl) return null;
    const program = gl.createProgram();
    for (const [type, source] of [
      [gl.VERTEX_SHADER, `
        attribute vec3 point;
        attribute float tone;
        uniform vec3 camera;
        uniform vec2 fit;
        varying float tint;
        void main() {
          float c = cos(camera.x), s = sin(camera.x);
          vec2 r = vec2(point.x*c - point.y*s, point.x*s + point.y*c);
          float e = sin(camera.y), f = cos(camera.y);
          gl_Position = vec4(fit * camera.z * vec2(r.x, r.y*e + point.z*f),
                             (r.y*f - point.z*e) * .5, 1.0);
          tint = tone;
        }
      `],
      [gl.FRAGMENT_SHADER, `
        precision mediump float;
        uniform vec3 trackColor;
        uniform vec3 edgeColor;
        varying float tint;
        void main() {
          gl_FragColor = vec4(mix(edgeColor, trackColor, tint), 1.0);
        }
      `],
    ]) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      gl.attachShader(program, shader);
      gl.deleteShader(shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
    gl.useProgram(program);
    const style = getComputedStyle(canvas);
    for (const [name, color] of [["trackColor", style.color], ["edgeColor", style.borderTopColor]]) {
      gl.uniform3fv(gl.getUniformLocation(program, name), color.match(/\d+(\.\d+)?/g).slice(0, 3).map((v) => Number(v) / 255));
    }
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    for (const [name, size, offset] of [["point", 3, 0], ["tone", 1, 12]]) {
      const loc = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 16, offset);
    }
    gl.enable(gl.DEPTH_TEST);
    return { gl, camera: gl.getUniformLocation(program, "camera"), fit: gl.getUniformLocation(program, "fit"), count: 0 };
  }

  function upload(points) {
    if (!graphics) return;
    const mesh = [];
    const quad = (a, b, c, d, za, zb, shade) => mesh.push(
      ...a, za, shade, ...b, za, shade, ...c, zb, shade,
      ...a, za, shade, ...c, zb, shade, ...d, zb, shade,
    );
    const count = points.length - 1;
    const edges = points.slice(0, count).map(([x, y], index) => {
      const a = points[(index + count - 1) % count];
      const b = points[(index + 1) % count];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const factor = .022 / (Math.hypot(dx, dy) || 1);
      return [[x - dy * factor, y + dx * factor], [x + dy * factor, y - dx * factor]];
    });
    for (let i = 0; i < count; i += 1) {
      const [a, b] = edges[i];
      const [c, d] = edges[(i + 1) % count];
      quad(a, c, d, b, 0, 0, 1);
      quad(a, c, c, a, 0, -.1, .25);
      quad(b, d, d, b, 0, -.1, .43);
    }
    graphics.gl.bufferData(graphics.gl.ARRAY_BUFFER, new Float32Array(mesh), graphics.gl.STATIC_DRAW);
    graphics.count = mesh.length / 4;
  }

  function syncMarks() {
    const fractions = prediction?.race_context?.markers;
    const roster = prediction?.drivers ?? [];
    const entries = Object.entries((track && fractions) || {})
      .filter(([id, value]) => /^[A-Z]{3}$/.test(id) && Number.isFinite(value) && value >= 0 && value <= 1)
      .sort(([a], [b]) => a.localeCompare(b));
    picker.hidden = entries.length === 0;
    const options = [new Option("Elegir piloto", ""), ...entries.map(([id, fraction]) =>
      new Option(`${id} · ${Math.floor(fraction % 1 * 100)} % de vuelta`, id))];
    select.replaceChildren(...options);
    select.value = entries.some(([id]) => id === selected) ? selected : "";
    select.options[0].disabled = true;
    overlay.replaceChildren(finish);
    marks = entries.map(([id, value]) => {
      const group = makeSvg("g");
      group.dataset.driver = id;
      group.style.setProperty("--car", `var(--driver-${id}, var(--mint))`);
      const active = id === selected;
      group.classList.toggle("selected", active);
      const leader = makeSvg("line");
      const dot = makeSvg("circle");
      dot.setAttribute("r", active ? "5" : "3.5");
      const tag = makeSvg("text");
      tag.textContent = id;
      const title = makeSvg("title");
      title.textContent = `${id} · posición estimada`;
      group.append(title, leader, dot, tag);
      overlay.append(group);
      return { group, leader, tag, point: pointAtLap(track, value), active };
    });
    const active = marks.find((mark) => mark.active);
    if (active) {
      overlay.append(active.group);
      marks = [active, ...marks.filter((mark) => mark !== active)];
    }
    overlay.toggleAttribute("hidden", !track);
    const missing = roster.filter(({ id }) => !Object.hasOwn(fractions ?? {}, id)).map(({ id }) => id);
    const note = entries.length
      ? `${entries.length} de ${roster.length} pilotos · color del equipo. Posición estimada por pasos por meta; no es GPS. Meta y sentido: flecha dorada.`
      : "Sin posiciones estimables en este corte.";
    const absent = entries.length && missing.length && missing.length <= 6 ? ` Sin cruce reciente utilizable: ${missing.join(", ")}.`
      : selected && !select.value ? ` ${selected}: sin posición.` : "";
    el("status").textContent = track ? `${note}${absent}${graphics ? "" : " WebGL no disponible."}` : "";
  }

  function drawMarks(width, height, orbit) {
    const scale = Math.min(width, height) * camera.zoom * (orbit ? .36 : .4);
    const c = orbit ? Math.cos(camera.azimuth) : 1;
    const s = orbit ? Math.sin(camera.azimuth) : 0;
    const tilt = orbit ? Math.sin(camera.elevation) : 1;
    const pixel = ([x, y]) => [width / 2 + scale * (x * c - y * s), height / 2 - scale * (x * s + y * c) * tilt];
    const [fx, fy] = pixel(pointAtLap(track, 0));
    const [tx, ty] = pixel(pointAtLap(track, .0002));
    finish.setAttribute("transform", `translate(${fx},${fy}) rotate(${Math.atan2(ty - fy, tx - fx) * 180 / Math.PI})`);
    const pixels = marks.map(({ point }) => pixel(point));
    const labels = [];
    marks.forEach(({ group, tag, leader }, index) => {
      const [x, y] = pixels[index];
      group.setAttribute("transform", `translate(${x},${y})`);
      // Keep each dot on its true anchor. Move only the code when cars bunch up.
      let slot;
      if (x >= 0 && x <= width && y >= 0 && y <= height) {
        for (const dy of [-11, 12, -27, 28, -43, 44, -59, 60]) {
          for (const dx of [0, 25, -25, 50, -50]) {
            const lx = x + dx, ly = y + dy;
            if (lx < 14 || lx > width - 14 || ly < 8 || ly > height - 8
                || labels.some(([a, b]) => Math.abs(lx - a) < 28 && Math.abs(ly - b) < 15)
                || pixels.some(([a, b]) => Math.abs(lx - a) < 15 && Math.abs(ly - b) < 9)) continue;
            labels.push([lx, ly]);
            slot = [dx, dy];
            break;
          }
          if (slot) break;
        }
      }
      tag.style.display = leader.style.display = slot ? "" : "none";
      if (!slot) return;
      const [dx, dy] = slot;
      tag.setAttribute("x", dx);
      tag.setAttribute("y", dy);
      leader.setAttribute("x2", dx);
      leader.setAttribute("y2", dy);
    });
  }

  function draw() {
    const { clientWidth: width, clientHeight: height } = stage;
    if (!track || !width || !height) return;
    const ratio = width / height;
    drawMarks(width, height, !canvas.hidden);
    if (canvas.hidden) {
      const radius = 1.25 / camera.zoom;
      const x = radius * Math.max(1, ratio);
      const y = radius * Math.max(1, 1 / ratio);
      svg.setAttribute("viewBox", `${-x} ${-y} ${2 * x} ${2 * y}`);
      return;
    }
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const pixelWidth = Math.round(width * dpr);
    const pixelHeight = Math.round(height * dpr);
    if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
    if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
    const { gl, count } = graphics;
    gl.viewport(0, 0, pixelWidth, pixelHeight);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.uniform3f(graphics.camera, camera.azimuth, camera.elevation, camera.zoom);
    gl.uniform2f(graphics.fit, .72 / Math.max(1, ratio), .72 * Math.min(1, ratio));
    gl.drawArrays(gl.TRIANGLES, 0, count);
  }

  function showView() {
    const orbit = Boolean(track && graphics && prefer3d);
    panel.dataset.view = !track ? "unavailable" : orbit ? "3d" : "2d";
    canvas.hidden = !orbit;
    svg.toggleAttribute("hidden", !track || orbit);
    el("mode").hidden = !track;
    el("mode").textContent = orbit ? "Extrusión gráfica · sin altimetría" : "Vista cenital · norte arriba";
    el("orbit").disabled = !graphics;
    el("orbit").setAttribute("aria-pressed", String(orbit));
    el("top").setAttribute("aria-pressed", String(Boolean(track && !orbit)));
    el("turn").hidden = !orbit;
    syncMarks();
    draw();
  }

  function setTrack(next, message) {
    track = next;
    if (!track && dialog.open) dialog.close();
    Object.assign(camera, defaultCamera);
    el("title").textContent = track?.name ?? "Circuito";
    el("empty").hidden = Boolean(track);
    el("empty").textContent = message;
    el("controls").hidden = !track;
    el("credit").hidden = !track;
    if (track) {
      el("source").href = `https://www.openstreetmap.org/${track.osm}`;
      svg.setAttribute("aria-label", `${track.name}: vista cenital, norte arriba`);
      canvas.setAttribute("aria-label", `${track.name}: vista orbital con extrusión gráfica`);
      el("line").setAttribute("d", `M${track.points.map(([x, y]) => `${x.toFixed(5)},${(-y).toFixed(5)}`).join(" L")} Z`);
      if (!graphicsTried) {
        graphicsTried = true;
        graphics = makeGraphics();
      }
      upload(track.points);
    }
    showView();
  }

  function render(event, label, nextPrediction, selectedId, onSelect) {
    const cut = el("cut");
    const cutText = label ?? "Sin corte";
    const changed = prediction !== nextPrediction || selected !== selectedId || cut.textContent !== cutText;
    if (cut.textContent !== cutText) cut.textContent = cutText;
    prediction = nextPrediction;
    selected = selectedId;
    onPick = onSelect;
    const next = event ? circuitKey(event) : null;
    if (key === next) {
      if (changed && track) { syncMarks(); draw(); }
      return;
    }
    key = next;
    if (!next) {
      setTrack(null, "No hay trazado local verificado para este GP.");
      return;
    }
    setTrack(null, "Buscando trazado local…");
    tracks ??= fetch("/circuits.json", { signal: AbortSignal.timeout(3000) }).then((response) => {
      if (!response.ok) throw new Error("Archivo de trazados no disponible");
      return response.json();
    }).then(({ features }) => new Map(features.map((feature) => [feature.id, project(feature)]))).catch(() => {
      tracks = null;
      return null;
    });
    void tracks.then((index) => {
      if (key !== next) return;
      if (!index) key = undefined;
      setTrack(index?.get(next), index ? "No hay trazado local verificado para este GP." : "No se pudo cargar el trazado local.");
    });
  }

  function zoom(factor) {
    camera.zoom = Math.max(.55, Math.min(3.5, camera.zoom * factor));
    draw();
  }

  select.addEventListener("change", () => onPick?.(select.value));
  overlay.addEventListener("click", (event) => {
    const id = event.target.closest("[data-driver]")?.dataset.driver;
    if (id) onPick?.(id);
  });
  el("orbit").addEventListener("click", () => { prefer3d = true; showView(); });
  el("top").addEventListener("click", () => { prefer3d = false; showView(); });
  el("turn").addEventListener("click", () => { camera.azimuth += Math.PI / 8; draw(); });
  el("zoom-in").addEventListener("click", () => zoom(1.24));
  el("zoom-out").addEventListener("click", () => zoom(1 / 1.24));
  el("reset").addEventListener("click", () => { Object.assign(camera, defaultCamera); draw(); });
  expand.addEventListener("click", () => {
    if (dialog.open) {
      dialog.close();
    } else {
      dialog.append(panel);
      dialog.showModal();
      expand.setAttribute("aria-expanded", "true");
      expand.textContent = "Cerrar mapa ampliado";
      expand.focus();
    }
  });
  dialog.addEventListener("close", () => {
    home.prepend(panel);
    expand.setAttribute("aria-expanded", "false");
    expand.textContent = "Ampliar mapa";
    expand.focus();
  });
  canvas.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId);
    drag = { x: event.clientX, y: event.clientY };
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag || !event.isPrimary) return;
    camera.azimuth += (event.clientX - drag.x) * .009;
    camera.elevation = Math.max(.24, Math.min(Math.PI / 2, camera.elevation + (event.clientY - drag.y) * .008));
    drag.x = event.clientX;
    drag.y = event.clientY;
    draw();
  });
  canvas.addEventListener("lostpointercapture", () => { drag = null; });
  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    graphics = null;
    prefer3d = false;
    showView();
  });
  canvas.addEventListener("webglcontextrestored", () => {
    graphics = makeGraphics();
    if (track) upload(track.points);
    showView();
  });
  new ResizeObserver(draw).observe(stage);
  return { render };
})();
