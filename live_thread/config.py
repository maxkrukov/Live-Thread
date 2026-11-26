"""Configuration defaults, XDG persistence (mode 0600), and input validation."""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

APP_ID = "live-thread"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_DIR = CONFIG_DIR / "sessions"
MODELS_DIR = DATA_DIR / "models"

DEFAULTS = {
    # Language model (any OpenAI-compatible /v1/chat/completions endpoint)
    "llm_url": "http://localhost:8080/v1/chat/completions",
    "llm_model": "",
    "llm_api_key": "",
    # Speech-to-text
    "stt_url": "",                # Optional remote STT server; empty = local faster-whisper
    "stt_token": "",
    "stt_model": "large-v3-turbo",
    "whisper_model": "small",     # Local model (or fallback when remote STT is unreachable)
    "language": "auto",           # Spoken language: auto | en | uk | ru | pl | de ...
    "glossary": "",               # Comma-separated domain terms for Whisper and the LLM
    # Thread behavior
    "context": "",                # Optional focus hint for the conversation
    "preset": "auto",             # auto | interview | lecture | meeting | media
    "notes_language": "auto",     # auto (match conversation) or a fixed language from THREAD_LANGUAGES
    "answer_questions": True,     # Detect spoken questions and stream concise Markdown answers
    "diarization": True,
    "diar_threshold": 0.4,
    "use_mic": False,
    "monitor_device": "",         # Empty = @DEFAULT_MONITOR@
    "mic_device": "",             # Empty = @DEFAULT_SOURCE@
    # UI & window
    "ui_language": "auto",        # auto | uk | en | ru | pl
    "window_opacity": 1.0,        # 0.3-1.0, KDE Plasma only
    "keep_above": False,          # Keep window on top, KDE Plasma only
    "port": 8765,
}

SECRET_KEYS = ("llm_api_key", "stt_token")
PRESETS = ("auto", "interview", "lecture", "meeting", "media")
THREAD_LANGUAGES = (
    "auto", "Ukrainian", "English", "Russian", "Polish", "German",
    "Spanish", "French", "Italian", "Portuguese", "Czech", "Turkish",
)


def load():
    """Load settings from CONFIG_FILE merged onto DEFAULTS."""
    settings = dict(DEFAULTS)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            settings.update({k: v for k, v in raw.items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("cannot read %s: %s", CONFIG_FILE, e)
    return settings


def save(settings):
    """Atomically persist settings to CONFIG_FILE with 0600 permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    payload = {k: settings[k] for k in DEFAULTS if k in settings}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_FILE)


def coerce(body, current=None):
    """Validate and coerce incoming settings from the web UI. Unknown keys are ignored."""
    del current  # kept for backwards compatibility
    out = {}
    for k, v in body.items():
        if k not in DEFAULTS:
            continue
        default = DEFAULTS[k]
        if k in SECRET_KEYS and not v:
            continue  # empty secret field preserves the existing value
        try:
            if isinstance(default, bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            else:
                v = str(v)
        except (TypeError, ValueError):
            continue
        if k == "notes_language" and v not in THREAD_LANGUAGES:
            continue
        if k == "preset" and v not in PRESETS:
            continue
        if k == "port" and not (1 <= v <= 65535):
            continue
        if k == "window_opacity":
            v = min(1.0, max(0.3, v))
        if k == "diar_threshold":
            v = min(0.9, max(0.1, v))
        out[k] = v
    return out


def public(settings):
    """Return UI-safe settings with secret values replaced by *_set booleans."""
    pub = {k: v for k, v in settings.items() if k not in SECRET_KEYS}
    pub.update({f"{k}_set": bool(settings.get(k)) for k in SECRET_KEYS})
    return pub
