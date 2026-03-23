"""全局配置常量"""

import os

# 将 ffmpeg 加入 PATH（如果尚未在 PATH 中）
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", r"C:\Users\lq_ka\Desktop\ffmpeg\bin")
if FFMPEG_BIN and FFMPEG_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = FFMPEG_BIN + os.pathsep + os.environ.get("PATH", "")

# 工作目录
WORKSPACE_ROOT = "workspace"

# 认证
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me-in-production")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# yt-dlp
YTDLP_FORMAT = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"

# demucs
DEMUCS_MODEL = "htdemucs"

# Whisper
WHISPER_MODEL_DEFAULT = "medium"

# OpenRouter API (Qwen 翻译)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
TRANSLATE_MODEL = "qwen/qwen3-235b-a22b-2507"
TRANSLATE_BATCH_SIZE = 20
TRANSLATE_CONCURRENCY = 5    # 同时发送的翻译批次数

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

# 上传文件大小限制
MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# ffmpeg
AUDIO_SAMPLE_RATE = 44100
