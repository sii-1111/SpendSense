"""Run SpendSense:  python app.py  ->  http://localhost:8000"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import categorise

ROOT = Path(__file__).parent
INDEX = (ROOT / "static" / "index.html").read_bytes()
SAMPLE = (ROOT / "data" / "sample_statement.csv").read_bytes()
MAX_BYTES = 2_000_000  # ~10k transactions


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, INDEX, "text/html; charset=utf-8")
        if self.path == "/sample.csv":
            return self._send(200, SAMPLE, "text/csv; charset=utf-8")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", 0))
        if size > MAX_BYTES:
            return self._send(413, {"error": "Statement too large for this demo (2 MB max)."})
        try:
            data = json.loads(self.rfile.read(size) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "Request body must be JSON."})
        try:
            if self.path == "/api/analyse":
                return self._send(200, categorise.analyse(data.get("csv", "")))
            if self.path == "/api/correct":
                categorise.save_correction(data["merchant_key"], data["direction"], data["category"])
                return self._send(200, {"saved": True})
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    mode = "MOCK (fake answers)" if os.getenv("JEV_MOCK") == "1" else "live Jev"
    print(f"SpendSense running on http://localhost:{port}  [{mode}]")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
