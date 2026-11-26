"""Розпізнавання мови: віддалений сервер (server/stt_server.py) з локальним резервом faster-whisper."""
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
from types import SimpleNamespace as NS

import numpy as np

from .audio import RATE
from .events import BUS
from .metrics import METRICS

log = logging.getLogger(__name__)

ALLOWED = ("en", "uk", "ru", "pl", "de")   # у режимі «авто» обираємо лише з цих мов
BATCH_MAX = 24.0                           # секунд звуку в одному запиті
REMOTE_RETRY = 30.0                        # після збою сервера — стільки секунд локально
MAX_COMPRESSION_RATIO = 2.4                # поріг зацикленої галюцинації Whisper
_REPEAT_LOOP = re.compile(r"(\b\w{2,}(?:[,.\s]+\w{2,}){0,2}\b)(?:[,.\s]+\1){3,}", re.I)


def clean_hallucination(text):
    """Прибирає типові зациклення Whisper («Верховного, Верховного, Верховного…»)."""
    s = (text or "").strip()
    if not s:
        return ""
    words_before = len(re.findall(r"\w+", s))
    cleaned = _REPEAT_LOOP.sub(r"\1", s).strip(" ,;.")
    words_after = len(re.findall(r"\w+", cleaned))
    # якщо більшість сегмента була зацикленням — відкидаємо весь сегмент як галюцинацію
    if words_before >= 8 and words_after < words_before * 0.4:
        return ""
    return cleaned


class Transcriber(threading.Thread):
    def __init__(self, settings, on_text):
        super().__init__(daemon=True, name="transcriber")
        self.settings, self.on_text = settings, on_text
        self.q = queue.Queue()
        self.model = None
        self._model_name = None
        self._diarizer = None
        self._diar_failed = False
        self.extra_terms = ""         # імена й терміни з автоматичного профілю вмісту

    def prompt(self):
        """Підказка для Whisper: словник користувача + терміни з профілю (обрізаємо до ~600 символів)."""
        parts = [x for x in ((self.settings.get("glossary") or "").strip(), self.extra_terms.strip()) if x]
        return ", ".join(parts)[:600]

    # -- бекенди
    def _load_local(self):
        from faster_whisper import WhisperModel
        name = self.settings["whisper_model"]
        BUS.status("whisper_loading", model=name)
        self.model = WhisperModel(name, device="cpu", compute_type="int8",
                                  cpu_threads=max(2, (os.cpu_count() or 4) - 2))
        self._model_name = name
        BUS.status("whisper_ready", model=name)

    def _remote(self, audio, words):
        s = self.settings
        q = urllib.parse.urlencode({"language": s["language"], "words": int(words),
                                    "model": s.get("stt_model") or "large-v3-turbo"})
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        req = urllib.request.Request(
            s["stt_url"].rstrip("/") + "/transcribe?" + q, data=pcm,
            headers={"Authorization": f"Bearer {s.get('stt_token', '')}",
                     "X-Prompt": urllib.parse.quote(self.prompt()),
                     "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        return [NS(text=g["text"], no_speech_prob=g["no_speech_prob"], avg_logprob=g["avg_logprob"],
                   compression_ratio=g.get("compression_ratio", 0.0),
                   words=[NS(start=a, end=b, word=w) for a, b, w in g["words"]]) for g in d["segments"]]

    def _local(self, audio, words):
        if self.model is None or self._model_name != self.settings["whisper_model"]:
            self._load_local()
        lang = self.settings["language"]
        if lang == "auto":
            try:
                _, _, probs = self.model.detect_language(audio)
                lang = max(((c, p) for c, p in probs if c in ALLOWED), key=lambda x: x[1])[0]
            except Exception:
                lang = None
        segs, _ = self.model.transcribe(
            audio, language=lang, beam_size=1, word_timestamps=words,
            initial_prompt=(self.prompt() or None),
            vad_filter=True, condition_on_previous_text=False)
        return list(segs)

    def diarizer(self):
        if self._diarizer is None and not self._diar_failed:
            try:
                from .diarize import Diarizer
                self._diarizer = Diarizer(threshold=float(self.settings.get("diar_threshold", 0.4)))
            except Exception as e:
                self._diar_failed = True
                log.warning("diarization disabled: %s", e)
                BUS.status("diar_unavailable", err=str(e))
        return self._diarizer

    # -- черга
    def _take_batch(self):
        """Whisper завжди рахує 30-секундне вікно, тож кілька фраз з черги одним запитом — майже безкоштовно."""
        who, audio, t_end = self.q.get()
        if who is None:
            return None
        gap = np.zeros(int(0.3 * RATE), dtype=np.float32)
        held = []
        while len(audio) < BATCH_MAX * RATE:
            try:
                nxt = self.q.get_nowait()
            except queue.Empty:
                break
            if nxt[0] != who or len(audio) + len(nxt[1]) > BATCH_MAX * RATE:
                held.append(nxt)
                break
            audio = np.concatenate([audio, gap, nxt[1]])
            t_end = nxt[2]
        for h in held:
            self.q.put(h)
        return who, audio, t_end

    def run(self):
        from .diarize import split_words
        remote_down_until = 0.0
        if self.settings.get("stt_url"):
            BUS.status("stt_remote", url=self.settings["stt_url"])
        while True:
            item = self._take_batch()
            if item is None:
                return
            who, audio, t_end = item
            METRICS.gauge("stt_queue", self.q.qsize())
            if self.q.qsize() >= 3:
                BUS.status("stt_behind", n=self.q.qsize())
            diar = who == "them" and self.settings.get("diarization", True)
            t0 = time.time()
            segs = None
            if self.settings.get("stt_url") and time.time() >= remote_down_until:
                try:
                    segs = self._remote(audio, diar)
                except Exception as e:
                    remote_down_until = time.time() + REMOTE_RETRY
                    METRICS.inc("stt_errors", backend="remote")
                    log.warning("STT server unavailable: %s", e)
                    BUS.status("stt_remote_down", err=str(e)[:120])
            try:
                if segs is None:
                    segs = self._local(audio, diar)
            except Exception as e:
                METRICS.inc("stt_errors", backend="local")
                log.exception("local STT failed")
                BUS.status("stt_failed", err=str(e)[:120])
                continue
            METRICS.observe("stt_request", time.time() - t0)
            METRICS.inc("stt_segments")
            # відкидаємо типові галюцинації на тиші/шумі та зациклення
            good = [s for s in segs if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0
                    and getattr(s, "compression_ratio", 0.0) < MAX_COMPRESSION_RATIO]
            if not good:
                continue
            seg_start = t_end - len(audio) / RATE
            lag = time.time() - t_end
            METRICS.observe("stt_lag", lag)
            if who == "me" or not diar:
                text = clean_hallucination(" ".join(s.text.strip() for s in good))
                if text:
                    self.on_text(who, text, lag, start=seg_start, end=t_end, spk="ME" if who == "me" else "S1")
                continue
            words = [(w.start, w.end, w.word) for s in good for w in (s.words or [])]
            d = self.diarizer()
            labels = []
            if d:
                try:
                    labels = d.label_windows(audio)
                except Exception as e:
                    log.warning("diarization failed: %s", e)
            for spk, text, a, b in split_words(words, labels, default="S1"):
                text = clean_hallucination(text)
                if text:
                    self.on_text(who, text, lag, start=seg_start + a, end=seg_start + b, spk=spk)
