#!/usr/bin/env bash
set -euo pipefail

DIARIZATION=1
DESKTOP=1

for arg in "$@"; do
  case "$arg" in
    --no-diarization) DIARIZATION=0 ;;
    --no-desktop) DESKTOP=0 ;;
    -h|--help)
      echo "Usage: $0 [--no-diarization] [--no-desktop]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/live-thread"
VENV_DIR="$DATA_DIR/venv"
MODELS_DIR="$DATA_DIR/models"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

MODEL_NAME="nemo_en_titanet_small.onnx"
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/$MODEL_NAME"

if ! command -v parec >/dev/null 2>&1; then
  echo "Warning: 'parec' not found. Install pulseaudio-utils or pipewire-pulseaudio for audio capture." >&2
fi

mkdir -p "$DATA_DIR" "$BIN_DIR"

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtualenv at $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip

if [ "$DIARIZATION" -eq 1 ]; then
  echo "Installing live-thread with diarization support..."
  "$VENV_DIR/bin/pip" install "$REPO_DIR[diarization]"
  mkdir -p "$MODELS_DIR"
  if [ ! -f "$MODELS_DIR/$MODEL_NAME" ]; then
    echo "Downloading speaker embedding model ($MODEL_NAME)..."
    curl -fL "$MODEL_URL" -o "$MODELS_DIR/$MODEL_NAME"
  fi
else
  echo "Installing live-thread without diarization..."
  "$VENV_DIR/bin/pip" install "$REPO_DIR"
fi

cat > "$BIN_DIR/live-thread" <<EOF
#!/bin/sh
# Already running? Just open the window.
PORT=\$("$VENV_DIR/bin/python" -c 'from live_thread import config; print(config.load()["port"])' 2>/dev/null || echo 8765)
if curl -s -m 1 -o /dev/null "http://127.0.0.1:\$PORT/api/health"; then
  exec xdg-open "http://127.0.0.1:\$PORT/"
fi
exec "$VENV_DIR/bin/live-thread" "\$@"
EOF
chmod +x "$BIN_DIR/live-thread"

if [ "$DESKTOP" -eq 1 ]; then
  mkdir -p "$APPS_DIR" "$ICONS_DIR"
  cp "$REPO_DIR/live_thread/web/icon.svg" "$ICONS_DIR/live-thread.svg"
  cat > "$APPS_DIR/live-thread.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Live Thread
GenericName=Conversation notes
Comment=Live topic thread with short notes for any conversation
Exec=$BIN_DIR/live-thread
Icon=live-thread
Terminal=false
Categories=Utility;
Keywords=transcription;notes;whisper;llm;
EOF
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "Installed live-thread to $VENV_DIR"
echo "Run 'live-thread' (make sure $BIN_DIR is in your PATH)."
