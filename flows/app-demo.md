---
name: app-demo
summary: A demo video of an app (web, terminal or desktop): you drive and record the app yourself, then narration and zooms.
status: ready
script: yes
inputs: the app (URL, binary, or repo plus how to run it) (required); what to show and for whom; length; language; formats (16:9, 9:16)
---

# App demo

## Steps
1. **Project:** `./lky new app-demo work/<name> --format 16:9 [--format 9:16]`.
2. **Explore the app** (run it, read its README, try the main paths). Use fake/demo data only: no real
   accounts, tokens, emails or customer data may appear on screen. If it needs a login, ask.
3. **Demo plan:** `script.json`, one line per step: `text` = narration, `actions` = what you'll do
   (capture.py -h): web `{"click": "text=Settings"}`, `{"type": ["#email", "ana@example.com"]}`,
   `{"goto": "/pricing"}`; terminal/desktop `{"run": "make test"}`, `{"key": "ctrl+s"}`, `{"wait": 1}`.
   - **APPROVAL:** the person approves the steps and the narration.
4. **Narration first:** `./tts.py -P work/<name>` (the capture then waits for each line's voice, so the
   voice never runs into the next action).
5. **Capture:** start the app, then
   - web: `./capture.py -P work/<name> --kind web --url http://localhost:3000`
   - CLI: `./capture.py -P work/<name> --kind terminal --cwd <demo folder>`
   - GUI: `./capture.py -P work/<name> --kind desktop --app "<command>"`
   It records on a virtual screen/browser (the person's desktop isn't touched), sets each line's `at`,
   puts the capture on the timeline and proposes zooms on every click and typing in `proposals/zooms.json`.
   A failing action stops it with the line and the reason: fix the selector and run it again.
6. **Zooms:** `./render.py -P work/<name> --preview --frames <the zooms' times>`; show the frames.
   - **APPROVAL:** the person OKs, edits or drops zooms in `proposals/zooms.json`; then
     `./capture.py -P work/<name> --apply`.
7. **Voice on the timeline:** `./tts.py -P work/<name> --place` (lines start at their `at`).
8. **End card (optional):** an image, or a rendered `title` scene (scenes.py), with a `dur` at the END of
   `timeline.video`. Not at the start: that would move the picture away from the voice's times.
9. **Render** every format: `./render.py -P work/<name>`. Optional: `./judge.py -P work/<name> --lines`.

## Checks
`./lky check -P work/<name>`; look at frames of every action for secrets on screen.
