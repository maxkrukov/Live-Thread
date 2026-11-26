# Changelog

All notable changes to this project will be documented in this file.

## [0.5.0] - 2026-11-26

### Added
- Real-time PipeWire/PulseAudio system audio and optional microphone capture with energy-based VAD phrase cutting.
- Speech-to-text pipeline supporting an optional remote Whisper STT server with automatic fallback to local `faster-whisper`.
- Optional online speaker diarization (`S1`, `S2`, …) via `sherpa-onnx` + TitaNet.
- Self-adapting thread engine with content profiling, live "Now" gist, topic cards with instant overviews, and fact-checked concise notes (`clarify`, `correct`, `confirm`, `add`).
- Q&A mode (`answer_questions`), manual ask bar (`/`), session summaries, note pinning/dismissal, and saved Markdown session history browser.
- Mode presets (`auto`, `interview`, `lecture`, `meeting`, `media`) and multilingual UI (`en`, `uk`, `ru`, `pl`).
- Live metrics panel, JSON (`/api/metrics`), and Prometheus (`/metrics`) exporter.
- KDE Plasma window opacity and keep-on-top controls via KWin scripting.
