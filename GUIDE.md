# Ashvane v1.0.0: complete guide

A free, local, open-source (AGPL-3.0) Windows app. It watches Twitch and Kick streams, catches hype moments, cuts vertical clips, captions them, uses local AI to judge the clips and write the titles, and posts them automatically. No paid APIs; everything runs on your PC.

## Install
- **Full installer:** `dist\Ashvane-Setup-1.0.0.exe` (about 1 GB, includes everything). Run it, accept the terms, and it offers to install Ollama.
- **Web installer:** a small file that downloads the latest full installer from GitHub Releases. Built by `installer\publish_release.bat` once the GitHub repo exists.
- **Updates:** set Settings → Updates → repo (`owner/name`) and the app checks on start. "Update now" installs silently, keeps your data and restarts.
- **Your data** lives in `%LOCALAPPDATA%\ClipBot` (legacy folder name, kept on purpose so existing data carries over).

## First run
1. **Profile.** Unique name (case-insensitive) and a password. Passwords are stored only as salted PBKDF2 hashes (600k rounds), never in plain text. Each profile has its own data folder.
2. **Setup tab.** Shows what is missing (ffmpeg, Ollama, models). Models download themselves based on your GPU.
3. **Sources.** Twitch Client ID + secret (dev.twitch.tv), Kick keys (kick.com → Settings → Developer).
4. **Accounts.** Connect buttons for YouTube and TikTok; Meta tokens for Instagram/Facebook; a webhook URL for Discord. Keys are encrypted on this PC.
5. **Posting.** Turn each platform on, set daily caps, posting hours and templates.
6. **Let it run.** Settings → "Start here" and each section's "How this works" explain every option.

## Pipeline (how a clip is made)
1. **Discovery** picks up to 15 streams per platform: forced streamers, then top categories, then "Fill empty slots" with the biggest remaining ones.
2. **Capture** records each stream into a rolling on-disk buffer (Streamlink), so a moment can be cut after it happens.
3. **Detector** fires on unusual chat speed (z-score), keywords, and jumps in audio loudness — plus a **combined rule**: when two signals are each ~60% of the way to their threshold, that fires too. This is what catches moments no single threshold would.
4. **Cut + transcribe.** ffmpeg cuts at high quality (CRF 14); faster-whisper (large-v3-turbo, CUDA) transcribes. Segments already pruned from the buffer are skipped instead of failing the cut.
5. **Safety.** Slurs and derogatory terms are blocked (hard-R N-word blocked, casual "nigga" allowed): bleeped in audio, masked in titles/captions/tags, and the clip is skipped. Level, bleep sound, extra words and exceptions are in Settings → Safety.
6. **Judge.** A local Ollama model scores 0–10 against an explicit rubric: payoff, emotion, hook, self-contained, clarity. A funny or shocking **line** counts even when the picture is static, so talking-head moments stop being thrown away as "chat only". The judge also picks the cut points and the exact beat (`peak_at`) the edit punches in on.
   - **Second look:** a clip that lands within 1.5 points of the pass mark is judged again with more frames before it is discarded.
   - **Profiles** by VRAM: light (small model, stricter rules), balanced (one `qwen3-vl:8b-instruct` sees, judges and writes — right for 10–24 GB cards like your RTX 5080), strong (big separate judge, 24 GB+).
7. **Copywriter** watches frames (one at the exact spike) plus chat's reaction, then writes title, description, tags, hashtags, first comment and the on-screen hook per platform.
8. **Editor** renders 1080×1920:
   - **Dead-air cut:** long pauses inside the clip are removed and the pieces joined, with captions re-timed to match. Pace is the whole game in short-form.
   - **Stage layout:** the stream fills a tall box (64% of frame height by default, sides cropped) instead of floating as a small letterboxed strip. Facecam layout stacks the webcam over the gameplay.
   - Word-level captions with a pop on the spoken word, hook box, **slow push-in** across the clip and an **eased punch-in** at the judge's peak, loudness normalisation, progress bar, watermark.
9. **Scheduler** posts within caps, windows and gaps. **Analytics** measures views and steers future picks.
10. **Backlog failsafe:** capture pauses when too many clips wait for the AI. Only one AI stage runs at a time.

## App tour
- **Live desk:** stream slots with chat sparklines. Click one to watch it full screen ~6 s behind live.
- **Clips:** everything it cut. Clips still working show what stage they are in.
- **Studio:** quick settings plus an Advanced drop-down; six themes (Sterling, Ember, Loud, Clean, Night Shift, Cinema). "Use for all new clips" saves the look.
- **Status card** (click it): system log with events, issues and analytics.
- **Desktop pet:**
  - Runs as a click-through overlay across your whole desktop, so it moves at 60 fps without dragging a window around — that was the old stutter.
  - Because a see-through window gets no mouse messages from Windows, Ashvane tracks the cursor itself: hover opens the card, drag moves the pet, double-click opens the app, and the card's buttons work.
  - Hover card floats above the pet with stats, the hottest stream and graphs.
- **Tray:** closing the window keeps it running. Start with Windows is optional and starts it quietly.
- **Look:** sterling chrome hardware on black, engraved labels, machined bevels, one ember lamp that only ever means ON AIR. All art is original (Blender scripts in `art/`).

## Settings worth knowing
| Setting | Why |
|---|---|
| Detector → Catch combined signals | The main dial for "am I missing clips?" Lower the strength to catch more. |
| AI → Second look at near-misses | Stops good clips dying on a thin first description. |
| AI → Judge profile | Auto picks by VRAM; force one if you want speed or strictness. |
| Edit → Cut the dead air | Turn off if you want the raw timing. |
| Edit → Stage height / position | How much of the frame the stream fills. |
| Posting → YouTube → 18+ | On by default: uploads are marked age-restricted. |
| Pets → Animation speed / walk speed / mouse polling | Feel of the desktop pet. |

## Legal
- LICENSE (AGPL-3.0), NOTICE.md, TERMS.md, PRIVACY.md. Terms are accepted in the installer and again when a profile is created.
- You are responsible for the rights to what you repost; "Blocked streamers" handles takedown requests.
- **Name:** "Ashvane" is a coined word. Web searches on 2026-09-23 found no app, software company or clipping tool using it. That is not a trademark clearance; search USPTO (tmsearch.uspto.gov) before registering or selling merch under it. Not legal advice.

## Build and release (developer)
- **Tests:** `.venv\Scripts\python -m pytest -q`.
- **Dev run:** `run.bat`.
- **Build:** `installer\build_installer.bat` → `dist\Ashvane-Setup-1.0.0.exe`.
- **Publish:** `winget install GitHub.cli`, `gh auth login`, create a public repo, `set CLIPBOT_REPO=owner/name`, `installer\publish_release.bat`.
- **Prompt upgrades:** prompts live in settings.json, so `config.upgrade_prompts` replaces a stored prompt that still matches an older shipped default. Add the old prompt's sha256 to `PROMPT_FIELDS` whenever you change a default, or existing installs keep the old one.

## Still open
- Push to GitHub.
- Instagram/Facebook test posts once Meta tokens are saved.
- First comment auto-posting (needs extra OAuth scopes).
- Brand-kit job to customise your channels (images and copy are in `brandkit\`).
