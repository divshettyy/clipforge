"""Unit tests for pipeline.silence pure logic (no ffmpeg needed)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline import silence  # noqa: E402


class _FakeProc:
    def __init__(self, stderr):
        self.stderr = stderr.encode()


class DetectSilences(unittest.TestCase):
    def run_detect(self, text, dur=10.0):
        with mock.patch.object(silence.subprocess, "run", return_value=_FakeProc(text)):
            return silence.detect_silences("x.mp4", 0, dur)

    def test_parses_pairs(self):
        t = "silence_start: 1.5\nsilence_end: 2.5 | silence_duration: 1\n"
        self.assertEqual(self.run_detect(t), [(1.5, 2.5)])

    def test_trailing_silence_clamped_to_duration(self):
        self.assertEqual(self.run_detect("silence_start: 8.0\n"), [(8.0, 10.0)])

    def test_negative_start_clamped_and_end_clamped(self):
        t = "silence_start: -0.02\nsilence_end: 12.0\n"
        self.assertEqual(self.run_detect(t), [(0.0, 10.0)])

    def test_no_silence(self):
        self.assertEqual(self.run_detect("nothing here"), [])


class KeepSegments(unittest.TestCase):
    def keep(self, sils, dur=10.0, **kw):
        with mock.patch.object(silence, "detect_silences", return_value=sils):
            return silence.keep_segments("x", 0, dur, **kw)

    def test_no_silences_keeps_all(self):
        self.assertEqual(self.keep([]), [(0.0, 10.0)])

    def test_middle_gap_is_cut_with_padding(self):
        k = self.keep([(4.0, 6.0)])
        self.assertEqual(len(k), 2)
        self.assertAlmostEqual(k[0][1], 4.06)
        self.assertAlmostEqual(k[1][0], 5.94)

    def test_all_silent_falls_back_to_full_clip(self):
        self.assertEqual(self.keep([(0.0, 10.0)]), [(0.0, 10.0)])

    def test_segments_sorted_and_non_overlapping(self):
        k = self.keep([(1.0, 2.0), (5.0, 7.0)])
        for (a, b), (c, d) in zip(k, k[1:]):
            self.assertLessEqual(b, c)
            self.assertLess(a, b)


class RemapAndFilters(unittest.TestCase):
    keep = [(0.0, 2.0), (4.0, 6.0)]

    def test_remap(self):
        r = silence.make_remap(self.keep)
        self.assertAlmostEqual(r(1.0), 1.0)
        self.assertAlmostEqual(r(5.0), 3.0)   # 2s kept before + 1s in
        self.assertIsNone(r(3.0))             # inside a cut
        self.assertIsNone(r(7.0))             # trailing

    def test_is_trimmed(self):
        self.assertTrue(silence.is_trimmed(self.keep, 10.0))
        self.assertFalse(silence.is_trimmed([(0.0, 10.0)], 10.0))

    def test_filters_have_matching_labels(self):
        v, a = silence.build_trim_filters(self.keep)
        self.assertIn("concat=n=2:v=1:a=0[vtrim]", v)
        self.assertIn("concat=n=2:v=0:a=1[atrim]", a)
        self.assertIn("[tv1]", v)
        self.assertIn("[ta1]", a)


if __name__ == "__main__":
    unittest.main()
