import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import highlight as H


def _tr(dur, step=10):
    segs = [{"start": float(t), "end": float(t + step), "text": "w1 w2 w3 w4 w5 w6"}
            for t in range(0, int(dur), step)]
    return {"duration": float(dur), "segments": segs}


def test_short_transcript_is_single_chunk():
    tr = _tr(600)
    assert H._chunk_transcript(tr) == [(0.0, 600.0, tr)]


def test_long_transcript_chunks_overlap_and_cover():
    tr = _tr(4000)
    chunks = H._chunk_transcript(tr)
    assert len(chunks) >= 3
    assert chunks[0][0] == 0.0 and chunks[-1][1] == 4000.0
    for a, b in zip(chunks, chunks[1:]):
        assert b[0] < a[1]  # overlap
    covered = {s["start"] for _, _, sub in chunks for s in sub["segments"]}
    assert covered == {s["start"] for s in tr["segments"]}


def test_quota():
    assert H._per_chunk_quota(6, 3) == 3
    assert H._per_chunk_quota(1, 5) == 2


def test_pick_highlights_chunks_and_merges(tmp_path, monkeypatch):
    tr = _tr(4000)
    (tmp_path / "transcript.json").write_text(json.dumps(tr))
    calls = []

    def complete(prompt, system=None, max_tokens=0):
        calls.append(prompt)
        # pick a moment inside this chunk: first transcript line's time
        first = prompt.split("Transcript:\n")[1].split("\n")[0].split()[0]
        m, s = map(int, first.split(":"))
        t = m * 60 + s + 100
        return json.dumps([{"slug": "c%d" % t, "title": "T%d" % t, "score": 70,
                            "start": t, "end": t + 30}])

    monkeypatch.setitem(sys.modules, "llm", types.SimpleNamespace(complete=complete))
    clips = H.pick_highlights(str(tmp_path), n=2)
    assert len(calls) >= 3
    assert len(clips) == 2
    assert all(0 <= c["start"] < c["end"] <= 4000 for c in clips)


def test_one_failing_chunk_does_not_sink_job(tmp_path, monkeypatch):
    tr = _tr(4000)
    (tmp_path / "transcript.json").write_text(json.dumps(tr))
    state = {"n": 0}

    def complete(prompt, system=None, max_tokens=0):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return json.dumps([{"slug": "a%d" % state["n"], "title": "A", "score": 60,
                            "start": 100.0 * state["n"], "end": 100.0 * state["n"] + 20}])

    monkeypatch.setitem(sys.modules, "llm", types.SimpleNamespace(complete=complete))
    assert H.pick_highlights(str(tmp_path), n=3)
