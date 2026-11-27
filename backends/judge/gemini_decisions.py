"""Gemini Decisions API (gemini-decision-1): closed questions scored in one pass, with calibrated
probabilities, over text, images, audio and video. Cloud, $0.10 per 1M input tokens, output free.

ENVIRONMENT  GEMINI_API_KEY
REST  POST https://generativelanguage.googleapis.com/v1beta/decisions  (x-goog-api-key)
      {"model", "input": [blocks], "questions": {name: {"type": noul|choice|score, "instructions", "criteria"}}}
"""

import json
import os
import urllib.error
import urllib.request

from backends import Base, BackendError
from backends._media import b64, clip, duration, expand
from yt_common import CONFIG

URL = "https://generativelanguage.googleapis.com/v1beta/decisions"


def blocks(inputs):
    out = []
    for p in expand(inputs):
        if "text" in p:
            out.append({"type": "text", "text": p["text"]})
        elif "image" in p:
            with open(p["image"], "rb") as f:
                ext = os.path.splitext(p["image"])[1].lower().lstrip(".")
                out.append({"type": "image", "data": b64(f.read()),
                            "mime_type": "image/png" if ext == "png" else "image/jpeg", "resolution": "medium"})
        elif "jpeg" in p:
            out.append({"type": "image", "data": b64(p["jpeg"]), "mime_type": "image/jpeg", "resolution": "medium"})
        elif "video" in p:
            a = float(p.get("start", 0))
            b = float(p.get("end") or duration(p["video"]))
            out.append({"type": "video", "data": b64(clip(p["video"], a, b)), "mime_type": "video/mp4"})
    return out


def question(q):
    """Our question format -> the API's."""
    d = {"type": q["type"], "instructions": q["ask"]}
    if q["type"] == "noul" and q.get("yes"):
        d["criteria"] = {"true_description": q["yes"], "false_description": q.get("no", "Otherwise.")}
    elif q["type"] == "choice":
        d["criteria"] = q["options"]
    elif q["type"] == "score":
        d["criteria"] = q["levels"]
    return d


def answer(q, a):
    """API answer -> {"answer", "score" (0-1), "confidence"}."""
    conf = float(a.get("confidence", 0))
    if q["type"] == "noul":
        p = float(a.get("noul", a.get("noul_probability_yes", 0)))
        return {"answer": p >= 0.5, "score": p, "confidence": conf}
    if q["type"] == "choice":
        return {"answer": a.get("choice"), "score": float((a.get("probabilities") or {}).get(a.get("choice"), conf)),
                "confidence": conf, "probabilities": a.get("probabilities")}
    k = len(q["levels"]) - 1
    s = float(a.get("score", 0))
    return {"answer": round(s), "score": s / k if k else 0.0, "confidence": conf}


class Backend(Base):
    caps = {"local": False, "probabilities": True, "video": True, "audio": True, "cost": "per input token"}

    def __init__(self):
        self.model = CONFIG.get("GEMINI_DECISION_MODEL") or "gemini-decision-1"

    def check(self):
        if not os.environ.get("GEMINI_API_KEY"):
            return False, "GEMINI_API_KEY is not set in the environment"
        return True, ""

    def ask(self, inputs, questions):
        body = {"model": self.model, "input": blocks(inputs),
                "questions": {name: question(q) for name, q in questions.items()}}
        req = urllib.request.Request(URL, json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json",
                                      "x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise BackendError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise BackendError(str(e))
        answers = data.get("answers", data)
        missing = [n for n in questions if n not in answers]
        if missing:
            raise BackendError(f"no answer for {missing}")
        out = {n: answer(q, answers[n]) for n, q in questions.items()}
        out["_usage"] = data.get("usage", {})
        return out
