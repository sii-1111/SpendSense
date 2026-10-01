"""SpendSense web app.

Local:   JEV_MOCK=1 python app.py   ->  http://localhost:8000
Hosted:  see README "Deploy" (Render config in render.yaml)

Ready for a public demo:
  - each visitor gets their own merchant memory (cookie session, kept in server memory)
  - per-visitor rate limit, plus a daily cap on real Jev calls to protect your API bill
  - uploaded statements are processed in memory and never written to disk or logged
"""
import json
import os
import secrets
import threading
import time
from collections import OrderedDict, defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import categorise

ROOT = Path(__file__).parent
INDEX = (ROOT / "static" / "index.html").read_bytes()
SAMPLE = (ROOT / "data" / "sample_statement.csv").read_bytes()
DUMMY = (ROOT / "data" / "dummy_statement.csv").read_bytes()

MOCK = os.getenv("JEV_MOCK") == "1"
MAX_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 2_000_000))          # ~10k transactions
RATE_PER_HOUR = int(os.getenv("RATE_LIMIT_PER_HOUR", 10))          # analyses per visitor per hour
DAILY_JEV_CALLS = int(os.getenv("DAILY_JEV_CALL_CAP", 3000))       # real-mode only; 0 = no cap
SESSION_TTL = 24 * 3600
MAX_SESSIONS = 5000


class SessionMemory:
    """One visitor's merchant memory. Same interface as categorise.FileMemory."""

    def __init__(self):
        self.data, self.lock = {}, threading.Lock()

    def load(self):
        with self.lock:
            return dict(self.data)

    def merge(self, entries, overwrite=False):
        with self.lock:
            self.data.update(entries if overwrite else {k: v for k, v in entries.items() if k not in self.data})


class Sessions:
    """In-memory sessions with expiry. Restarting the server clears them, which suits a demo."""

    def __init__(self):
        self.items, self.lock = OrderedDict(), threading.Lock()

    def get(self, sid):
        now = time.time()
        with self.lock:
            for old in [k for k, (_, t) in self.items.items() if now - t > SESSION_TTL]:
                del self.items[old]
            if sid not in self.items:
                if len(self.items) >= MAX_SESSIONS:
                    self.items.popitem(last=False)
                self.items[sid] = (SessionMemory(), now)
            mem, _ = self.items[sid]
            self.items[sid] = (mem, now)
            self.items.move_to_end(sid)
            return mem

    def reset(self, sid):
        with self.lock:
            self.items.pop(sid, None)


class Limits:
    def __init__(self):
        self.hits, self.lock = defaultdict(deque), threading.Lock()
        self.day, self.jev_calls_today = time.strftime("%Y-%m-%d"), 0

    def allow(self, *keys):
        """Sliding one-hour window per key. Returns (ok, minutes_to_wait)."""
        now = time.time()
        with self.lock:
            for k in keys:
                q = self.hits[k]
                while q and now - q[0] > 3600:
                    q.popleft()
                if len(q) >= RATE_PER_HOUR:
                    return False, int((3600 - (now - q[0])) // 60) + 1
            for k in keys:
                self.hits[k].append(now)
            return True, 0

    def budget_left(self):
        with self.lock:
            today = time.strftime("%Y-%m-%d")
            if today != self.day:
                self.day, self.jev_calls_today = today, 0
            return MOCK or not DAILY_JEV_CALLS or self.jev_calls_today < DAILY_JEV_CALLS

    def spend(self, calls):
        with self.lock:
            self.jev_calls_today += calls


SESSIONS, LIMITS = Sessions(), Limits()


class Handler(BaseHTTPRequestHandler):
    server_version = "SpendSense"

    # ---- helpers
    def _session(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        sid = c["sid"].value if "sid" in c else ""
        self._new_sid = None
        if not (len(sid) == 32 and all(ch in "0123456789abcdef" for ch in sid)):
            sid = self._new_sid = secrets.token_hex(16)
        return sid

    def _ip(self):
        fwd = self.headers.get("X-Forwarded-For", "")  # set by Render/Railway/Fly proxies
        return (fwd.split(",")[0].strip() if fwd else self.client_address[0]) or "unknown"

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_new_sid", None):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie", f"sid={self._new_sid}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}{secure}")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # ---- routes
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._session()
        if self.path in ("/", "/index.html"):
            return self._send(200, INDEX, "text/html; charset=utf-8")
        if self.path == "/sample.csv":
            return self._send(200, DUMMY, "text/csv; charset=utf-8")
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        if self.path == "/api/config":
            return self._send(200, {"mode": "mock" if MOCK else "live", "rate_limit_per_hour": RATE_PER_HOUR,
                                    "max_upload_mb": round(MAX_BYTES / 1e6, 1)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        sid = self._session()
        size = int(self.headers.get("Content-Length", 0) or 0)
        if size > MAX_BYTES:
            return self._send(413, {"error": f"Statement too large for this demo ({MAX_BYTES // 1_000_000} MB max)."})
        try:
            data = json.loads(self.rfile.read(size) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "Request body must be JSON."})
        try:
            if self.path == "/api/analyse":
                ok, wait = LIMITS.allow("ip:" + self._ip(), "sid:" + sid)
                if not ok:
                    return self._send(429, {"error": f"Demo limit reached ({RATE_PER_HOUR} statements an hour). Try again in {wait} min."},
                                      extra={"Retry-After": str(wait * 60)})
                if not LIMITS.budget_left():
                    return self._send(503, {"error": "Today's demo budget is used up. Please try again tomorrow."})
                result = categorise.analyse(str(data.get("csv", "")), memory=SESSIONS.get(sid))
                LIMITS.spend(result["stats"]["jev_calls"])
                return self._send(200, result)
            if self.path == "/api/correct":
                categorise.save_correction(str(data["merchant_key"])[:80], data["direction"], data["category"],
                                           memory=SESSIONS.get(sid))
                return self._send(200, {"saved": True})
            if self.path == "/api/reset":
                SESSIONS.reset(sid)
                return self._send(200, {"reset": True})
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # always answer in JSON so the page can show what went wrong
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):  # log method + path + status only, never bodies
        if os.getenv("ACCESS_LOG") == "1":
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    if not MOCK and not os.getenv("TYPESAFE_API_KEY"):
        raise SystemExit("Set TYPESAFE_API_KEY for live mode, or JEV_MOCK=1 for the demo mode.")
    print(f"SpendSense running on http://localhost:{port}  [{'MOCK (fake answers)' if MOCK else 'live Jev'}]", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
