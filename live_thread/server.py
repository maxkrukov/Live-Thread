"""HTTP-сервер: статичний веб-інтерфейс, REST API, події (SSE) та метрики."""
import json
import logging
import mimetypes
import os
import queue
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, audio, config, window
from .events import BUS
from .metrics import METRICS

log = logging.getLogger(__name__)
WEB = Path(__file__).resolve().parent / "web"
BOOT_ID = f"{os.getpid()}-{int(time.time())}"


def _session_path(name):
    """Безпечно розв'язує ім'я файлу сесії (захист від path traversal)."""
    if not name or Path(name).name != name or not name.endswith(".md"):
        return None
    p = (config.SESSIONS_DIR / name).resolve()
    root = config.SESSIONS_DIR.resolve()
    return p if root in p.parents else None


def _list_sessions():
    if not config.SESSIONS_DIR.exists():
        return []
    out = []
    for p in sorted(config.SESSIONS_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:100]:
        try:
            st = p.stat()
            title = ""
            with p.open(encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("# Live Thread") or s.startswith("## "):
                        continue
                    title = s.lstrip("#*- ").replace("**", "")[:90]
                    break
            out.append({"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime), "title": title})
        except OSError:
            continue
    return out


def make_handler(engine):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"LiveThread/{__version__}"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        # -- helpers
        def _send(self, code, body, ctype):
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if n < 0 or n > 1_000_000:
                return None
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return None
            return data if isinstance(data, dict) else None

        def _same_origin(self):
            """Захист від CSRF: зміни приймаємо лише з нашої ж сторінки (або без Origin — напр. curl)."""
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            return origin is None or origin.split("://", 1)[-1] == host

        # -- GET
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                path = "/index.html"
            if path == "/api/state":
                snap = engine.state_snapshot()
                return self._json({
                    "version": __version__, "boot": BOOT_ID, "settings": config.public(snap["settings"]),
                    "listening": snap["listening"], "topics": snap["topics"], "gist": snap["gist"],
                    "profile": snap["profile"], "transcript": snap["transcript"], "clients": len(BUS.clients),
                    "window_control": window.supported()})
            if path == "/api/metrics":
                return self._json(METRICS.snapshot())
            if path == "/metrics":
                return self._send(200, METRICS.prometheus(), "text/plain; version=0.0.4")
            if path == "/api/health":
                return self._json({"ok": True, "version": __version__})
            if path == "/api/audio-devices":
                return self._json(audio.list_devices())
            if path == "/api/sessions":
                return self._json({"sessions": _list_sessions()})
            if path.startswith("/api/sessions/"):
                name = urllib.parse.unquote(path[len("/api/sessions/"):])
                p = _session_path(name)
                if not p or not p.is_file():
                    return self._json({"error": "not found"}, 404)
                return self._json({"name": p.name, "content": p.read_text(encoding="utf-8", errors="ignore")})
            if path == "/api/events":
                return self._events()
            f = (WEB / path.lstrip("/")).resolve()
            if WEB in f.parents and f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                return self._send(200, f.read_bytes(), ctype)
            self.send_error(404)

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = BUS.subscribe()
            METRICS.gauge("ui_clients", len(BUS.clients))
            q.put(json.dumps({"type": "hello", "boot": BOOT_ID, "version": __version__}))
            try:
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(f"data: {msg}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                BUS.unsubscribe(q)
                METRICS.gauge("ui_clients", len(BUS.clients))

        # -- POST
        def do_POST(self):
            if not self._same_origin():
                return self._json({"error": "forbidden"}, 403)
            body = self._body()
            if body is None:
                return self._json({"error": "bad request"}, 400)
            p = self.path.split("?", 1)[0]
            if p == "/api/settings":
                changed = config.coerce(body, engine.settings)
                with engine.lock:
                    engine.settings.update(changed)
                    config.save(engine.settings)
                    pub = config.public(engine.settings)
                t = engine.transcriber
                if "diar_threshold" in changed and t and t._diarizer:
                    t._diarizer.threshold = engine.settings["diar_threshold"]
                if {"use_mic", "monitor_device", "mic_device"} & changed.keys():
                    engine.sync_sources()
                if {"window_opacity", "keep_above"} & changed.keys():
                    window.apply(engine.settings["window_opacity"], engine.settings["keep_above"])
                BUS.emit("settings", settings=pub)
                return self._json({"ok": True, "settings": pub})
            if p == "/api/ask":
                q = str(body.get("question") or "").strip()[:500]
                engine.trigger_ask(q or None)
            elif p == "/api/summary":
                engine.trigger_summary()
            elif p == "/api/note/dismiss":
                ok = engine.dismiss_note(body.get("id"))
                return self._json({"ok": ok})
            elif p == "/api/note/pin":
                ok = engine.pin_note(body.get("id"), body.get("pinned"))
                return self._json({"ok": ok})
            elif p == "/api/sessions/delete":
                sp = _session_path(str(body.get("name") or ""))
                if not sp or not sp.is_file():
                    return self._json({"error": "not found"}, 404)
                sp.unlink(missing_ok=True)
                return self._json({"ok": True})
            elif p == "/api/start":
                engine.start()
            elif p == "/api/stop":
                engine.stop()
            elif p == "/api/refresh":
                engine.kick(force=True)
            elif p == "/api/reset":
                engine.reset()
            elif p == "/api/window":
                ok = window.apply(engine.settings["window_opacity"], engine.settings["keep_above"])
                return self._json({"ok": ok})
            else:
                return self._json({"error": "not found"}, 404)
            self._json({"ok": True})

    return Handler


def metrics_pump(engine):
    """Раз на 2 с шлемо метрики у відкриті вікна й періодично автозберігаємо сесію."""
    while True:
        time.sleep(2)
        try:
            if BUS.clients:
                BUS.emit("metrics", **METRICS.snapshot())
            engine.maybe_autosave()
        except Exception as e:
            log.debug("metrics pump error: %s", e)


def serve(engine, host="127.0.0.1", port=8765):
    srv = ThreadingHTTPServer((host, port), make_handler(engine))
    srv.daemon_threads = True
    threading.Thread(target=metrics_pump, args=(engine,), daemon=True, name="metrics").start()
    return srv
