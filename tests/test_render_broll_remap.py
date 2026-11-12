"""Regression test: b-roll cutaway windows must remap through the silence
keep-map exactly like captions and the reframe track do.

Why this matters: when trim_silence is on, render_clip()'s [vbase] layer
reads the trimmed/concatenated [vtrim] timeline, not the original clip's
timeline -- but broll_spec windows are given in original clip-relative
seconds. Captions (_remap_words) and the reframe track already remap
through silence.make_remap() so they land on the right moment after a
cut; b-roll previously did not, so combining "add b-roll" with "trim
silence" on the same clip would silently overlay the cutaway over the
wrong footage (or over nothing, if the window now falls past the
shortened trimmed duration).

render.py now shares one `_remap_span()` helper between captions and
b-roll, so this test exercises that helper directly against a real
silence.make_remap() output -- no ffmpeg, no network, no video file
needed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import render, silence  # noqa: E402


class TestRemapSpanAgainstRealKeepMap(unittest.TestCase):
    """A 60s clip with the middle 10s (20-30) cut by silence detection:
    keep = [(0, 20), (30, 60)] -> a 50s trimmed timeline."""

    def setUp(self):
        self.keep = [(0.0, 20.0), (30.0, 60.0)]
        self.remap = silence.make_remap(self.keep)

    def test_window_entirely_before_the_cut_is_unchanged(self):
        # Nothing to remap -- the cut hasn't happened yet at this point.
        span = render._remap_span(5.0, 10.0, self.remap)
        self.assertEqual(span, (5.0, 10.0))

    def test_window_entirely_after_the_cut_shifts_back_by_the_gap_length(self):
        # clip-relative 40-45 is 10s into the second kept segment (which
        # starts at clip-relative 30, trimmed-timeline 20) -> trimmed 30-35.
        # The unfixed code would have fired this overlay at raw t=40-45 on
        # the 50s-long trimmed output, landing over clip-relative 50-55
        # instead of the intended 40-45.
        span = render._remap_span(40.0, 45.0, self.remap)
        self.assertEqual(span, (30.0, 35.0))

    def test_window_spanning_the_cut_boundary_collapses_across_it(self):
        # clip-relative 15-35 straddles the 10s gap; the trimmed version
        # should be exactly 10s shorter (15 -> 25, i.e. 25 - 15 == 35 - 15 - 10).
        span = render._remap_span(15.0, 35.0, self.remap)
        self.assertEqual(span, (15.0, 25.0))

    def test_window_entirely_inside_the_cut_gap_is_dropped(self):
        span = render._remap_span(22.0, 27.0, self.remap)
        self.assertIsNone(span)


class TestCaptionsAndBrollShareTheSameRemapBehavior(unittest.TestCase):
    """The regression this guards against: captions and b-roll silently
    disagreeing about where a cut moved things to."""

    def test_identical_span_remaps_identically_for_both_use_sites(self):
        keep = [(0.0, 12.0), (18.0, 50.0)]
        remap = silence.make_remap(keep)

        # _remap_words wraps _remap_span per-word; call both paths on the
        # same underlying span and confirm they agree.
        word_span = render._remap_words([(20.0, 24.0, "hello")], keep)
        direct_span = render._remap_span(20.0, 24.0, remap)

        self.assertEqual((word_span[0][0], word_span[0][1]), direct_span)


if __name__ == "__main__":
    unittest.main()
