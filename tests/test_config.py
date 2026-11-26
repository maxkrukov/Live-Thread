"""Tests for live_thread.config."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from live_thread import config


class ConfigTests(unittest.TestCase):
    def test_coerce_types_and_bounds(self):
        out = config.coerce({
            "port": "9000",
            "use_mic": "true",
            "answer_questions": "false",
            "window_opacity": "0.1",
            "diar_threshold": "1.5",
            "preset": "interview",
            "notes_language": "Ukrainian",
            "unknown_key": "ignored",
        })
        self.assertEqual(out["port"], 9000)
        self.assertIs(out["use_mic"], True)
        self.assertIs(out["answer_questions"], False)
        self.assertEqual(out["window_opacity"], 0.3)
        self.assertEqual(out["diar_threshold"], 0.9)
        self.assertEqual(out["preset"], "interview")
        self.assertEqual(out["notes_language"], "Ukrainian")
        self.assertNotIn("unknown_key", out)

    def test_coerce_rejects_invalid_values(self):
        out = config.coerce({
            "port": 70000,
            "preset": "nonexistent",
            "notes_language": "Klingon",
            "llm_api_key": "",
            "stt_token": "",
        })
        self.assertEqual(out, {})

    def test_public_masks_secrets(self):
        s = dict(config.DEFAULTS, llm_api_key="sk-secret", stt_token="")
        pub = config.public(s)
        self.assertNotIn("llm_api_key", pub)
        self.assertNotIn("stt_token", pub)
        self.assertTrue(pub["llm_api_key_set"])
        self.assertFalse(pub["stt_token_set"])

    def test_save_and_load_roundtrip_with_0600_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "live-thread"
            cfg_file = cfg_dir / "config.json"
            with mock.patch.object(config, "CONFIG_DIR", cfg_dir), \
                 mock.patch.object(config, "CONFIG_FILE", cfg_file):
                s = dict(config.DEFAULTS, llm_model="qwen3-32b", llm_api_key="tok123", port=8888)
                config.save(s)
                mode = stat.S_IMODE(os.stat(cfg_file).st_mode)
                self.assertEqual(mode, 0o600)
                loaded = config.load()
                self.assertEqual(loaded["llm_model"], "qwen3-32b")
                self.assertEqual(loaded["llm_api_key"], "tok123")
                self.assertEqual(loaded["port"], 8888)


if __name__ == "__main__":
    unittest.main()
