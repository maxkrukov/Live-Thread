"""HTTP API and security tests for live_thread.server."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from live_thread import config
from live_thread.engine import Engine
from live_thread.server import _session_path, serve


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(config, "CONFIG_DIR", root),
            mock.patch.object(config, "CONFIG_FILE", root / "config.json"),
            mock.patch.object(config, "SESSIONS_DIR", root / "sessions"),
        ]
        for p in self.patches:
            p.start()
        self.engine = Engine(dict(config.DEFAULTS))
        self.srv = serve(self.engine, host="127.0.0.1", port=0, start_pump=False)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8")

    def _post(self, path, payload, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_health_state_metrics_and_static(self):
        code, _, body = self._get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])

        code, _, body = self._get("/api/state")
        self.assertEqual(code, 200)
        state = json.loads(body)
        self.assertIn("settings", state)
        self.assertNotIn("llm_api_key", state["settings"])

        code, ctype, body = self._get("/metrics")
        self.assertEqual(code, 200)
        self.assertIn("text/plain", ctype)
        self.assertIn("live_thread_uptime_seconds", body)

        code, ctype, body = self._get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("Live Thread", body)

    def test_settings_update_and_csrf_protection(self):
        code, data = self._post("/api/settings", {"preset": "lecture", "llm_api_key": "secret123"})
        self.assertEqual(code, 200)
        self.assertEqual(data["settings"]["preset"], "lecture")
        self.assertTrue(data["settings"]["llm_api_key_set"])
        self.assertNotIn("llm_api_key", data["settings"])

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/settings", {"preset": "media"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(ctx.exception.code, 403)
        ctx.exception.close()

    def test_session_path_prevents_traversal(self):
        self.assertIsNone(_session_path("../config.json"))
        self.assertIsNone(_session_path("/etc/passwd"))
        self.assertIsNone(_session_path("notes.txt"))
        p = _session_path("2026-11-26_08-00-00.md")
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "2026-11-26_08-00-00.md")


if __name__ == "__main__":
    unittest.main()
