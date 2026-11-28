"""Minimal Standard MIDI File (SMF Type 1) writer with humanization helpers."""

import random
import struct


def _vlq(value):
    """Encode a non-negative integer as MIDI Variable-Length Quantity."""
    value = max(0, int(value))
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


class Track:
    """One MIDI track bound to a channel (0-15; channel 9 is General MIDI drums)."""

    def __init__(self, name, channel=0, program=None, volume=100, pan=64, reverb=40, chorus=15):
        self.name = name
        self.channel = channel & 0x0F
        self.events = []  # (tick, sort_order, bytes)
        meta_name = self.name.encode("ascii", "replace")
        self.events.append((0, 0, b"\xFF\x03" + _vlq(len(meta_name)) + meta_name))
        if program is not None and self.channel != 9:
            self.events.append((0, 1, bytes([0xC0 | self.channel, program & 0x7F])))
        self.cc(0, 7, volume, order=2)
        self.cc(0, 10, pan, order=2)
        self.cc(0, 91, reverb, order=2)
        self.cc(0, 93, chorus, order=2)

    def cc(self, tick, controller, value, order=3):
        t = max(0, int(round(tick)))
        self.events.append((t, order, bytes([0xB0 | self.channel, controller & 0x7F, max(0, min(127, int(value)))])))

    def note(self, start_tick, dur_tick, pitch, velocity=80):
        s = max(0, int(round(start_tick)))
        d = max(1, int(round(dur_tick)))
        p = max(0, min(127, int(pitch)))
        v = max(1, min(127, int(velocity)))
        # Order note-off (2) before note-on (4) at identical ticks so re-triggered notes don't clip
        self.events.append((s, 4, bytes([0x90 | self.channel, p, v])))
        self.events.append((s + d, 2, bytes([0x80 | self.channel, p, 0])))

    def encode(self):
        ordered = sorted(self.events, key=lambda e: (e[0], e[1]))
        body = bytearray()
        prev = 0
        for tick, _, msg in ordered:
            body += _vlq(tick - prev)
            body += msg
            prev = tick
        body += b"\x00\xFF\x2F\x00"  # End of Track
        return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


class MidiFile:
    """Multi-track SMF Type 1 builder with beat-based scheduling and subtle humanization."""

    def __init__(self, bpm=90.0, ppq=480, seed=0):
        self.bpm = float(bpm)
        self.ppq = int(ppq)
        self.rng = random.Random(seed)
        self.conductor = []  # (tick, sort_order, bytes)
        self.tracks = []
        self.set_tempo(0.0, self.bpm)

    def ticks(self, beats):
        return int(round(float(beats) * self.ppq))

    def set_tempo(self, beat, bpm):
        us_per_beat = max(100000, min(2000000, int(round(60_000_000 / float(bpm)))))
        t = max(0, self.ticks(beat))
        self.conductor.append((t, 0, b"\xFF\x51\x03" + struct.pack(">I", us_per_beat)[1:]))

    def add_track(self, name, channel=0, program=None, volume=100, pan=64, reverb=40, chorus=15):
        tr = Track(name, channel=channel, program=program, volume=volume, pan=pan, reverb=reverb, chorus=chorus)
        self.tracks.append(tr)
        return tr

    def add_note(self, track, beat, dur_beats, pitch, velocity=80, jitter=0.0, vel_jitter=0):
        j_tick = int(round(self.rng.uniform(-jitter, jitter) * self.ppq)) if jitter > 0 else 0
        v_delta = self.rng.randint(-vel_jitter, vel_jitter) if vel_jitter > 0 else 0
        s = max(0, self.ticks(beat) + j_tick)
        d = max(self.ppq // 32, self.ticks(dur_beats))
        track.note(s, d, pitch, max(1, min(127, int(velocity) + v_delta)))

    def add_cc(self, track, beat, controller, value):
        track.cc(self.ticks(beat), controller, value)

    def _encode_conductor(self):
        ordered = sorted(self.conductor, key=lambda e: (e[0], e[1]))
        body = bytearray()
        prev = 0
        for tick, _, msg in ordered:
            body += _vlq(tick - prev)
            body += msg
            prev = tick
        body += b"\x00\xFF\x2F\x00"
        return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)

    def to_bytes(self):
        all_tracks = [self._encode_conductor()] + [tr.encode() for tr in self.tracks]
        header = b"MThd" + struct.pack(">IHHH", 6, 1, len(all_tracks), self.ppq)
        return header + b"".join(all_tracks)

    def write(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())
        return path
