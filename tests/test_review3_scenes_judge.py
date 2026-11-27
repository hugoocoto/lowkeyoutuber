"""Regression tests for the CodeRabbit review of PR #3: scenes, render stamp, judge, media, project."""
import io
import json
import os
import sys

import pytest

import backends
import judge
import project as proj
import render
import scenes
from backends import BackendError
from backends._media import clip, frames
from conftest import ff
from fakes import FakeJudge, FakeRender

# ---------------------------------------------------------------- helpers


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def run_main(mod, monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", [mod.__name__, *argv, "--json"])
    code = 0
    try:
        mod.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1]) if out else None


class BadRender(FakeRender):
    model = "bad-render"

    def render_scene(self, scene_dir, out, fps, w, h, draft=False):
        raise BackendError("render crashed")


@pytest.fixture
def renders(monkeypatch):
    backends.register("render", "f_render", FakeRender)
    backends.register("render", "f_bad", BadRender)
    monkeypatch.setitem(backends.CONFIG, "RENDER", "f_render")
    yield
    backends.unregister("render", "f_render")
    backends.unregister("render", "f_bad")


def write_script(d, lines):
    with open(os.path.join(d, "script.json"), "w") as f:
        json.dump({"lang": "es", "lines": lines}, f)


# ---------------------------------------------------------------- render.py


def test_media_stamp_includes_variant_files(tmp_path):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    for n in ("a.mp4", "b.mp4"):
        (tmp_path / "p" / n).write_bytes(b"x" * 10)
    pj = {"media": {"scene:01": {"file": "a.mp4", "kind": "video", "variants": {"9:16": "a.mp4", "16:9": "b.mp4"}}},
          "timeline": {"video": [], "audio": []}}
    before = render.media_stamp(d, pj)
    (tmp_path / "p" / "b.mp4").write_bytes(b"y" * 25)   # only the secondary variant changes
    after = render.media_stamp(d, pj)
    assert before != after
    assert render.media_stamp(d, pj) == after   # unchanged files keep the stamp


# ---------------------------------------------------------------- project.py


def test_validate_rejects_malformed_variants_without_raising(tmp_path):
    d = str(tmp_path / "p")
    p = proj.new(d, "generated-short")
    (tmp_path / "p" / "a.mp4").write_bytes(b"x")
    for bad in (["clip.mp4"], "clip.mp4", {"9:16": None}, {"9:16": 3}):
        p["media"] = {"m": {"file": "a.mp4", "variants": bad}}
        for check in (True, False):
            errs = proj.validate(d, p, check_files=check)
            assert any("variant" in e for e in errs), (bad, check, errs)
    p["media"] = {"m": {"file": "a.mp4", "variants": {"9:16": "a.mp4"}}}
    assert not [e for e in proj.validate(d, p) if "variant" in e]


# ---------------------------------------------------------------- backends/_media.py


def test_frames_required_reports_failed_extraction(tmp_path):
    missing = str(tmp_path / "nope.mp4")
    assert frames(missing, [0.5]) == []   # default stays lenient (search.py relies on it)
    with pytest.raises(BackendError):
        frames(missing, [0.5], required=True)
    with pytest.raises(BackendError):
        clip(missing, 0, 1)


# ---------------------------------------------------------------- backends/judge


def test_gemini_media_failure_is_a_backend_error(tmp_path):
    from backends.judge import gemini_decisions as gd
    qs = {"ok": {"type": "noul", "ask": "?"}}
    with pytest.raises(BackendError):
        gd.Backend().ask([{"video": str(tmp_path / "nope.mp4"), "start": 0, "end": 1}], qs)
    with pytest.raises(BackendError):
        gd.Backend().ask([{"image": str(tmp_path / "nope.png")}], qs)


def ollama_answer(monkeypatch, content):
    from backends.judge import ollama_vlm as ov
    monkeypatch.setattr(ov.urllib.request, "urlopen",
                        lambda req, timeout=None: Resp(json.dumps({"message": {"content": content}}).encode()))
    return ov


def test_ollama_rejects_omitted_or_invalid_answers(monkeypatch):
    qs = {"readable": {"type": "noul", "ask": "?"}, "broken": {"type": "noul", "ask": "?"}}
    ov = ollama_answer(monkeypatch, '{"readable": true}')   # "broken" not answered
    with pytest.raises(BackendError):
        ov.Backend().ask(["hola"], qs)
    ov = ollama_answer(monkeypatch, '{"readable": true, "broken": "maybe"}')
    with pytest.raises(BackendError):
        ov.Backend().ask(["hola"], qs)
    ov = ollama_answer(monkeypatch, '{"readable": true, "broken": false}')
    assert ov.Backend().ask(["hola"], qs)["broken"]["answer"] is False


# ---------------------------------------------------------------- judge.py


class QuotaJudge(FakeJudge):
    model = "quota-judge"
    caps = {"local": False, "probabilities": True, "video": False}

    def ask(self, inputs, questions):
        raise BackendError("HTTP 429 quota")


class LocalJudge(FakeJudge):
    model = "local-judge"
    caps = {"local": True, "probabilities": False, "video": False}


@pytest.fixture
def judges(monkeypatch):
    for n, c in (("f_judge", FakeJudge), ("f_quota", QuotaJudge), ("f_local", LocalJudge)):
        backends.register("judge", n, c)
    FakeJudge.seen = []
    yield
    for n in ("f_judge", "f_quota", "f_local"):
        backends.unregister("judge", n)


def scene_project(tmp_path, media):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    for n in ("a.mp4", "b.mp4"):
        ff("-f", "lavfi", "-i", "color=blue:s=320x568:d=2", os.path.join(d, "scenes", n))
    p = proj.load(d)
    p["media"] = {"scene:01": media}
    proj.save(d, p)
    write_script(d, [{"id": "01", "text": "Todo bien"}])
    return d


def test_judge_checks_every_scene_variant(tmp_path, judges, monkeypatch, capsys):
    d = scene_project(tmp_path, {"file": "scenes/a.mp4", "kind": "video",
                                 "variants": {"9:16": "scenes/a.mp4", "16:9": "scenes/b.mp4"}})
    p = proj.load(d)   # judged: the file each of the project's formats renders
    p["formats"] = {"16:9": [1920, 1080], "9:16": [1080, 1920]}
    proj.save(d, p)
    code, rep = run_main(judge, monkeypatch, capsys, "-P", d, "--scenes", "--backend", "f_judge")
    names = sorted(r["name"] for r in rep["results"])
    assert names == ["scene 01 16:9", "scene 01 9:16"]
    assert {r["format"] for r in rep["results"]} == {"9:16", "16:9"}
    assert len(FakeJudge.seen) == 2


def test_judge_uses_fallback_chain_and_answering_backend(tmp_path, judges, monkeypatch, capsys):
    d = scene_project(tmp_path, {"file": "scenes/a.mp4", "kind": "video"})
    code, rep = run_main(judge, monkeypatch, capsys, "-P", d, "--scenes", "--backend", "f_quota,f_local")
    r = rep["results"][0]
    assert "error" not in r and r["backend"] == "f_local"


def test_judge_failed_frames_are_an_error_not_a_pass(tmp_path, judges, monkeypatch, capsys):
    d = scene_project(tmp_path, {"file": "scenes/missing.mp4", "kind": "video"})
    code, rep = run_main(judge, monkeypatch, capsys, "-P", d, "--scenes", "--backend", "f_judge", "--strict")
    r = rep["results"][0]
    assert code == 1 and r.get("error") and "fails" not in r
    assert FakeJudge.seen == []   # nothing was asked on narration alone


# ---------------------------------------------------------------- scenes.py


def title_line(title):
    return [{"id": "01", "text": "Hola a todos", "scene": {"type": "title", "title": title}}]


def test_scene_html_without_metadata_is_kept(tmp_path, renders, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    write_script(d, title_line("Hola"))
    idx = os.path.join(d, "scenes", "01", "index.html")
    os.makedirs(os.path.dirname(idx))
    mine = ('<html><body><div data-composition-id="main" data-duration="1.0"></div></body></html>'
            "<!-- hand made -->")
    with open(idx, "w") as f:
        f.write(mine)
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert s["edited_by_hand"] == ["01 16:9"]
    assert open(idx).read() == mine


def test_generated_hash_survives_a_failed_render(tmp_path, renders, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    idx = os.path.join(d, "scenes", "01", "index.html")
    write_script(d, title_line("Hola"))
    assert run_main(scenes, monkeypatch, capsys, "-P", d)[0] == 0
    write_script(d, title_line("Adios"))   # new document is written, then the render fails
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d, "--backend", "f_bad")
    assert code == 1 and s["failed"] and "Adios" in open(idx).read()
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)   # retry: still ours, not a hand edit
    assert code == 0 and s["edited_by_hand"] == [] and s["rendered"] == ["01 16:9"]
    write_script(d, title_line("Chau"))
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert s["edited_by_hand"] == [] and "Chau" in open(idx).read()


def test_changed_image_bytes_render_again(tmp_path, renders, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    img = os.path.join(d, "media", "shot.png")
    os.makedirs(os.path.dirname(img), exist_ok=True)
    ff("-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", img)
    write_script(d, [{"id": "01", "text": "Mira esto", "scene": {"type": "image", "src": "media/shot.png"}}])
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert s["rendered"] == ["01 16:9"]
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert s["unchanged"] == ["01 16:9"] and not s["rendered"]
    ff("-f", "lavfi", "-i", "color=green:s=64x64", "-frames:v", "1", img)   # same path, new picture
    code, s = run_main(scenes, monkeypatch, capsys, "-P", d)
    assert s["rendered"] == ["01 16:9"]


# ---------------------------------------------------------------- second review round
def test_failed_custom_render_keeps_generated_hash_and_gen_key_paired(tmp_path, renders, monkeypatch, capsys):
    built = []

    def fake_build(sc, line, W, H, dur, clock, fdir, work, meta, root, lang, errors=None):
        built.append(sc["brief"])
        return (f'<html><body><div data-composition-id="main" data-duration="{dur}"></div>'
                f'<!-- {sc["brief"]} --></body></html>')
    monkeypatch.setattr(scenes, "build", fake_build)
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    idx = os.path.join(d, "scenes", "01", "index.html")

    def custom(brief):
        write_script(d, [{"id": "01", "text": "Hola", "scene": {"type": "custom", "brief": brief}}])
    custom("A")
    assert run_main(scenes, monkeypatch, capsys, "-P", d)[0] == 0
    custom("B")
    assert run_main(scenes, monkeypatch, capsys, "-P", d, "--backend", "f_bad")[0] == 1
    custom("A")   # back to A: the HTML on disk is B's, so A must be generated again
    assert run_main(scenes, monkeypatch, capsys, "-P", d)[0] == 0
    assert built == ["A", "B", "A"] and "<!-- A -->" in open(idx).read()
    custom("B")
    run_main(scenes, monkeypatch, capsys, "-P", d, "--backend", "f_bad")
    n = len(built)
    run_main(scenes, monkeypatch, capsys, "-P", d)   # unchanged retry of B reuses B's HTML
    assert len(built) == n and "<!-- B -->" in open(idx).read()


def test_judge_samples_stay_inside_the_interval():
    for start, end in ((10, 11), (10, 10.2)):
        assert all(start <= t < end for t in judge.inside(start, end))


def test_judge_checks_media_file_for_formats_without_a_variant(tmp_path, judges, monkeypatch, capsys):
    d = scene_project(tmp_path, {"file": "scenes/a.mp4", "kind": "video", "variants": {"9:16": "scenes/b.mp4"}})
    p = proj.load(d)
    p["formats"] = {"16:9": [1920, 1080], "9:16": [1080, 1920]}
    proj.save(d, p)
    code, rep = run_main(judge, monkeypatch, capsys, "-P", d, "--scenes", "--backend", "f_judge")
    assert sorted(r["format"] for r in rep["results"]) == ["16:9", "9:16"]   # a.mp4 (16:9) and b.mp4


def test_media_stamp_sees_a_same_size_same_second_replacement(tmp_path):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    f = tmp_path / "p" / "a.mp4"
    f.write_bytes(b"x" * 10)
    os.utime(f, ns=(1_700_000_000_100_000_000, 1_700_000_000_100_000_000))
    pj = {"media": {"m": {"file": "a.mp4", "kind": "video"}}, "timeline": {"video": [], "audio": []}}
    before = render.media_stamp(d, pj)
    f.write_bytes(b"y" * 10)
    os.utime(f, ns=(1_700_000_000_900_000_000, 1_700_000_000_900_000_000))   # same second, same size
    assert render.media_stamp(d, pj) != before
