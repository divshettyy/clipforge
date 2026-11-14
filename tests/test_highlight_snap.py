"""Boundary snapping, max-length cap and overlap de-duplication in highlight.py."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import highlight as h

SEGS = [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 9.5},
        {"start": 9.5, "end": 15.0}, {"start": 40.0, "end": 47.0}]


class SnapTests(unittest.TestCase):
    def test_snaps_to_nearby_edges(self):
        self.assertEqual(h._snap_boundaries(4.6, 14.2, SEGS), (4.0, 15.0))

    def test_leaves_far_boundaries(self):
        self.assertEqual(h._snap_boundaries(20.0, 30.0, SEGS), (20.0, 30.0))

    def test_never_inverts(self):
        s, e = h._snap_boundaries(9.0, 9.7, SEGS)
        self.assertGreater(e, s)

    def test_empty_segments(self):
        self.assertEqual(h._snap_boundaries(1.0, 12.0, []), (1.0, 12.0))


class OverlapTests(unittest.TestCase):
    def test_drops_lower_ranked_overlap(self):
        clips = [{"start": 10, "end": 40, "score": 90},
                 {"start": 12, "end": 38, "score": 70},
                 {"start": 60, "end": 80, "score": 60}]
        out = h._drop_overlaps(clips)
        self.assertEqual([c["score"] for c in out], [90, 60])

    def test_keeps_adjacent(self):
        clips = [{"start": 0, "end": 20, "score": 80}, {"start": 20, "end": 40, "score": 70}]
        self.assertEqual(len(h._drop_overlaps(clips)), 2)


if __name__ == "__main__":
    unittest.main()
