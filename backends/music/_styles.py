"""Customizable music styles, chord progressions, and video-adaptive arrangement engine."""

import json
import os
import random

from backends.music._midi import MidiFile
from yt_common import CONFIG

NOTE_TO_PC = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11,
}

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "pentatonic_minor": [0, 3, 5, 7, 10],
}

# Built-in styles: GM program numbers (0-based), musical parameters, and ffmpeg mastering filters.
STYLES = {
    "lofi": {
        "description": "Warm chill electric piano, soft bass, swung dusty groove (great for coding/explainers)",
        "bpm": 82,
        "key": "F",
        "scale": "major",
        "swing": 0.14,
        "jitter": 0.024,
        "chords": [(0, "7"), (5, "m7"), (1, "m7"), (4, "7")],       # I - vi - ii - V
        "tension_chords": [(5, "m7"), (3, "7"), (1, "m7"), (4, "sus4")],
        "programs": {"chords": 4, "bass": 33, "arp": 25, "pad": 89},
        "volumes": {"chords": 96, "bass": 98, "arp": 66, "pad": 58, "drums": 78},
        "reverb": {"chords": 55, "bass": 10, "arp": 65, "pad": 80, "drums": 30},
        "drum_kit": "soft",
        "master_af": "highpass=f=38,lowpass=f=4400,equalizer=f=2200:t=q:w=1.2:g=-3.5,acompressor=threshold=-16dB:ratio=2.5:attack=15:release=180",
    },
    "ambient_tech": {
        "description": "Clean marimba/kalimba plucks, airy pad, gentle pulse (ideal for app demos and tutorials)",
        "bpm": 104,
        "key": "D",
        "scale": "dorian",
        "swing": 0.0,
        "jitter": 0.010,
        "chords": [(0, "m7"), (3, "7"), (5, "maj7"), (4, "sus2")],
        "tension_chords": [(0, "sus2"), (5, "m7"), (1, "m7"), (4, "sus4")],
        "programs": {"chords": 5, "bass": 38, "arp": 12, "pad": 89},
        "volumes": {"chords": 76, "bass": 86, "arp": 88, "pad": 72, "drums": 68},
        "reverb": {"chords": 65, "bass": 10, "arp": 70, "pad": 90, "drums": 35},
        "drum_kit": "minimal",
        "master_af": "highpass=f=35,lowpass=f=8500,equalizer=f=2400:t=q:w=1.3:g=-4.0,aecho=0.8:0.75:35:0.18",
    },
    "synthwave": {
        "description": "Driving 8th-note synth bass, retro poly chords, bright arp (great for Shorts and reveals)",
        "bpm": 112,
        "key": "A",
        "scale": "minor",
        "swing": 0.0,
        "jitter": 0.006,
        "chords": [(0, "m7"), (5, "maj7"), (2, "maj7"), (6, "7")],   # i - VI - III - VII
        "tension_chords": [(0, "m7"), (3, "m7"), (5, "maj7"), (4, "7")],
        "programs": {"chords": 90, "bass": 39, "arp": 81, "pad": 95},
        "volumes": {"chords": 86, "bass": 102, "arp": 78, "pad": 64, "drums": 92},
        "reverb": {"chords": 55, "bass": 15, "arp": 60, "pad": 85, "drums": 40},
        "drum_kit": "punchy",
        "master_af": "highpass=f=35,lowpass=f=11000,equalizer=f=2200:t=q:w=1.2:g=-3.0,acompressor=threshold=-14dB:ratio=3:attack=8:release=120",
    },
    "minimal": {
        "description": "Sparse felt piano and subtle pluck with almost no drums (max voice clarity)",
        "bpm": 94,
        "key": "C",
        "scale": "major",
        "swing": 0.04,
        "jitter": 0.018,
        "chords": [(0, "maj7"), (4, "sus2"), (5, "m7"), (3, "maj7")],
        "tension_chords": [(5, "m7"), (3, "maj7"), (1, "m7"), (4, "sus4")],
        "programs": {"chords": 0, "bass": 32, "arp": 45, "pad": 88},
        "volumes": {"chords": 84, "bass": 76, "arp": 72, "pad": 54, "drums": 48},
        "reverb": {"chords": 70, "bass": 15, "arp": 55, "pad": 85, "drums": 25},
        "drum_kit": "shaker",
        "master_af": "highpass=f=45,lowpass=f=6000,equalizer=f=2000:t=q:w=1.4:g=-4.0",
    },
    "tension": {
        "description": "Minor pulsing sub bass and staccato strings for bugs, warnings, and dramatic hooks",
        "bpm": 116,
        "key": "E",
        "scale": "phrygian",
        "swing": 0.0,
        "jitter": 0.008,
        "chords": [(0, "m7"), (1, "maj7"), (0, "m7"), (4, "sus4")],
        "tension_chords": [(0, "m7"), (1, "maj7"), (3, "m7"), (4, "7")],
        "programs": {"chords": 91, "bass": 39, "arp": 45, "pad": 92},
        "volumes": {"chords": 74, "bass": 100, "arp": 84, "pad": 74, "drums": 84},
        "reverb": {"chords": 75, "bass": 10, "arp": 50, "pad": 95, "drums": 45},
        "drum_kit": "tension",
        "master_af": "highpass=f=32,lowpass=f=7500,equalizer=f=2300:t=q:w=1.2:g=-3.5,acompressor=threshold=-15dB:ratio=3:attack=10:release=140",
    },
    "chiptune": {
        "description": "Retro 8-bit square/saw arpeggios and crisp pulse drums",
        "bpm": 126,
        "key": "G",
        "scale": "major",
        "swing": 0.0,
        "jitter": 0.0,
        "chords": [(0, "maj"), (4, "maj"), (5, "min"), (3, "maj")],
        "tension_chords": [(5, "min"), (3, "maj"), (1, "min"), (4, "maj")],
        "programs": {"chords": 80, "bass": 38, "arp": 81, "pad": 80},
        "volumes": {"chords": 78, "bass": 92, "arp": 76, "pad": 52, "drums": 84},
        "reverb": {"chords": 15, "bass": 5, "arp": 20, "pad": 25, "drums": 10},
        "drum_kit": "punchy",
        "master_af": "highpass=f=50,lowpass=f=9000,equalizer=f=2400:t=q:w=1.2:g=-3.5",
    },
}


def load_styles():
    """Merge built-in styles with user overrides from ~/.config/lowkeyoutuber/music_styles.json."""
    merged = {k: dict(v) for k, v in STYLES.items()}
    path = os.path.expanduser(CONFIG.get("MUSIC_STYLES") or "~/.config/lowkeyoutuber/music_styles.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                custom = json.load(f)
            if isinstance(custom, dict):
                for name, spec in custom.items():
                    if isinstance(spec, dict):
                        base = dict(merged.get(spec.get("extends", name), merged["lofi"]))
                        for sub in ("programs", "volumes", "reverb"):
                            if isinstance(spec.get(sub), dict):
                                base[sub] = {**base.get(sub, {}), **spec[sub]}
                        for k, v in spec.items():
                            if k not in ("programs", "volumes", "reverb", "extends"):
                                base[k] = v
                        merged[name] = base
        except (OSError, ValueError):
            pass
    return merged


def style_from_prompt(prompt, styles=None):
    """Pick the best matching style name from a natural-language prompt."""
    styles = styles or load_styles()
    low = (prompt or "").lower()
    if low in styles:
        return low
    aliases = {
        "lofi": ("lofi", "lo-fi", "chill", "study", "relax", "calm", "coding", "warm", "jazz"),
        "ambient_tech": ("ambient", "tech", "demo", "clean", "modern", "tutorial", "product", "explain"),
        "synthwave": ("synthwave", "retro", "energetic", "upbeat", "fast", "hype", "neon", "hook", "short"),
        "minimal": ("minimal", "piano", "quiet", "subtle", "gentle", "soft", "voiceover", "background"),
        "tension": ("tension", "bug", "error", "dark", "suspense", "debug", "warning", "dramatic", "problem"),
        "chiptune": ("chiptune", "8-bit", "8bit", "arcade", "game", "pixel", "fun"),
    }
    scores = {name: sum(w in low for w in words) for name, words in aliases.items() if name in styles}
    best = max(scores, key=lambda k: scores[k]) if scores else "lofi"
    return best if scores.get(best, 0) > 0 else "lofi"


def _scale_pcs(key, scale_name):
    root = NOTE_TO_PC.get((key or "C").strip().upper(), 0)
    intervals = SCALES.get(scale_name, SCALES["major"])
    return root, [(root + i) % 12 for i in intervals]


def _chord_pitches(root_pc, scale_pcs, degree, quality):
    idx = degree % len(scale_pcs)
    bass_pc = scale_pcs[idx]
    third = scale_pcs[(idx + 2) % len(scale_pcs)]
    fifth = scale_pcs[(idx + 4) % len(scale_pcs)]
    seventh = scale_pcs[(idx + 6) % len(scale_pcs)]
    ninth = scale_pcs[(idx + 1) % len(scale_pcs)]
    if quality == "sus2":
        pcs = [bass_pc, ninth, fifth, seventh]
    elif quality == "sus4":
        fourth = scale_pcs[(idx + 3) % len(scale_pcs)]
        pcs = [bass_pc, fourth, fifth, seventh]
    elif quality in ("maj", "min"):
        pcs = [bass_pc, third, fifth]
    elif quality in ("m9", "maj9"):
        pcs = [bass_pc, third, fifth, seventh, ninth]
    else:
        pcs = [bass_pc, third, fifth, seventh]
    return bass_pc, pcs


def _voice_lead(prev_notes, pcs, lo=52, hi=74):
    """Arrange chord pitch classes near the previous voicing for smooth transitions."""
    if not prev_notes:
        out = []
        for pc in pcs:
            n = lo + ((pc - lo) % 12)
            while n in out and n + 12 <= hi:
                n += 12
            out.append(n)
        return sorted(out)
    center = sum(prev_notes) / len(prev_notes)
    out = []
    for pc in pcs:
        cands = [n for n in range(lo, hi + 1) if n % 12 == pc]
        if not cands:
            cands = [lo + ((pc - lo) % 12)]
        best = min(cands, key=lambda n: (abs(n - center), n in out))
        out.append(best)
    return sorted(set(out))


def _section_at(sections, sec_t, default_energy=0.6):
    for s in sections or ():
        if float(s.get("start", 0)) <= sec_t < float(s.get("end", 1e9)):
            return s
    return {"energy": default_energy, "mood": "normal"}


def _drum_hit(midi, drum_tr, beat, kit_note, vel, swing, jitter):
    frac = beat - int(beat)
    sw = swing * 0.5 if abs(frac - 0.5) < 0.05 else 0.0
    midi.add_note(drum_tr, beat + sw, 0.2, kit_note, vel, jitter=jitter, vel_jitter=6)


def _add_drum_bar(midi, drum_tr, bar_beat, kit, energy, swing, jitter, riser=False):
    kick, snare, rim, chh, ohh, crash = 36, 38, 37, 42, 46, 49
    for step in range(8):  # 8th notes across a 4/4 bar
        b = bar_beat + step * 0.5
        if kit == "shaker":
            if step % 2 == 1 and energy > 0.3:
                _drum_hit(midi, drum_tr, b, 70, int(35 + 25 * energy), swing, jitter)
            continue
        if kit == "minimal":
            if step in (0, 5) and energy >= 0.45:
                _drum_hit(midi, drum_tr, b, kick, int(55 + 25 * energy), swing, jitter)
            if step == 4 and energy >= 0.5:
                _drum_hit(midi, drum_tr, b, rim, int(48 + 20 * energy), swing, jitter)
            if energy >= 0.65:
                _drum_hit(midi, drum_tr, b, chh, 36 if step % 2 else 48, swing, jitter)
        elif kit == "tension":
            if step in (0, 3, 6):
                _drum_hit(midi, drum_tr, b, kick, int(68 + 30 * energy), 0.0, jitter)
            if step == 4 and energy >= 0.55:
                _drum_hit(midi, drum_tr, b, 45, int(60 + 25 * energy), 0.0, jitter)
            if energy >= 0.6:
                _drum_hit(midi, drum_tr, b, chh, 42 if step % 2 else 56, 0.0, jitter)
        elif kit == "punchy":
            if step in (0, 4) or (step == 6 and energy > 0.7):
                _drum_hit(midi, drum_tr, b, kick, int(80 + 28 * energy), swing, jitter)
            if step in (2, 6):
                _drum_hit(midi, drum_tr, b, snare, int(74 + 28 * energy), swing, jitter)
            _drum_hit(midi, drum_tr, b, ohh if step == 7 and energy > 0.75 else chh,
                      58 if step % 2 == 0 else 40, swing, jitter)
        else:  # "soft" (lofi)
            if step == 0 or (step == 5 and energy > 0.55):
                _drum_hit(midi, drum_tr, b, kick, int(65 + 25 * energy), swing, jitter)
            if step in (2, 6) and energy >= 0.4:
                _drum_hit(midi, drum_tr, b, snare if energy > 0.65 else rim, int(55 + 24 * energy), swing, jitter)
            if energy >= 0.45:
                _drum_hit(midi, drum_tr, b, chh, 46 if step % 2 == 0 else 32, swing, jitter)
    if riser:
        for i in range(4):
            _drum_hit(midi, drum_tr, bar_beat + 3.0 + i * 0.25, snare, 45 + i * 16, 0.0, jitter * 0.5)
        _drum_hit(midi, drum_tr, bar_beat + 4.0, crash, 78, 0.0, 0.0)


def build_midi(out_mid, duration=30.0, style="lofi", bpm=None, key=None, scale=None,
               sections=None, seed=0, overrides=None):
    """Generate a video-adaptive multi-track MIDI file and return (style_spec, metadata)."""
    styles = load_styles()
    st_name = style if style in styles else style_from_prompt(style, styles)
    st = dict(styles[st_name])
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                st[k] = v

    bpm = float(bpm or st["bpm"])
    key = key or st["key"]
    scale_name = scale or st["scale"]
    swing = float(st.get("swing", 0.0))
    jitter = float(st.get("jitter", 0.01))
    duration = max(float(duration or 30.0), 4.0)

    root_pc, scale_pcs = _scale_pcs(key, scale_name)
    beats_per_sec = bpm / 60.0
    # Leave ~1.2s at the end for the final resolving chord to ring out cleanly
    music_dur = max(duration - 1.2, duration * 0.8)
    n_bars = max(1, int(round((music_dur * beats_per_sec) / 4.0)))
    bar_sec = 4.0 / beats_per_sec

    midi = MidiFile(bpm=bpm, seed=seed)
    vols, revs, progs = st["volumes"], st["reverb"], st["programs"]
    tr_chords = midi.add_track("Chords", 0, progs["chords"], vols["chords"], pan=58, reverb=revs["chords"])
    tr_bass = midi.add_track("Bass", 1, progs["bass"], vols["bass"], pan=64, reverb=revs["bass"])
    tr_arp = midi.add_track("Arp", 2, progs["arp"], vols["arp"], pan=74, reverb=revs["arp"])
    tr_pad = midi.add_track("Pad", 3, progs["pad"], vols["pad"], pan=52, reverb=revs["pad"])
    tr_drums = midi.add_track("Drums", 9, None, vols["drums"], pan=64, reverb=revs["drums"])

    prev_voicing = None
    rng = random.Random(seed)
    for bar in range(n_bars):
        bar_beat = bar * 4.0
        sec_t = bar * bar_sec
        sec = _section_at(sections, sec_t)
        energy = float(sec.get("energy", 0.6))
        mood = (sec.get("mood") or "normal").lower()
        is_last = (bar == n_bars - 1)

        prog = st["tension_chords"] if mood in ("tension", "bug", "warning") else st["chords"]
        deg, qual = prog[bar % len(prog)]
        if is_last:
            deg, qual = prog[0]  # Always resolve to tonic on the final bar

        bass_pc, pcs = _chord_pitches(root_pc, scale_pcs, deg, qual)
        voicing = _voice_lead(prev_voicing, pcs)
        prev_voicing = voicing

        # 1. Pad: holds across the bar for harmonic continuity
        if sec.get("pad", True) and (energy >= 0.2 or is_last):
            pad_vel = int(42 + 32 * energy)
            pad_dur = 5.2 if is_last else 3.95
            for n in voicing[:3]:
                midi.add_note(tr_pad, bar_beat, pad_dur, n, pad_vel, jitter=jitter * 0.5, vel_jitter=3)

        # 2. Chords: warm comping or resolving final hit
        if sec.get("chords", True):
            c_vel = int(52 + 38 * energy)
            if is_last:
                for i, n in enumerate(voicing):
                    midi.add_note(tr_chords, bar_beat + i * 0.03, 5.0, n, max(50, c_vel - 8), jitter=jitter)
            else:
                hits = [0.0, 2.5] if energy >= 0.5 else [0.0]
                for h in hits:
                    dur_c = 1.9 if len(hits) == 2 else 3.6
                    for i, n in enumerate(voicing):
                        midi.add_note(tr_chords, bar_beat + h + i * 0.015, dur_c, n, c_vel,
                                      jitter=jitter, vel_jitter=5)

        # 3. Bass: root + fifth movement scaled by energy
        if sec.get("bass", energy >= 0.35):
            bass_root = 36 + ((bass_pc - 36) % 12)
            bass_fifth = bass_root + 7 if bass_root + 7 <= 55 else bass_root - 5
            b_vel = int(64 + 34 * energy)
            if is_last:
                midi.add_note(tr_bass, bar_beat, 4.8, bass_root, b_vel, jitter=jitter)
            elif st_name == "synthwave" or mood in ("tension", "build"):
                for step in range(8):
                    p = bass_root if step % 4 != 3 else bass_fifth
                    midi.add_note(tr_bass, bar_beat + step * 0.5, 0.42, p,
                                  b_vel if step % 2 == 0 else int(b_vel * 0.8), jitter=jitter, vel_jitter=4)
            else:
                midi.add_note(tr_bass, bar_beat, 1.8, bass_root, b_vel, jitter=jitter, vel_jitter=4)
                if energy >= 0.45:
                    midi.add_note(tr_bass, bar_beat + 2.0, 0.9, bass_fifth, int(b_vel * 0.85), jitter=jitter)
                if energy >= 0.65:
                    midi.add_note(tr_bass, bar_beat + 3.0, 0.85, bass_root, int(b_vel * 0.8), jitter=jitter)

        # 4. Arp / melody plucks: pulls back during "explain" so speech stays uncluttered
        want_arp = sec.get("arp")
        if want_arp is None:
            want_arp = (energy >= 0.45 and mood != "explain" and not is_last)
        if want_arp:
            arp_notes = [n + 12 for n in voicing]
            step_beats = 0.5 if energy >= 0.65 else 1.0
            steps = int(4.0 / step_beats)
            for s in range(steps):
                if rng.random() < 0.15 and mood != "hook":
                    continue
                b = bar_beat + s * step_beats
                sw = swing * 0.5 if step_beats == 0.5 and s % 2 == 1 else 0.0
                pitch = arp_notes[s % len(arp_notes)]
                midi.add_note(tr_arp, b + sw, step_beats * 0.85, pitch, int(48 + 30 * energy),
                              jitter=jitter, vel_jitter=6)

        # 5. Drums: follow section energy and explicit overrides
        want_drums = sec.get("drums")
        if want_drums is None:
            want_drums = (energy >= 0.42 and not is_last)
        if want_drums:
            next_sec = _section_at(sections, (bar + 1) * bar_sec)
            riser = bool(sec.get("riser")) or (next_sec.get("energy", 0.5) - energy >= 0.3)
            _add_drum_bar(midi, tr_drums, bar_beat, st.get("drum_kit", "soft"), energy, swing, jitter, riser=riser)

    midi.write(out_mid)
    return st, {"style": st_name, "bpm": bpm, "key": key, "scale": scale_name, "bars": n_bars,
                "duration": round(n_bars * bar_sec + 1.2, 3)}
