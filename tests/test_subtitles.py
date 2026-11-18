"""Unit tests for pipeline.subtitles (SRT cue grouping and timestamps)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline import subtitles  # noqa: E402


def W(s, e, t):
    return (s, e, t)


class SrtTime(unittest.TestCase):
    def test_format(self):
        self.assertEqual(subtitles._srt_time(3661.5), "01:01:01,500")

    def test_negative_clamped(self):
        self.assertEqual(subtitles._srt_time(-2), "00:00:00,000")

    def test_rounding_carries(self):
        self.assertEqual(subtitles._srt_time(0.9996), "00:00:01,000")


class GroupWords(unittest.TestCase):
    def test_breaks_on_sentence_end(self):
        ws = [W(0, .3, "Hi"), W(.3, .6, "there."), W(.6, .9, "Next"), W(.9, 1.2, "one")]
        g = subtitles.group_words(ws)
        self.assertEqual([[x[2] for x in c] for c in g], [["Hi", "there."], ["Next", "one"]])

    def test_breaks_on_long_pause(self):
        ws = [W(0, .3, "a"), W(.3, .6, "b"), W(3.0, 3.3, "c")]
        self.assertEqual(len(subtitles.group_words(ws)), 2)

    def test_respects_max_words(self):
        ws = [W(i * .2, i * .2 + .2, "w%d" % i) for i in range(15)]
        self.assertTrue(all(len(c) <= 7 for c in subtitles.group_words(ws)))

    def test_respects_max_duration(self):
        ws = [W(i, i + .9, "w%d" % i) for i in range(8)]
        for c in subtitles.group_words(ws, max_words=99):
            self.assertLessEqual(c[-1][1] - c[0][0], 3.5 + 1.0)

    def test_empty(self):
        self.assertEqual(subtitles.group_words([]), [])

    def test_no_words_lost(self):
        ws = [W(i * .3, i * .3 + .25, "w%d." % i if i % 4 == 3 else "w%d" % i) for i in range(20)]
        flat = [x for c in subtitles.group_words(ws) for x in c]
        self.assertEqual(flat, ws)


class WriteSrt(unittest.TestCase):
    def test_cues_do_not_overlap_and_are_numbered(self):
        words = [{"s": 10 + i * .3, "e": 10 + i * .3 + .5, "w": ("x%d." % i if i % 3 == 2 else "x%d" % i)}
                 for i in range(9)]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "transcript.json"), "w") as f:
                json.dump({"segments": [{"words": words}]}, f)
            p = subtitles.write_srt(d, {"start": 10, "end": 14, "slug": "t"})
            blocks = open(p, encoding="utf-8").read().strip().split("\n\n")
        self.assertEqual(len(blocks), 3)
        prev_end = -1
        for n, b in enumerate(blocks, 1):
            lines = b.split("\n")
            self.assertEqual(lines[0], str(n))
            a, _, z = lines[1].partition(" --> ")
            self.assertLessEqual(prev_end, subtitles_to_s(a) + 1e-6)
            prev_end = subtitles_to_s(z)
            self.assertGreater(prev_end, subtitles_to_s(a))


def subtitles_to_s(t):
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


if __name__ == "__main__":
    unittest.main()
