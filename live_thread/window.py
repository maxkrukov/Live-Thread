"""Window controls (opacity, keep-on-top) via KWin scripting on KDE Plasma."""
import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)

WINDOW_TITLE = "Live Thread"


def _qdbus():
    for name in ("qdbus6", "qdbus-qt6", "qdbus"):
        if shutil.which(name):
            return name
    return None


def supported():
    return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper() and _qdbus() is not None


def apply(opacity=1.0, keep_above=False):
    """Apply opacity and keep-above flags to all windows titled 'Live Thread'."""
    q = _qdbus()
    if not supported() or q is None:
        return False
    opacity = min(1.0, max(0.3, float(opacity)))
    script = (
        "var ws = workspace.windowList();"
        "for (var i = 0; i < ws.length; i++) {"
        f"  if (ws[i].caption.indexOf({WINDOW_TITLE!r}) === 0) {{"
        f"    ws[i].opacity = {opacity:.2f}; ws[i].keepAbove = {'true' if keep_above else 'false'};"
        "  }"
        "}"
    )
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
            path = f.name
        name = f"live_thread_window_{os.getpid()}"
        sid = subprocess.run(
            [q, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", path, name],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        if not sid.lstrip("-").isdigit():
            return False
        subprocess.run(
            [q, "org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script.run"],
            capture_output=True, timeout=3,
        )
        subprocess.run(
            [q, "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", name],
            capture_output=True, timeout=3,
        )
        return True
    except Exception as e:
        log.warning("window control failed: %s", e)
        return False
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
