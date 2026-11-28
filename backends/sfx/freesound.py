"""Freesound CC0 SFX downloader (API v2 with FREESOUND_API_KEY, cached locally)."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from backends import Base, BackendError
from backends.sfx._cache import cache_dir, find_cached, record_entry, slug


class Backend(Base):
    caps = {"local": False, "network": True, "cc0": True}

    def check(self):
        if not os.environ.get("FREESOUND_API_KEY"):
            return False, "FREESOUND_API_KEY is not set in the environment"
        return True, ""

    def search(self, prompt, max_dur=None, limit=8):
        key = os.environ.get("FREESOUND_API_KEY")
        if not key:
            raise BackendError("FREESOUND_API_KEY is not set")
        flt = ['license:"Creative Commons 0"']
        if max_dur is not None:
            flt.append(f"duration:[0.05 TO {float(max_dur):.2f}]")
        params = urllib.parse.urlencode({
            "query": prompt,
            "filter": " ".join(flt),
            "fields": "id,name,tags,duration,previews,license,username,url",
            "sort": "rating_desc",
            "page_size": limit,
        })
        req = urllib.request.Request(
            f"https://freesound.org/apiv2/search/text/?{params}",
            headers={"Authorization": f"Token {key}", "User-Agent": "lowkeyoutuber/1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise BackendError(f"Freesound search failed: {e}") from e
        return data.get("results", [])

    def get(self, prompt, max_dur=None):
        cached = find_cached(prompt, max_dur=max_dur, source="freesound", min_score=2)
        if cached:
            return cached
        results = self.search(prompt, max_dur=max_dur, limit=8)
        if not results:
            raise BackendError(f"no CC0 Freesound match for {prompt!r}")
        pick = next((r for r in results if (r.get("previews") or {}).get("preview-hq-mp3")
                     or (r.get("previews") or {}).get("preview-lq-mp3")), None)
        if not pick:
            raise BackendError(f"Freesound results for {prompt!r} had no preview URL")
        previews = pick["previews"]
        url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3")
        ext = ".mp3" if "mp3" in url else ".ogg"
        dst = os.path.join(cache_dir(), f"fs_{pick['id']}_{slug(pick.get('name') or prompt)}{ext}")
        if not os.path.isfile(dst):
            req = urllib.request.Request(url, headers={"User-Agent": "lowkeyoutuber/1"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    blob = resp.read()
            except (urllib.error.URLError, TimeoutError) as e:
                raise BackendError(f"Freesound download failed: {e}") from e
            with open(dst, "wb") as f:
                f.write(blob)
        record_entry(
            dst,
            query=prompt,
            tags=pick.get("tags") or [],
            duration=pick.get("duration"),
            license_name="CC0",
            source="freesound",
            url=pick.get("url") or url,
            author=pick.get("username") or "",
        )
        return dst
