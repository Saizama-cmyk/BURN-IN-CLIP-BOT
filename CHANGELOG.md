# Changelog

Every released version of Ashvane (called BURN-IN until 1.1.0), newest first. The desktop app and the phone app ship on their
own schedules, so they have their own version numbers: desktop releases are tagged `v<version>`,
phone releases `phone-v<version>`.

Dates are the day the release was tagged.

## Desktop

### 1.1.0 — 2026-09-23

**New name: Ashvane.** Same app, same settings, same clips. The old name was hard to search,
clashed with a clothing trademark and said nothing good about the product.

**Better clips, fewer of them**

- The judge now scores the way a stranger scrolling past would, and the pass mark is 7 instead of
  6. It rejects moments where chat is the only story, low-stakes moments (a small in-game reward, a
  mispronounced word) and inside jokes. On the last 14 real moments it kept 3 instead of 10.
- Cuts open 1-2 seconds before the payoff and aim for 15-35 seconds: viewers decide in the first
  3 seconds whether to swipe.
- Titles are written to make people stop scrolling, not to describe the clip. The writer no longer
  copies the judge's plain description, never talks about chat (viewers of a Short can't see it)
  and is sent back once to rewrite when it writes "reacts to", "rants about" or "chat goes wild".
- The title box over the top of finished clips is off by default. Turn it back on per clip in
  Studio.
- Your saved prompts and settings upgrade by themselves unless you edited them.
**Window and pet**

- The desktop pet no longer covers the screen with a grey sheet. The WebView2 update from
  mid-September made Windows 11 paint its dark backdrop behind the pet's see-through window; the
  pet window now turns that backdrop off, so only the pet shows.
- Opening Ashvane yourself always opens the window, centred on the screen. "Start in the tray"
  now applies only when Windows starts Ashvane at sign-in.
- The splash plays on every launch where you can see it: if the window starts in the tray, it
  waits until you open it, and it is no longer cut off before it finishes.

- New identity rendered in Blender: two metal vanes leaning into an A, cut through by an ember
  slash. It is the icon everywhere Windows shows one (taskbar, Start, title bar, tray, Alt+Tab,
  installer, Apps list), and the animated splash when the app opens.
- Everything Windows knows the app by carries the new name: `Ashvane.exe` in
  `%LOCALAPPDATA%\Programs\Ashvane`, the Start menu entry and desktop shortcut, the Apps &
  Features entry, the taskbar identity and the "Start with Windows" entry.
- Your data moves by itself. On the first start the data folder is renamed from
  `%LOCALAPPDATA%\ClipBot` to `%LOCALAPPDATA%\Ashvane` (instant, nothing is copied) and every stored
  file path is pointed at the new place. Saved keys and tokens still open.
- Updating from BURN-IN removes its old program folder, shortcuts and autostart entry, so there is
  one app, not two. The phone-remote firewall script also removes the rule under the old name.

**Kick and speed**

- Clips are made again. A slip in the new "AI stage deadline" code threw away every clip the
  AI had passed, right before rendering (NameError: verdict). Fixed, with a test that walks a
  passed clip all the way to render.

- Kick works again without developer keys. When the keys are missing or Kick refuses them, the
  top live Kick streams now come from Kick's own public front-page list, so Kick is watched and
  clipped either way. (Your Kick keys have been refused all day; this no longer stops Kick.)
- Much faster AI. The vision model was the "thinking" build of qwen3-vl, which writes a long
  hidden essay before every answer; on one GPU that meant 70 s timeouts, a full queue and
  captures paused for hours. It now uses the instruct build, and keeps it loaded for 3 hours
  instead of reloading 6 GB after every quiet spell.

**Phone connection**

- The phone kept getting signed out. Its sign-in was an ordinary dashboard session, so the
  30-minute idle auto-lock ended it whenever you weren't looking at the PC. The phone app now gets
  its own device sign-in that lasts 90 days (Settings → App) and is exempt from the idle lock;
  the phone has its own Face ID / fingerprint lock instead.
- **Test connection** in Settings → Dashboard (and in the Live desk's Phone remote box) tries
  every way the phone can reach this PC - home Wi-Fi, Tailscale address, Tailscale name - and
  says exactly what to type on the phone, or what to fix.
- The phone-connection help is now three numbered steps.

**Live viewer**

- Smoother. It used to jump to the last captured frame whenever it fell behind and then stall
  waiting for the next segment. It now holds a steady distance behind live (10 s by default,
  Settings → Viewer) by playing a few percent faster or slower, and only jumps after a real stall.

**For the phone assistant**

- The PC can now look at pictures and watch videos for the phone: `/api/assistant/upload`,
  `/look` and `/watch`. A link is fetched the way streams are (Twitch, Kick, YouTube and plain
  video links); only the first few minutes are taken, and uploads are deleted after a few hours.
  All of it waits its turn on the GPU behind the clip pipeline, so it never slows clipping down.

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

### 1.0.20 — 2026-09-23

- A launch splash every time the app opens: the Ashvane mark settles in and an ember line fills
  before the app appears underneath.

### 1.0.19 — 2026-09-23

- The assistant's model stays downloaded. It used to ask again whenever the phone's free space
  dipped below the model's size - counting the model's own space against it. A model that is on
  the phone is now always used.
- Downloads carry on from where they stopped (closing the app no longer restarts from zero) and
  keep going with the screen locked.
- Much faster download when your PC is connected: the PC fetches the model once over its own
  connection and the phone copies it across your home Wi-Fi.

### 1.0.18 — 2026-09-23

- New name and look: **Ashvane**, with the same Blender-rendered icon as the desktop app and a new
  launch screen. It installs as a new app (new app ID), so after installing it, sign in to your
  PC once more and delete the old BURN-IN app. The assistant's model downloads again.
- **Redesigned throughout.** One tab bar - Desk, Assistant, Code - with a calmer, warmer look:
  real icons, sentence-case labels, one accent colour, cards instead of boxed-in panels.
- **Code is now its own workspace**, not a copy of the chat: projects stored on the phone, a file
  tree, an editor with line numbers, tabs, undo and a row of the symbols phone keyboards hide,
  and an **agent** that reads your files and proposes changes as diffs you apply or discard.
  Web projects run right in the app; JavaScript runs with its console shown. It uses the phone's
  own model, or your PC's when you choose it.
- **Stays signed in to your PC** (the PC keeps the phone's sign-in for 90 days), and **Test
  connection** on the sign-in screen and the Live desk shows step by step what works and what
  to fix.

### 1.0.17 — 2026-09-23

- **Face ID, Touch ID or your passcode** lock the app on iPhone; fingerprint, face unlock or your
  PIN on Android. It asks when the app opens and again after a minute away. The phone does the
  checking; the app never sees or stores any of it.
- **The assistant grew up.** Attach photos, videos and files. Skills you call with `/name` -
  research, learn, go-over, grill-me, watch-this, reverse-engineer, summarize, brainstorm, plan
  and explain-code - plus your own, written here or imported from a SKILL.md link. Rules and
  custom instructions that apply to every chat, saved chat history, and a Code mode.
- It still thinks on the phone. The PC is only asked to see pictures and watch videos, and only
  when it is reachable.

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
