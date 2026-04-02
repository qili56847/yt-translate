# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YouTube 英文视频中文配音工具 — 自动下载、人声分离、转录、翻译、语音合成、视频合成的一站式流水线。支持翻译后人工核对字幕。

## Commands

```bash
# Web UI (Flask, port 5000) — 翻译后自动暂停等待核对
python app.py

# CLI — 一次跑完
python main.py "https://www.youtube.com/watch?v=VIDEO_ID" --keep-workspace

# CLI — 翻译后暂停等待人工核对
python main.py "URL" --review --keep-workspace

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
4. **translate** (`steps/translate.py`) — OpenRouter API batch-translates SRT to Chinese; merges Whisper fragments first, calculates per-segment target char count from time window, validates and retranslates segments exceeding tolerance, uses versioned MD5 cache
5. **[review]** — optional human review step: CLI `--review` pauses for manual editing; Web UI shows inline SRT editor with save/skip/continue. Review uses `translated_review.srt` (split short sentences), original `translated.srt` preserved for TTS
6. **synthesize** (`steps/synthesize.py`) — Edge-TTS generates Chinese speech per segment with global fixed +40% rate, only speeds up/truncates overlong segments (no slowdown). Uses `fit_segments_to_audio()` to shrink subtitle end times to actual speech duration, then `wrap_long_segments()` to produce display subtitle
7. **compose** (`steps/compose.py`) — ffmpeg mixes voice track with background audio, burns Chinese subtitles (FontSize=16, libass), outputs final MP4

Each step caches its output; if intermediate files exist in `workspace/<video_id>/`, the step is skipped.

**Two entry points:**
- `main.py` — CLI with argparse, supports `--skip-to` for resuming and `--review` for human review
- `app.py` — Flask web server with SSE real-time progress streaming, inline SRT editor for review

**Authentication (`auth.py`):** Supabase + Flask-Login session-based auth, all routes require login, auto-creates admin on first run.

**Key utilities:** `utils/progress.py` (CLI/SSE dual output), `utils/srt.py` (SRT parsing, `merge_segments`, `split_long_segments`, `wrap_long_segments`, `fit_segments_to_audio`), `utils/audio.py` (ffmpeg wrapper)

**Config (`config.py`):** All tunable constants, most support environment variable override.

## Key Environment Variables

- `OPENROUTER_API_KEY` — required for translation step
- `SUPABASE_URL` / `SUPABASE_KEY` — Supabase connection (required for auth)
- `ADMIN_USERNAME` / `ADMIN_PASSWORD` — initial admin credentials
- `SECRET_KEY` — Flask session secret
- `FFMPEG_BIN` — custom ffmpeg binary path

## Key Design Decisions

- **Segment merging**: Whisper fragments are merged before translation (gap ≤100ms, short segments <500ms, max 15s) via `utils/srt.merge_segments()`
- **Translation char targeting**: each segment gets a target char count = `window_ms * TTS_TARGET_FILL / ms_per_char`; segments deviating >3 chars are auto-retranslated. Prompt hints that English terms cost more TTS time
- **Translation API response**: `_extract_text()` handles both plain string and structured (thinking model) responses; `json.loads` result normalized to handle dict-list returns
- **Translation cache**: versioned MD5 hash (source text + timestamps + model + rate params); cached result re-validated against current length tolerance before reuse
- **Human review gate**: `pipeline.py` accepts `review_callback`; CLI uses `input()` blocking, Web UI uses `threading.Event` with 1-hour timeout (timeout = task cancelled). Review writes to `translated_review.srt` (not original), Web UI reads review file, saves back to `translated.srt`
- **Review API security**: all review endpoints (`/api/srt`, `/api/review-continue`, `/api/events`) verify task owner via `current_user.id`
- **Global unified TTS rate +40%**: all segments use the same Edge-TTS rate for consistent listening experience; no per-segment slowdown
- **Subtitle timing from actual audio**: `fit_segments_to_audio()` uses real TTS duration (from `_align_segment`) to set subtitle `end_ms`, eliminating long pauses between subtitle switches
- **Subtitle display pipeline**: synthesize produces three SRT files: `translated_merged.srt` (timing-fitted merged segments), `translated_display.srt` (wrap_long_segments for burn-in, max 2 lines × 18 chars), and `translated_review.srt` (split short sentences for editing)
- **Time alignment**: overlong segments → speedup (max 1.4x atempo) or truncation with fade-out; short segments keep natural duration (no artificial slowdown)
- **TTS concurrency** is 4, with retry up to 3 times and empty-file detection; `SEGMENT_GAP_MS=10` prevents overlap
- Subtitle burning uses FontSize=16 (libx264 CRF 20, preset fast); without subtitles, video stream is copied
- `amix` with `normalize=0` preserves individual track volumes (background 0.8x, voice 1.2x)
- Windows paths in ffmpeg subtitle filter need backslash→forward slash and colon escaping
