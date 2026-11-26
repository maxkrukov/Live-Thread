# Live Thread

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform Linux](https://img.shields.io/badge/platform-Linux%20(PipeWire%20%2F%20PulseAudio)-informational)
![License MIT](https://img.shields.io/badge/license-MIT-green)

**A live topic thread for any conversation.** Live Thread listens to system audio (and optionally your microphone) during a video, call, lecture, interview, or podcast, recognises speech, works out *what is being discussed right now*, and keeps a live thread of concise notes next to it: **clarify · correct · confirm · add · answer**. When the conversation moves to a new subject it creates a topic card with an instant overview of key items.

[Українська версія](README.uk.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

## Features

- **Self-adapting** — detects what is playing (tech interview, lecture, meeting, podcast, news, film, cartoon, chat…), learns domain vocabulary and typical misrecognitions, and feeds those terms back to both Whisper and the LLM.
- **Mode presets** — `auto`, `interview`, `lecture`, `meeting`, and `media` presets tailor prompts to your context.
- **Q&A mode & ask bar** — catches spoken questions automatically and streams concise Markdown answers, or lets you ask a custom question at any time (`/` shortcut).
- **Now line & topic cards** — a live ≤10-word gist of the current discussion, plus per-topic cards with instant overviews and pin/dismiss note controls.
- **Fact-checked notes** — notes must add outside knowledge; duplicates, restatements, guesses, and ASR-error comments are filtered out, and claims are verified before display.
- **Speaker separation** — online speaker diarization (`S1`, `S2`, `S3`…) using `sherpa-onnx` + TitaNet (optional).
- **Flexible audio sources** — captures PipeWire/PulseAudio system output (`@DEFAULT_MONITOR@`) and optionally your microphone (`@DEFAULT_SOURCE@`), with device selection in settings.
- **Session summaries & history** — generate on-demand summaries and browse, search, copy, download, or delete saved Markdown sessions (`~/.config/live-thread/sessions/*.md`).
- **Metrics** — live latency (`p50`/`p95`), notes/min, rejection reasons, queue depth, and errors in the UI, as JSON (`/api/metrics`), and in Prometheus format (`/metrics`).
- **Multilingual UI & thread language** — UI in English, Українська, Русский, and Polski; notes in the conversation's language or any fixed language you pick.
- **Window controls (KDE Plasma)** — adjustable window opacity and keep-on-top via KWin scripting.
- **Private by default** — binds to `127.0.0.1:8765`; audio and text are sent only to the servers *you* configure.

> Please use it responsibly: in many jurisdictions you must inform participants before recording or transcribing a conversation.

## Architecture

```text
system audio (+ mic) ──► energy VAD phrases ──► speech-to-text ──► speaker labels ──► thread engine ──► web UI
 (PipeWire/PulseAudio)    0.5 s pause / 6 s      remote STT or      TitaNet ONNX        any OpenAI-      (SSE)
                                                 local Whisper      (optional)          compatible LLM
```

## Requirements

- Linux with PipeWire or PulseAudio (`parec`, `pactl`), Python 3.10+
- An **OpenAI-compatible** `/v1/chat/completions` LLM endpoint (vLLM, sglang, llama.cpp server, Ollama, LM Studio, OpenAI, OpenRouter, etc.). A 20–30B instruction model works well.
- Optional: a remote Whisper STT server for faster transcription on laptops
- Optional: KDE Plasma (`qdbus`) for window opacity and keep-on-top

## Installation

```bash
git clone <repo-url> live-thread && cd live-thread
./scripts/install.sh            # --no-diarization to skip speaker separation, --no-desktop to skip desktop entry
live-thread                     # or launch "Live Thread" from the application menu
```

Open **Settings (⚙)** in the web UI (`http://127.0.0.1:8765/`) and set at least the LLM endpoint (`llm_url`) and model name (`llm_model`). Configuration is saved to `~/.config/live-thread/config.json` with mode `600`. See [`config.example.json`](config.example.json) for all options.

### CLI Options

```bash
live-thread [--port 8765] [--host 127.0.0.1] [--no-open] [--listen] [--debug] [--version]
```

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `/` | Focus the Ask bar |
| `Esc` | Blur input or close the open drawer |
| `Alt + L` | Start / stop listening |
| `Alt + Q` | Toggle Q&A mode |
| `Alt + A` | Force answer on the latest transcript lines |
| `Alt + S` | Generate a pinned Markdown session summary |
| `Alt + H` | Toggle saved sessions history drawer |

## Remote STT API (Optional)

If `stt_url` is configured, Live Thread sends raw 16 kHz mono 16-bit signed little-endian PCM to:

- `POST <stt_url>/transcribe?language=<lang>&words=<0|1>&model=<stt_model>`
- Headers: `Authorization: Bearer <stt_token>`, `X-Prompt: <urlencoded glossary>`, `Content-Type: application/octet-stream`
- Response JSON: `{"segments": [{"text": "...", "no_speech_prob": 0.0, "avg_logprob": -0.2, "compression_ratio": 1.1, "words": [[start, end, "word"], ...]}]}`

If the remote server is unreachable, Live Thread automatically falls back to local `faster-whisper` and retries the remote endpoint after 30 seconds.

## Key Settings

| Setting | Description |
|---|---|
| `llm_url`, `llm_model`, `llm_api_key` | OpenAI-compatible chat completions endpoint, model name, and optional bearer token. |
| `stt_url`, `stt_token`, `stt_model` | Optional remote STT server settings. Leave `stt_url` empty for local `faster-whisper`. |
| `whisper_model` | Local `faster-whisper` model (`small`, `base`, `medium`, `large-v3-turbo`). |
| `language` | Conversation language (`auto` detects among `en`, `uk`, `ru`, `pl`, `de`). |
| `glossary` | Comma-separated domain terms to improve Whisper spelling and LLM understanding. |
| `preset` | Focus preset: `auto`, `interview`, `lecture`, `meeting`, or `media`. |
| `notes_language` | Output language for notes (`auto` follows the conversation language). |
| `answer_questions` | Automatically detect spoken questions and stream answers. |
| `diarization`, `diar_threshold` | Enable speaker separation (`0.4` default; raise to `0.5` if speakers merge, lower to `0.35` if one speaker splits). |
| `use_mic`, `monitor_device`, `mic_device` | Capture microphone alongside system audio and select specific PipeWire/PulseAudio sources. |

## API & Metrics

- `GET /api/health` — health check (`{"ok": true, "version": "0.5.0"}`)
- `GET /api/state` — current state snapshot (settings, topics, gist, profile, transcript)
- `GET /api/events` — Server-Sent Events stream
- `GET /api/audio-devices` — available PipeWire/PulseAudio monitor and mic sources
- `GET /api/sessions`, `GET /api/sessions/<name>` — list/read saved Markdown sessions
- `GET /api/metrics` — live metrics as JSON
- `GET /metrics` — Prometheus metrics (`live_thread_e2e_note_seconds`, `live_thread_stt_lag_seconds`, `live_thread_llm_note_seconds`, `live_thread_notes_total`, `live_thread_notes_rejected_total`, `live_thread_notes_per_minute`, …)

## Development & Testing

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[diarization,dev]"
.venv/bin/pytest -v
.venv/bin/python -m live_thread --debug --no-open
```

### Code Map

- `live_thread/__main__.py` — CLI entrypoint and browser launcher
- `live_thread/audio.py` — PipeWire/PulseAudio capture (`parec`) and energy VAD segmentation
- `live_thread/stt.py` — remote STT client + local `faster-whisper` fallback & hallucination filtering
- `live_thread/diarize.py` — online speaker diarization (`sherpa-onnx` TitaNet)
- `live_thread/engine.py` — thread engine, content profiling, Q&A, fact-checking, summaries & session export
- `live_thread/llm.py` — OpenAI-compatible streaming chat completions client
- `live_thread/server.py` — HTTP server, REST API, SSE event stream & static UI hosting
- `live_thread/events.py` — SSE pub/sub event bus
- `live_thread/metrics.py` — counters, sliding latency quantiles & Prometheus exporter
- `live_thread/config.py` — XDG config loading/saving (`0600`) and validation
- `live_thread/window.py` — KDE Plasma KWin opacity and keep-on-top control
- `live_thread/web/` — frontend (`index.html`, `app.js`, `i18n.js`, `style.css`, `icon.svg`)

## License

MIT — see [LICENSE](LICENSE).
