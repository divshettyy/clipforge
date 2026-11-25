"""Speech-delivery stats and the bounded score adjustment in highlight.py."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import highlight as h


class StatsTests(unittest.TestCase):
    def test_full_coverage(self):
        segs = [{"start": 0, "end": 10, "text": " ".join(["w"] * 25)}]
        st = h._speech_stats(0, 10, segs)
        self.assertEqual(st, {"wps": 2.5, "dead_air": 0.0, "lead_in": 0.0})

    def test_partial_overlap_is_prorated(self):
        segs = [{"start": 0, "end": 10, "text": " ".join(["w"] * 20)}]
        st = h._speech_stats(5, 10, segs)           # half the segment -> 10 words / 5 s
        self.assertEqual(st["wps"], 2.0)

    def test_dead_air(self):
        segs = [{"start": 0, "end": 4, "text": "a b c d"}]
        self.assertEqual(h._speech_stats(0, 10, segs)["dead_air"], 0.6)

    def test_no_segments(self):
        self.assertEqual(h._speech_stats(0, 10, []), {"wps": 0.0, "dead_air": 1.0, "lead_in": 10})


class CleanEndTests(unittest.TestCase):
    segs = [{"start": 0, "end": 5, "text": "First thought."},
            {"start": 5, "end": 12, "text": "and then we kept going"}]

    def test_sentence_end(self):
        self.assertTrue(h._ends_clean(5.0, self.segs))

    def test_mid_sentence(self):
        self.assertFalse(h._ends_clean(12.0, self.segs))

    def test_no_punctuation_is_no_signal(self):
        self.assertTrue(h._ends_clean(3.0, [{"start": 0, "end": 9, "text": "no marks here"}]))

    def test_penalty(self):
        base = {"wps": 3.0, "dead_air": 0.0}
        self.assertEqual(h._adjust_score(70, dict(base, clean_end=False)), 70)
        self.assertEqual(h._adjust_score(70, dict(base, clean_end=True)), 73)


class AdjustTests(unittest.TestCase):
    def test_brisk_bonus(self):
        self.assertEqual(h._adjust_score(70, {"wps": 3.0, "dead_air": 0.0}), 73)

    def test_dead_air_and_slow_penalty(self):
        self.assertEqual(h._adjust_score(70, {"wps": 0.5, "dead_air": 0.5}), 55)

    def test_bounds(self):
        self.assertEqual(h._adjust_score(99, {"wps": 3.0, "dead_air": 0.0}), 100)
        self.assertEqual(h._adjust_score(3, {"wps": 0.0, "dead_air": 1.0, "lead_in": 10}), 0)


if __name__ == "__main__":
    unittest.main()
