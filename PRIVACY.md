# Privacy: BURN-IN

**Everything stays on your computer.** There is no account with BURN-IN, no telemetry, no
analytics, no ads and no tracking.

- **Your profile password** is never stored. Only a salted PBKDF2-SHA256 hash (600,000
  rounds) is kept, in your Windows user folder. BURN-IN never sees it.
- **API keys and tokens** you enter are encrypted with Windows DPAPI, so only your Windows
  account on this PC can read them.
- **Clips, transcripts, logs and settings** are stored locally in `%LOCALAPPDATA%\ClipBot`.
  Uninstalling asks whether to delete them.
- **The AI runs locally** (Ollama and Whisper on your PC). No clip, frame or transcript is
  sent to an AI company.

**What does leave your PC, only because you asked for it:**
- Requests to Twitch and Kick to find streams and read public chat.
- Uploads to the platforms *you* connect (YouTube, TikTok, Instagram, Facebook, Discord), under
  those platforms' privacy policies.
- A check of the project's GitHub page for updates (turn it off in Settings → Updates).
- Web fonts loaded from Google Fonts by the dashboard.
