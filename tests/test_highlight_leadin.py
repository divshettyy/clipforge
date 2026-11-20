import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import highlight as h


class TestLeadIn(unittest.TestCase):
    def test_lead_in_measured(self):
        segs = [{"start": 12.0, "end": 20.0, "text": "one two three four five six"}]
        st = h._speech_stats(10.0, 20.0, segs)
        self.assertAlmostEqual(st["lead_in"], 2.0)

    def test_immediate_speech_has_zero_lead_in(self):
        segs = [{"start": 9.0, "end": 20.0, "text": "a b c d e f g h"}]
        self.assertEqual(h._speech_stats(10.0, 20.0, segs)["lead_in"], 0.0)

    def test_no_speech_lead_in_is_full_window(self):
        self.assertEqual(h._speech_stats(0.0, 10.0, [])["lead_in"], 10.0)

    def test_long_lead_in_costs_points(self):
        base = {"dead_air": 0.0, "wps": 3.0}
        self.assertEqual(h._adjust_score(80, dict(base, lead_in=0.2)) - h._adjust_score(80, dict(base, lead_in=2.5)), 4)

    def test_adjust_score_without_lead_in_key(self):
        self.assertEqual(h._adjust_score(80, {"dead_air": 0.0, "wps": 3.0}), 83)


if __name__ == "__main__":
    unittest.main()
