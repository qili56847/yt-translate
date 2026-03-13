"""流水线编排器：串联所有步骤"""

import os
import re
import shutil

from config import WORKSPACE_ROOT, WHISPER_MODEL_DEFAULT, TTS_VOICE_DEFAULT
from steps.download import download
from steps.separate import separate
from steps.transcribe import transcribe
from steps.translate import translate
from steps.synthesize import synthesize
from steps.compose import compose

STEPS = ["download", "separate", "transcribe", "translate", "synthesize", "compose"]


def _extract_video_id(url: str) -> str:
    """从 YouTube URL 提取视频 ID"""
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # fallback: 用 URL hash
    return str(abs(hash(url)))[:12]


def run_pipeline(
    video_url: str,
    output_path: str = "output.mp4",
    voice: str = TTS_VOICE_DEFAULT,
    whisper_model: str = WHISPER_MODEL_DEFAULT,
    keep_workspace: bool = False,
    skip_to: str | None = None,
) -> str:
    """
    运行完整处理流水线。
    返回输出文件路径。
    """
    video_id = _extract_video_id(video_url)
    work_dir = os.path.join(WORKSPACE_ROOT, video_id)
    os.makedirs(work_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  YouTube 英文视频中文配音工具")
    print(f"  视频 ID: {video_id}")
    print(f"  工作目录: {work_dir}")
    print(f"{'='*60}\n")

    # 确定起始步骤
    start_idx = 0
    if skip_to:
        if skip_to not in STEPS:
            raise ValueError(f"未知步骤: {skip_to}，可选: {', '.join(STEPS)}")
        start_idx = STEPS.index(skip_to)
        print(f"从步骤 '{skip_to}' 开始（跳过前 {start_idx} 步）\n")

    # 各步骤所需的中间文件路径（用于 skip-to）
    video_path = os.path.join(work_dir, "original.mp4")
    audio_path = os.path.join(work_dir, "original_audio.wav")
    vocals_path = os.path.join(work_dir, "htdemucs", "original_audio", "vocals.wav")
    no_vocals_path = os.path.join(work_dir, "htdemucs", "original_audio", "no_vocals.wav")
    srt_path = os.path.join(work_dir, "transcript.srt")
    translated_path = os.path.join(work_dir, "translated.srt")
    voice_track_path = os.path.join(work_dir, "chinese_voice_track.wav")

    # 执行各步骤
    for step_name in STEPS[start_idx:]:
        print(f"\n--- 步骤: {step_name} ---")

        if step_name == "download":
            result = download(video_url, work_dir)
            video_path = result["video"]
            audio_path = result["audio"]

        elif step_name == "separate":
            result = separate(audio_path, work_dir)
            vocals_path = result["vocals"]
            no_vocals_path = result["no_vocals"]

        elif step_name == "transcribe":
            srt_path = transcribe(vocals_path, work_dir, whisper_model)

        elif step_name == "translate":
            translated_path = translate(srt_path, work_dir)

        elif step_name == "synthesize":
            voice_track_path = synthesize(translated_path, work_dir, voice)

        elif step_name == "compose":
            compose(video_path, no_vocals_path, voice_track_path, output_path, subtitle_path=translated_path)

    print(f"\n{'='*60}")
    print(f"  完成！输出文件: {output_path}")
    print(f"{'='*60}\n")

    # 清理工作目录
    if not keep_workspace:
        shutil.rmtree(work_dir, ignore_errors=True)
        print("工作目录已清理")

    return output_path
