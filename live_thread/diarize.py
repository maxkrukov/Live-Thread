"""Online speaker diarization in 1.5 s sliding windows without knowing speaker count upfront."""
import os
import threading
from pathlib import Path

import numpy as np

RATE = 16000
WIN = int(1.5 * RATE)
HOP = int(0.75 * RATE)
MODEL_NAME = "nemo_en_titanet_small.onnx"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    + MODEL_NAME
)


def model_path():
    from .config import MODELS_DIR
    return Path(os.environ.get("LIVE_THREAD_SPEAKER_MODEL", MODELS_DIR / MODEL_NAME))


class Diarizer:
    def __init__(self, threshold=0.4, max_speakers=6, threads=4):
        import sherpa_onnx
        path = model_path()
        if not path.exists():
            raise FileNotFoundError(
                f"speaker model not found: {path} (run scripts/install.sh or download {MODEL_URL})"
            )
        self.ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=threads)
        )
        self.threshold = threshold
        self.max_speakers = max_speakers
        self.centroids = []          # sum of L2-normalized embeddings
        self.counts = []
        self.ids = []                # stable speaker IDs: S1, S2, ...
        self.next_id = 1
        self.pending = None          # candidate new speaker awaiting confirmation from next window
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.centroids, self.counts, self.ids = [], [], []
            self.next_id, self.pending = 1, None

    def _emb(self, x):
        s = self.ex.create_stream()
        s.accept_waveform(RATE, x)
        s.input_finished()
        e = np.asarray(self.ex.compute(s), dtype=np.float32)
        return e / (np.linalg.norm(e) + 1e-9)

    def _sims(self, e):
        return [float(c @ e / (np.linalg.norm(c) + 1e-9)) for c in self.centroids]

    def _new(self, e):
        self.centroids.append(e.copy())
        self.counts.append(1)
        self.ids.append(f"S{self.next_id}")
        self.next_id += 1
        return len(self.centroids) - 1

    def _assign(self, e):
        sims = self._sims(e)
        best = int(np.argmax(sims)) if sims else -1
        if best >= 0 and sims[best] > self.threshold:
            self.pending = None
            self.centroids[best] += e
            self.counts[best] += 1
            return best
        # Unmatched voice: promote to a new speaker only after a second consistent window.
        if self.pending is not None and float(self.pending @ e) > self.threshold:
            k = self._new(self.pending + e) if len(self.centroids) < self.max_speakers else best
            self.pending = None
            return k
        if not self.centroids:
            return self._new(e)
        self.pending = e
        return best                   # temporarily assign to closest known speaker

    def _merge(self):
        """Merge speakers whose centroids have converged. Returns {dropped_id: kept_id}."""
        remap = {}
        i = 0
        while i < len(self.centroids):
            j = i + 1
            while j < len(self.centroids):
                a, b = self.centroids[i], self.centroids[j]
                if float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)) > self.threshold + 0.15:
                    kept, dropped = self.ids[i], self.ids[j]
                    self.centroids[i] = a + b
                    self.counts[i] += self.counts[j]
                    for lst in (self.centroids, self.counts, self.ids):
                        del lst[j]
                    remap[dropped] = kept
                    for k, v in list(remap.items()):
                        if v == dropped:
                            remap[k] = kept
                else:
                    j += 1
            i += 1
        return remap

    def label_windows(self, audio):
        """Return smoothed (t_start, t_end, speaker_id) labels for windows of an audio segment."""
        with self.lock:
            out = []
            if len(audio) < WIN:
                if len(audio) >= RATE // 2:
                    k = self._assign(self._emb(audio))
                    out.append((0.0, len(audio) / RATE, self.ids[k]))
            else:
                starts = list(range(0, len(audio) - WIN + 1, HOP))
                if starts[-1] + WIN < len(audio):
                    starts.append(len(audio) - WIN)
                for s in starts:
                    x = audio[s:s + WIN]
                    if np.sqrt(np.mean(x * x)) < 0.005:
                        continue
                    k = self._assign(self._emb(x))
                    out.append((s / RATE, (s + WIN) / RATE, self.ids[k]))
            remap = self._merge()
            res = [(t0, t1, remap.get(sid, sid)) for t0, t1, sid in out]
            return _smooth(res)


def _smooth(labels):
    """Remove isolated single-window speaker flips surrounded by the same speaker."""
    lab = [item[2] for item in labels]
    for i in range(1, len(lab) - 1):
        if lab[i - 1] == lab[i + 1] != lab[i]:
            lab[i] = lab[i - 1]
    return [(a, b, s) for (a, b, _), s in zip(labels, lab)]


def split_words(words, labels, default="S1"):
    """Assign timestamped Whisper words to speakers.

    words: [(start, end, text)] -> [(speaker, text, t_start, t_end)]
    """
    if not words:
        return []
    if not labels:
        return [(default, "".join(w[2] for w in words).strip(), words[0][0], words[-1][1])]
    groups = []                       # [spk, [words]]
    for w in words:
        mid = (w[0] + w[1]) / 2
        # Pick the window whose center is closest to the word midpoint.
        spk = min(labels, key=lambda item: abs((item[0] + item[1]) / 2 - mid))[2]
        if groups and groups[-1][0] == spk:
            groups[-1][1].append(w)
        else:
            groups.append([spk, [w]])
    # Merge tiny 1-2 word fragments into the preceding speaker turn.
    merged = []
    for spk, ws in groups:
        if merged and len(ws) <= 2:
            merged[-1][1].extend(ws)
        else:
            merged.append([spk, ws])
    return [(spk, "".join(w[2] for w in ws).strip(), ws[0][0], ws[-1][1]) for spk, ws in merged]
