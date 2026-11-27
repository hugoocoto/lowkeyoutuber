"""Gemini Embedding 2: text, images, video and audio in one vector space (cloud).

ENVIRONMENT  GEMINI_API_KEY
CONFIG       GEMINI_EMBED_DIMS="768" (128-3072, Matryoshka: smaller keeps most of the quality)
REST  POST .../v1beta/models/gemini-embedding-2:embedContent  {"content": {"parts": [...]}}
      Input limit 8,192 tokens per item: embed short windows (search.py uses ~10 s).
"""

import json
import os
import urllib.error
import urllib.request

from backends import Base, BackendError
from backends._media import b64, clip, duration
from yt_common import CONFIG

URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:embedContent"


class Backend(Base):
    caps = {"local": False, "text": True, "image": True, "video": True, "cost": "per input token"}

    def __init__(self):
        self.model = CONFIG.get("GEMINI_EMBED_MODEL") or "gemini-embedding-2"
        self.dims = int(CONFIG.get("GEMINI_EMBED_DIMS") or 768)

    def check(self):
        if not os.environ.get("GEMINI_API_KEY"):
            return False, "GEMINI_API_KEY is not set in the environment"
        return True, ""

    def cache_id(self):
        return [self.model, self.dims]

    def parts(self, item, task):
        parts = []
        if item.get("text"):
            t = item["text"]
            # task prefixes recommended for gemini-embedding-2 retrieval
            parts.append({"text": f"task: search result | query: {t}" if task == "query" else f"title: none | text: {t}"})
        if item.get("image"):
            with open(item["image"], "rb") as f:
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64(f.read())}})
        if item.get("jpeg"):
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64(item["jpeg"])}})
        if item.get("video"):
            a = float(item.get("start", 0))
            b = float(item.get("end") or duration(item["video"]))
            parts.append({"inline_data": {"mime_type": "video/mp4", "data": b64(clip(item["video"], a, b, fps=1))}})
        return parts

    def embed(self, items, task="document"):
        out = []
        for it in items:
            body = {"model": f"models/{self.model}", "content": {"parts": self.parts(it, task)},
                    "output_dimensionality": self.dims}
            req = urllib.request.Request(URL.format(m=self.model), json.dumps(body).encode(),
                                         {"Content-Type": "application/json",
                                          "x-goog-api-key": os.environ.get("GEMINI_API_KEY", "")})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                raise BackendError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
            except (urllib.error.URLError, OSError, ValueError) as e:
                raise BackendError(str(e))
            emb = data.get("embedding") or (data.get("embeddings") or [{}])[0]
            if not emb.get("values"):
                raise BackendError(f"no embedding in the answer: {str(data)[:200]}")
            out.append(emb["values"])
        return out
