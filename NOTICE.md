# Notices

**Ashvane**, © Ashvane. Licensed under the GNU Affero General Public
License v3.0 (see `LICENSE`). You may use, study, change and share it; if you run a modified
version as a network service, you must offer its source code to that service's users.

## Trademarks
"Ashvane" is the name of this project. Twitch, Kick, YouTube, TikTok,
Instagram, Facebook, Discord, NVIDIA, Ollama and all other product names are trademarks of their
respective owners. They are mentioned only to describe what this software works with. This
project is **not affiliated with, endorsed by, or sponsored by** any of them.

## Third-party software bundled in the installer
| Component | License |
|---|---|
| Python 3.12 runtime | PSF License |
| FastAPI, Pydantic, faster-whisper, CTranslate2, ONNX Runtime, pythonnet | MIT |
| uvicorn, httpx, websockets, pywebview | BSD-3-Clause |
| Streamlink | BSD-2-Clause |
| Pillow | MIT-CMU (HPND) |
| pystray | LGPL-3.0 (used unmodified; the full source of this app is public, so it can be relinked) |
| PyInstaller bootloader | GPL-2.0 with the bootloader exception |
| NVIDIA cuBLAS / cuDNN runtime libraries | NVIDIA Software License (redistributable runtime components) |

## Installed or downloaded separately (not bundled)
ffmpeg (LGPL/GPL, installed via winget), Ollama (MIT), the Qwen models pulled through Ollama
(Apache-2.0), the Whisper speech model (MIT, downloaded on first use), and the web fonts IBM Plex
Sans, Barlow Condensed and Pirata One (SIL Open Font License, loaded from Google Fonts).

## Original work
All code, the user interface, the 3D art (logo, splash, backdrop, pets, icons) and the sound
effects were created for this project. The 3D art is rendered from the Blender scripts in `art/`
and the sounds are synthesized by `art/sounds.py`.
