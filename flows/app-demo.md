---
name: app-demo
summary: A demo video of an app (web, terminal or desktop): you drive and record the app yourself, then narration and zooms.
status: ready
script: yes
inputs: the app (URL, binary, or repo plus how to run it) (required); what to show and for whom; where it will be posted; length; language; formats (16:9, 9:16, 1:1, 4:5)
---

# App demo

## Steps
1. **Project:** `lky new work/<name> --flow app-demo --format 16:9 [--format 9:16]`.
2. **Explore the app** (run it, read its README, try the main paths). Use fake/demo data only: no real
   accounts, tokens, emails or customer data may appear on screen. If it needs a login, ask.
3. **Demo plan:** `script.json`, one line per step: `text` = narration, `actions` = what you'll do
   (capture.py -h): web `{"click": "text=Settings"}`, `{"type": ["#email", "ana@example.com"]}`,
   `{"goto": "/pricing"}`; terminal/desktop `{"run": "make test"}`, `{"key": "ctrl+s"}`, `{"wait": 1}`.
   - **Check-in:** the person approves the steps and the narration.
4. **Narration first:** `lky run tts.py -P work/<name>` (the capture then waits for each line's voice, so the
   voice never runs into the next action).
5. **Capture:** start the app, then
   - web: `lky run capture.py -P work/<name> --kind web --url http://localhost:3000`
   - CLI: `lky run capture.py -P work/<name> --kind terminal --cwd <demo folder>`
   - GUI: `lky run capture.py -P work/<name> --kind desktop --app "<command>"`
   It records on a virtual screen/browser (the person's desktop isn't touched), sets each line's `at`,
   puts the capture on the timeline and proposes zooms in `proposals/zooms.json` for clicks and typing
   into web elements. Not every one gets a zoom: actions close in time are merged, and a zoom shorter
   than 1 s is dropped. Desktop/terminal typing and `run` have no target on screen, so they get no
   proposed zoom. Compare the action log with `proposals/zooms.json` and add missing zooms by hand.
   An action that fails to run (selector not found, xdotool error) stops it with the line and the reason.
   It does NOT see a terminal command fail: `{"run": "make test"}` counts as done even if `make test` exits
   with an error, so look at the frames to check what each command printed.
   Before running it again after a failure, put the app back to its initial demo state: actions that
   already ran (a created record, a changed setting) would otherwise be repeated on top of it. Then fix
   the selector and run it again.
6. **Zooms:** the timeline has no zooms until `--apply`, so preview the proposals on a throwaway copy:
   `cp -r work/<name> work/<name>-zooms && lky run capture.py -P work/<name>-zooms --apply &&
   lky run render.py -P work/<name>-zooms --preview --frames <each zoom's at + dur/2>` (at `at` itself the zoom hasn't started yet); show the frames, then delete
   the copy.
   - **Check-in:** the person OKs, edits or drops zooms in `proposals/zooms.json` (of the real project);
     then `lky run capture.py -P work/<name> --apply`.
7. **Voice on the timeline:** `lky run tts.py -P work/<name> --place` (lines start at their `at`).
8. **End card (optional):** an image, or a rendered `title` scene (scenes.py), with a `dur` at the END of
   `timeline.video`. Not at the start: that would move the picture away from the voice's times.
9. **Render** every format: `lky run render.py -P work/<name>`. Optional: `lky run judge.py -P work/<name> --lines`.

## Checks
`lky check -P work/<name>`; look at frames of every action for secrets on screen.
