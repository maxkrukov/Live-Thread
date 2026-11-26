"""Налаштування: значення за замовчуванням, читання/запис, міграція зі старої версії."""
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
    # --- мовна модель (будь-який OpenAI-сумісний /v1/chat/completions: vLLM, sglang, llama.cpp, Ollama, OpenAI…)
    "llm_url": "http://localhost:8080/v1/chat/completions",
    "llm_model": "",
    "llm_api_key": "",
    # --- розпізнавання мови
    "stt_url": "",                # сервер server/stt_server.py; порожньо — розпізнавати локально
    "stt_token": "",
    "stt_model": "large-v3-turbo",
    "whisper_model": "small",     # локальна модель (або резерв, якщо сервер недоступний)
    "language": "auto",           # мова розмови: auto | en | uk | ru | pl | de ...
    "glossary": "",               # як правильно пишуться терміни теми (підказка для Whisper і моделі)
    # --- тред
    "context": "",                # про що розмова, на чому фокусуватись
    "preset": "auto",             # auto | interview | lecture | meeting | media
    "notes_language": "auto",     # auto (мовою розмови) | English | Ukrainian | Russian | Polish
    "answer_questions": True,     # ловити запитання й давати короткі відповіді у Markdown
    "diarization": True,
    "diar_threshold": 0.4,
    "use_mic": False,
    "monitor_device": "",         # порожньо — системний @DEFAULT_MONITOR@
    "mic_device": "",             # порожньо — системний @DEFAULT_SOURCE@
    # --- інтерфейс
    "ui_language": "auto",        # auto | uk | en | ru | pl
    "window_opacity": 1.0,        # 0.3–1.0, лише KDE Plasma
    "keep_above": False,          # вікно поверх інших, лише KDE Plasma
    "port": 8765,
}

SECRET_KEYS = ("llm_api_key", "stt_token")
PRESETS = ("auto", "interview", "lecture", "meeting", "media")
# мови треду (назва англійською йде в промпт моделі)
THREAD_LANGUAGES = ("auto", "Ukrainian", "English", "Russian", "Polish", "German", "Spanish", "French",
                    "Italian", "Portuguese", "Czech", "Turkish")
LEGACY_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "interview-coach" / "settings.json"
LEGACY_LANG = {"як у розмові": "auto", "як у запитанні": "auto", "Ukrainian": "Ukrainian",
               "English": "English", "Russian": "Russian"}


def _migrate():
    """Переносимо налаштування з попередньої версії (Interview Coach), якщо нових ще немає."""
    if CONFIG_FILE.exists() or not LEGACY_FILE.exists():
        return {}
    try:
        old = json.loads(LEGACY_FILE.read_text())
    except Exception:
        return {}
    keep = {k: v for k, v in old.items() if k in DEFAULTS}
    if "notes_language" in keep:
        keep["notes_language"] = LEGACY_LANG.get(keep["notes_language"], "auto")
    log.info("migrated settings from %s", LEGACY_FILE)
    return keep


def load():
    s = dict(DEFAULTS)
    s.update(_migrate())
    try:
        s.update({k: v for k, v in json.loads(CONFIG_FILE.read_text()).items() if k in DEFAULTS})
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("cannot read %s: %s", CONFIG_FILE, e)
    return s


def save(s):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: s[k] for k in DEFAULTS if k in s}, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)                       # тут лежать токени
    tmp.replace(CONFIG_FILE)


def coerce(body, current):
    """Перевіряє й приводить типи значень з веб-інтерфейсу. Невідомі ключі ігноруються."""
    out = {}
    for k, v in body.items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        if k in SECRET_KEYS and not v:
            continue                           # порожнє поле секрету = не змінювати
        try:
            if isinstance(d, bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(d, int):
                v = int(v)
            elif isinstance(d, float):
                v = float(v)
            else:
                v = str(v)
        except (TypeError, ValueError):
            continue
        if k == "notes_language" and v not in THREAD_LANGUAGES:
            continue
        if k == "preset" and v not in PRESETS:
            continue
        if k == "window_opacity":
            v = min(1.0, max(0.3, v))
        if k == "diar_threshold":
            v = min(0.9, max(0.1, v))
        out[k] = v
    return out


def public(s):
    """Налаштування для веб-інтерфейсу: секрети не віддаємо, лише ознаку, що вони задані."""
    p = {k: v for k, v in s.items() if k not in SECRET_KEYS}
    p.update({f"{k}_set": bool(s.get(k)) for k in SECRET_KEYS})
    return p
