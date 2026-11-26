"""Метрики: лічильники, ковзні вибірки затримок, експорт у JSON і формат Prometheus."""
import threading
import time
from collections import Counter, deque

# Ковзні вибірки (секунди): скільки останніх значень тримати
WINDOW = 300

TIMINGS = {
    "stt_lag": "Speech end → text on screen",
    "stt_request": "Speech recognition request duration",
    "llm_note": "Model request start → note",
    "e2e_note": "Speech end → note on screen",
    "llm_qa": "Model request start → question answer",
    "e2e_qa": "Speech end → question answer on screen",
    "brief": "Topic overview generation",
    "profile": "Content profile detection",
}


class Metrics:
    def __init__(self):
        self.started = time.time()
        self.lock = threading.Lock()
        self.counters = Counter()
        self.timings = {k: deque(maxlen=WINDOW) for k in TIMINGS}
        self.note_times = deque(maxlen=2000)
        self.gauges = {}

    # -- запис
    def inc(self, name, n=1, **labels):
        key = name + "".join(f"|{k}={v}" for k, v in sorted(labels.items()))
        with self.lock:
            self.counters[key] += n

    def observe(self, name, value):
        if value is None or value < 0:
            return
        with self.lock:
            self.timings[name].append(float(value))

    def note(self):
        with self.lock:
            self.note_times.append(time.time())

    def gauge(self, name, value):
        with self.lock:
            self.gauges[name] = value

    # -- читання
    @staticmethod
    def _quantile(vals, q):
        if not vals:
            return None
        s = sorted(vals)
        return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]

    def snapshot(self):
        with self.lock:
            now = time.time()
            timings = {}
            for k, d in self.timings.items():
                v = list(d)
                timings[k] = {"count": len(v), "p50": self._quantile(v, 0.5), "p95": self._quantile(v, 0.95),
                              "avg": (sum(v) / len(v)) if v else None, "last": v[-1] if v else None}
            per_min = sum(1 for t in self.note_times if now - t <= 300) / 5.0
            counters = {}
            for key, n in self.counters.items():
                name, *labels = key.split("|")
                counters.setdefault(name, {})["|".join(labels) or "total"] = n
            return {"uptime": round(now - self.started), "timings": timings, "counters": counters,
                    "gauges": dict(self.gauges), "notes_per_min": round(per_min, 2)}

    def prometheus(self):
        """Текстовий формат Prometheus (/metrics)."""
        snap = self.snapshot()
        out = ["# HELP live_thread_uptime_seconds Process uptime", "# TYPE live_thread_uptime_seconds gauge",
               f"live_thread_uptime_seconds {snap['uptime']}"]
        for k, t in snap["timings"].items():
            m = f"live_thread_{k}_seconds"
            out += [f"# HELP {m} {TIMINGS[k]}", f"# TYPE {m} summary"]
            for q in ("p50", "p95"):
                if t[q] is not None:
                    out.append(f'{m}{{quantile="{0.5 if q == "p50" else 0.95}"}} {t[q]:.3f}')
            out.append(f"{m}_count {t['count']}")
        with self.lock:
            items = list(self.counters.items())
        seen = set()
        for key, n in sorted(items):
            name, *labels = key.split("|")
            m = f"live_thread_{name}_total"
            if m not in seen:
                out += [f"# TYPE {m} counter"]
                seen.add(m)
            lab = ",".join(f'{l.split("=")[0]}="{l.split("=")[1]}"' for l in labels)
            out.append(f"{m}{{{lab}}} {n}" if lab else f"{m} {n}")
        for g, v in snap["gauges"].items():
            out += [f"# TYPE live_thread_{g} gauge", f"live_thread_{g} {float(v)}"]
        out += ["# TYPE live_thread_notes_per_minute gauge", f"live_thread_notes_per_minute {snap['notes_per_min']}"]
        return "\n".join(out) + "\n"


METRICS = Metrics()
