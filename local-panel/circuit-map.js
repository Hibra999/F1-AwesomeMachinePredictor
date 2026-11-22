"use strict";

const circuitMap = (() => {
  const el = (id) => document.getElementById(`circuit-${id}`);
  const panel = el("panel");
  const stage = el("stage");
  const canvas = el("3d");
  const svg = el("2d");
  const dialog = el("dialog");
  const expand = el("expand");
  const home = panel.parentNode;
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
  let currentEvent;
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
    return {
      ...properties,
      points: coords.map(([x, y]) => [(x - cx) * cos / scale, (y - cy) / scale]),
    };
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
        varying float tint;
        void main() {
          gl_FragColor = vec4(mix(vec3(.065, .24, .26), vec3(.62, .98, .85), tint), 1.0);
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

  function draw() {
    const { clientWidth: width, clientHeight: height } = stage;
    if (!track || !width || !height) return;
    const ratio = width / height;
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
    svg.hidden = !track || orbit;
    el("mode").hidden = !track;
    el("mode").textContent = orbit ? "Extrusión gráfica · sin altimetría" : "Vista cenital · norte arriba";
    el("orbit").disabled = !graphics;
    el("orbit").setAttribute("aria-pressed", String(orbit));
    el("top").setAttribute("aria-pressed", String(Boolean(track && !orbit)));
    el("turn").hidden = !orbit;
    el("status").textContent = `Trazado de referencia; sin posiciones de autos.${track && !graphics ? " WebGL no disponible." : ""}`;
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

  function render(event, label) {
    const cut = el("cut");
    const cutText = label ?? "Sin corte";
    if (cut.textContent !== cutText) cut.textContent = cutText;
    if (currentEvent === event) return;
    currentEvent = event;
    const next = event ? circuitKey(event) : null;
    if (key === next) return;
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
      if (!index) key = null;
      setTrack(index?.get(next), index ? "No hay trazado local verificado para este GP." : "No se pudo cargar el trazado local.");
    });
  }

  function zoom(factor) {
    camera.zoom = Math.max(.55, Math.min(3.5, camera.zoom * factor));
    draw();
  }

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
