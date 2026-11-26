# Contributing to Live Thread

Thank you for your interest in contributing to Live Thread!

## Development Setup

```bash
git clone https://github.com/maxkrukov/Live-Thread.git live-thread && cd live-thread
python3 -m venv .venv
.venv/bin/pip install -e ".[diarization,dev]" ruff
```

Run the app in debug mode without opening a new browser tab:

```bash
.venv/bin/python -m live_thread --debug --no-open
```

## Running Checks

Before opening a pull request, run:

```bash
.venv/bin/ruff check live_thread tests
.venv/bin/pytest -v
bash -n scripts/install.sh
node --check live_thread/web/app.js
node --check live_thread/web/i18n.js
```

## Adding a UI Language

1. Open `live_thread/web/i18n.js`.
2. Add the language code and native name to `LANGS`.
3. Copy the `en` dictionary block and translate all values (keep `{placeholder}` tokens intact).
4. Verify syntax with `node --check live_thread/web/i18n.js`.

## Pull Request Guidelines

- Keep PRs focused and small.
- Never commit personal `config.json` files, API keys, tokens, or recorded sessions.
- Add or update unit tests in `tests/` for behavioral changes.
- Update `README.md` (and `README.uk.md` when applicable) if you add settings, API endpoints, or shortcuts.
