"""Tests for live_thread.engine helpers and note/Q&A pipeline."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from live_thread import config, engine


class EngineHelperTests(unittest.TestCase):
    def test_detect_language(self):
        self.assertEqual(engine.detect_language("Hello world", "Polish"), "Polish")
        self.assertEqual(engine.detect_language("How does Kubernetes StatefulSet work?"), "English")
        self.assertEqual(engine.detect_language("Як працює Kubernetes та які є відмінності?"), "Ukrainian")
        self.assertEqual(engine.detect_language("Как работает сборщик мусора в этом языке?"), "Russian")
        self.assertEqual(engine.detect_language("Jak działa ten system?", "auto", "pl"), "Polish")

    def test_same_topic_and_same_question(self):
        self.assertTrue(engine.same_topic("Kubernetes StatefulSet", "Kubernetes StatefulSets"))
        self.assertFalse(engine.same_topic("Kubernetes", "PostgreSQL VACUUM"))
        self.assertTrue(engine.same_question("What is the difference between TCP and UDP?", "Compare TCP vs UDP"))
        self.assertFalse(engine.same_question("What is TCP?", "How does DNS work?"))

    def test_parse_line(self):
        self.assertEqual(engine.parse_line("**NOTE:** add | **WAL** ensures crash recovery"), ("NOTE", "add | **WAL** ensures crash recovery"))
        self.assertEqual(engine.parse_line("GIST: Comparing Kafka and RabbitMQ"), ("GIST", "Comparing Kafka and RabbitMQ"))
        self.assertEqual(engine.parse_line("TOPIC: Message brokers"), ("TOPIC", "Message brokers"))
        self.assertEqual(engine.parse_line("plain text"), (None, "plain text"))

    def test_clean_md_fence_and_parse_qa(self):
        self.assertEqual(engine.clean_md_fence("```markdown\n**Yes** — it is.\n```"), "**Yes** — it is.")
        self.assertEqual(engine.clean_md_fence("```bash\nls -la\n```"), "```bash\nls -la\n```")
        q, a = engine.parse_qa("Q: What is `chmod 600`?\nA:\n```markdown\nOwner **read/write** only.\n```")
        self.assertEqual(q, "What is `chmod 600`?")
        self.assertEqual(a, "Owner **read/write** only.")
        self.assertEqual(engine.parse_qa("NONE"), (None, None))

    def test_duplicate_and_noise_filters(self):
        existing = [{"kind": "add", "text": "**StatefulSet** gives pods stable network identities"}]
        self.assertTrue(engine.is_duplicate("**StatefulSet** provides stable network identities to pods", existing))
        self.assertFalse(engine.is_duplicate("**DaemonSet** runs one pod per node", existing))
        self.assertTrue(engine.is_trivial("**Chase** — персонаж"))
        self.assertFalse(engine.is_trivial("**Chase** — поліцейський пес з Щенячого патруля"))
        self.assertTrue(engine.is_empty_confirm("**Docker** дійсно стандарт", "docker"))
        self.assertFalse(engine.is_empty_confirm("**Docker** uses **cgroups** and **namespaces**", "docker"))


class EnginePipelineTests(unittest.TestCase):
    def setUp(self):
        self.eng = engine.Engine(dict(config.DEFAULTS, answer_questions=False))
        self.eng.profile = {"type": "tech_talk", "title": "Linux", "participants": "", "languages": "en", "terms": [], "focus": ""}

    def test_handle_line_gist_topic_note_pin_dismiss(self):
        self.eng._checks = mock.MagicMock(return_value=(True, False))
        self.eng.brief = mock.MagicMock()
        st = {"notes": [], "topic": False, "t0": time.time(), "sid": self.eng._session_id, "recent": "linux", "speech_end": time.time()}

        self.eng.handle_line("GIST: Explaining Linux inodes", st, seq=1)
        self.eng.handle_line("TOPIC: Linux inodes", st, seq=1)
        self.eng.handle_line("NOTE: add | **inode** stores metadata and block pointers, not the filename", st, seq=1)

        self.assertEqual(self.eng.gist, "Explaining Linux inodes")
        self.assertEqual(len(self.eng.topics), 1)
        self.assertEqual(self.eng.topics[0]["title"], "Linux inodes")
        self.assertEqual(len(self.eng.topics[0]["notes"]), 1)

        note_id = self.eng.topics[0]["notes"][0]["id"]
        self.assertTrue(self.eng.pin_note(note_id, True))
        self.assertTrue(self.eng.topics[0]["notes"][0]["pinned"])
        self.assertTrue(self.eng.dismiss_note(note_id))
        self.assertEqual(self.eng.topics[0]["notes"], [])
        self.assertEqual(len(self.eng.rejected), 1)

    def test_ask_question_and_save_session(self):
        self.eng.llm.complete = mock.MagicMock(return_value="Q: What is an inode?\nA:\n**inode** stores file metadata.")
        note = self.eng.ask_question("What is an inode?")
        self.assertIsNotNone(note)
        self.assertEqual(note["kind"], "answer")
        self.assertEqual(note["question"], "What is an inode?")

        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp) / "sessions"
            with mock.patch.object(config, "SESSIONS_DIR", sessions_dir):
                path = self.eng.save_session(final=True)
                self.assertTrue(path.is_file())
                content = path.read_text(encoding="utf-8")
                self.assertIn("What is an inode?", content)
                self.assertIn("**inode** stores file metadata.", content)

        self.eng.save_session = mock.MagicMock()
        self.eng.reset()
        self.assertEqual(self.eng.topics, [])
        self.assertEqual(self.eng.transcript, [])


if __name__ == "__main__":
    unittest.main()
