---
name: app-demo
summary: A demo video of an app (web, CLI or desktop) from the app itself, with narration and zooms.
status: partial
script: yes
inputs: the app (URL, binary, or repo plus how to run it) (required); what to show and for whom; length; language; formats (16:9, 9:16)
---

# App demo

## Steps
1. **Project:** `./lky new app-demo work/<name> --format 16:9 [--format 9:16]`.
2. **Explore the app** (run it, read its README, try the main paths). Use fake/demo data only:
   no real accounts, tokens, emails or customer data may appear on screen. If it needs a login, ask.
3. **Demo plan:** write `script.json`: one line per step, `text` = narration, plus `"action"` = what
   happens on screen (`"open /settings, toggle dark mode"`).
   - **APPROVAL:** the person approves the steps and the narration.
4. **Capture:** until capture.py exists (issue #1 M5), the person records the screen following the
   plan (OBS), at 2560x1440 or 1920x1080 with large fonts; for a terminal app you may produce the
   recording yourself with a terminal recorder that's installed (e.g. VHS tape files), if the person
   agrees. Then `./lky add-media -P work/<name> capture.mp4 --id cap`.
5. **Times:** find when each action happens in the capture (frames + the plan) and set each line's
   `"at"` (seconds in the capture). Then continue exactly like flows/voice-over.md steps 4-6, using
   `cap` as the video, and add zooms on the clicked or typed area for each action.
6. **Title and end card (optional):** images with `dur` at the start and end of `timeline.video`.
7. **Render** both formats: `./render.py -P work/<name>`.

## Checks
`./lky check -P work/<name>`; look at frames of every action for secrets on screen.

## Not built yet
- capture.py: the agent drives and records the app itself (Playwright, terminal, Xvfb) and writes an
  event log of clicks, so zooms land exactly where the click happened (issue #1 M5).
