"""Tests for live_thread.stt and live_thread.diarize pure helpers."""
import unittest

from live_thread.diarize import _smooth, split_words
from live_thread.stt import clean_hallucination


class SttDiarizeTests(unittest.TestCase):
    def test_clean_hallucination_strips_and_drops_loops(self):
        self.assertEqual(clean_hallucination("Hello world"), "Hello world")
        self.assertEqual(clean_hallucination("word, word, word, word, word, word, word, word, word"), "")
        self.assertEqual(
            clean_hallucination("Actual speech before and after test, test, test, test, test in the sentence"),
            "Actual speech before and after test in the sentence",
        )

    def test_smooth_removes_single_flip(self):
        labels = [(0.0, 1.5, "S1"), (0.75, 2.25, "S2"), (1.5, 3.0, "S1")]
        self.assertEqual([x[2] for x in _smooth(labels)], ["S1", "S1", "S1"])

    def test_split_words_groups_and_merges_short_fragments(self):
        words = [
            (0.0, 0.3, "Hello"),
            (0.3, 0.6, " there"),
            (0.6, 0.9, " friend"),
            (2.0, 2.3, " Nice"),
            (2.3, 2.6, " to"),
            (2.6, 2.9, " meet"),
            (2.9, 3.2, " you"),
        ]
        labels = [(0.0, 1.5, "S1"), (1.5, 3.5, "S2")]
        turns = split_words(words, labels)
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0][0], "S1")
        self.assertEqual(turns[0][1], "Hello there friend")
        self.assertEqual(turns[1][0], "S2")
        self.assertEqual(turns[1][1], "Nice to meet you")

    def test_split_words_empty_labels_uses_default(self):
        turns = split_words([(0.0, 0.5, "Hi")], [])
        self.assertEqual(turns, [("S1", "Hi", 0.0, 0.5)])


if __name__ == "__main__":
    unittest.main()
