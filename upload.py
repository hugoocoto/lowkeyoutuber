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
"""

import argparse
import json
import os
import re
import sys
import time

from yt_common import CONFIG_DIR as CONF, die, log

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


def main():
    p = argparse.ArgumentParser(description="Upload a publish.sh folder to YouTube (as private by default).")
    p.add_argument("folder")
    p.add_argument("--video", help="video file (default: <folder>/video.mp4)")
    p.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    p.add_argument("--publish-at", help="schedule, ISO 8601 UTC like 2026-11-02T17:00:00Z (implies private)")
    p.add_argument("--category", default="22",
                   help="category id: 22 People & Blogs (default), 27 Education, 28 Science & Tech, 20 Gaming")
    p.add_argument("--secrets", default=os.path.join(CONF, "client_secret.json"), help="OAuth client JSON")
    p.add_argument("--force", action="store_true", help="upload even if uploaded.json exists")
    p.add_argument("--dry-run", action="store_true", help="only check the folder and show what would be uploaded")
    args = p.parse_args()

    folder = args.folder
    video = args.video or os.path.join(folder, "video.mp4")
    if not os.path.isfile(video):
        die(f"no video at {video}")
    done = os.path.join(folder, "uploaded.json")
    if os.path.isfile(done) and not args.force and not args.dry_run:
        die(f"already uploaded ({read(folder, 'uploaded.json')}). Use --force to upload again.")

    clean = lambda s: re.sub(r"[<>]", "", s)   # YouTube rejects < and >
    title = next((line.strip() for line in read(folder, "titles.txt").splitlines() if line.strip()), "")
    title = clean(title)[:100] or os.path.splitext(os.path.basename(video))[0]
    description = clean(read(folder, "description.txt")).encode("utf-8")[:5000].decode("utf-8", "ignore")
    tags = [t.strip() for t in read(folder, "tags.txt").split(",") if t.strip()]
    language = ""
    words_json = os.path.splitext(video)[0] + ".words.json"
    if os.path.isfile(words_json):
        with open(words_json, encoding="utf-8") as f:
            language = json.load(f).get("language", "")

    srt = os.path.splitext(video)[0] + ".srt"
    thumb = next((os.path.join(folder, n) for n in ("thumbnail.jpg", "thumbnail.png")
                  if os.path.isfile(os.path.join(folder, n))), None)
    problems = []
    if not os.path.isfile(os.path.join(folder, "titles.txt")):
        problems.append("no titles.txt: the file name will be the title")
    if not description:
        problems.append("empty description")
    if thumb and os.path.getsize(thumb) > 2 * 1024 * 1024:
        problems.append("thumbnail is over YouTube's 2 MB limit")
    log(f"Title:       {title}"
        f"\nPrivacy:     {'scheduled ' + args.publish_at if args.publish_at else args.privacy}"
        f"\nDescription: {len(description)} characters, {'with' if re.search(r'^0:00 ', description, re.M) else 'no'} chapters"
        f"\nTags:        {len(tags)}\nLanguage:    {language or '?'}"
        f"\nSubtitles:   {'yes' if os.path.isfile(srt) else 'no'}"
        f"\nThumbnail:   {os.path.basename(thumb) if thumb else 'none (YouTube picks a frame)'}")
    for problem in problems:
        log(f"Warning: {problem}")
    if args.dry_run:
        return 1 if problems else 0

    yt = youtube(args.secrets)
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    snippet = {"title": title, "description": description, "tags": tags, "categoryId": args.category}
    if language:
        snippet["defaultLanguage"] = snippet["defaultAudioLanguage"] = language
    status = {"privacyStatus": "private" if args.publish_at else args.privacy, "selfDeclaredMadeForKids": False}
    if args.publish_at:
        status["publishAt"] = args.publish_at
    try:
        resp = resumable(yt.videos().insert(
            part="snippet,status", body={"snippet": snippet, "status": status},
            media_body=MediaFileUpload(video, chunksize=16 * 1024 * 1024, resumable=True)))
    except HttpError as e:
        die(f"upload failed: {e}")
    vid = resp["id"]
    url = f"https://youtu.be/{vid}"
    with open(done, "w", encoding="utf-8") as f:
        json.dump({"id": vid, "url": url, "studio": f"https://studio.youtube.com/video/{vid}/edit"}, f, indent=2)
    log(f"Uploaded: {url}")

    if os.path.isfile(srt):
        try:
            yt.captions().insert(part="snippet",
                                 body={"snippet": {"videoId": vid, "language": language or "en", "name": ""}},
                                 media_body=MediaFileUpload(srt, mimetype="application/octet-stream")).execute()
            log("Subtitles added.")
        except HttpError as e:
            log(f"Subtitles failed (add them in Studio): {e}")

    if thumb:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb)).execute()
            log("Thumbnail set.")
        except HttpError as e:
            log(f"Thumbnail failed (is the channel phone-verified?): {e}")
    else:
        log("No thumbnail.jpg in the folder: YouTube will pick a frame. Set one in Studio.")

    log(f"\nCheck it, then publish: https://studio.youtube.com/video/{vid}/edit")


if __name__ == "__main__":
    sys.exit(main())
