# Ashvane — brief for Claude Code

Free, local, open-source (AGPL-3.0) stream clipper for Windows: watch top Twitch/Kick streams,
detect hype from chat/keywords/audio, cut from a rolling buffer, transcribe (faster-whisper),
judge + write copy with a local multimodal model (Ollama), render 9:16 captioned clips (ffmpeg),
and post on a schedule to YouTube, TikTok, Instagram, Facebook and Discord.

## Rules
- $0: no paid APIs/services. Only local AI (Ollama, Whisper). ffmpeg does all editing.
- Python 3.12, deps limited to requirements.txt (+ nvidia cuBLAS/cuDNN wheels for GPU Whisper).
- Async everywhere; `logging.getLogger("clipbot.<module>")`; never swallow exceptions; kill every
  child process on shutdown.
- **Everything configurable**: no tunable literals in code (enforced by tests/test_no_hardcoded.py);
  every setting has a label + help and appears in the generated Settings form.
- All design, art (Blender scripts in art/), sounds and names are original. Never copy another
  product's branding, marks or UI. Brand names live in `clipbot/config.py` (COMPANY, PRODUCT).
- Don't call anything done that wasn't run: `python -m pytest -q` must stay green.

## Map
clipbot/: discovery, capture, chat, detector, assembler, transcribe, vision, clipability (judge),
aiplan (judge profiles), copywriter, safety (slur filter + bleep), editor, studio, scheduler,
publishers/, analytics, clip_import, pets, syslog, updater, ollama_setup, desktop (window/tray/pet),
dashboard/ (FastAPI + static UI, pet.html). installer/: Inno Setup full + web installer, release.
