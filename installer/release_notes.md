## BURN-IN 1.0.3

**Fixed**

- The live viewer plays again. Stream segments start mid-audio-frame, so their audio came out with
  no valid profile and the browser rejected the whole stream. Audio is now re-encoded for the
  viewer, and it steps over the tiny gaps between segments instead of freezing.
- Storage no longer fills up in hours - that was what backed the queue up. Rejected cuts now go
  after 15 minutes, clean-up runs every 5, and only the finished clip is kept.
- Keys a platform refuses are reported once instead of retried every minute.

**Changed**

- Titles and captions hook instead of narrate: short, payoff first.
- The top bar is simpler: no Local workspace label, Desktop pet or Open clips buttons.

The full history is in [CHANGELOG.md](https://github.com/Saizama-cmyk/BURN-IN-CLIP-BOT/blob/main/CHANGELOG.md).
