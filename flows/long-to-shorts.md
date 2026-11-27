---
name: long-to-shorts
summary: Make vertical Shorts (9:16, word-by-word captions) from the best moments of a long video.
status: ready
script: no
inputs: the long video (required); how many Shorts and how long (default 5 of 30-60 s); facecam box (config CAM); layout (stack, screen or cam)
---

# Shorts from a long video

## Steps
1. **Check the layout on one frame:** `python clips.py video.mp4 --preview -o <out>/shorts` and look at
   `preview.jpg`. A wrong facecam box gives an empty facecam: `clips.py` warns and saves `cam_check.jpg`.
2. **Propose:** `python clips.py video.mp4 --propose -o <out>/shorts [--count 5 --min 30 --max 60]`.
   It writes `clips.json` (start, end, title, summary, score) without rendering.
3. **Review the proposals yourself first:** read the transcript around each one. Drop clips that need
   earlier context, that cross a hard cut, or that end mid-action. With a judge backend,
   `./judge.py --clips <out>/shorts/clips.json --video video.mp4` adds a `judge` score to each clip
   (hook, works without context, ends cleanly) and lists the ones that fail.
   - **APPROVAL:** show the person the remaining proposals (times, title, one-line summary); they pick.
     Times can be edited in `clips.json` before rendering.
4. **Render the approved ones:** `python clips.py video.mp4 -o <out>/shorts --pick 1,3`.
5. **Look at them:** extract 3 frames of each Short (start, middle, end) and check captions don't cover
   the face or the code, and that the last frame isn't mid-sentence or mid-action.
6. **Upload (optional), after the long video:** `python upload.py --shorts <publish folder>` (titles come
   from `clips.json`, each Short links to the full video).

## Limits
- clips.py doesn't zoom inside Shorts: for code-heavy clips prefer the `screen` layout, or rebuild the clip
  as a project timeline with zooms and render it with render.py at 9:16.
