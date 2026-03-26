# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YouTube 英文视频中文配音工具 — 自动下载、人声分离、转录、翻译、语音合成、视频合成的一站式流水线。

## Commands

```bash
# Web UI (Flask, port 5000)
python app.py

# CLI
python main.py "https://www.youtube.com/watch?v=VIDEO_ID" --keep-workspace

# CLI with local file
python main.py --file video.mp4 --keep-workspace

# CLI with options
python main.py "URL" --voice zh-CN-YunxiNeural --whisper-model large --skip-to translate --keep-workspace

# Install dependencies
pip install -r requirements.txt

# For GPU acceleration, install CUDA PyTorch:
pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

## Architecture

Six-step sequential pipeline orchestrated by `pipeline.py`:

1. **download** (`steps/download.py`) — yt-dlp downloads video, ffmpeg extracts audio to WAV
2. **separate** (`steps/separate.py`) — demucs splits audio into vocals + no_vocals (auto-detects GPU)
3. **transcribe** (`steps/transcribe.py`) — Whisper ASR generates English SRT (auto-detects GPU)
4. **translate** (`steps/translate.py`) — OpenRouter API (Qwen) batch-translates SRT to Chinese with retry logic
5. **synthesize** (`steps/synthesize.py`) — Edge-TTS generates Chinese speech per segment, time-aligns via truncation with fade-out, mixes into single voice track (uniform speech rate across all segments)
6. **compose** (`steps/compose.py`) — ffmpeg mixes voice track with background audio, burns Chinese subtitles (libass), outputs final MP4

Each step caches its output; if intermediate files exist in `workspace/<video_id>/`, the step is skipped.

**Two entry points:**
- `main.py` — CLI with argparse, supports `--skip-to` for resuming
- `app.py` — Flask web server with SSE real-time progress streaming to browser

**Authentication (`auth.py`):** Supabase + Flask-Login session-based auth,所有路由需登录，auto-creates admin on first run。详见代码。

**Key utilities:** `utils/progress.py`(CLI/SSE双输出)、`utils/srt.py`(SRT解析)、`utils/audio.py`(ffmpeg封装)

**Config (`config.py`):** 所有可调常量，大部分支持环境变量覆盖。

## Key Environment Variables

- `OPENROUTER_API_KEY` — required for translation step
- `SUPABASE_URL` / `SUPABASE_KEY` — Supabase connection (required for auth)
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — initial admin credentials
- `SECRET_KEY` — Flask session secret
- `FFMPEG_BIN` — custom ffmpeg binary path

## Key Design Decisions

- TTS uses uniform speech rate: a single global rate is computed from all segments, clamped to [-20%, +40%], ensuring consistent listening experience
- TTS concurrency is 4 (`TTS_CONCURRENCY`), with retry up to 3 times and empty-file detection
- Time alignment uses truncation with 200ms fade-out only (no atempo speed shifting); `SEGMENT_GAP_MS=30` prevents audio overlap between adjacent segments
- Subtitle burning requires video re-encoding (libx264 CRF 20, preset fast); without subtitles, video stream is copied
- `amix` with `normalize=0` preserves individual track volumes (background 0.8x, voice 1.2x)
- Translation prompt enforces Chinese word count at 0.6-0.8x of English word count for sync
- Windows paths in ffmpeg subtitle filter need backslash→forward slash and colon escaping
