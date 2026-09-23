## BURN-IN 1.0.2

Fixes for the app falling behind live and reporting dozens of clips "waiting".

**Fixed**

- A clip that hit an unexpected bug used to take its queue slot with it, permanently. The error
  escaped, so the clip kept its stage, still counted as in flight, and was resumed on every start
  — forever. A handful of those held the backlog failsafe on and stopped capture altogether. Any
  unexpected error now writes the clip off, logs the bug in full, and the queue keeps moving.
- Clips are written to whichever of your drives is actually there. Adding a drive on the Storage
  page puts it in a pool: the first with room wins, and one that is missing, unplugged or full is
  skipped rather than breaking the write. Pulling a USB stick is no longer an outage.
- The code that picked that drive used `os` without importing it, so it raised on every clip.
- A cut with no decodable audio — a silent stream, or a file truncated because a drive went away
  mid-write — crashed the transcriber instead of being judged. It is treated as silence now.
- Clips that can never finish are cleared at startup instead of being resumed forever.

**Changed**

- A stuck clip no longer holds the queue behind it: a short leash on the first model attempt, the
  full timeout only on the retry, and a hard deadline for the whole AI stage.
- Vision is cheaper, measured rather than guessed — 4 frames at 512px instead of 6 at 768px, which
  measured 38.2s per clip against 52.2s.
- The judge's model stays loaded between stages instead of being evicted and reloaded.

The full history is in [CHANGELOG.md](https://github.com/Saizama-cmyk/BURN-IN-CLIP-BOT/blob/main/CHANGELOG.md).
