"""A local vision model through Ollama answers the same closed questions (free, private).
It sees frames, not motion or sound, and its answers aren't calibrated probabilities
(caps probabilities=False): flows use stricter thresholds with it.

CONFIG  OLLAMA_VISION_MODEL="gemma4:12b" (any Ollama model that takes images)
"""

import json
import os
import urllib.error
import urllib.request

from backends import Base, BackendError
from backends._media import as_frames, b64, expand
from yt_common import CONFIG


def prompt_for(questions):
    lines = ["Answer about the images (frames of a video, in order) and the text below.",
             "Return ONLY JSON: {\"<name>\": <answer>, ...} with these answers:"]
    for name, q in questions.items():
        if q["type"] == "noul":
            lines.append(f'- "{name}": true or false. {q["ask"]}' + (f' (true means: {q["yes"]})' if q.get("yes") else ""))
        elif q["type"] == "choice":
            opts = "; ".join(f'"{k}" = {v}' for k, v in q["options"].items())
            lines.append(f'- "{name}": one of the keys. {q["ask"]} Options: {opts}')
        else:
            lv = "; ".join(f"{i} = {v}" for i, v in enumerate(q["levels"]))
            lines.append(f'- "{name}": an integer 0-{len(q["levels"]) - 1}. {q["ask"]} Levels: {lv}')
    return "\n".join(lines)


class Backend(Base):
    caps = {"local": True, "probabilities": False, "video": False, "audio": False, "cost": "free"}

    def __init__(self):
        self.model = CONFIG.get("OLLAMA_VISION_MODEL") or "gemma4:12b"
        url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.url = (url if url.startswith("http") else "http://" + url).rstrip("/")

    def check(self):
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=3) as r:
                tags = [m["name"] for m in json.load(r).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            return False, f"Ollama is not running at {self.url}"
        if self.model not in tags and f"{self.model}:latest" not in tags:
            return False, f"vision model {self.model} not pulled (ollama pull {self.model})"
        return True, ""

    def ask(self, inputs, questions):
        texts, images = [], []
        for p in expand(inputs):
            if "text" in p:
                texts.append(p["text"])
            elif "image" in p:
                with open(p["image"], "rb") as f:
                    images.append(b64(f.read()))
            elif "jpeg" in p:
                images.append(b64(p["jpeg"]))
            elif "video" in p:
                images += [b64(j) for j in as_frames(p)]
        body = {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0},
                "messages": [{"role": "user", "content": prompt_for(questions) + "\n\n" + "\n".join(texts),
                              "images": images}]}
        req = urllib.request.Request(self.url + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                raw = json.load(r)["message"]["content"]
            got = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            raise BackendError(f"Ollama failed: {e}")
        out = {}
        for name, q in questions.items():
            v = got.get(name)
            if v is None:
                raise BackendError(f"Ollama gave no answer for {name!r}")
            if q["type"] == "noul":
                if str(v).lower() not in ("true", "false", "yes", "no", "1", "0"):
                    raise BackendError(f"Ollama gave an invalid answer for {name!r}: {v!r}")
                yes = v is True or str(v).lower() in ("true", "yes", "1")
                out[name] = {"answer": yes, "score": 0.8 if yes else 0.2, "confidence": 0.6}
            elif q["type"] == "choice":
                ok = v in q["options"]
                out[name] = {"answer": v if ok else None, "score": 0.6 if ok else 0.0, "confidence": 0.6 if ok else 0.0}
            else:
                k = len(q["levels"]) - 1
                try:
                    n = max(0, min(k, int(round(float(v)))))
                    out[name] = {"answer": n, "score": n / k if k else 0.0, "confidence": 0.6}
                except (TypeError, ValueError):
                    out[name] = {"answer": None, "score": 0.0, "confidence": 0.0}
        return out
