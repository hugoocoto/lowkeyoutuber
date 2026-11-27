"""Turn media references into what multimodal backends send: frames as JPEG, short clips, audio."""

import base64
import os
import subprocess
import tempfile


def frames(video, times, width=768):
    """JPEG bytes of the frames at these seconds."""
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            f = os.path.join(tmp, f"{i}.jpg")
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", video,
                            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", f], capture_output=True)
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    out.append(fh.read())
    return out


def clip(video, start, end, width=640, fps=4):
    """A small MP4 of [start, end] (low fps and size: multimodal APIs bill by frames)."""
    with tempfile.TemporaryDirectory() as tmp:
        f = os.path.join(tmp, "c.mp4")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                        "-i", video, "-vf", f"fps={fps},scale={width}:-2", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "30", "-c:a", "aac", "-b:a", "64k", "-ac", "1", f], capture_output=True)
        with open(f, "rb") as fh:
            return fh.read()


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def b64(data):
    return base64.b64encode(data).decode("ascii")


def expand(inputs, max_frames=6):
    """Normalize judge/embed inputs to a list of parts:
    {"text": str} | {"image": path} | {"jpeg": bytes} | {"video": path, "start"?, "end"?}
    Videos become clips for backends that take video, or evenly spaced frames otherwise."""
    parts = []
    for it in inputs if isinstance(inputs, list) else [inputs]:
        if isinstance(it, str):
            parts.append({"text": it})
        else:
            parts.append(it)
    return parts


def as_frames(part, max_frames=6):
    """JPEG frames for a video part (for backends without video input)."""
    v = part["video"]
    a = float(part.get("start", 0))
    b = float(part.get("end") or duration(v))
    n = max(1, min(max_frames, int((b - a) / 1.5) + 1))
    times = [a + (b - a) * (i + 0.5) / n for i in range(n)]
    return frames(v, times)
