import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import validation as v  # noqa: E402


class Url(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(v.clean_url("  https://youtu.be/abc123xyz "), "https://youtu.be/abc123xyz")

    def test_rejects(self):
        for bad in (None, 5, "", "  ", "file:///etc/passwd", "ftp://x/y", "/tmp/a.mp4",
                    "http://", "https://a.com/" + "x" * 3000):
            with self.assertRaises(ValueError, msg=repr(bad)):
                v.clean_url(bad)


class Clips(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(v.clamp_clips(None), 6)
        self.assertEqual(v.clamp_clips("abc"), 6)
        self.assertEqual(v.clamp_clips(0), 6)
        self.assertEqual(v.clamp_clips(-4), 1)
        self.assertEqual(v.clamp_clips(100000), 20)
        self.assertEqual(v.clamp_clips("8"), 8)


class Guidance(unittest.TestCase):
    def test_guidance(self):
        self.assertEqual(v.clean_guidance(None), "")
        self.assertEqual(v.clean_guidance(" hi "), "hi")
        self.assertEqual(len(v.clean_guidance("x" * 5000)), 2000)


if __name__ == "__main__":
    unittest.main()
