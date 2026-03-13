"""全局配置常量"""

import os

# 工作目录
WORKSPACE_ROOT = "workspace"

# yt-dlp
YTDLP_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"

# demucs
DEMUCS_MODEL = "htdemucs"

# Whisper
WHISPER_MODEL_DEFAULT = "medium"

# OpenRouter API (Qwen 翻译)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
TRANSLATE_MODEL = "deepseek/deepseek-v3.2"
TRANSLATE_BATCH_SIZE = 40
TRANSLATE_CONCURRENCY = 3    # 同时发送的翻译批次数

# Edge-TTS
TTS_VOICE_DEFAULT = "zh-CN-XiaoxiaoNeural"
TTS_CONCURRENCY = 4

# Edge-TTS 语速（全局统一模式：所有段用同一个语速，确保听感一致）
TTS_MS_PER_CHAR = 230        # 中文每字符预估时长 (ms)，用于计算全局 TTS rate
TTS_RATE_CLAMP_MIN = -20     # 全局 TTS rate 下限 (%)
TTS_RATE_CLAMP_MAX = 40      # 全局 TTS rate 上限 (%)

# 时间对齐（不做 atempo 变速，仅截断兜底）
MAX_SPEED_RATIO = 1.5        # audio.py 兼容用，synthesize 不再使用
FADE_OUT_MS = 200            # 截断时的 fade-out 时长 (ms)
SEGMENT_GAP_MS = 30          # 相邻段之间最小间隔 (ms)，防止音频粘连

# ffmpeg
AUDIO_SAMPLE_RATE = 44100
