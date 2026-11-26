"""CLI entry point: python -m live_thread [--port N] [--no-open] [--listen] [--debug]."""
import argparse
import logging
import signal
import subprocess
import sys
import threading
import time

from . import __version__, config, window
from .events import BUS
from .server import serve

log = logging.getLogger("live_thread")


def open_window(url, engine):
    """Open a browser window only if an existing tab did not reconnect via SSE."""
    time.sleep(4)
    if not BUS.clients:
        for cmd in (
            ["google-chrome", f"--app={url}"],
            ["chromium-browser", f"--app={url}"],
            ["chromium", f"--app={url}"],
            ["xdg-open", url],
        ):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                break
            except FileNotFoundError:
                continue
        time.sleep(3)
    s = engine.settings
    if s.get("window_opacity", 1.0) < 1.0 or s.get("keep_above"):
        window.apply(s["window_opacity"], s["keep_above"])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="live-thread", description="Live topic thread for conversations")
    ap.add_argument("--port", type=int, help="HTTP port (default from config, 8765)")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser window")
    ap.add_argument("--listen", action="store_true", help="start listening immediately")
    ap.add_argument("--debug", action="store_true", help="enable debug logging")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from .engine import Engine

    engine = Engine(config.load())
    if not config.CONFIG_FILE.exists():
        config.save(engine.settings)
    port = args.port or int(engine.settings["port"])
    srv = serve(engine, args.host, port)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{port}/"
    log.info("Live Thread %s on %s", __version__, url)
    if not engine.settings.get("llm_model"):
        log.warning("llm_model is empty — open settings and configure the language model")
    if args.listen:
        engine.start()
    if not args.no_open:
        threading.Thread(target=open_window, args=(url, engine), daemon=True).start()

    def _term(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        engine.save_session()
        log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
