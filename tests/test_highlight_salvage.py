import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.highlight import _extract_json_array


def test_complete_array_unchanged():
    assert _extract_json_array('[{"a":1},{"a":2}]') == [{"a": 1}, {"a": 2}]


def test_truncated_array_salvages_complete_objects():
    txt = '[{"title":"x } y","start":1,"end":20},{"title":"two","start":5,"end":30},{"title":"cut off","sta'
    got = _extract_json_array(txt)
    assert [g["title"] for g in got] == ["x } y", "two"]


def test_truncated_with_no_complete_object_raises():
    import pytest
    with pytest.raises(RuntimeError):
        _extract_json_array('[{"title":"cut')
