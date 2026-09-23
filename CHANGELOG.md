# Changelog

Every released version of BURN-IN, newest first. The desktop app and the phone app ship on their
own schedules, so they have their own version numbers: desktop releases are tagged `v<version>`,
phone releases `phone-v<version>`.

Dates are the day the release was tagged.

## Desktop

### 1.0.4 — 2026-09-23

**Fixed**

- The app crawled for the first few minutes after starting. It resumed every clip left unfinished
  by the last session, however old - 73 at once on this PC, close to an hour of model time on
  moments long past their moment. The queue flooded, every capture stalled while it all started,
  and the window felt frozen. Clips older than 15 minutes are now written off before they are
  queued, so a restart picks up where it left off and nothing more.

### 1.0.3 — 2026-09-23

**Fixed**

- The live viewer showed nothing. Stream segments start mid-audio-frame, so their audio was
  copied out with no valid profile; the browser's streaming player rejected the whole thing.
  The viewer's audio is now re-encoded (cached, about 0.16s a segment), and the player steps over
  the hairline gaps between segments instead of freezing in them.
- Storage no longer fills up in hours. 20.9 GB of raw cuts had piled up: rejected cuts were kept
  a day, the clean-up ran hourly, and every passing clip kept its source next to the finished
  video. Rejected cuts now go after 15 minutes, clean-up runs every 5, leftovers go after 30, and
  only the finished clip is kept.
- A platform that refuses your keys is asked once, not every minute. The Live tab says to paste
  them again, and discovery resumes as soon as you do.

**Changed**

- Titles and captions hook instead of narrate: short, payoff first, a question the clip answers.
  "Willneff Gets Confused Why Bits Went to His Chat, Not the Charity Stream" is now
  "Willneff's chat was LMAO about bits".
- The top bar no longer has the Local workspace label or the Desktop pet and Open clips buttons.

### 1.0.2 — 2026-09-22

Fixes for the app falling behind live and reporting dozens of clips "waiting".

**Fixed**

- A clip that hit an unexpected bug used to take its queue slot with it. The error escaped the
  task, so the clip kept its stage in the database, still counted as in flight, and was resumed
  on every start — forever. A handful of those was enough to hold the backlog failsafe on and
  stop capture altogether. Any unexpected error now writes the clip off, logs the bug in full,
  and the queue keeps moving.
- Clips are written to whichever of your drives is actually there. Listing a drive on the Storage
  page adds it to a pool: the first with room wins, and one that is missing, unplugged or full is
  skipped rather than breaking the write. Pulling a USB stick is no longer an outage.
- The code that picked that drive referred to `os` without importing it, so it raised on every
  single clip. This is what filled the queue after the previous release.
- A cut with no decodable audio — a silent stream, or a file truncated because a drive went away
  mid-write — crashed the transcriber instead of being judged. It is now treated as silence.
- Clips that can never finish are cleared at startup. A clip is only cuttable while its buffered
  video still exists, minutes not hours, but half-finished ones were resumed forever. Anything
  unfinished and older than **Give up on unfinished cuts after (h)** is written off.
- The stale-clip cleanup shipped in 1.0.1 was written against columns the database does not have
  and raised every time it ran. It works now, and is covered by a test.

**Changed**

- A stuck clip no longer holds the queue behind it. Only one clip is in the model at a time, so
  the first attempt now gets a short leash (**First try timeout (s)**, 70s) and only the retry
  gets the full one. The whole AI stage for one clip has a hard deadline (**Give up on a clip
  after (s)**, 240s), after which it is dropped and the next clip starts.
- Vision is cheaper, measured rather than guessed: 6 frames at 768px took 52.2s per clip, 4 at
  512px took 38.2s. The defaults follow the measurement.
- `vision_keep_alive` matches `keep_alive`, so an 8 GB model is no longer reloaded between stages.

### 1.0.1 — 2026-09-22

**Fixed**

- The installer would not compile at all: the firewall task's path contained a backslash-n that
  became a newline. It now runs `allow-phone-remote.cmd`, which ships beside the app and asks for
  administrator rights itself. That script took a `/quiet` flag so it does not wait for a keypress
  in a window nobody can see.
- The sideload feed only listed the release under `versions`. Older AltStore builds read flat
  fields beside it and refused with "version does not match". Both are written now, so any
  sideloader can install the phone app.

### 1.0.0 — 2026-09-20

First release.

- Watches the top Twitch and Kick streams and cuts from a rolling buffer when chat spikes.
- Transcribes with faster-whisper, judges and writes copy with a local multimodal model.
- Renders 9:16 captioned clips with ffmpeg and posts them on a schedule to YouTube, TikTok,
  Instagram, Facebook and Discord.
- Live viewer: click any stream to watch what BURN-IN sees, with chat and the hype gauges.
- System panel: events, issues and analytics behind the status card.
- Studio: restyle any clip with themes, quick settings and an advanced drawer.
- Safety: slurs and derogatory terms are masked, bleeped, or the clip is skipped.
- Judge profiles that adapt to your GPU, and automatic updates.

## Phone

### 1.0.16 — 2026-09-23

- The assistant works without a PC. The app opens straight away; Remote shows the sign-in until
  you connect, and Assistant runs on the phone on its own.
- New store page: full-size icon, four screenshots and a proper description.

### 1.0.13 — 2026-09-22

- The assistant runs on the phone itself. It reads the device's memory and chip and picks the
  largest model that will actually fit, then downloads it — resumably — instead of asking you to
  choose one.

### 1.0.12 — 2026-09-22

- The whole desk on your phone: live streams, clips, studio settings and the system panel.
- A master switch at the top swaps between driving the PC and talking to the assistant.

### 1.0.11 — 2026-09-21

- Connects over HTTPS to a Tailscale name, or plain HTTP to a home address, whichever you give
  it — so iOS stops refusing the connection without weakening anything.

### 1.0.10 and earlier — 2026-09-20

- First sideloadable builds: status, streams, clips and a post-now button, over your own network.
