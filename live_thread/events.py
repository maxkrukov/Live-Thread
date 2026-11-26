"""Шина подій для веб-інтерфейсу (Server-Sent Events)."""
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
                except queue.Full:            # повільний клієнт — пропускаємо подію, а не блокуємо все
                    pass

    def status(self, code, **params):
        """Статус для інтерфейсу: код + параметри, переклад робить сам інтерфейс."""
        self.emit("status", code=code, params=params)


BUS = Bus()
