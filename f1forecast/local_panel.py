"""Serve the local race panel and only its explicitly authorized files."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread
from time import monotonic


from .analysis_data import ANALYSIS_JSON, RECENT_JSON
from .pages import verify_recent


def serve_panel(*, port: int, work: Path, live: Path | None, replay: Path | None):
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
        "/data/live.json": (live or work / "live.json", "application/json"),
        "/data/replay.json": (replay or panel / "sao-paulo-2026.json", "application/json"),
    }
    refresh_lock = Lock()
    last_gp, next_check, checked_capture = None, 0, None

    def capture_path():
        path = routes["/data/live.json"][0]
        return panel / "las-vegas-2026.json" if live is None and not path.exists() else path

    def recent_path():
        nonlocal last_gp, next_check
        public = site / RECENT_JSON
        private = work / "recent-podiums.json"
        report = json.loads(public.read_bytes())
        latest = public
        try:
            cached = json.loads(private.read_bytes())
            verify_recent(cached)
            if cached["as_of_utc"] >= report["as_of_utc"]:
                report, latest = cached, private
        except (OSError, ValueError, KeyError, TypeError):
            pass
        path = capture_path()
        try:
            version = (path, path.stat().st_mtime_ns)
            capture = json.loads(path.read_bytes())
        except (OSError, ValueError):
            return latest
        gp = capture.get("event", {}).get("id")
        if capture.get("mode") != "live" or capture.get("complete") is not True or not gp:
            return latest
        if (path == panel / "las-vegas-2026.json" or checked_capture == version) and any(
                r["gp"] == gp and r["status"] == "final" and r.get("race_evaluation") for r in report["races"]):
            return latest
        if not refresh_lock.acquire(blocking=False):
            return latest
        if gp == last_gp and monotonic() < next_check:
            refresh_lock.release()
            return latest
        last_gp, next_check = gp, monotonic() + 300

        def refresh():
            nonlocal checked_capture
            try:
                from .recent_results import refresh_report

                refresh_report(work, private, live_path=path)
                checked_capture = version
            except Exception as exc:
                print(f"Retrospectiva {gp}: {exc}. Se conserva el último resultado.", flush=True)
            finally:
                refresh_lock.release()

        Thread(target=refresh, daemon=True).start()
        return latest

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
            if self.path == "/data/live.json":
                path = capture_path()
            elif self.path == f"/{RECENT_JSON}":
                path = recent_path()
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
