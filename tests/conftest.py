import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def ff(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


@pytest.fixture
def media(tmp_path):
    """Small synthetic inputs: a 6 s 1280x720 video with a tone, a 10 s tone, a square image."""
    d = tmp_path / "src"
    d.mkdir()
    ff("-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=6", "-f", "lavfi", "-i", "sine=f=300:d=6",
       "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(d / "rec.mp4"))
    ff("-f", "lavfi", "-i", "sine=f=500:d=10", str(d / "music.wav"))
    ff("-f", "lavfi", "-i", "testsrc=s=800x800", "-frames:v", "1", str(d / "card.png"))
    return d


@pytest.fixture(autouse=True)
def cpu_encoder(monkeypatch):
    """Tests run anywhere: force the CPU encoder path."""
    import yt_common
    monkeypatch.setattr(yt_common, "_NVENC", False)


def probe(path):
    import json
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)
