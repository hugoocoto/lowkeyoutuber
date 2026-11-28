"""Zero-key CC0 SFX fetcher (Wikimedia Commons public API + local zip/folder CC0 pack importer)."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from backends import Base, BackendError, input_hash
from backends.sfx._cache import cache_dir, find_cached, probe_duration, record_entry, slug

AUDIO = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus")


def import_pack(pack_path, tag=""):
    """Import a downloaded CC0 zip pack (e.g. Kenney.nl audio pack) into the local SFX cache."""
    dst_root = os.path.join(cache_dir(), "packs", slug(tag or os.path.splitext(os.path.basename(pack_path))[0]))
    os.makedirs(dst_root, exist_ok=True)
    imported = []
    with zipfile.ZipFile(pack_path) as zf:
        for member in zf.namelist():
            if member.endswith("/") or not member.lower().endswith(AUDIO):
                continue
            rel = os.path.normpath(member).lstrip(os.sep)
            if rel.startswith(".."):
                continue
            target = os.path.join(dst_root, os.path.basename(rel))
            if target in imported:  # ui/click.wav and game/click.wav: keep both
                target = os.path.join(dst_root, rel.replace(os.sep, "_"))
            with zf.open(member) as src, open(target, "wb") as out:
                out.write(src.read())
            words_from_name = os.path.splitext(os.path.basename(rel))[0].replace("_", " ").replace("-", " ").split()
            record_entry(target, query=" ".join(words_from_name), tags=[tag, *words_from_name] if tag else words_from_name,
                         license_name="CC0", source="pack")
            imported.append(target)
    return imported


class Backend(Base):
    caps = {"local": False, "network": True, "cc0": True}

    def search(self, prompt, limit=10):
        params = urllib.parse.urlencode({
            "action": "query",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrsearch": f"{prompt} filetype:audio",
            "gsrlimit": limit,
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata|size",
            "format": "json",
        })
        req = urllib.request.Request(
            f"https://commons.wikimedia.org/w/api.php?{params}",
            headers={"User-Agent": "lowkeyoutuber/1.0 (https://github.com/lowkeyoutuber)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise BackendError(f"CC0 Wikimedia search failed: {e}") from e
        pages = (data.get("query") or {}).get("pages") or {}
        hits = []
        for page in pages.values():
            ii = (page.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata") or {}
            lic = ((meta.get("LicenseShortName") or {}).get("value") or "").lower()
            if "cc0" not in lic and "public domain" not in lic:
                continue
            url = ii.get("url")
            if url and (ii.get("mime") or "").startswith("audio/"):
                hits.append({
                    "title": page.get("title", ""),
                    "url": url,
                    "license": (meta.get("LicenseShortName") or {}).get("value") or "CC0",
                    "author": ((meta.get("Artist") or {}).get("value") or "").strip(),
                })
        return hits

    def get(self, prompt, max_dur=None):
        cached = find_cached(prompt, max_dur=max_dur, source="wikimedia", min_score=2)
        if cached:
            return cached
        hits = self.search(prompt, limit=10)
        for h in hits:
            ext = os.path.splitext(urllib.parse.urlparse(h["url"]).path)[1].lower() or ".ogg"
            if ext not in AUDIO:
                ext = ".ogg"
            dst = os.path.join(cache_dir(), f"cc0_{slug(h['title'])}_{input_hash(h['url'])[:8]}{ext}")
            if not os.path.isfile(dst):
                req = urllib.request.Request(h["url"], headers={"User-Agent": "lowkeyoutuber/1.0"})
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        blob = resp.read()
                except (urllib.error.URLError, TimeoutError):
                    continue
                with open(dst, "wb") as f:
                    f.write(blob)
            dur = probe_duration(dst)
            if max_dur is not None and (dur <= 0 or dur > float(max_dur) + 0.05):
                continue
            record_entry(dst, query=prompt, tags=prompt.split(), duration=dur,
                         license_name=h["license"], source="wikimedia", url=h["url"], author=h["author"])
            return dst
        raise BackendError(f"no matching CC0 audio found on Wikimedia Commons for {prompt!r}")
