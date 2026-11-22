"""Serve the local race panel and only its explicitly authorized files."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


from .analysis_data import ANALYSIS_JSON, RECENT_JSON


def serve_panel(*, port: int, live: Path, replay: Path):
    root = Path(__file__).resolve().parent.parent
    panel = root / "local-panel"
    site = root / "site"
    routes = {
        "/": (panel / "index.html", "text/html; charset=utf-8"),
        "/index.html": (panel / "index.html", "text/html; charset=utf-8"),
        "/app.js": (panel / "app.js", "text/javascript; charset=utf-8"),
        "/race.css": (panel / "styles.css", "text/css; charset=utf-8"),
        "/map.css": (panel / "map.css", "text/css; charset=utf-8"),
        "/circuit-map.js": (panel / "circuit-map.js", "text/javascript; charset=utf-8"),
        "/circuits.json": (panel / "circuits.json", "application/json"),
        "/styles.css": (site / "styles.css", "text/css; charset=utf-8"),
        "/analysis.html": (site / "analysis.html", "text/html; charset=utf-8"),
        "/analysis.css": (site / "analysis.css", "text/css; charset=utf-8"),
        "/analysis.js": (site / "analysis.js", "text/javascript; charset=utf-8"),
        "/insights.js": (site / "insights.js", "text/javascript; charset=utf-8"),
        f"/{RECENT_JSON}": (site / RECENT_JSON, "application/json"),
        f"/{ANALYSIS_JSON}": (site / ANALYSIS_JSON, "application/json"),
        "/vendor/plotly-basic.min.js": (site / "vendor/plotly-basic.min.js", "text/javascript; charset=utf-8"),
        "/data/live.json": (live, "application/json"),
        "/data/replay.json": (replay, "application/json"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.serve_file(send_body=True)

        def do_HEAD(self):
            self.serve_file(send_body=False)

        def end_headers(self):
            route = routes.get(self.path)
            if route and route[1] == "application/json":
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def serve_file(self, *, send_body):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.send_error(403)
                return
            route = routes.get(self.path)
            if route is None:
                self.send_error(404)
                return
            path, content_type = route
            try:
                content = path.read_bytes()
            except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError):
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if send_body:
                self.wfile.write(content)

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"Panel local: http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
