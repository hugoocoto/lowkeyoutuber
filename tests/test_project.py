import pytest

import project as proj


def test_new_load_validate(tmp_path):
    d = tmp_path / "p"
    proj.new(str(d), "generated-short", formats=("9:16",))
    p = proj.load(str(d))
    assert p["formats"] == {"9:16": [1080, 1920]}
    assert proj.validate(str(d), p) == []
    for sub in proj.DIRS:
        assert (d / sub).is_dir()
    with pytest.raises(proj.ProjectError):
        proj.new(str(d), "generated-short")


def test_validate_reports_problems(tmp_path):
    d = tmp_path / "p"
    p = proj.new(str(d), "edit-recording")
    p["media"]["rec"] = {"file": "nope.mp4"}
    p["timeline"]["video"] = [
        {"src": "rec", "in": 5, "out": 2},
        {"color": "#000"},
        {"src": "rec", "in": 0, "out": 3, "zooms": [{"at": 0, "dur": 1, "rect": [0.5, 0.5, 2, 0.1]}]},
    ]
    p["timeline"]["audio"] = [{"src": "rec"}]
    errs = " | ".join(proj.validate(str(d), p))
    for needle in ("file not found", "'out' must be after 'in'", "needs 'in' and 'out'", "'rect'", "needs 'at'"):
        assert needle in errs


def test_script_validation():
    assert proj.validate_script({"lines": [{"id": "01", "text": "hola"}]}) == []
    errs = proj.validate_script({"lines": [{"id": "01", "text": ""}, {"id": "01", "text": "x"}, {"text": "y"}]})
    assert len(errs) == 3
    assert proj.validate_script({}) == ["needs a 'lines' list"]


def test_wrong_version(tmp_path):
    d = tmp_path / "p"
    p = proj.new(str(d), "x")
    p["version"] = 99
    proj.save(str(d), p)
    with pytest.raises(proj.ProjectError):
        proj.load(str(d))
