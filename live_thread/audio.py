"""PipeWire/PulseAudio audio capture (`parec`) and energy-based phrase segmentation."""
import json
import logging
import shutil
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 480                      # 30 мс
SILENCE_END = 0.5                # секунд тиші, щоб закрити фразу
MAX_SEGMENT = 6.0                # якщо пауз немає — ріжемо приблизно кожні 6 с…
CUT_SEARCH = 1.5                 # …у найтихшому місці останніх 1.5 с, щоб не розрізати слово
MIN_SPEECH = 0.5                 # коротші шматки ігноруємо

MONITOR = "@DEFAULT_MONITOR@"    # те, що звучить у колонках/навушниках
MIC = "@DEFAULT_SOURCE@"


def available():
    return shutil.which("parec") is not None


def list_devices():
    """Return available monitor (output) and microphone sources from PipeWire/PulseAudio."""
    monitors, mics = [], []
    if not shutil.which("pactl"):
        return {"monitors": monitors, "mics": mics}
    try:
        r = subprocess.run(["pactl", "-f", "json", "list", "sources"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            for s in json.loads(r.stdout):
                name = s.get("name") or ""
                if not name:
                    continue
                item = {"name": name, "label": s.get("description") or name}
                if s.get("monitor_source") or name.endswith(".monitor"):
                    monitors.append(item)
                else:
                    mics.append(item)
            return {"monitors": monitors, "mics": mics}
    except Exception:
        pass
    try:
        r = subprocess.run(["pactl", "list", "short", "sources"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                name = parts[1].strip()
                item = {"name": name, "label": name}
                if name.endswith(".monitor"):
                    monitors.append(item)
                else:
                    mics.append(item)
    except Exception:
        pass
    return {"monitors": monitors, "mics": mics}


class Source(threading.Thread):
    """Read 16 kHz mono PCM via `parec` and deliver speech phrases to on_segment(name, audio, t_end)."""

    def __init__(self, name, device, on_segment, on_exit=None):
        super().__init__(daemon=True, name=f"source-{name}")
        self.name, self.device, self.on_segment, self.on_exit = name, device, on_segment, on_exit
        self.proc = None
        self.running = False
        self.level = 0.0

    def _close_proc(self):
        proc, self.proc = self.proc, None
        if not proc:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        if proc.stdout:
            try:
                proc.stdout.close()
            except OSError:
                pass

    def run(self):
        self.running = True
        cmd = ["parec", "-d", self.device, "--format=s16le", f"--rate={RATE}",
               "--channels=1", "--raw", "--latency-msec=60"]
        dt = FRAME / RATE
        retries = 0
        while self.running:
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                log.error("parec not found — install pulseaudio-utils / pipewire-pulseaudio")
                self.running = False
                if self.on_exit:
                    self.on_exit(self, "parec_missing")
                return
            noise = 0.004
            buf, speaking, silence, voiced, pre = [], False, 0.0, 0.0, []
            got_audio = False
            while self.running:
                raw = self.proc.stdout.read(FRAME * 2)
                if not raw or len(raw) < FRAME * 2:
                    break
                got_audio = True
                retries = 0
                x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
                self.level = rms
                is_voice = rms > max(0.008, noise * 3.0)
                if not speaking:
                    if not is_voice:
                        noise = 0.97 * noise + 0.03 * rms       # adaptive noise floor
                    pre = (pre + [x])[-10:]                       # 300 ms pre-roll before speech starts
                    if is_voice:
                        speaking, buf, silence, voiced = True, list(pre), 0.0, 0.0
                    continue
                buf.append(x)
                if is_voice:
                    silence, voiced = 0.0, voiced + dt
                else:
                    silence += dt
                if silence >= SILENCE_END:
                    if voiced >= MIN_SPEECH:
                        self.on_segment(self.name, np.concatenate(buf), time.time() - silence)
                    speaking, buf, pre = False, [], []
                elif len(buf) * dt >= MAX_SEGMENT:
                    # Continuous speech without pauses: split at the quietest trailing frame.
                    tail = int(CUT_SEARCH / dt)
                    energies = [float(np.mean(f * f)) for f in buf[-tail:]]
                    cut = len(buf) - tail + int(np.argmin(energies)) + 1
                    head, buf = buf[:cut], buf[cut:]
                    self.on_segment(self.name, np.concatenate(head), time.time() - len(buf) * dt)
                    voiced = len(buf) * dt
            self._close_proc()
            if not self.running:
                break
            retries += 1
            if retries > 2:
                log.warning("audio source %s exited unexpectedly", self.name)
                self.running = False
                if self.on_exit:
                    self.on_exit(self, "audio_lost")
                break
            log.info("audio source %s interrupted (got_audio=%s), retrying (%d/2)…", self.name, got_audio, retries)
            time.sleep(0.8)
        self.level = 0.0
        self._close_proc()

    def stop(self):
        self.running = False
        self.level = 0.0
        self._close_proc()
