"""
Local hub: upload .tsu files and view Jira + Allure reports on one page.

  python tools/hub.py
  npm run hub
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from project_paths import collect_tsu_files, load_config, tsu_import_dir  # noqa: E402
from manual_report import write_empty_catalog  # noqa: E402

HUB_HTML = Path(__file__).with_name("hub.html")
HOST = "127.0.0.1"
PORT = int(os.environ.get("TOSCA_HUB_PORT") or load_config().get("hubPort") or 8765)


def _safe_under(root: Path, rel: str) -> Path | None:
    candidate = (root / rel.lstrip("/")).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _file_list() -> list[dict]:
    rows = []
    inbox = tsu_import_dir()
    for path in collect_tsu_files(inbox):
        stat = path.stat()
        rel = path.relative_to(inbox).as_posix()
        rows.append({
            "name": rel,
            "size": f"{stat.st_size / 1024:.0f} KB",
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return rows


def _run_convert() -> tuple[bool, str]:
    convert = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "tsu_to_playwright.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if convert.returncode != 0:
        err = (convert.stderr or convert.stdout or "Convert failed").strip()
        return False, err[-2000:]
    allure = subprocess.run(
        ["npx", "allure", "generate", "allure-results", "--clean", "-o", "allure-report"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        shell=(os.name == "nt"),
    )
    extra = ""
    if allure.returncode != 0:
        extra = " Jira catalog updated; Allure HTML could not be rebuilt."
    return True, "Conversion complete. Reports are ready." + extra


def _parse_upload(headers: dict, body: bytes) -> tuple[str, bytes]:
    ctype = headers.get("Content-Type", "")
    match = re.search(r"boundary=(.+)", ctype)
    if not match:
        raise ValueError("Missing multipart boundary")
    boundary = match.group(1).strip().strip('"').encode()
    parts = body.split(b"--" + boundary)
    for part in parts:
        if b"filename=" not in part:
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        name_m = re.search(br'filename="([^"]+)"', head)
        filename = unquote(name_m.group(1).decode("utf-8", "replace")) if name_m else "upload.tsu"
        payload = data
        if payload.endswith(b"--"):
            payload = payload[:-2]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        return Path(filename).name, payload
    raise ValueError("No file in upload")


class HubHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _json(self, code: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path, fallback_html: str | None = None):
        if not path.exists() or not path.is_file():
            if fallback_html:
                raw = fallback_html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_error(404, "Not found")
            return
        data = path.read_bytes()
        suffix = path.suffix.lower()
        types = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript",
            ".css": "text/css",
            ".json": "application/json",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".txt": "text/plain; charset=utf-8",
            ".md": "text/markdown; charset=utf-8",
        }
        self.send_response(200)
        self.send_header("Content-Type", types.get(suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in ("/", "/index.html"):
            self._send_file(HUB_HTML)
            return
        if path == "/api/status":
            allure_ok = (ROOT / "allure-report" / "index.html").exists()
            catalog_ok = (ROOT / "reports" / "manual-catalog.html").exists()
            self._json(200, {
                "files": _file_list(),
                "importDir": str(tsu_import_dir()),
                "allure": allure_ok,
                "catalog": catalog_ok,
            })
            return
        if path.startswith("/reports/"):
            target = _safe_under(ROOT / "reports", path[len("/reports/"):])
            missing = (
                "<html><body style='font-family:Segoe UI;padding:24px'>"
                "<p>No converted test cases yet. Upload a .tsu file or click Convert all.</p>"
                "</body></html>"
            )
            if target:
                self._send_file(target, fallback_html=missing if target.name == "manual-catalog.html" else None)
                return
        if path.startswith("/allure"):
            rel = path[len("/allure"):].lstrip("/") or "index.html"
            target = _safe_under(ROOT / "allure-report", rel)
            missing = (
                "<html><body style='font-family:Segoe UI;padding:24px'>"
                "<p>Allure report is not generated yet. Upload a .tsu file or click Convert all.</p>"
                "</body></html>"
            )
            if target:
                self._send_file(target, fallback_html=missing if rel == "index.html" else None)
                return
        self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if parsed.path == "/api/upload":
            try:
                name, payload = _parse_upload(dict(self.headers), body)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            if not name.lower().endswith(".tsu"):
                self._json(400, {"error": "Only .tsu files are accepted"})
                return
            if len(payload) > 80 * 1024 * 1024:
                self._json(400, {"error": "File is larger than 80 MB"})
                return
            dest = tsu_import_dir() / Path(name).name
            dest.write_bytes(payload)
            ok, message = _run_convert()
            self._json(200 if ok else 500, {
                "saved": dest.name,
                "path": str(dest),
                "error": None if ok else message,
                "message": message if ok else None,
            })
            return
        if parsed.path == "/api/convert":
            ok, message = _run_convert()
            self._json(200 if ok else 500, {"error": None if ok else message, "message": message if ok else None})
            return
        self.send_error(404, "Not found")


def main() -> int:
    tsu_import_dir()
    catalog = ROOT / "reports" / "manual-catalog.html"
    if not catalog.exists():
        write_empty_catalog()
    server = ThreadingHTTPServer((HOST, PORT), HubHandler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Hub running at {url}")
    print(f"Upload folder: {tsu_import_dir()}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHub stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
