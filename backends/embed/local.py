"""Local embeddings, free and private: OpenCLIP for pictures (and text in the same space as
pictures) plus an Ollama text-embedding model for transcripts. An item with both a picture and
text gets both vectors concatenated, as does a query, so one cosine search covers both.

    pip install open_clip_torch     (downloads the ViT-B-32 weights once)
CONFIG  OLLAMA_EMBED_MODEL="nomic-embed-text"   OPENCLIP_MODEL="ViT-B-32"  OPENCLIP_PRETRAINED="laion2b_s34b_b79k"
"""

import importlib.util
import io
import json
import os
import urllib.error
import urllib.request

from backends import Base, BackendError
from backends._media import as_frames
from yt_common import CONFIG


def unit(v):
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


class Backend(Base):
    caps = {"local": True, "text": True, "image": True, "video": True, "cost": "free"}

    def __init__(self):
        self.text_model = CONFIG.get("OLLAMA_EMBED_MODEL") or "nomic-embed-text"
        self.clip_name = CONFIG.get("OPENCLIP_MODEL") or "ViT-B-32"
        self.clip_pre = CONFIG.get("OPENCLIP_PRETRAINED") or "laion2b_s34b_b79k"
        self.model = f"{self.clip_name}+{self.text_model}"
        url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.url = (url if url.startswith("http") else "http://" + url).rstrip("/")
        self._clip = None

    def check(self):
        if importlib.util.find_spec("open_clip") is None:
            return False, "open_clip_torch is not installed (pip install open_clip_torch)"
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=3) as r:
                tags = [m["name"] for m in json.load(r).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            return False, f"Ollama is not running at {self.url} (needed for text embeddings)"
        if self.text_model not in tags and f"{self.text_model}:latest" not in tags:
            return False, f"ollama pull {self.text_model}"
        return True, ""

    def cache_id(self):
        return [self.model, self.clip_pre]

    def _load_clip(self):
        if self._clip is None:
            import open_clip
            import torch
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            model, _, pre = open_clip.create_model_and_transforms(self.clip_name, pretrained=self.clip_pre, device=dev)
            self._clip = (model.eval(), pre, open_clip.get_tokenizer(self.clip_name), dev, torch)
        return self._clip

    def _image_vec(self, jpegs):
        from PIL import Image
        model, pre, _, dev, torch = self._load_clip()
        with torch.no_grad():
            x = torch.stack([pre(Image.open(io.BytesIO(j)).convert("RGB")) for j in jpegs]).to(dev)
            v = model.encode_image(x).mean(0)
        return unit(v.float().cpu().tolist())

    def _clip_text(self, text):
        model, _, tok, dev, torch = self._load_clip()
        with torch.no_grad():
            v = model.encode_text(tok([text]).to(dev))[0]
        return unit(v.float().cpu().tolist())

    def _text_vec(self, text):
        body = {"model": self.text_model, "input": text}
        req = urllib.request.Request(self.url + "/api/embed", json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return unit(json.load(r)["embeddings"][0])
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            raise BackendError(f"Ollama embed failed: {e}")

    def _text_dims(self):
        if not getattr(self, "_tdims", None):
            self._tdims = len(self._text_vec("."))
        return self._tdims

    def embed(self, items, task="document"):
        out = []
        for it in items:
            jpegs = []
            if it.get("image"):
                with open(it["image"], "rb") as f:
                    jpegs.append(f.read())
            if it.get("jpeg"):
                jpegs.append(it["jpeg"])
            if it.get("video"):
                jpegs += as_frames(it, max_frames=3)
            text = it.get("text") or ""
            try:
                # pictures in CLIP space; without a picture, the text in CLIP space (same dimensions)
                vis = self._image_vec(jpegs) if jpegs else self._clip_text(text or " ")
            except Exception as e:   # torch/PIL errors vary
                raise BackendError(f"OpenCLIP failed: {e}")
            txt = self._text_vec(text) if text else [0.0] * self._text_dims()
            out.append(vis + txt)
        return out
