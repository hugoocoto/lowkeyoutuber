#!/usr/bin/env python3
"""
upload.py - Upload a folder made by publish.sh to YouTube: the video, title,
description (with chapters), tags, subtitles and thumbnail.

Reads from the folder:
    video.mp4        the video
    titles.txt       first line = title
    description.txt
    tags.txt         comma-separated
    video.srt        subtitles (optional)
    thumbnail.jpg    or thumbnail.png (optional; pick one from thumbs/ or make your own)
Writes uploaded.json with the video id, and refuses to upload the same folder
twice (use --force to upload again).

SHORTS (--shorts)
    Uploads the clips in <folder>/shorts/ made by clips.py instead: title from
    clips.json + " #shorts", its summary, a link to the full video (when
    uploaded.json exists, so upload the video first), the footer and the tags.
    Captions are burned in, so no subtitles. YouTube makes vertical videos
    under 3 minutes Shorts by itself. Ids go to shorts/uploaded.json; clips
    already in it are skipped.

SETUP (once)
    1. https://console.cloud.google.com : create a project and enable
       "YouTube Data API v3".
    2. OAuth consent screen: External, then add your Google account as a
       test user.
    3. Credentials > Create credentials > OAuth client ID > Desktop app.
       Download the JSON as ~/.config/lowkeyoutuber/client_secret.json
    4. In the venv: pip install google-api-python-client google-auth-oauthlib
    The first run opens the browser to log in. The token is saved next to the
    client secret. While the consent screen is in "Testing", Google expires it
    after 7 days, and you just log in again.

LIMITS
    - YouTube locks videos uploaded through an unaudited API project to
      PRIVATE. That's why this uploads as private by default: check the video
      in YouTube Studio, then publish it there.
    - The default API quota allows only a few uploads per day.
    - Custom thumbnails need a verified channel (phone verification).

USAGE
    upload.py video_publish/
    upload.py video_publish/ --category 28                 # Science & Technology
    upload.py video_publish/ --publish-at 2026-11-02T17:00:00Z
    upload.py video_publish/ --dry-run                     # check, don't upload
    upload.py video_publish/ --shorts                      # the Shorts in video_publish/shorts/
"""

import argparse
import json
import os
import re
import sys
import time

from yt_common import CONFIG, CONFIG_DIR as CONF, die, log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]   # force-ssl: captions


def youtube(secrets):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        die("missing libraries. In the venv run: pip install google-api-python-client google-auth-oauthlib")

    token = os.path.join(CONF, "token.json")
    creds = Credentials.from_authorized_user_file(token, SCOPES) if os.path.isfile(token) else None
    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:   # revoked or expired refresh token: log in again
            creds = None
    if not creds or not creds.valid:
        if not os.path.isfile(secrets):
            die(f"no OAuth client file at {secrets}. See the SETUP section in: upload.py -h")
        creds = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES).run_local_server(port=0)
        os.makedirs(CONF, exist_ok=True)
        with open(token, "w") as f:
            f.write(creds.to_json())
        os.chmod(token, 0o600)
    return build("youtube", "v3", credentials=creds)


def resumable(request):
    from googleapiclient.errors import HttpError
    resp, tries = None, 0
    while resp is None:
        try:
            status, resp = request.next_chunk()
            if status:
                log(f"\rUploading: {status.progress():.0%}", end="")
        except HttpError as e:
            if e.resp.status not in (500, 502, 503, 504) or tries >= 6:
                raise
            tries += 1
            time.sleep(2 ** tries)
        except OSError:
            if tries >= 6:
                raise
            tries += 1
            time.sleep(2 ** tries)
    log("\rUploading: 100%")
    return resp


def read(folder, name):
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


FULL_VIDEO = {"es": "Vídeo completo", "en": "Full video", "fr": "Vidéo complète", "de": "Ganzes Video",
              "it": "Video completo", "pt": "Vídeo completo", "ca": "Vídeo complet", "gl": "Vídeo completo"}
clean = lambda s: re.sub(r"[<>]", "", s)   # YouTube rejects < and >


def describe(item, args):
    """Print what would be uploaded; return the list of problems."""
    problems = []
    if not item["description"]:
        problems.append("empty description")
    elif "http" not in item["description"]:
        problems.append("no links in the description (is FOOTER in the config set, and does the file exist?)")
    if item["thumb"] and os.path.getsize(item["thumb"]) > 2 * 1024 * 1024:
        problems.append("thumbnail is over YouTube's 2 MB limit")
    srt, thumb = item["srt"], item["thumb"]
    log(f"Title:       {item['title']}"
        f"\nPrivacy:     {'scheduled ' + args.publish_at if args.publish_at else args.privacy}"
        f"\nDescription: {len(item['description'])} characters, "
        f"{'with' if re.search(r'^0:00 ', item['description'], re.M) else 'no'} chapters"
        f"\nTags:        {len(item['tags'])}\nLanguage:    {item['language'] or '?'}"
        f"\nSubtitles:   {'yes' if srt and os.path.isfile(srt) else 'no'}"
        f"\nThumbnail:   {os.path.basename(thumb) if thumb else 'none (YouTube picks a frame)'}")
    for problem in problems:
        log(f"Warning: {problem}")
    return problems


def upload(yt, item, args, record):
    """Upload one video with its subtitles and thumbnail. record(id) runs right
    after the video is in, so a later failure can't cause a second upload."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    snippet = {"title": item["title"], "description": item["description"], "tags": item["tags"],
               "categoryId": args.category}
    if item["language"]:
        snippet["defaultLanguage"] = snippet["defaultAudioLanguage"] = item["language"]
    status = {"privacyStatus": "private" if args.publish_at else args.privacy, "selfDeclaredMadeForKids": False}
    if args.publish_at:
        status["publishAt"] = args.publish_at
    try:
        resp = resumable(yt.videos().insert(
            part="snippet,status", body={"snippet": snippet, "status": status},
            media_body=MediaFileUpload(item["video"], chunksize=16 * 1024 * 1024, resumable=True)))
    except HttpError as e:
        die(f"upload failed: {e}")
    vid = resp["id"]
    record(vid)
    log(f"Uploaded: https://youtu.be/{vid}")

    if item["srt"] and os.path.isfile(item["srt"]):
        try:
            yt.captions().insert(part="snippet",
                                 body={"snippet": {"videoId": vid, "language": item["language"] or "en", "name": ""}},
                                 media_body=MediaFileUpload(item["srt"], mimetype="application/octet-stream")).execute()
            log("Subtitles added.")
        except HttpError as e:
            log(f"Subtitles failed (add them in Studio): {e}")

    if item["thumb"]:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(item["thumb"])).execute()
            log("Thumbnail set.")
        except HttpError as e:
            log(f"Thumbnail failed (is the channel phone-verified?): {e}")
    elif not item.get("short"):
        log("No thumbnail.jpg in the folder: YouTube will pick a frame. Set one in Studio.")
    log(f"Check it, then publish: https://studio.youtube.com/video/{vid}/edit")


def language_of(folder):
    path = os.path.join(folder, "video.words.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("language", "")
    return ""


def footer_text():
    path = CONFIG.get("FOOTER")
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def shorts(args):
    folder = args.folder
    sdir = os.path.join(folder, "shorts")
    cj = os.path.join(sdir, "clips.json")
    if not os.path.isfile(cj):
        die(f"no {cj}: make the Shorts with clips.py (or publish.sh -s) first")
    with open(cj, encoding="utf-8") as f:
        clips = json.load(f)
    by_file = {c["file"]: c for c in clips if c.get("file")}
    for name in sorted(os.listdir(sdir)):   # clips rendered before clips.json had "file": NN_ = number
        m = re.match(r"(\d+)_.*\.mp4$", name)
        if m and name not in by_file and 1 <= int(m.group(1)) <= len(clips):
            by_file[name] = clips[int(m.group(1)) - 1]
    if not by_file:
        die(f"no rendered clips in {sdir}")

    done_path = os.path.join(sdir, "uploaded.json")
    done = {}
    if os.path.isfile(done_path):
        with open(done_path, encoding="utf-8") as f:
            done = json.load(f)
    language = language_of(folder)
    full = ""
    if os.path.isfile(os.path.join(folder, "uploaded.json")):
        full = json.loads(read(folder, "uploaded.json")).get("url", "")
    else:
        log("Warning: the full video isn't uploaded yet (no uploaded.json), so the Shorts won't link to it.")
    tags = [t.strip() for t in read(folder, "tags.txt").split(",") if t.strip()]
    tags = tags[:12] + ["shorts"]
    footer = footer_text()

    todo = []
    for name, c in sorted(by_file.items()):
        if name in done and not args.force:
            log(f"Already uploaded, skipping: {name} ({done[name]['url']})")
            continue
        parts = [c.get("summary", "").strip()]
        if full:
            parts.append(f"{FULL_VIDEO.get(language, 'Full video')}: {full}")
        parts += [footer, "#shorts"]
        todo.append({"name": name, "video": os.path.join(sdir, name), "short": True,
                     "title": clean(c.get("title", "") or name)[:100 - len(" #shorts")].strip() + " #shorts",
                     "description": clean("\n\n".join(p for p in parts if p))[:5000],
                     "tags": tags, "language": language, "srt": None, "thumb": None})
    if not todo:
        return 0
    problems = False
    for item in todo:
        log(f"\n--- {item['name']}")
        problems |= bool(describe(item, args))
    if args.dry_run:
        return 1 if problems else 0

    def record(name, vid):
        done[name] = {"id": vid, "url": f"https://youtu.be/{vid}"}
        with open(done_path, "w", encoding="utf-8") as f:
            json.dump(done, f, indent=2)

    yt = youtube(args.secrets)
    for item in todo:
        log(f"\n--- {item['name']}")
        upload(yt, item, args, lambda vid, name=item["name"]: record(name, vid))
    return 0


def main():
    p = argparse.ArgumentParser(description="Upload a publish.sh folder to YouTube (as private by default).")
    p.add_argument("folder")
    p.add_argument("--video", help="video file (default: <folder>/video.mp4)")
    p.add_argument("--shorts", action="store_true", help="upload the Shorts in <folder>/shorts/ (see SHORTS)")
    p.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    p.add_argument("--publish-at", help="schedule, ISO 8601 UTC like 2026-11-02T17:00:00Z (implies private)")
    p.add_argument("--category", default="22",
                   help="category id: 22 People & Blogs (default), 27 Education, 28 Science & Tech, 20 Gaming")
    p.add_argument("--secrets", default=os.path.join(CONF, "client_secret.json"), help="OAuth client JSON")
    p.add_argument("--force", action="store_true", help="upload even if uploaded.json exists")
    p.add_argument("--dry-run", action="store_true", help="only check the folder and show what would be uploaded")
    args = p.parse_args()

    if args.shorts:
        return shorts(args)

    folder = args.folder
    video = args.video or os.path.join(folder, "video.mp4")
    if not os.path.isfile(video):
        die(f"no video at {video}")
    done = os.path.join(folder, "uploaded.json")
    if os.path.isfile(done) and not args.force and not args.dry_run:
        die(f"already uploaded ({read(folder, 'uploaded.json')}). Use --force to upload again.")

    title = next((line.strip() for line in read(folder, "titles.txt").splitlines() if line.strip()), "")
    language = ""
    words_json = os.path.splitext(video)[0] + ".words.json"
    if os.path.isfile(words_json):
        with open(words_json, encoding="utf-8") as f:
            language = json.load(f).get("language", "")
    item = {"video": video, "language": language,
            "title": clean(title)[:100] or os.path.splitext(os.path.basename(video))[0],
            "description": clean(read(folder, "description.txt")).encode("utf-8")[:5000].decode("utf-8", "ignore"),
            "tags": [t.strip() for t in read(folder, "tags.txt").split(",") if t.strip()],
            "srt": os.path.splitext(video)[0] + ".srt",
            "thumb": next((os.path.join(folder, n) for n in ("thumbnail.jpg", "thumbnail.png")
                           if os.path.isfile(os.path.join(folder, n))), None)}
    problems = describe(item, args)
    if not os.path.isfile(os.path.join(folder, "titles.txt")):
        log("Warning: no titles.txt: the file name will be the title")
        problems.append("no titles.txt")
    if args.dry_run:
        return 1 if problems else 0

    def record(vid):
        with open(done, "w", encoding="utf-8") as f:
            json.dump({"id": vid, "url": f"https://youtu.be/{vid}",
                       "studio": f"https://studio.youtube.com/video/{vid}/edit"}, f, indent=2)

    upload(youtube(args.secrets), item, args, record)


if __name__ == "__main__":
    sys.exit(main())
