"""Regression test: pick_highlights() must clamp model-returned clip
start/end times to the transcript's real duration.

Why this matters: the highlight picker reads mm:ss timestamps off a
compacted transcript and asks an LLM to return clip boundaries. The model
can round, misread, or (rarely) hallucinate a boundary past the actual
video length, or a small negative start. Left unclamped, that bad pick
reaches render_clip()'s `ffmpeg -ss start -t duration` on a source shorter
than `start`, which produces an empty/zero-frame clip file that "succeeds"
with nothing in the job status to explain why it's broken. The fix clamps
against the transcript's own duration before a pick is ever written to
clips.json, and drops anything that clamps down to too little to be a
real clip.

No network, no ffmpeg, no Claude CLI: llm.complete() is monkeypatched, so
this exercises pure data validation.
"""
import json
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fake_llm_module(reply_json):
    """Install a stand-in `llm` module so pick_highlights()'s lazy
    `import llm` resolves to this instead of shelling out to a real
    backend."""
    mod = types.ModuleType("llm")
    mod.complete = lambda prompt, system="", max_tokens=4096: json.dumps(reply_json)
    sys.modules["llm"] = mod


def _write_transcript(job_dir, duration):
    with open(os.path.join(job_dir, "transcript.json"), "w") as f:
        json.dump(
            {
                "language": "en",
                "duration": duration,
                "segments": [
                    {"start": 0.0, "text": "Intro line one."},
                    {"start": 5.0, "text": "Something happens here."},
                    {"start": max(0.0, duration - 5.0), "text": "The ending."},
                ],
            },
            f,
        )


class TestHighlightClamping(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.job_dir = self._tmp.name
        self._saved_llm = sys.modules.get("llm")

    def tearDown(self):
        self._tmp.cleanup()
        if self._saved_llm is not None:
            sys.modules["llm"] = self._saved_llm
        else:
            sys.modules.pop("llm", None)

    def test_clip_past_video_end_is_clamped_not_dropped(self):
        """start/end both inside range except end overruns -> clamp end."""
        import pipeline.highlight as highlight

        duration = 90.0
        _write_transcript(self.job_dir, duration)
        _fake_llm_module([
            {
                "slug": "overrun",
                "title": "Runs past the end",
                "reason": "test",
                "hook": "h",
                "payoff": "p",
                "retention_risk": "none",
                "score": 80,
                "start": 70.0,
                "end": 140.0,  # 50s past the real 90s duration
            }
        ])

        clips = highlight.pick_highlights(self.job_dir, n=1)

        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["start"], 70.0)
        self.assertEqual(clips[0]["end"], duration)  # clamped, not left dangling
        self.assertLessEqual(clips[0]["end"], duration)

    def test_negative_start_is_clamped_to_zero(self):
        import pipeline.highlight as highlight

        duration = 60.0
        _write_transcript(self.job_dir, duration)
        _fake_llm_module([
            {
                "slug": "negative-start",
                "title": "Starts before zero",
                "score": 70,
                "start": -4.5,
                "end": 20.0,
            }
        ])

        clips = highlight.pick_highlights(self.job_dir, n=1)

        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["start"], 0.0)
        self.assertEqual(clips[0]["end"], 20.0)

    def test_pick_entirely_past_duration_is_dropped(self):
        """A pick that clamps down to under the minimum clip length must be
        dropped, not written as a near-zero-length clip."""
        import pipeline.highlight as highlight

        duration = 100.0
        _write_transcript(self.job_dir, duration)
        _fake_llm_module([
            {
                "slug": "good-clip",
                "title": "A real clip",
                "score": 60,
                "start": 10.0,
                "end": 30.0,
            },
            {
                "slug": "hallucinated",
                "title": "Entirely past the end",
                "score": 90,
                "start": 101.0,
                "end": 102.5,  # clamps to [100, 100] -> zero length -> dropped
            },
        ])

        clips = highlight.pick_highlights(self.job_dir, n=2)

        slugs = {c["slug"] for c in clips}
        self.assertIn("good-clip", slugs)
        self.assertNotIn("hallucinated", slugs)
        for c in clips:
            self.assertLessEqual(c["end"], duration)
            self.assertGreaterEqual(c["start"], 0.0)

    def test_valid_clip_within_duration_is_unchanged(self):
        import pipeline.highlight as highlight

        duration = 120.0
        _write_transcript(self.job_dir, duration)
        _fake_llm_module([
            {
                "slug": "fine",
                "title": "Already valid",
                "score": 55,
                "start": 12.0,
                "end": 40.0,
            }
        ])

        clips = highlight.pick_highlights(self.job_dir, n=1)

        self.assertEqual(clips[0]["start"], 12.0)
        self.assertEqual(clips[0]["end"], 40.0)


if __name__ == "__main__":
    unittest.main()
