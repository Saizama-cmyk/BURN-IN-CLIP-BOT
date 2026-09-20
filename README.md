# BURN-IN

A free, local Windows app. It watches the top live streams on Twitch and Kick and detects hype moments from chat rate, keywords and audio loudness. For each moment it cuts a clip from a rolling capture buffer and transcribes it with faster-whisper. A local Qwen model (Ollama) judges whether the moment is clip-worthy and writes the title, caption and hashtags. BURN-IN then renders a vertical 1080x1920 clip with captions and posts it on a schedule to YouTube Shorts, TikTok, Instagram Reels, Facebook Reels and Discord.

Everything runs on your PC. The only software cost is $0: ffmpeg does all editing, and Whisper and Qwen run on your GPU.

## Install (the easy way)
Run **`BURN-IN-Setup-<version>.exe`** (build it with `installeruild_installer.bat`). It installs BURN-IN for your Windows account (no admin), adds a **BURN-IN** Start-menu folder and an optional desktop shortcut, and can install the free prerequisites for you (**ffmpeg** and **Ollama**, through winget). Updating keeps your settings, keys and clips. Uninstall from Windows Settings → Apps → *BURN-IN*; it asks before deleting your data.

## Install (manual / developers)

1. Prerequisites: Python 3.12, **ffmpeg** on PATH (`winget install Gyan.FFmpeg`), **Ollama** with the model pulled (`ollama pull qwen3-coder:30b`), and the Edge WebView2 runtime (already on Windows 11).
2. Set up the environment:
   ```
   py -3.12 -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. `build.bat` builds `dist\BURN-IN\ClipBot.exe` (PyInstaller, one-folder, windowed).
4. `install.bat` copies it to `%LOCALAPPDATA%\Programs\BURN-IN` and creates Start Menu and Desktop shortcuts. Run `install.bat /quiet` for an unattended install.
5. `uninstall.bat` (also in Start Menu → "Uninstall BURN-IN") removes the program, the shortcuts and the Run key. It asks before deleting your data.

Your settings, database, tokens, logs, buffers and clips live in `%LOCALAPPDATA%\BURN-IN`, so reinstalling or updating never wipes them. You can move the folders under **Settings → App**.

### Dev mode
| Command | What runs |
|---|---|
| `python -m clipbot` | Console mode; dashboard at http://127.0.0.1:8787 |
| `python -m clipbot --window` (or `run.bat`) | Native window + tray |
| `python -m clipbot --headless` | Engine + dashboard, no window |
| `python -m pytest -q` | Test suite |
| `python scripts\live_clipability.py` | One real call to your Ollama model |
| `python scripts\e2e_live.py xqc kaicenat` | Real capture → cut → Whisper → Qwen → render on live channels (no keys needed) |

## Using it
The app opens as **SPIKE OS**: a sidebar for the six sections, with the live status, backlog meter, GPU, Pause and Lock pinned at the bottom. It starts behind a password lock with local profiles (salted PBKDF2 hashes, stored only on this PC).
- **Live tab**: stream monitors with live thumbnails and chat-rate sparklines vs baseline, pipeline stage counts, samples (click to preview the clip, transcript and verdict) and posting status.
- **Clips tab**: a gallery of everything cut (ready, posted, rejected, still working). Open one to watch it, see the frames the AI looked at, copy per-platform captions, download it, or **Publish now**: tick any connected platforms and it posts immediately, skipping the daily cap, gap and posting window. A rejected clip is rendered first.
- **Growth tab**: views and followers per platform, what is performing, and whether the scheduler is steering toward it yet.
- **System tab**: **Scan this PC** recommends worker counts, encoder, Whisper device and more (nothing changes until you apply), and **Storage** lets you move any data folder.
- **Settings tab**: every threshold, path, prompt, template, style and account. The form is generated from `clipbot/config.py`. Fields marked RESTART apply after **Restart BURN-IN**; everything else applies live. Each section has **Reset to default**. **Export** leaves out secrets unless you tick the box; **Import** keeps your stored secrets when the file has none.
- **Setup tab**: a checklist of what is missing. Each **Fix in Settings** button jumps to the right field. It opens automatically on first run.
- **Tray**: Open BURN-IN, a live status line, Pause/Resume capture, Open clips folder, Quit. Closing the window keeps BURN-IN in the tray (**Settings → App**).
- **Backlog failsafe**: when in-flight clips reach 80 % of capacity, capture pauses. It resumes at 50 %. Both thresholds are in **Settings → Backlog**.

## Studio
Restyle any clip in seconds (**Studio** tab): pick a theme (Sterling, Ember, Loud, Clean, Night Shift, Cinema, or your own saved ones), use **Quick** for the everyday tweaks or open **Advanced** for everything, trim, and edit the hook. The phone preview updates instantly and a real render follows a second later. **Apply to this clip** re-renders it at full quality; **Use for all new clips** saves the look to Settings → Editor.

## Safety
Settings → Safety keeps slurs and derogatory terms out of posts: they are masked in titles, captions, hashtags and comments, masked in the burned-in captions, **bleeped** in the audio, and clips where someone says one are never posted. Casual slang and everyday swearing are left alone unless you raise the level or add words under *Also block*.

## Desktop pet
Five 3D pets (Blip, Ember, Moss, Nib, Glitch) live on your desktop while BURN-IN runs, even with the window closed. They react to clips, posts and spikes with moodlets and sounds, wander, nap and can be dragged; hover for stats, the hottest stream and graphs. Turn it on or off any time under Settings → Desktop pet.

## How it judges a moment
1. **Detect**: chat rate, keyword and audio-loudness spikes fire on a live stream.
2. **Cut and check**: BURN-IN cuts the clip from the rolling buffer and rejects it if the capture is black, silent or too short.
3. **Listen**: Whisper writes a timestamped transcript.
4. **Look**: ffmpeg grabs **6 frames** spread over the clip, one of them exactly at the spike. The vision model (`qwen3-vl:8b` in Ollama) describes each frame: the scene, what is happening, the facecam reaction, on-screen alerts. The description appears in the sample preview under *What the vision model saw*.
5. **Write**: the writer model (the vision model by default) *watches* frames from the clip, reads the transcript and chat's reaction, picks the vibe, and writes titles, descriptions, tags and a first comment per platform.
6. **Judge**: `qwen3-coder:30b` reads the transcript, the visual description, the chat sample and the spike numbers. It decides between `moment` (post it) and `chat_only`, `keyword_false` or `dead_air` (reject), then writes the title, caption, hashtags and trim points.

Only one model runs on the GPU at a time. **Settings → AI** has the switches: *Look at the video*, the vision model (`qwen3-vl:4b` is lighter), frames per clip, frame size, and both prompts. If the vision model is missing or fails, clips are still judged on transcript and chat, and the judge is told there is no visual description.

## Accounts: what you need to set up (all free)

### Twitch (discovery), Settings → Twitch
1. Go to https://dev.twitch.tv/console/apps and click **Register Your Application**.
2. Name: anything unique. **OAuth Redirect URLs**: `https://localhost`. The form requires HTTPS; BURN-IN never opens this URL because it uses an app token (client credentials). Category: *Application Integration*. Client type: **Confidential**. Complete the captcha, then click **Create**.
3. Copy the **Client ID** into `twitch.client_id`. Click **New Secret** and copy it into `twitch.client_secret`.

Chat is read anonymously and needs no keys.

### Kick (discovery), Settings → Kick
Official guide: https://docs.kick.com/getting-started/kick-apps-setup
1. On kick.com, turn on **Two-Factor Authentication** (Account Settings → Security). The developer tools stay hidden until 2FA is on.
2. Open **Account Settings → Developer** (https://kick.com/settings/developer) and create an app.
3. **OAuth Redirect URIs**: Kick only accepts **HTTPS** addresses. Enter `https://localhost`. BURN-IN never opens it, because it signs in with an *App Access Token* (the client-credentials flow, no browser login). Leave no empty extra redirect row, since an empty row also shows "Redirect URIs must use HTTPS protocol."
4. BURN-IN only reads public livestream and channel data, which the App Access Token covers without any user scopes. If the form insists on at least one scope, a read-only one such as `user:read` or `channel:read` is fine.
5. Copy the **Client ID** into `kick.client_id` and the **Client Secret** into `kick.client_secret`, then **Save**. The Live tab's Kick line should change from "no client ID/secret" to "N streams selected" within one refresh (45 s).

Kick chat needs no keys. It is read anonymously through Kick's public websocket, the same way kick.com does. If Kick's website blocks the chatroom lookup (Cloudflare), that monitor shows "chat unavailable" and still clips from audio spikes.

### YouTube Shorts, Settings → Accounts
1. In https://console.cloud.google.com create a project and enable **YouTube Data API v3**.
2. Under **OAuth consent screen**, choose External and add yourself as a test user.
3. Under **Credentials**, choose Create credentials → OAuth client ID → **Desktop app**.
4. Copy the client ID into `accounts.youtube_client_id` and the secret into `accounts.youtube_client_secret`, then **Save**.
5. Click **Connect YouTube** at the top of the Accounts section and sign in within your browser.
6. Turn on **Settings → Posting → YouTube → Post to YouTube**.

Unverified apps get a small daily upload quota. The default daily cap of 6 stays inside it.

### TikTok, Settings → Accounts (about 30 minutes, plus waiting for TikTok's review)
TikTok is the strictest platform. It needs a **verified website** with Terms and Privacy pages and an **https** sign-in redirect, and until it audits your app it only posts **privately**. BURN-IN ships that website ready-made in the `tiktok-site` folder.

**A. Put the website online (free, GitHub Pages)**
1. Create a free account at https://github.com. Make a new **public** repository named `clipbot`.
2. In the repository click **Add file → Upload files**. Drag in everything inside `the repo's `tiktok-site` folder`: `index.html`, `terms.html`, `privacy.html` and the `callback` folder. Then click **Commit changes**.
3. Open **Settings → Pages**. Under *Build and deployment* pick **Deploy from a branch**, branch **main**, folder **/ (root)**, then **Save**. After about a minute the site is live at `https://YOURNAME.github.io/clipbot/`.
   Your URLs will be:
   - Website: `https://YOURNAME.github.io/clipbot/`
   - Terms: `https://YOURNAME.github.io/clipbot/terms.html`
   - Privacy: `https://YOURNAME.github.io/clipbot/privacy.html`
   - Redirect URI: `https://YOURNAME.github.io/clipbot/callback/` (keep the trailing `/`)

**B. Create the TikTok app**
1. Sign in at https://developers.tiktok.com with your TikTok account, then open **Manage apps → Connect an app** (register it under your individual account).
2. **Basic information**: upload an app icon (1024×1024 PNG; `assets\clipbot.png` scaled up works), enter a name and a description, pick a category, and paste the **Terms of Service URL** and **Privacy Policy URL** from step A.
3. **Platforms**: tick **Web** and enter the **Website URL**. Click **Verify** and choose **URL prefix** (`https://YOURNAME.github.io/clipbot/`). TikTok gives you a small signature `.txt` file: upload it to the same GitHub repository (Add file → Upload files → Commit), wait a minute, and click **Verify** again. Do the same for the Terms and Privacy URLs if TikTok asks.
4. **Products → Add products**: add **Login Kit** and **Content Posting API**. In the Content Posting API settings turn on **Direct Post**.
5. **Login Kit → Redirect URI**: add `https://YOURNAME.github.io/clipbot/callback/` exactly.
6. **Scopes**: make sure `user.info.basic`, `video.upload` and `video.publish` are listed.
7. Copy the **Client key** and **Client secret** from the top of the app page.

**C. Connect BURN-IN**
1. Settings → Accounts: paste the key into `tiktok_client_key`, the secret into `tiktok_client_secret`, and the redirect into `tiktok_redirect_uri`, **exactly** as registered. Click **Save**.
2. Click **Connect TikTok**. Your browser opens TikTok; approve the app. The callback page sends you straight back to BURN-IN, which shows "TikTok connected".
   If the page just shows an address instead, copy it and paste it into **"TikTok didn't come back to BURN-IN?"** under the Connect buttons, then click **Finish TikTok connect**.
3. Turn on **Settings → Posting → TikTok → Post to TikTok**.

**D. Until TikTok audits your app** (this is TikTok's rule, not BURN-IN's)
- Posts can only be **private**. Keep **Posting → TikTok → Privacy** on `SELF_ONLY`.
- Your TikTok account itself must be set to **Private** (TikTok app → Settings and privacy → Privacy → Private account). Otherwise uploads fail with `unaudited_client_can_only_post_to_private_accounts`.
- To go public: in the developer portal, fill in **App review** (explain that the app posts your own edited clips), upload a short screen recording of BURN-IN posting (TikTok requires at least one demo video), and click **Submit for review**. When it is approved, switch your account back to public and set Privacy to `PUBLIC_TO_EVERYONE`.

### Instagram Reels, Settings → Accounts (about 10 minutes)
You only need **one token**. BURN-IN looks up your account ID and renews the token by itself (the renewal interval is **Posting → Instagram → Refresh token after**).
1. **Make the account professional**: Instagram app → Settings and activity → Account type and tools → **Switch to professional account** (Creator or Business, both free).
2. Go to https://developers.facebook.com/apps, log in with Facebook, and click **Create app**. When asked for the type or use case, choose **Business** (a new Business app is required). Name it `BURN-IN`.
3. In the app's left menu open **Instagram → API setup with Instagram business login**.
4. Under **Generate access tokens**, click **Add account** and log in to the Instagram account you post to. If Instagram shows a *tester invite*, accept it (instagram.com → Settings → Apps and websites → Tester invites), then come back.
5. Click **Generate token** next to the account, approve the permissions (they include `instagram_business_basic` and `instagram_business_content_publish`) and **copy the token**. It lasts 60 days, and BURN-IN keeps renewing it.
6. Settings → Accounts: paste it into **Instagram access token**. Leave *Instagram user ID* empty. Click **Save**.
7. Turn on **Settings → Posting → Instagram → Post to Instagram**.

Posting to your own account works while the Meta app stays in Development mode, so no App Review is needed. Instagram allows 100 API posts per 24 h; BURN-IN's default cap is 25.

### Facebook Page Reels, Settings → Accounts (about 10 minutes)
Reels go to a **Facebook Page** you manage, not a personal profile. Create a free Page first at facebook.com/pages/create if you have none.
1. In the same Meta app (developers.facebook.com/apps → BURN-IN), click **Add use case** and add the one for **managing Pages / Pages API**, so the Page permissions become available.
2. Open the **Graph API Explorer**: https://developers.facebook.com/tools/explorer
   - **Meta App**: pick `BURN-IN`. **User or Page**: *User Token*.
   - Under **Permissions**, add `pages_show_list`, `pages_read_engagement`, `pages_manage_posts` (and `pages_manage_metadata` if it is listed).
   - Click **Generate Access Token** and approve it for your Page.
3. **Make it long-lived**: click the blue ⓘ next to the token → **Open in Access Token Tool** (or go to https://developers.facebook.com/tools/debug/accesstoken and paste it) → scroll down → **Extend Access Token** → copy the new token.
4. Back in the Graph API Explorer, paste that long-lived token into the *Access Token* box, type `me/accounts` in the query field, and click **Submit**.
5. In the result, find your Page and copy:
   - `"id"` → Settings → Accounts → **Facebook Page ID**
   - `"access_token"` → **Facebook Page token**. A Page token made from a long-lived user token does not expire.
6. **Save**, then turn on **Settings → Posting → Facebook → Post to Facebook**.

Facebook accepts Reels 3–90 s long at 9:16, which is what BURN-IN renders.

### Discord, Settings → Accounts
In the Discord channel, open Edit Channel → Integrations → Webhooks → **New Webhook** → **Copy Webhook URL**. Paste it into `accounts.discord_webhook`. Clips up to 24 MB are attached; larger ones are sent as text.

### Whisper on the GPU
If the dashboard shows Whisper as `cpu … CUDA failed: cublas64_12.dll is not found`, install the CUDA libraries into the venv:
```
.venv\Scripts\pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```
BURN-IN adds their DLL folders automatically. For the installed exe, run `build.bat` again afterwards so they get bundled. Whisper keeps working on CPU (int8) until then; it is just slower.

## Layout
```
clipbot/config.py        every setting (the Settings form is generated from it)
clipbot/discovery.py     Twitch Helix + Kick public API stream selection
clipbot/capture.py       streamlink → ffmpeg rolling segment buffers
clipbot/chat.py          anonymous Twitch IRC + Kick Pusher chat
clipbot/detector.py      chat / keyword / audio spike detection
clipbot/assembler.py     cut + quality check
clipbot/transcribe.py    faster-whisper with CUDA→CPU fallback
clipbot/clipability.py   Ollama judge (JSON verdicts)
clipbot/editor.py        9:16 render, ASS captions + watermark, intro/outro
clipbot/publishers/      YouTube, TikTok, Instagram, Facebook, Discord + OAuth
clipbot/scheduler.py     caps, gaps, posting window, retention
clipbot/pipeline.py      orchestration + backlog failsafe
clipbot/dashboard/       FastAPI server + single-file dashboard
clipbot/desktop.py       window, tray, single instance, restart
art/spike_scene.py       Blender scene: 3D app mark, splash animation, backdrop
art/build_art.py         renders → bloom/grade → dashboard art, icon master
```

## Brand art (Blender)
The 3D logo, the animated splash and the backdrop are rendered in **Blender** (free) from `art/spike_scene.py`. Blender is only a build-time tool, and the app ships the finished files in `clipbot/dashboard/static/art/`. To re-render after changing the scene:
```
.venv\Scripts\python art\build_art.py --render
```
Blender is found through the `BLENDER` environment variable, then PATH, then the default install folder. Add `--fast` for quick low-sample previews. Without `--render`, the script only reprocesses the existing renders in `art/out/`. `build.bat` then puts the new icon into the exe.

## License, terms and privacy
Free and open source under the **GNU AGPL v3.0** (`LICENSE`). Using it means agreeing to `TERMS.md` (you are responsible for having the right to post clips and for following each platform's rules). Everything runs locally; see `PRIVACY.md`. Third-party credits and trademark notes: `NOTICE.md`. Not affiliated with Twitch, Kick, YouTube, TikTok, Meta or Discord.
