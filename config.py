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
TRANSLATE_MODEL = "deepseek/deepseek-v3.2"
TRANSLATE_BATCH_SIZE = 20
TRANSLATE_CONCURRENCY = 5    # 同时发送的翻译批次数

# Edge-TTS
TTS_VOICE_DEFAULT = "zh-CN-XiaoxiaoNeural"
TTS_CONCURRENCY = 4

# Edge-TTS 语速
TTS_NATURAL_MS_PER_CHAR = 251   # Edge-TTS 在 rate=+0% 时的实测自然速度 (ms/字)
TTS_FIXED_RATE = 40             # TTS 固定语速 (%)，所有段统一
TTS_TARGET_FILL = 0.95          # 翻译目标填充率：TTS 音频占可用时间窗的比例
TRANSLATE_CHAR_TOLERANCE = 3    # 翻译校验：实际字数与目标偏差超过此值则重译

# 段落合并（合并 Whisper 切碎的连续语流，消除碎片段）
MERGE_GAP_THRESHOLD_MS = 100    # gap ≤ 此值的相邻段合并 (ms)
MERGE_SHORT_THRESHOLD_MS = 500  # 短于此值的碎片段强制合并到相邻段 (ms)
MERGE_MAX_DURATION_MS = 15000   # 合并后单段最大时长 (ms)，防止 TTS 失败

# 时间对齐（atempo + 截断兜底）
MAX_SPEED_RATIO = 1.5        # audio.py 兼容用
FADE_OUT_MS = 200            # 截断时的 fade-out 时长 (ms)
SEGMENT_GAP_MS = 10          # 相邻段之间最小间隔 (ms)，防止音频粘连

# 时间压缩（去除段间停顿，使画面与语音同步）
TIMELINE_COMPRESS_ENABLED = True    # 启用时间压缩
TIMELINE_TAIL_MS = 200              # 每段语音后保留的尾巴 (ms)，留呼吸感
TIMELINE_MAX_GAP_MS = 300           # 段间最多保留的原视频转场时长 (ms)

# 上传文件大小限制
MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

# ffmpeg
AUDIO_SAMPLE_RATE = 44100
