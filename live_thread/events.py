"""In-process pub/sub event bus for Server-Sent Events (SSE)."""
import json
import queue
import threading


class Bus:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=1000)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def emit(self, _type, /, **data):
        msg = json.dumps({**data, "type": _type}, ensure_ascii=False)
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    # Drop events for slow clients instead of blocking producers.
                    pass

    def status(self, code, **params):
        """Emit a localized UI status code with optional template parameters."""
        self.emit("status", code=code, params=params)


BUS = Bus()
