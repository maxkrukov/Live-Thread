"""Tests for live_thread.metrics."""
import unittest

from live_thread.metrics import Metrics


class MetricsTests(unittest.TestCase):
    def test_snapshot_and_prometheus_export(self):
        m = Metrics()
        m.inc("notes", kind="add")
        m.inc("notes_rejected", 2, reason='quote"test')
        m.observe("stt_lag", 1.0)
        m.observe("stt_lag", 2.0)
        m.observe("stt_lag", 3.0)
        m.gauge("stt_queue", 1)
        m.note()

        snap = m.snapshot()
        self.assertEqual(snap["counters"]["notes"]["kind=add"], 1)
        self.assertEqual(snap["timings"]["stt_lag"]["count"], 3)
        self.assertEqual(snap["timings"]["stt_lag"]["p50"], 2.0)
        self.assertEqual(snap["gauges"]["stt_queue"], 1)

        prom = m.prometheus()
        self.assertIn('live_thread_notes_total{kind="add"} 1', prom)
        self.assertIn(r'live_thread_notes_rejected_total{reason="quote\"test"} 2', prom)
        self.assertIn('live_thread_stt_lag_seconds{quantile="0.5"} 2.000', prom)
        self.assertIn("live_thread_stt_queue 1.0", prom)


if __name__ == "__main__":
    unittest.main()
