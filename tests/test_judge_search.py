import io
import json
import os
import sys

import pytest

import backends
import judge
import project as proj
import search
from conftest import ff
from fakes import FakeEmbed, FakeJudge


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def mock_urlopen(monkeypatch, module, answer, sent):
    def fake(req, timeout=None):
        sent.append({"url": req.full_url, "body": json.loads(req.data), "headers": dict(req.headers)})
        return Resp(json.dumps(answer).encode())
    monkeypatch.setattr(module.urllib.request, "urlopen", fake)


def test_gemini_decisions_request_and_answers(monkeypatch, tmp_path):
    from backends.judge import gemini_decisions as gd
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    img = tmp_path / "f.png"
    ff("-f", "lavfi", "-i", "color=red:s=32x32", "-frames:v", "1", str(img))
    sent = []
    mock_urlopen(monkeypatch, gd, {"answers": {
        "ok": {"type": "noul", "noul": 0.93, "confidence": 0.9},
        "kind": {"type": "choice", "choice": "code", "probabilities": {"code": 0.8, "face": 0.2}, "confidence": 0.8},
        "fit": {"type": "score", "score": 3.0, "confidence": 0.7, "probabilities": {}}}, "usage": {"total_output_tokens": 0}},
        sent)
    qs = {"ok": {"type": "noul", "ask": "Readable?", "yes": "all text readable"},
          "kind": {"type": "choice", "ask": "What is it?", "options": {"code": "code", "face": "a face"}},
          "fit": {"type": "score", "ask": "Match?", "levels": ["a", "b", "c", "d", "e"]}}
    out = gd.Backend().ask([{"image": str(img)}, "Narration: hola"], qs)
    req = sent[0]
    assert req["url"].endswith("/v1beta/decisions") and req["headers"]["X-goog-api-key"] == "k"
    assert req["body"]["model"] == "gemini-decision-1"
    assert [b["type"] for b in req["body"]["input"]] == ["image", "text"]
    assert req["body"]["questions"]["ok"]["criteria"]["true_description"] == "all text readable"
    assert req["body"]["questions"]["fit"]["criteria"] == ["a", "b", "c", "d", "e"]
    assert out["ok"]["answer"] is True and out["kind"]["answer"] == "code" and out["fit"]["score"] == 0.75


def test_ollama_vlm_answers(monkeypatch):
    from backends.judge import ollama_vlm as ov
    sent = []
    mock_urlopen(monkeypatch, ov, {"message": {"content": '{"ok": true, "fit": 2}'}}, sent)
    out = ov.Backend().ask(["hola"], {"ok": {"type": "noul", "ask": "?"},
                                      "fit": {"type": "score", "ask": "?", "levels": ["a", "b", "c", "d", "e"]}})
    assert out["ok"]["answer"] is True and out["fit"]["answer"] == 2 and out["fit"]["score"] == 0.5
    assert sent[0]["body"]["format"] == "json"


def test_gemini_embedding_request(monkeypatch):
    from backends.embed import gemini_embedding as ge
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    sent = []
    mock_urlopen(monkeypatch, ge, {"embedding": {"values": [0.1, 0.2]}}, sent)
    v = ge.Backend().embed([{"text": "gdb segfault"}], task="query")
    assert v == [[0.1, 0.2]]
    b = sent[0]["body"]
    assert sent[0]["url"].endswith("models/gemini-embedding-2:embedContent")
    assert b["content"]["parts"][0]["text"].startswith("task: search result | query:") and b["output_dimensionality"] == 768


def test_verdict():
    a = {"readable": {"answer": True, "score": 0.9}, "broken": {"answer": True, "score": 0.8},
         "matches": {"answer": 2, "score": 0.55}}
    assert judge.verdict(a, strict=False) == ["broken"]
    assert judge.verdict(a, strict=True) == ["broken", "matches"]   # no calibrated probabilities: stricter


@pytest.fixture
def fakes(monkeypatch):
    backends.register("judge", "f_judge", FakeJudge)
    backends.register("embed", "f_embed", FakeEmbed)
    FakeJudge.seen = []
    yield
    backends.unregister("judge", "f_judge")
    backends.unregister("embed", "f_embed")


def run_main(mod, monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", [mod.__name__, *argv])
    code = 0
    try:
        mod.main()
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().out


def test_judge_scenes(tmp_path, fakes, monkeypatch, capsys):
    d = str(tmp_path / "p")
    proj.new(d, "generated-short")
    for i in ("01", "02"):
        ff("-f", "lavfi", "-i", "color=blue:s=320x568:d=2", os.path.join(d, "scenes", f"{i}.mp4"))
    p = proj.load(d)
    p["media"] = {f"scene:{i}": {"file": f"scenes/{i}.mp4", "kind": "video"} for i in ("01", "02")}
    proj.save(d, p)
    with open(os.path.join(d, "script.json"), "w") as f:
        json.dump({"lines": [{"id": "01", "text": "Todo bien"}, {"id": "02", "text": "Esto está roto"}]}, f)
    code, out = run_main(judge, monkeypatch, capsys, "-P", d, "--scenes", "--backend", "f_judge", "--json", "--strict")
    rep = json.loads(out)
    assert code == 1
    res = {r["name"]: r["fails"] for r in rep["results"]}
    assert res["scene 01"] == [] and set(res["scene 02"]) == {"readable", "broken", "matches"}
    assert os.path.isfile(os.path.join(d, "checks", "judge.json"))
    # the judge got frames of the scene and the narration
    inputs = FakeJudge.seen[0][0]
    assert any("jpeg" in x for x in inputs if isinstance(x, dict)) and any("Todo bien" in x for x in inputs if isinstance(x, str))


def test_judge_clips(tmp_path, fakes, monkeypatch, capsys):
    v = tmp_path / "long.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:d=20", str(v))
    clips = tmp_path / "clips.json"
    clips.write_text(json.dumps([{"start": 1, "end": 8, "title": "Uno", "summary": "s"},
                                 {"start": 10, "end": 18, "title": "Dos", "summary": "s"}]))
    code, _ = run_main(judge, monkeypatch, capsys, "--clips", str(clips), "--video", str(v), "--backend", "f_judge")
    got = json.loads(clips.read_text())
    assert code == 0 and [c["title"] for c in got] == ["Uno", "Dos"] and got[0]["judge"]["fails"] == []
    assert set(got[0]["judge"]) == {"hook", "standalone", "clean_end", "fails"}


def test_search_index_and_query(tmp_path, fakes, monkeypatch, capsys):
    monkeypatch.setitem(backends.CONFIG, "SEARCH_INDEX", str(tmp_path / "idx.json"))
    monkeypatch.setitem(backends.CONFIG, "TRANSCRIBE", "none_at_all")
    v = tmp_path / "rec.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:d=25", str(v))
    (tmp_path / "rec.words.json").write_text(json.dumps({"words": [
        {"w": "hola", "s": 1, "e": 1.5}, {"w": "lista", "s": 3, "e": 3.4},
        {"w": "abro", "s": 12, "e": 12.4}, {"w": "gdb", "s": 12.5, "e": 13}, {"w": "segfault", "s": 13.2, "e": 14},
        {"w": "make", "s": 22, "e": 22.5}]}))
    code, _ = run_main(search, monkeypatch, capsys, "index", str(v), "--backend", "f_embed")
    assert code == 0
    code, out = run_main(search, monkeypatch, capsys, "query", "gdb segfault", "--json")
    hits = json.loads(out)
    assert hits[0]["start"] == 10.0 and "gdb" in hits[0]["text"]
    # unchanged file: not embedded again
    code, _ = run_main(search, monkeypatch, capsys, "index", str(v), "--backend", "f_embed")
    assert "unchanged" in capsys.readouterr().err or code == 0
