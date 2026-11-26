"""Двигун живого треду: тема розмови, суть («зараз»), огляд теми та короткі нотатки."""
import json
import logging
import re
import threading
import time

from . import config
from .audio import MIC, MONITOR, Source
from .events import BUS
from .llm import LLM
from .metrics import METRICS
from .stt import Transcriber

log = logging.getLogger(__name__)

NOTE_KINDS = {
    "clarify": "clarify", "correct": "correct", "confirm": "confirm", "add": "add", "answer": "answer",
    "уточнення": "clarify", "виправлення": "correct", "підтвердження": "confirm", "доповнення": "add",
    "уточнение": "clarify", "исправление": "correct", "подтверждение": "confirm", "дополнение": "add",
}

QUESTION = re.compile(
    r"\?|\b(как|какие|какой|какая|что|чем|почему|зачем|когда|где|кто|расскаж\w*|объясн\w*|назов\w*|отлич\w*|сравн\w*|"
    r"як|які|який|яка|що|чим|чому|навіщо|коли|де|хто|чи|розкаж\w*|поясн\w*|назв\w*|відмін\w*|порівня\w*|"
    r"jak|jakie|jaki|co|czym|dlaczego|kiedy|gdzie|kto|wyjaśn\w*|porówn\w*|"
    r"how|what|which|why|when|where|who|explain|difference|compare|versus|vs|tell me|can you|could you|"
    r"is there|are there|does|do we|should we)\b", re.I)

# нотатки, що коментують помилки розпізнавання, а не суть
ASR_COMMENT = re.compile(r"не\s*[«\"']|\(не\s|распозна|розпізна|речь ид[её]т о|мова йде про|misrecogn|"
                         r"misheard|опечат|скорее всего,? (?:речь|имел)|ймовірно,? (?:мова|мал)", re.I)
# «порожні» підтвердження
VAGUE = re.compile(r"действительно|является|активно|стандарт|правильн|верно|корректн|indeed|correct|"
                   r"справді|дійсно|правильно", re.I)
# пункти огляду без змісту: «інформації немає», «не згадується» тощо
NO_INFO = re.compile(r"не упомина|отсутству|не подтвержд|нет данных|не указан|не згаду|відсутн|не підтвердж|"
                     r"немає даних|not mentioned|no information|not available|unknown|невідом|неизвест", re.I)
# здогадки замість знань — такі нотатки не показуємо
SPECULATIVE = re.compile(r"\b(імовірно|ймовірно|можливо|мабуть|вероятно|возможно|наверное|probably|likely|perhaps|"
                         r"maybe|presumably|prawdopodobnie|być może|chyba)\b", re.I)
# «порожні» нотатки: «**X** — персонаж / ім'я …» без назви твору чи справжньої ролі
TRIVIAL_HEAD = re.compile(r"^\*\*[^*]{1,40}\*\*\s*[—–:-]\s*(це\s+|это\s+|is\s+(a\s+|the\s+)?)?"
                          r"(персонаж|character|ім'?я|імена|имя|имена|name|names|герой|hero)\w*\b", re.I)
HAS_SUBSTANCE = re.compile(r"[«»\"“”]|\b(лідер|лидер|leader|рятувальн|спасател|rescue|пілот|пилот|pilot|поліц|"
                           r"полиц|police|пожежн|пожарн|fire|mayor|мер\b)", re.I)
FROM_WORK = re.compile(r"\b(з|із|из|from|w|z)\s+[A-ZА-ЯІЇЄ]")      # «з Щенячого патруля» — з великої літери


def is_trivial(text):
    return bool(TRIVIAL_HEAD.search(text)) and not HAS_SUBSTANCE.search(text) and not FROM_WORK.search(text)


# переказ розмови замість нових знань («Учасники обговорюють…», «S2 каже…»)
RESTATE = re.compile(r"^\W*(учасники|співрозмовники|спікер|ведучі?|розмова|участники|собеседники|спикер|ведущи[йе]|"
                     r"разговор|speakers?|participants|the conversation|host|uczestnicy|rozmówcy|S\d+|ME)\b", re.I)
CONTENT_TYPES = ("tech_interview", "tech_talk", "lecture", "podcast", "news", "film", "cartoon", "series",
                 "sports", "meeting", "chat", "music", "other")
PRESET_GUIDANCE = {
    "interview": (
        "Mode preset: TECH INTERVIEW. Focus on exact technical definitions, trade-offs, commands, "
        "comparisons (X vs Y), and catching interviewer questions immediately."
    ),
    "lecture": (
        "Mode preset: LECTURE / TECH TALK. Focus on key concepts, architecture, terminology, and "
        "crisp structured explanations."
    ),
    "meeting": (
        "Mode preset: MEETING. Focus on decisions, owners, deadlines, risks, architecture choices, "
        "and concrete action items."
    ),
    "media": (
        "Mode preset: FILM / PODCAST / SHOW. Focus on who is who, plot context, background on "
        "people/events/places, cultural references, and translating notable foreign lines."
    ),
}
GENERIC_TOPIC = re.compile(r"(перв|втор|трет|друг|следующ|наступн|нов|another|next|second|first|drug|kolejn)\w*\s+"
                           r"(вопрос|питанн|question)", re.I)
PREFIX = re.compile(r"^[\s*_-]*(GIST|TOPIC|NOTE)\s*[*_]*\s*:\s*[*_]*\s*", re.I)


def detect_language(text, setting="auto", conversation_lang="auto"):
    """Мова нотаток: задана користувачем або мова останніх реплік."""
    if setting and setting != "auto":
        return setting
    latin = sum(ch.isascii() and ch.isalpha() for ch in text)
    cyr = sum("Ѐ" <= ch <= "ӿ" for ch in text)
    if latin > cyr:
        return "Polish" if conversation_lang == "pl" else "English"
    uk = sum(ch in "іїєґІЇЄҐ" for ch in text)
    ru = sum(ch in "ыэъёЫЭЪЁ" for ch in text)
    if uk > ru:
        return "Ukrainian"
    if ru > uk or conversation_lang == "ru":
        return "Russian"
    return "Ukrainian"


def tokens(text):
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 3}


def bold_terms(text):
    return {w.strip().lower()[:6] for w in re.findall(r"\*\*(.+?)\*\*", text)}


def same_topic(a, b):
    ta = {w[:5] for w in re.findall(r"\w+", a.lower()) if len(w) > 2}
    tb = {w[:5] for w in re.findall(r"\w+", b.lower()) if len(w) > 2}
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.5


Q_STOPWORDS = {
    "what", "which", "where", "when", "why", "how", "who", "does", "did", "can", "could", "should", "would",
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "there", "their", "about",
    "difference", "between", "compare", "versus", "explain", "tell", "work", "works", "mean", "means",
    "що", "таке", "який", "яка", "які", "чим", "чому", "коли", "хто", "різниця", "між", "як", "працює", "поясни", "розкажи", "про", "для",
    "что", "такое", "какой", "какая", "какие", "чем", "почему", "когда", "кто", "разница", "между", "как", "работает", "объясни", "расскажи", "отличие", "отличается",
    "jak", "jaki", "jakie", "czym", "dlaczego", "kiedy", "kto", "różnica", "między", "działa", "wyjaśnij", "jest",
}


def same_question(a, b):
    raw_a = [w for w in re.findall(r"\w+", a.lower()) if len(w) > 2]
    raw_b = [w for w in re.findall(r"\w+", b.lower()) if len(w) > 2]
    ta = {w[:5] for w in raw_a if w not in Q_STOPWORDS} or {w[:5] for w in raw_a}
    tb = {w[:5] for w in raw_b if w not in Q_STOPWORDS} or {w[:5] for w in raw_b}
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.55


def is_duplicate(text, existing):
    """Повтор — якщо схоже за словами (>45%) або має ті самі ключові терміни."""
    a, ta = tokens(text), bold_terms(text)
    if not a:
        return True
    for n in existing:
        if n.get("kind") == "answer":
            continue
        b = tokens(n["text"])
        if b and len(a & b) / len(a | b) > 0.45:
            return True
        tb = bold_terms(n["text"])
        if ta and tb and len(ta & tb) >= max(2, 0.6 * min(len(ta), len(tb))):
            return True
    return False


def is_empty_confirm(text, recent_text):
    """Підтвердження без жодного нового терміна, якого не було в розмові."""
    terms = [t.strip().lower() for t in re.findall(r"\*\*(.+?)\*\*", text)]
    new = [t for t in terms if t[:5] not in recent_text]
    return not new or (len(new) < 2 and bool(VAGUE.search(text)))


def parse_line(line):
    """'**NOTE:** add | text' → ('NOTE', 'add | text'). Жирний текст у кінці не чіпаємо."""
    line = PREFIX.sub(lambda m: m.group(1).upper() + ": ", line.strip())
    for tag in ("GIST", "TOPIC", "NOTE"):
        if line.startswith(tag + ":"):
            return tag, line[len(tag) + 1:].strip()
    return None, line


QA_Q = re.compile(r"^[\s*_-]*Q\s*[*_]*\s*:\s*[*_]*\s*(.+)$", re.I)
QA_A = re.compile(r"^[\s*_-]*A\s*[*_]*\s*:\s*[*_]*\s*(.*)$", re.I)


def parse_qa(text):
    """Розбирає відповідь Q&A ('Q: ...\\nA:\\n<markdown>'). Повертає (q, a) або (None, None)."""
    raw = (text or "").strip()
    if not raw or raw.strip(" .*\n").upper() == "NONE":
        return None, None
    q, a_lines, in_a = "", [], False
    for line in raw.splitlines():
        if not in_a:
            mq = QA_Q.match(line)
            if mq and not q:
                q = mq.group(1).strip().strip("*\"«»")
                continue
            ma = QA_A.match(line)
            if ma:
                in_a = True
                if ma.group(1).strip():
                    a_lines.append(ma.group(1).rstrip())
                continue
        else:
            a_lines.append(line.rstrip())
    ans = "\n".join(a_lines).strip()
    if ans.startswith("```") and ans.endswith("```") and ans.count("```") == 2:
        inner = ans.splitlines()
        if len(inner) >= 3 and inner[0].lstrip().startswith("```") and inner[0].strip()[3:].lower() in ("", "md", "markdown"):
            ans = "\n".join(inner[1:-1]).strip()
    if not q or not ans or q.strip(" .*").upper() == "NONE" or ans.strip(" .*").upper() == "NONE":
        return None, None
    return q, ans


class Engine:
    MIN_NEW_WORDS = 6        # без запитання — оновлюємо тред кожні ~6 нових слів
    WINDOW_SEC = 60          # скільки останньої розмови бачить модель
    MAX_INFLIGHT = 2         # паралельних запитів до моделі
    QA_DEBOUNCE_Q = 0.5      # після '?' чекаємо пів секунди на дописку
    QA_DEBOUNCE_KW = 1.2     # без '?' чекаємо трохи довше, щоб договорили питання
    AUTOSAVE_EVERY = 90.0    # секунд між автозбереженнями поточної сесії

    def __init__(self, settings=None):
        self.settings = settings or config.load()
        self.llm = LLM(self.settings)
        self.transcript = []          # [{spk, text, t, end}]
        self.topics = []              # [{id, title, brief, notes:[{kind, text, t}]}]
        self.gist = ""
        self.profile = {}             # автоматично визначений профіль вмісту (тип, що це, учасники, словник)
        self._note_id = 0
        self.rejected = []            # нотатки, відкинуті перевіркою фактів (хибні) — модель не повинна їх повторювати
        self.obvious_rejected = []    # нотатки, відкинуті як загальновідомі (для дедуплікації, але НЕ хибні!)
        self.answered_questions = []  # уже відповідані запитання (дедуплікація)
        self._profile_busy = False
        self._profile_t = 0.0
        self._profile_words = 0
        self._profile_topics = 0
        self.sources, self.transcriber, self.listening = [], None, False
        self.lock = threading.Lock()
        self._session_id = 0
        self._session_file = None
        self._autosave_t = 0.0
        self._autosave_rev = 0
        self._rev = 0
        self._inflight = 0
        self._dirty = False
        self._seq = 0
        self._newest_out = 0
        self._words_since = 0
        self._note_cursor = (-1, 0)   # (line_idx, char_offset) — коректно бачить і склеєні репліки
        self._qa_cursor = (-1, 0)
        self._qa_busy = False
        self._qa_pending = False
        self._qa_timer = None

    def state_snapshot(self):
        """Потокобезпечний знімок стану для GET /api/state."""
        with self.lock:
            return {
                "settings": dict(self.settings),
                "listening": self.listening,
                "topics": [{**t, "brief": list(t.get("brief", [])), "notes": [dict(n) for n in t.get("notes", [])]}
                           for t in self.topics],
                "gist": self.gist,
                "profile": dict(self.profile),
                "transcript": [dict(x) for x in self.transcript[-30:]],
            }

    def _cursor_now(self):
        if not self.transcript:
            return (-1, 0)
        return (len(self.transcript) - 1, len(self.transcript[-1]["text"]))

    def _new_text_since(self, cursor):
        if not self.transcript:
            return ""
        c_idx, c_off = cursor
        if c_idx < 0:
            return " ".join(x["text"] for x in self.transcript)
        parts = []
        if c_idx < len(self.transcript):
            tail = self.transcript[c_idx]["text"][c_off:].strip()
            if tail:
                parts.append(tail)
        for x in self.transcript[c_idx + 1:]:
            if x["text"].strip():
                parts.append(x["text"].strip())
        return " ".join(parts)

    @staticmethod
    def _is_new_line(abs_idx, line, new_from):
        if isinstance(new_from, tuple):
            c_idx, c_off = new_from
            return abs_idx > c_idx or (abs_idx == c_idx and len(line["text"]) > c_off)
        return abs_idx >= new_from

    # ------------------------------------------------------------- audio
    def _on_source_exit(self, src, reason):
        with self.lock:
            if src in self.sources:
                self.sources.remove(src)
            still_has_main = any(s.name == "them" for s in self.sources)
            should_stop = self.listening and not still_has_main
        BUS.status(reason)
        if should_stop:
            self.stop()

    def _device(self, key, default):
        v = (self.settings.get(key) or "").strip()
        return v or default

    def start(self):
        if self.listening:
            return
        if self.transcriber is None:
            self.transcriber = Transcriber(self.settings, self.on_text)
            self.transcriber.start()
        sources = [Source("them", self._device("monitor_device", MONITOR), self.on_segment, on_exit=self._on_source_exit)]
        if self.settings.get("use_mic"):
            sources.append(Source("me", self._device("mic_device", MIC), self.on_segment, on_exit=self._on_source_exit))
        with self.lock:
            self.sources = sources
            self.listening = True
        for s in sources:
            s.start()
        METRICS.gauge("listening", 1)
        BUS.emit("listening", on=True)
        threading.Thread(target=self._levels, daemon=True, name="levels").start()

    def sync_sources(self):
        """Оновлює пристрої захоплення на льоту, якщо змінили use_mic/monitor_device/mic_device."""
        to_start, to_stop = [], []
        with self.lock:
            if not self.listening:
                return
            want_mon = self._device("monitor_device", MONITOR)
            mon = next((s for s in self.sources if s.name == "them"), None)
            if not mon or mon.device != want_mon:
                if mon:
                    self.sources.remove(mon)
                    to_stop.append(mon)
                ns = Source("them", want_mon, self.on_segment, on_exit=self._on_source_exit)
                self.sources.append(ns)
                to_start.append(ns)
            mic = next((s for s in self.sources if s.name == "me"), None)
            want_mic = bool(self.settings.get("use_mic"))
            want_mic_dev = self._device("mic_device", MIC)
            if want_mic and (not mic or mic.device != want_mic_dev):
                if mic:
                    self.sources.remove(mic)
                    to_stop.append(mic)
                ns = Source("me", want_mic_dev, self.on_segment, on_exit=self._on_source_exit)
                self.sources.append(ns)
                to_start.append(ns)
            elif not want_mic and mic:
                self.sources.remove(mic)
                to_stop.append(mic)
        for s in to_stop:
            s.stop()
        for s in to_start:
            s.start()

    def stop(self):
        with self.lock:
            if self._qa_timer:
                self._qa_timer.cancel()
                self._qa_timer = None
            self._qa_pending = False
            sources, self.sources, self.listening = list(self.sources), [], False
        for s in sources:
            s.stop()
        METRICS.gauge("listening", 0)
        BUS.emit("listening", on=False)

    def _levels(self):
        while self.listening:
            BUS.emit("levels", level=round(min(1.0, max((s.level for s in self.sources), default=0) * 12), 3))
            time.sleep(0.2)

    def on_segment(self, who, audio, t_end=None):
        if self.transcriber:
            self.transcriber.q.put((who, audio, t_end or time.time()))
            METRICS.gauge("stt_queue", self.transcriber.q.qsize())

    # ------------------------------------------------------------- transcript
    def on_text(self, who, text, lag, start=None, end=None, spk=None):
        now = time.time()
        spk = spk or ("ME" if who == "me" else "S1")
        start = start if start is not None else now
        with self.lock:
            last = self.transcript[-1] if self.transcript else None
            if last and last["spk"] == spk and start - last["end"] < 3.0:
                last["text"] = (last["text"] + " " + text).strip()
                last["t"], last["end"] = now, end or now
                merged = True
            else:
                self.transcript.append({"spk": spk, "text": text, "t": now, "end": end or now})
                merged = False
            self._words_since += len(text.split())
            self._rev += 1
        METRICS.inc("lines")
        BUS.emit("line", spk=spk, text=self.transcript[-1]["text"], merged=merged, lag=round(lag, 1))
        self._profile_words += len(text.split())
        self.kick()
        self.maybe_profile()

    # ------------------------------------------------------------- engine loop
    def kick(self, force=False):
        """Новий текст → одразу новий запит (до MAX_INFLIGHT паралельно). Застарілі відповіді відкидаються."""
        if self.settings.get("answer_questions"):
            self.maybe_answer(force=force)
        last = self.transcript[-1]["text"] if self.transcript else ""
        question = self._words_since >= 3 and bool(QUESTION.search(last[-160:]))
        if not (force or question or self._words_since >= self.MIN_NEW_WORDS):
            return
        with self.lock:
            if self._inflight >= self.MAX_INFLIGHT:
                self._dirty = True
                return
            self._inflight += 1
            self._seq += 1
            seq = self._seq
            self._words_since = 0
        METRICS.gauge("llm_inflight", self._inflight)
        threading.Thread(target=self._run, args=(seq,), daemon=True, name=f"step-{seq}").start()

    def _run(self, seq):
        try:
            self._step(seq)
        except Exception as e:
            METRICS.inc("llm_errors")
            log.warning("thread step failed: %s", e)
            BUS.status("llm_down", err=str(e)[:120])
        finally:
            with self.lock:
                self._inflight -= 1
                again, self._dirty = self._dirty, False
            METRICS.gauge("llm_inflight", self._inflight)
            if again:
                self.kick(force=True)

    def _all_notes(self, n=14):
        return [x for tp in self.topics for x in tp["notes"]][-n:]

    def _lang(self, text):
        return detect_language(text, self.settings.get("notes_language", "auto"), self.settings.get("language"))

    # ------------------------------------------------------------- content profile
    PROFILE_FIRST_WORDS = 20      # перший профіль — щойно набралось трохи тексту
    PROFILE_EVERY = 90            # далі уточнюємо раз на ~90 с (якщо є новий текст) або після 2 нових тем

    def maybe_profile(self):
        if self._profile_busy:
            return
        now = time.time()
        first = not self.profile and self._profile_words >= self.PROFILE_FIRST_WORDS
        refresh = self.profile and self._profile_words >= 60 and (
            now - self._profile_t >= self.PROFILE_EVERY or len(self.topics) - self._profile_topics >= 2)
        if first or refresh:
            self._profile_busy = True
            threading.Thread(target=self.update_profile, daemon=True, name="profile").start()

    def update_profile(self):
        t0 = time.time()
        try:
            with self.lock:
                sid = self._session_id
                self._profile_words, self._profile_topics = 0, len(self.topics)
                snap_tr = list(self.transcript[-40:])
                snap_topics = [t["title"] for t in self.topics[-8:]]
                prev_prof = dict(self.profile) if self.profile else None
            text = "\n".join(f"[{x['spk']}] {x['text']}" for x in snap_tr)[-3000:]
            lang = self._lang(" ".join(x["text"] for x in snap_tr[-4:]))
            prev = json.dumps(prev_prof, ensure_ascii=False) if prev_prof else "(none)"
            ctx = self.settings.get("context", "").strip()
            sys_p = (
                "You classify live content from a noisy speech-to-text transcript (possibly mixed languages, "
                "dubbed shows, misspelled names) so that an assistant can adapt its notes.\n"
                "Work step by step inside the JSON: first list the proper names you hear (normalise spelling), then "
                "check whether that SET of names together matches a well-known film, cartoon, series, game, show or "
                "public figure; only if several names fit one work, name it (in its common title). Technical talks: "
                "identify the field and stack from the questions. Also list words that are clearly misrecognised "
                "versions of a term given the context (e.g. 'Volta' for 'Vault' in a secrets talk). Do not invent — if nothing fits, describe the "
                "content generically.\n"
                f"Answer with ONE JSON object, nothing else:\n"
                '{"names_heard": ["..."], "identified_work": "<known title or empty>", '
                '"type": one of ' + json.dumps(CONTENT_TYPES) + ", "
                f'"title": "<what it is, max 8 words, in {lang}>", '
                f'"participants": "<who speaks / main characters, max 12 words, in {lang}>", '
                '"languages": "<spoken languages>", '
                '"terms": ["<up to 25 names and terms likely to be heard, correct original spelling>"], '
                '"asr_fixes": {"<misrecognised word as it appears>": "<intended term>"}, '
                '"focus": "<one English sentence: which notes help a listener of THIS content>"}'
                + (f"\nUser-provided context (trust it): {ctx}" if ctx else ""))
            user = (f"Previous profile: {prev}\nTopics so far: {', '.join(snap_topics) or '-'}"
                    f"\nTranscript:\n{text}")
            out = self.llm.complete([{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
                                    max_tokens=450)
            m = re.search(r"\{.*\}", out, re.S)
            if not m:
                return
            d = json.loads(m.group(0))
            prof = {
                "type": d.get("type") if d.get("type") in CONTENT_TYPES else "other",
                "title": str(d.get("title", "") or d.get("identified_work", ""))[:80],
                "work": str(d.get("identified_work", ""))[:80],
                "participants": str(d.get("participants", ""))[:120],
                "languages": str(d.get("languages", ""))[:60],
                "terms": list(dict.fromkeys([str(x)[:40] for x in (d.get("terms") or []) + (d.get("names_heard") or [])
                                             if str(x).strip()]))[:25],
                "focus": str(d.get("focus", ""))[:200],
                "asr_fixes": {str(k)[:30]: str(v)[:40] for k, v in list((d.get("asr_fixes") or {}).items())[:12]
                              if str(k).strip() and str(v).strip() and str(k).lower() != str(v).lower()}
                if isinstance(d.get("asr_fixes"), dict) else {},
            }
            names = [str(x) for x in (d.get("names_heard") or []) if str(x).strip()]
            if prof["type"] in ("film", "cartoon", "series", "other") and not prof["work"] and len(names) >= 3:
                work = self.identify_work(names)
                if work:
                    prof["work"] = work
                    prof["title"] = work if not prof["title"] or len(prof["title"]) > 40 else f"{work} · {prof['title']}"
            elif prof["work"] and prof["work"] not in prof["title"]:
                prof["title"] = f"{prof['work']} · {prof['title']}"[:80]
            with self.lock:
                if sid != self._session_id:
                    return
                self.profile = prof
                self._profile_t = time.time()
            if self.transcriber:                    # словник профілю (і виправлені терміни) допомагають Whisper
                self.transcriber.extra_terms = ", ".join(dict.fromkeys(list(prof["asr_fixes"].values()) + prof["terms"]))
            METRICS.inc("profiles")
            METRICS.observe("profile", time.time() - t0)
            BUS.emit("profile", profile=prof)
        except Exception as e:
            log.warning("profile failed: %s", e)
        finally:
            self._profile_busy = False

    def identify_work(self, names):
        """Вузьке питання «з якого відомого твору ці імена» — надійніше, ніж у загальному профілі."""
        try:
            out = self.llm.complete([
                {"role": "system", "content": "You are an expert in films, cartoons, TV series, games and books. "
                 "Given character names heard in a (possibly dubbed, misspelled) show, reply with the international "
                 "title of the well-known work in which SEVERAL of these names appear together, or NONE if you are "
                 "not sure. Reply with the title only."},
                {"role": "user", "content": ", ".join(names[:20])}], max_tokens=20)
        except Exception:
            return ""
        out = out.strip().strip(".\"«»")
        return "" if not out or out.upper().startswith("NONE") or len(out) > 60 else out

    def profile_block(self):
        preset = self.settings.get("preset", "auto")
        p_line = (PRESET_GUIDANCE[preset] + "\n") if preset in PRESET_GUIDANCE else ""
        p = self.profile
        if not p:
            return p_line + "Content profile: not detected yet — infer the kind of content from the conversation.\n"
        work = f"; identified work={p['work']} (use your knowledge of it: characters, plot, world)" if p.get("work") else ""
        fixes = "; ".join(f"'{a}' = {b}" for a, b in (p.get("asr_fixes") or {}).items())
        fixes = f"Known misrecognitions in this transcript: {fixes}.\n" if fixes else ""
        return p_line + fixes + (f"Content profile (auto-detected): type={p['type']}; what={p['title']}{work}; "
                f"participants={p['participants']}; languages={p['languages']}.\n"
                f"Note focus for this content: {p['focus']}\n")

    def build_prompt(self, recent, new_from, idx0):
        lines = [f"{'NEW' if self._is_new_line(idx0 + i, x, new_from) else 'old'} [{x['spk']}] {x['text']}"
                 for i, x in enumerate(recent)]
        cur = self.topics[-1] if self.topics else None
        said = "\n".join(
            f"- Q: {n['question']}" if n.get("kind") == "answer" and n.get("question") else f"- {n['text']}"
            for n in self._all_notes()) or "(none)"
        titles = ", ".join(t["title"] for t in self.topics[-6:]) or "(none)"
        lang = self._lang(" ".join(x["text"] for x in recent[-4:]))
        ctx = self.settings.get("context", "").strip()
        domain = ", ".join(x for x in ((self.settings.get("glossary") or "")[:300],
                                        ", ".join(self.profile.get("terms", [])[:20])) if x)
        fixed = self.settings.get("notes_language", "auto") != "auto"
        qa_on = bool(self.settings.get("answer_questions"))
        sys_p = (
            f"Write GIST, TOPIC and NOTE text in {lang}.\n"
            + (f"The conversation may be in any language — always translate your output into {lang}; keep "
               "technical terms, commands and names in their original form.\n" if fixed else "") +
            "You are a smart live companion listening to whatever is playing: a technical talk, an interview, a "
            "lecture, a podcast, news, a film, a cartoon or a casual chat (speech-to-text: may contain misrecognised "
            "words and mixed languages — silently interpret them, NEVER comment on spelling or recognition errors).\n"
            "First work out what kind of content it is and what is happening RIGHT NOW (lines marked NEW; 'old' "
            "lines are context). Then, when useful, add a short note that helps the listener follow and understand:\n"
            "- technical talk / interview: facts, commands, terms; correct factual mistakes;\n"
            "- lecture / podcast / news: key concepts, definitions, background on people, places, events;\n"
            "- film / cartoon / story: who is who, what is happening and why, meaning of important foreign-language "
            "lines or cultural references;\n"
            "- casual chat / podcast / event: background on people, companies, products and terms mentioned.\n"
            "Skip common knowledge this listener already has (capitals, leaders of major countries, famous "
            "companies or regions, everyday words, basic terms of the field for a specialist audience).\n"
            "A note must add knowledge from OUTSIDE the conversation (fact, definition, background, translation). "
            "Never restate or summarise what the speakers said — the GIST line already does that.\n"
            "Note kinds: clarify (explain something vague), correct (a clear factual error), confirm (only with a "
            "concrete new detail — never bare agreement), add (useful missing information or context).\n"
            + self.profile_block() +
            "Rules: only state what you are confident is correct; if unsure, skip — never write guesses "
            "('probably', 'maybe', 'імовірно', 'вероятно'). Never 'correct' a true statement "
            "or a character's line in a story. Never guess meanings of unknown codes or names. Never repeat an "
            "already-given note. Skip filler (greetings, 'ok', 'thanks', laughter). Never write empty notes that "
            "only say someone is a character or a name — every note must carry real information.\n"
            + (f"Likely domain terms: {domain}\n" if domain else "") +
            "Output format, nothing else:\n"
            f"GIST: <max 10 words in {lang}: what is being asked/discussed/happening right now>\n"
            "TOPIC: <2-5 words>   (ONLY when the subject, question or scene clearly changes; a passing mention is "
            "NOT a new topic. Name it after the subject, never 'Second question' or 'Casual banter'. Omit the line if "
            "it did not change. If returning to an earlier topic, reuse its exact title)\n"
            "NOTE: <clarify|correct|confirm|add> | <max 16 words, key words in **bold**>\n"
            "Up to 2 NOTE lines for two different useful points; or NOTE: NONE if nothing useful.\n"
            + ("" if qa_on else
               "If someone just asked a real knowledge question (not a character's line in a story), give an 'add' NOTE "
               "that directly answers it.\n")
            + (f"\nUser context: {ctx}\n" if ctx else ""))
        fresh_q = None if qa_on else next(
            (x["text"] for i, x in reversed(list(enumerate(recent[-3:], max(0, len(recent) - 3))))
             if self._is_new_line(idx0 + i, x, new_from) and QUESTION.search(x["text"][-160:])), None)
        user = ((f"Possibly a question was just asked: «{fresh_q[-200:]}» — if it is a real knowledge question, "
                 "answer it in a NOTE.\n" if fresh_q else "")
                + f"Current topic: {cur['title'] if cur else '(none)'}\nEarlier topics: {titles}\n"
                f"Notes already given (do not repeat):\n{said}\n"
                + ("Notes rejected as FALSE (never repeat these claims):\n"
                   + "\n".join(f"- {n['text']}" for n in self.rejected[-6:]) + "\n" if self.rejected else "")
                + "\nConversation:\n" + "\n".join(lines)
                + f"\n\n(Write the TOPIC title and the NOTE in {lang}.)")
        return [{"role": "system", "content": sys_p}, {"role": "user", "content": user}]

    def _step(self, seq):
        t0 = time.time()
        with self.lock:
            sid = self._session_id
            cutoff = t0 - self.WINDOW_SEC
            recent = [dict(x) for x in self.transcript if x["t"] >= cutoff] or [dict(x) for x in self.transcript[-6:]]
            new_from = self._note_cursor
            self._note_cursor = self._cursor_now()
            idx0 = len(self.transcript) - len(recent)
        if not recent:
            return
        METRICS.inc("llm_requests")
        st = {"notes": [], "topic": False, "t0": t0, "sid": sid,
              "recent": " ".join(x["text"] for x in recent[-4:]).lower(),
              "speech_end": max(x["end"] for x in recent)}
        for line in self.llm.stream_lines(self.build_prompt(recent, new_from, idx0), max_tokens=140):
            if sid != self._session_id or seq < self._newest_out:  # скинули сесію або є новіший результат
                METRICS.inc("llm_stale")
                return
            self.handle_line(line, st, seq)
        if sid != self._session_id:
            return
        took = time.time() - t0
        BUS.emit("tick", took=round(took, 2), notes=len(st["notes"]),
                 result="note" if st["notes"] else ("topic" if st["topic"] else "none"))

    # ------------------------------------------------------------- output handling
    def _mark(self, seq):
        if seq > self._newest_out:
            self._newest_out = seq

    def _reject(self, reason):
        METRICS.inc("notes_rejected", reason=reason)

    def handle_line(self, line, st, seq):
        if "sid" in st and st["sid"] != self._session_id:
            return
        tag, body = parse_line(line)
        if tag == "GIST":
            gist = body.strip(" *")
            if gist and gist.upper() != "NONE":
                with self.lock:
                    if "sid" in st and st["sid"] != self._session_id:
                        return
                    self._mark(seq)
                    self.gist = gist
                BUS.emit("gist", text=gist)
        elif tag == "TOPIC":
            self._on_topic(body.strip(" *"), st, seq)
        elif tag == "NOTE" and len(st["notes"]) < 2:
            self._on_note(body, st, seq)

    def _on_topic(self, topic, st, seq):
        if topic.strip(" .").upper() in ("NONE", "", "-") or GENERIC_TOPIC.search(topic):
            return
        with self.lock:
            if "sid" in st and st["sid"] != self._session_id:
                return
            if not self.profile:                         # поки не зрозуміло, що це за вміст, теми часто хибні
                return
            cur = self.topics[-1] if self.topics else None
            if cur and same_topic(topic, cur["title"]):
                return
            self._mark(seq)
            st["topic"] = True
            prev = next((t for t in reversed(self.topics) if same_topic(t["title"], topic)), None)
            if prev:                                      # повернулися до старої теми — піднімаємо її картку
                self.topics.remove(prev)
                self.topics.append(prev)
                rev_id, rev_title = prev["id"], prev["title"]
                revived = True
            else:
                tp = {"id": max((t["id"] for t in self.topics), default=0) + 1, "title": topic, "notes": [],
                      "brief": [], "t": time.time()}
                self.topics.append(tp)
                revived = False
        if revived:
            BUS.emit("topic", id=rev_id, title=rev_title, revived=True)
            return
        METRICS.inc("topics")
        BUS.emit("topic", id=tp["id"], title=topic)
        threading.Thread(target=self.brief, args=(tp,), daemon=True, name="brief").start()

    def _on_note(self, body, st, seq):
        kind, _, text = body.partition("|")
        kind = NOTE_KINDS.get(kind.strip(" *").lower(), "add")
        text = text.strip() or body.strip()
        if text.strip(" .*").upper() in ("NONE", "-", ""):
            return
        if ASR_COMMENT.search(text):
            return self._reject("asr_comment")
        if SPECULATIVE.search(text):
            return self._reject("speculative")
        if is_trivial(text):
            return self._reject("trivial")
        if RESTATE.search(text):
            return self._reject("restate")
        with self.lock:
            cur_notes = list(self.topics[-1]["notes"][-12:]) if self.topics else []
            existing = cur_notes + self._all_notes(8) + list(self.rejected) + list(self.obvious_rejected) + list(st["notes"])
        if is_duplicate(text, existing):
            return self._reject("duplicate")
        if kind == "confirm" and is_empty_confirm(text, st["recent"]):
            return self._reject("empty_confirm")
        # перевірки ДО показу (обидві паралельно, ≈0.15–0.25 с): хибне чи загальновідоме на екран не потрапляє
        good, obvious = self._checks(text)
        if not good:
            log.info("note rejected (verify): %s", text)
            with self.lock:
                self.rejected = (self.rejected + [{"text": text}])[-10:]
            return self._reject("verify")
        if obvious:
            log.info("note rejected (obvious): %s", text)
            with self.lock:
                self.obvious_rejected = (self.obvious_rejected + [{"text": text}])[-15:]
            return self._reject("obvious")
        created_placeholder = False
        with self.lock:
            if ("sid" in st and st["sid"] != self._session_id) or seq < self._newest_out:
                return self._reject("stale")
            self._mark(seq)
            self._note_id += 1
            note = {"id": self._note_id, "kind": kind, "text": text, "t": time.time()}
            st["notes"].append(note)
            if not self.topics:
                self.topics.append({"id": 1, "title": "—", "notes": [], "brief": [], "t": time.time()})
                created_placeholder = True
            topic = self.topics[-1]
            topic["notes"].append(note)
            topic_id = topic["id"]
            self._rev += 1
        if created_placeholder:
            BUS.emit("topic", id=1, title="—")
        llm_t = time.time() - st["t0"]
        e2e = time.time() - st["speech_end"]
        METRICS.inc("notes", kind=kind)
        METRICS.note()
        METRICS.observe("llm_note", llm_t)
        METRICS.observe("e2e_note", e2e)
        BUS.emit("note", topic=topic_id, took=round(llm_t, 2), e2e=round(e2e, 1), **note)

    def _checks(self, text):
        """Перевірка фактів і «загальновідомості» паралельно. Повертає (правдиве, очевидне)."""
        res = {}
        t1 = threading.Thread(target=lambda: res.__setitem__("true", self.verify(text)), daemon=True)
        t2 = threading.Thread(target=lambda: res.__setitem__("obvious", self.is_obvious(text)), daemon=True)
        t1.start(); t2.start(); t1.join(timeout=5); t2.join(timeout=5)
        return res.get("true", True), res.get("obvious", False)

    VERIFY_PROMPT = (
        "You are an expert fact checker. The claim may be in any language; judge only its factual content.\n"
        "Estimate the probability (0-100) that the claim is factually correct. Minor imprecision still counts as "
        "correct; invented options, fake entities, or clear contradictions are incorrect. Claims about what the "
        "speakers said, do or plan count as correct. Recent software tools, AI models, frameworks and CLI tools "
        "(2024–2026) may be real even if unfamiliar — do NOT score below 30 solely for unfamiliarity unless the "
        "claim is clearly fabricated or contradictory. Reply with the number only.")
    VERIFY_MIN = 30          # нижче — відкидаємо (на тестовому наборі 16/19: 1 хибне пройшло, 2 правдиві відкинуто)

    def verify(self, claim):
        """Самоперевірка факту (≈0.1 с): модель оцінює ймовірність правдивості 0–100."""
        try:
            ctx = self.topics[-1]["title"] if self.topics else ""
            user = f"Topic: {ctx}\nClaim: {claim.replace('**', '')}" if ctx and ctx != "—" else claim.replace("**", "")
            out = self.llm.complete([{"role": "system", "content": self.VERIFY_PROMPT},
                                     {"role": "user", "content": user}], max_tokens=4)
            m = re.search(r"\d+", out)
            return int(m.group()) >= self.VERIFY_MIN if m else True
        except Exception:
            return True                              # перевірка недоступна — не блокуємо

    OBVIOUS_PROMPT = (
        "Decide if a note shown to a listener is OBVIOUS (adds nothing) or USEFUL. Audience: {aud}.\n"
        "OBVIOUS = a bare definition of something everyone in this audience knows: a country's capital, the leader "
        "of a major country, a famous company/TV channel/region, an everyday word, or the most basic term of the "
        "field for specialists.\n"
        "USEFUL = a specific fact, number, date, name, mechanism, history, translation or nuance the listener may "
        "not know or may not remember exactly.\n"
        "Examples:\n"
        "Audience: football broadcast | 'Messi — Argentine footballer' -> OBVIOUS\n"
        "Audience: football broadcast | 'Offside rule changed in 2025: whole body must be beyond defender' -> USEFUL\n"
        "Audience: Python talk | 'Python — programming language' -> OBVIOUS\n"
        "Audience: Python talk | 'GIL prevents parallel CPU threads; 3.13 adds a free-threaded build' -> USEFUL\n"
        "Audience: Spanish film | 'Madrid — capital of Spain' -> OBVIOUS\n"
        "Audience: Spanish film | 'vale — Spanish for «okay»' -> USEFUL\n"
        "Audience: economics news | 'ECB — central bank of the euro area' -> OBVIOUS\n"
        "Audience: economics news | 'Bretton Woods (1944) — system of fixed exchange rates tied to the dollar' -> USEFUL\n"
        "Reply with exactly one word: OBVIOUS or USEFUL.")

    def audience(self):
        p = self.profile
        if not p:
            return "general listener"
        return f"{p.get('type', 'content')}: {p.get('title', '')}; listener reads notes in " \
               f"{self._lang(' '.join(x['text'] for x in self.transcript[-3:]))}"

    def is_obvious(self, claim):
        """Загальновідоме для цієї аудиторії (≈0.1 с). На тестовому наборі з прикладами — 12/14 стабільно."""
        aud = self.audience()
        try:
            out = self.llm.complete([
                {"role": "system", "content": self.OBVIOUS_PROMPT.format(aud=aud)},
                {"role": "user", "content": f"Audience: {aud} | '{claim.replace('**', '')}'"}], max_tokens=3)
            return out.strip().upper().startswith("OBVIOUS")
        except Exception:
            return False

    def _parallel(self, fn, items, default=False):
        res = [default] * len(items)

        def run(i):
            res[i] = fn(items[i])
        ts = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(items))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=5)
        return res

    def _verify_many(self, claims):
        res = [True] * len(claims)

        def run(i):
            res[i] = self.verify(claims[i])
        ts = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(claims))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=5)
        return res

    def brief(self, tp):
        """Розкрити тему одразу: ключові види/компоненти/варіанти. Паралельно до нотаток."""
        t0 = time.time()
        with self.lock:
            sid = self._session_id
            p = dict(self.profile)
            recent = " ".join(x["text"] for x in self.transcript[-6:])
        if p.get("type") in ("film", "cartoon", "series") and not p.get("work"):
            return                                   # невпізнаний сюжет — огляд був би вигадкою
        lang = self._lang(recent)
        ctx = self.settings.get("context", "").strip()
        fixed = self.settings.get("notes_language", "auto") != "auto"
        sys_p = (
            f"Write in {lang}.\n"
            + (f"Always write in {lang}, whatever the language of the conversation.\n" if fixed else "") +
            "You are an expert. Live content (a talk, lecture, news, film, cartoon or chat) just moved to a topic. "
            "Instantly expand it for the listener: for a knowledge subject list the main kinds / components / "
            "options / key facts; for a well-known film, series or cartoon list the main characters and who they "
            "are (only if the work is identified in the profile — never invent roles); for news give the key "
            "background facts.\n"
            "Output 3-6 lines, each exactly: - **term** — what it is, max 8 words\n"
            "Use only REAL, official names from documentation — never invent categories or terms; 3 true lines "
            "are better than 6 with a made-up one. No intro, no outro.\n"
            "If the topic is private or unknown to you (a specific company, a private person, the speakers' own "
            "experience, an unrecognised story), output exactly: NONE" + (f"\nUser context: {ctx}" if ctx else ""))
        user = (self.profile_block() + f"Topic: {tp['title']}\nWhat is being asked: {self.gist or '-'}\n"
                f"Recent conversation (noisy speech recognition): {recent[-1200:]}")
        try:
            out = self.llm.complete([{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
                                    max_tokens=220)
        except Exception as e:
            BUS.status("brief_failed", err=str(e)[:120])
            return
        items = []
        for raw in out.splitlines():
            raw = raw.strip()
            if raw[:2] in ("- ", "• ", "* "):
                raw = raw[2:].strip()
            elif not raw.startswith("**"):
                continue
            if len(raw) > 3 and not NO_INFO.search(raw) and not is_trivial(raw) and not SPECULATIVE.search(raw):
                items.append(raw)
        items = items[:6]
        # в огляді теми перевіряємо лише правдивість (НЕ is_obvious — інакше зникають базові типи теми)
        ok = self._verify_many(items)
        if len(items) - sum(ok):
            METRICS.inc("brief_items_rejected", len(items) - sum(ok))
        items = [i for i, good in zip(items, ok) if good]
        if items:
            with self.lock:
                if sid != self._session_id:
                    return
                tp["brief"] = items
            METRICS.inc("briefs")
            METRICS.observe("brief", time.time() - t0)
            BUS.emit("brief", topic=tp["id"], items=items, took=round(time.time() - t0, 2))

    # ------------------------------------------------------------- Q&A (catch questions & concise Markdown answers)
    def maybe_answer(self, force=False):
        if not self.settings.get("answer_questions"):
            return
        with self.lock:
            if not self.transcript:
                return
            tail = self._new_text_since(self._qa_cursor)[-260:]
            if not tail:
                if not force:
                    return
                tail = " ".join(x["text"] for x in self.transcript[-3:])[-260:]
            if len(tail.split()) < 3 or not QUESTION.search(tail):
                return
            delay = 0.0 if force else (self.QA_DEBOUNCE_Q if "?" in tail else self.QA_DEBOUNCE_KW)
            if self._qa_timer:
                self._qa_timer.cancel()
                self._qa_timer = None
            if delay > 0:
                t = threading.Timer(delay, self._schedule_qa)
                t.daemon = True
                self._qa_timer = t
                t.start()
                return
        self._schedule_qa()

    def _schedule_qa(self):
        with self.lock:
            self._qa_timer = None
            if not self.settings.get("answer_questions"):
                return
            if self._qa_busy:
                self._qa_pending = True
                return
            self._qa_busy = True
        threading.Thread(target=self._run_qa, daemon=True, name="qa").start()

    def _run_qa(self):
        try:
            self.answer_question()
        except Exception as e:
            METRICS.inc("llm_errors")
            log.warning("qa step failed: %s", e)
            BUS.status("llm_down", err=str(e)[:120])
        finally:
            with self.lock:
                self._qa_busy = False
                again, self._qa_pending = self._qa_pending, False
            if again:
                self.maybe_answer()

    def trigger_ask(self, question=None):
        """Ручний запит із UI: відповісти на введене питання або примусово на останні репліки."""
        def run():
            try:
                if question:
                    self.ask_question(question)
                else:
                    self.answer_question(force=True)
            except Exception as e:
                METRICS.inc("llm_errors")
                log.warning("manual ask failed: %s", e)
                BUS.status("llm_down", err=str(e)[:120])
        threading.Thread(target=run, daemon=True, name="qa-manual").start()

    def _qa_style_rules(self, lang):
        return (
            "Answer style — SIMPLE, LESS TEXT, GREAT MARKDOWN (readable in 2 seconds):\n"
            "- No intro, no filler, no repeating the question inside the answer.\n"
            "- Plain, simple wording. Aim for 15–55 words total (2–5 compact lines).\n"
            "- Pick the cleanest Markdown layout for the question:\n"
            "  * Comparisons ('X vs Y', 'difference'): a tiny 2–3 column Markdown table (<=4 rows, 2–5 words/cell).\n"
            "  * Concepts / 'what is': 1 short line with **bold** key term + 2–3 crisp `- **Point** — ...` bullets.\n"
            "  * Commands / syntax / how-to: short line + `inline code` or tiny fenced code block (1–3 lines).\n"
            "  * Simple factual / yes-no: 1–2 direct lines with **bold** takeaway.\n"
            "Output format (nothing else):\n"
            f"Q: <clean short question in {lang}, max 12 words>\n"
            "A:\n"
            f"<concise Markdown answer in {lang}>"
        )

    def build_qa_prompt(self, recent, new_from, idx0, force=False):
        lines = [f"{'NEW' if force or self._is_new_line(idx0 + i, x, new_from) else 'old'} [{x['spk']}] {x['text']}"
                 for i, x in enumerate(recent)]
        lang = self._lang(" ".join(x["text"] for x in recent[-4:]))
        ctx = self.settings.get("context", "").strip()
        domain = ", ".join(x for x in ((self.settings.get("glossary") or "")[:300],
                                        ", ".join(self.profile.get("terms", [])[:20])) if x)
        fixed = self.settings.get("notes_language", "auto") != "auto"
        asked = "\n".join(f"- {q}" for q in self.answered_questions[-10:]) or "(none)"
        sys_p = (
            f"Write both Q and A in {lang}.\n"
            + (f"Always translate both Q and A into {lang}; keep technical terms, commands and identifiers in "
               "their original form.\n" if fixed else "") +
            "You catch real questions asked in a live conversation (noisy speech-to-text: silently fix misheard "
            "terms, NEVER mention recognition or spelling errors) and give an instant, ultra-scannable answer.\n"
            "Only answer if there is a genuine, answerable knowledge/technical/practical question in the NEW lines "
            "(or finishing in NEW).\n"
            "Output exactly `NONE` (nothing else) if:\n"
            "- there is no real question;\n"
            "- it is small talk, a greeting, rhetorical, or character dialogue in a film/cartoon/series;\n"
            "- it is private/personal to the speaker (e.g. 'tell me about your last job');\n"
            + ("" if force else "- the same question is already in the 'Already answered' list;\n")
            + "- you are not confident in the answer.\n"
            + self.profile_block()
            + (f"Likely domain terms: {domain}\n" if domain else "")
            + (f"User context: {ctx}\n" if ctx else "")
            + self._qa_style_rules(lang)
        )
        user = (f"Already answered (do not repeat):\n{asked}\n\n"
                f"Conversation:\n" + "\n".join(lines) + f"\n\n(Write Q and A in {lang}, or NONE.)")
        return [{"role": "system", "content": sys_p}, {"role": "user", "content": user}]

    def _stream_llm(self, messages, max_tokens=420):
        """Потокові дельти з LLM (із сумісністю для тестів, які підміняють лише llm.complete)."""
        if "complete" in self.llm.__dict__ and "stream_chunks" not in self.llm.__dict__:
            out = self.llm.complete(messages, max_tokens=max_tokens)
            if out:
                yield out
            return
        yield from self.llm.stream_chunks(messages, max_tokens=max_tokens)

    @staticmethod
    def _clean_md_fence(ans):
        s = (ans or "").strip()
        if s.startswith("```") and s.endswith("```") and s.count("```") == 2:
            inner = s.splitlines()
            if len(inner) >= 3 and inner[0].lstrip().startswith("```") and inner[0].strip()[3:].lower() in ("", "md", "markdown"):
                return "\n".join(inner[1:-1]).strip()
        return s

    @staticmethod
    def _split_partial_qa(raw):
        """Якщо у потоці вже є Q: та A:, повертає (q, partial_answer); інакше (None, None)."""
        q, a_lines, in_a = "", [], False
        for line in raw.splitlines():
            if not in_a:
                mq = QA_Q.match(line)
                if mq and not q:
                    q = mq.group(1).strip().strip("*\"«»")
                    continue
                ma = QA_A.match(line)
                if ma:
                    in_a = True
                    if ma.group(1).strip():
                        a_lines.append(ma.group(1).rstrip())
                    continue
            else:
                a_lines.append(line.rstrip())
        if in_a and q and q.strip(" .*").upper() != "NONE":
            return q, "\n".join(a_lines).lstrip("\n")
        return None, None

    def _create_answer_note(self, q, a, sid, dedup=True, pinned=False):
        created_placeholder = False
        with self.lock:
            if sid != self._session_id:
                return None, None
            if dedup and any(same_question(q, old) for old in self.answered_questions[-12:]):
                METRICS.inc("qa_skipped", reason="duplicate")
                return None, None
            self.answered_questions = (self.answered_questions + [q])[-20:]
            self._note_id += 1
            note = {"id": self._note_id, "kind": "answer", "question": q, "text": a, "t": time.time()}
            if pinned:
                note["pinned"] = True
            if not self.topics:
                self.topics.append({"id": 1, "title": "—", "notes": [], "brief": [], "t": time.time()})
                created_placeholder = True
            topic = self.topics[-1]
            topic["notes"].append(note)
            topic_id = topic["id"]
            self._rev += 1
        if created_placeholder:
            BUS.emit("topic", id=1, title="—")
        return note, topic_id

    def _stream_answer(self, messages, sid, t0, speech_end, fallback_q=None, dedup=True,
                       max_tokens=420, status_done="qa_done", pinned=False):
        raw = ""
        note, topic_id = None, None
        last_emit = 0.0
        for chunk in self._stream_llm(messages, max_tokens=max_tokens):
            if sid != self._session_id:
                return None
            raw += chunk
            if note is None:
                stripped = raw.lstrip()
                if not fallback_q and stripped.upper().startswith("NONE") and len(stripped) >= 4:
                    METRICS.inc("qa_skipped", reason="none")
                    return None
                pq, pa = self._split_partial_qa(raw)
                if not pq and fallback_q and "\n" in raw and not QA_Q.match(raw.splitlines()[0]):
                    pq, pa = fallback_q, raw.strip()
                if pq:
                    note, topic_id = self._create_answer_note(pq, pa or "", sid, dedup=dedup, pinned=pinned)
                    if note is None:
                        return None
                    BUS.emit("note", topic=topic_id, streaming=True, **note)
                    last_emit = time.time()
            else:
                pq, pa = self._split_partial_qa(raw)
                ans = pa if pq else raw.strip()
                now = time.time()
                if now - last_emit >= 0.06:
                    BUS.emit("note_update", id=note["id"], question=pq or note["question"], text=ans, streaming=True)
                    last_emit = now
        q, a = parse_qa(raw)
        if not a and fallback_q:
            a = self._clean_md_fence(raw)
            q = fallback_q
        if not q or not a or a.strip(" .*").upper() == "NONE":
            if note is not None:
                self.dismiss_note(note["id"], record_rejected=False)
            else:
                METRICS.inc("qa_skipped", reason="none")
            return None
        if not fallback_q and ASR_COMMENT.search(a):
            if note is not None:
                self.dismiss_note(note["id"], record_rejected=False)
            METRICS.inc("qa_skipped", reason="asr_comment")
            return None
        llm_t = time.time() - t0
        e2e = time.time() - speech_end
        if note is None:
            note, topic_id = self._create_answer_note(q, a, sid, dedup=dedup, pinned=pinned)
            if note is None:
                return None
            BUS.emit("note", topic=topic_id, took=round(llm_t, 2), e2e=round(e2e, 1), streaming=False, **note)
        else:
            with self.lock:
                if sid != self._session_id:
                    return None
                note["question"] = q
                note["text"] = a
                self._rev += 1
            BUS.emit("note_update", id=note["id"], question=q, text=a,
                     took=round(llm_t, 2), e2e=round(e2e, 1), streaming=False)
        METRICS.inc("notes", kind="answer")
        METRICS.note()
        METRICS.observe("llm_qa", llm_t)
        METRICS.observe("e2e_qa", e2e)
        BUS.status(status_done, took=round(llm_t, 1))
        return note

    def answer_question(self, force=False):
        t0 = time.time()
        with self.lock:
            if not self.transcript:
                return None
            sid = self._session_id
            cutoff = t0 - self.WINDOW_SEC
            recent = [dict(x) for x in self.transcript if x["t"] >= cutoff][-10:] or [dict(x) for x in self.transcript[-6:]]
            new_from = self._qa_cursor
            self._qa_cursor = self._cursor_now()
            idx0 = len(self.transcript) - len(recent)
            speech_end = max(x["end"] for x in recent)
        BUS.status("qa_thinking")
        METRICS.inc("llm_requests")
        return self._stream_answer(
            self.build_qa_prompt(recent, new_from, idx0, force=force),
            sid, t0, speech_end, dedup=not force, max_tokens=420,
        )

    def ask_question(self, question):
        """Пряма відповідь на питання, введене користувачем вручну у веб-інтерфейсі."""
        question = (question or "").strip()
        if not question:
            return None
        t0 = time.time()
        with self.lock:
            sid = self._session_id
            recent = [dict(x) for x in self.transcript[-8:]]
        lang = self._lang(question)
        ctx = self.settings.get("context", "").strip()
        domain = ", ".join(x for x in ((self.settings.get("glossary") or "")[:300],
                                        ", ".join(self.profile.get("terms", [])[:20])) if x)
        fixed = self.settings.get("notes_language", "auto") != "auto"
        sys_p = (
            f"Write both Q and A in {lang}.\n"
            + (f"Always translate both Q and A into {lang}; keep technical terms, commands and identifiers in "
               "their original form.\n" if fixed else "")
            + "Answer the user's question directly, using the recent conversation only as background context.\n"
            + self.profile_block()
            + (f"Likely domain terms: {domain}\n" if domain else "")
            + (f"User context: {ctx}\n" if ctx else "")
            + self._qa_style_rules(lang)
        )
        conv = "\n".join(f"[{x['spk']}] {x['text']}" for x in recent)
        user = (f"Recent conversation:\n{conv}\n\n" if conv else "") + f"Question: {question}"
        BUS.status("qa_thinking")
        METRICS.inc("llm_requests")
        return self._stream_answer(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
            sid, t0, t0, fallback_q=question, dedup=False, max_tokens=420,
        )

    def trigger_summary(self):
        """Згенерувати структурований Markdown-підсумок поточної сесії."""
        def run():
            try:
                self.summarize_session()
            except Exception as e:
                METRICS.inc("llm_errors")
                log.warning("summary failed: %s", e)
                BUS.status("llm_down", err=str(e)[:120])
        threading.Thread(target=run, daemon=True, name="summary").start()

    def summarize_session(self):
        t0 = time.time()
        with self.lock:
            if not self.transcript and not self.topics:
                return None
            sid = self._session_id
            tr = "\n".join(f"[{x['spk']}] {x['text']}" for x in self.transcript[-80:])[-6000:]
            notes = "\n".join(
                f"- [{tp['title']}] " + (f"Q: {n['question']}" if n.get("kind") == "answer" else n["text"])
                for tp in self.topics for n in tp.get("notes", [])[-12:]
            )[-2000:]
            sample = " ".join(x["text"] for x in self.transcript[-5:])
        lang = self._lang(sample or "summary")
        fixed = self.settings.get("notes_language", "auto") != "auto"
        sys_p = (
            f"Write in {lang}.\n"
            + (f"Always write in {lang}, keeping technical terms and commands in original form.\n" if fixed else "")
            + "Create a crisp, high-signal Markdown summary of this conversation.\n"
            "Keep it short, scannable, and well-structured (<= 140 words total):\n"
            "- **Main topics** (2–4 short bullets)\n"
            "- **Key takeaways / answers** (3–5 short bullets)\n"
            "- **Action items / open questions** (only if any exist)\n"
            "Output format (nothing else):\n"
            f"Q: <short title for Session Summary in {lang}, max 6 words>\n"
            "A:\n"
            f"<concise Markdown summary in {lang}>"
        )
        user = (self.profile_block()
                + (f"Notes so far:\n{notes}\n\n" if notes else "")
                + f"Transcript:\n{tr}")
        BUS.status("summary_thinking")
        METRICS.inc("llm_requests")
        return self._stream_answer(
            [{"role": "system", "content": sys_p}, {"role": "user", "content": user}],
            sid, t0, t0, fallback_q="Summary", dedup=False, max_tokens=550,
            status_done="summary_done", pinned=True,
        )

    def dismiss_note(self, note_id, record_rejected=True):
        """Видаляє нотатку з треду та (опціонально) заносить у rejected, щоб модель не повторювала."""
        try:
            nid = int(note_id)
        except (TypeError, ValueError):
            return False
        removed = None
        with self.lock:
            for tp in self.topics:
                for i, n in enumerate(tp["notes"]):
                    if n.get("id") == nid:
                        removed = tp["notes"].pop(i)
                        break
                if removed:
                    break
            if not removed:
                return False
            if record_rejected and removed.get("kind") != "answer" and removed.get("text"):
                self.rejected = (self.rejected + [{"text": removed["text"]}])[-10:]
            self._rev += 1
        BUS.emit("retract", id=nid)
        return True

    def pin_note(self, note_id, pinned=None):
        """Закріплює або відкріплює нотатку/відповідь."""
        try:
            nid = int(note_id)
        except (TypeError, ValueError):
            return False
        new_val = None
        with self.lock:
            for tp in self.topics:
                for n in tp["notes"]:
                    if n.get("id") == nid:
                        new_val = (not bool(n.get("pinned"))) if pinned is None else bool(pinned)
                        n["pinned"] = new_val
                        self._rev += 1
                        break
                if new_val is not None:
                    break
        if new_val is None:
            return False
        BUS.emit("note_pin", id=nid, pinned=new_val)
        return True

    # ------------------------------------------------------------- session
    def maybe_autosave(self):
        with self.lock:
            if not self.transcript or self._rev == self._autosave_rev:
                return
            if time.time() - self._autosave_t < self.AUTOSAVE_EVERY:
                return
        self.save_session(final=False)

    def save_session(self, final=True):
        with self.lock:
            if not self.transcript and not any(tp.get("notes") for tp in self.topics):
                return None
            if self._session_file is None:
                self._session_file = time.strftime("%Y-%m-%d_%H-%M-%S") + ".md"
            name = self._session_file
            profile = dict(self.profile) if self.profile else None
            topics = [{**t, "brief": list(t.get("brief", [])), "notes": [dict(n) for n in t.get("notes", [])]}
                      for t in self.topics]
            transcript = [dict(x) for x in self.transcript]
            rev = self._rev
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        out = [f"# Live Thread — {name[:-3]}"]
        if profile:
            out.append(f"**{profile['type']}** — {profile['title']} ({profile['participants']})")
        out.append("## Topics & notes")
        for tp in topics:
            out.append(f"### {tp['title']}")
            out += [f"- {b}" for b in tp.get("brief", [])]
            for n in tp["notes"]:
                if n.get("kind") == "answer":
                    q = n.get("question") or "Answer"
                    out.append(f"#### Q: {q}\n\n{n['text']}")
                else:
                    out.append(f"- **{n['kind']}**: {n['text']}")
        out += ["", "## Transcript"] + [f"**[{x['spk']}]** {x['text']}" for x in transcript]
        path = config.SESSIONS_DIR / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n\n".join(out), encoding="utf-8")
        tmp.replace(path)
        with self.lock:
            self._autosave_t = time.time()
            self._autosave_rev = rev
        if final:
            BUS.status("session_saved", name=name)
        return path

    def reset(self):
        self.save_session(final=True)
        with self.lock:
            if self._qa_timer:
                self._qa_timer.cancel()
                self._qa_timer = None
            self._session_id += 1
            self._session_file = None
            self._autosave_t = 0.0
            self._autosave_rev = 0
            self._rev = 0
            self._newest_out = self._seq
            self.transcript, self.topics, self.gist, self.profile = [], [], "", {}
            self.rejected, self.obvious_rejected, self.answered_questions = [], [], []
            self._profile_words = self._profile_topics = 0
            self._words_since = 0
            self._note_cursor = (-1, 0)
            self._qa_cursor = (-1, 0)
            self._qa_pending = False
        if self.transcriber and self.transcriber._diarizer:
            self.transcriber._diarizer.reset()
        BUS.emit("reset")
