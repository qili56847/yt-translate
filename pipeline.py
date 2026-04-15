"""流水线编排器：串联所有步骤"""

import hashlib
import json
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


def _generate_local_id(file_path: str) -> str:
    """根据本地文件生成唯一 ID: local_{name}_{hash[:8]}"""
    name = os.path.splitext(os.path.basename(file_path))[0]
    # 清理文件名，只保留字母数字和下划线
    name = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fff]', '_', name)[:32]
    # 读取文件前 1MB 计算 hash
    h = hashlib.md5()
    with open(file_path, 'rb') as f:
        h.update(f.read(1024 * 1024))
    return f"local_{name}_{h.hexdigest()[:8]}"


def run_pipeline(
    video_url: str | None = None,
    output_path: str = "output.mp4",
    voice: str = TTS_VOICE_DEFAULT,
    whisper_model: str = WHISPER_MODEL_DEFAULT,
    keep_workspace: bool = False,
    skip_to: str | None = None,
    local_file: str | None = None,
    review_callback=None,
) -> str:
    """
    运行完整处理流水线。
    返回输出文件路径。
    """
    # 验证输入
    if not video_url and not local_file:
        raise ValueError("必须提供 video_url 或 local_file 之一")
    if video_url and local_file:
        raise ValueError("video_url 和 local_file 不能同时提供")

    if local_file:
        if not os.path.isfile(local_file):
            raise FileNotFoundError(f"本地文件不存在: {local_file}")
        video_id = _generate_local_id(local_file)
        display_name = os.path.basename(local_file)
    else:
        video_id = _extract_video_id(video_url)
        display_name = video_id

    work_dir = os.path.join(WORKSPACE_ROOT, video_id)
    os.makedirs(work_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  英文视频中文配音工具")
    print(f"  {'文件' if local_file else '视频 ID'}: {display_name}")
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
    translated_source_path = os.path.join(work_dir, "translated.srt")
    translated_merged = os.path.join(work_dir, "translated_merged.srt")
    translated_display = os.path.join(work_dir, "translated_display.srt")
    subtitle_path = translated_source_path
    if os.path.exists(translated_display):
        subtitle_path = translated_display
    elif os.path.exists(translated_merged):
        subtitle_path = translated_merged
    voice_track_path = os.path.join(work_dir, "chinese_voice_track.wav")
    keep_ranges_path = os.path.join(work_dir, "keep_ranges.json")
    keep_ranges: list[tuple[int, int]] | None = None

    # 执行各步骤
    for step_name in STEPS[start_idx:]:
        print(f"\n--- 步骤: {step_name} ---")

        if step_name == "download":
            result = download(video_url, work_dir, local_file=local_file)
            video_path = result["video"]
            audio_path = result["audio"]

        elif step_name == "separate":
            result = separate(audio_path, work_dir)
            vocals_path = result["vocals"]
            no_vocals_path = result["no_vocals"]

        elif step_name == "transcribe":
            srt_path = transcribe(vocals_path, work_dir, whisper_model)

        elif step_name == "translate":
            translated_source_path = translate(srt_path, work_dir)
            if review_callback:
                # 拆分长段为短句，方便用户核对（写到临时文件，不覆盖原文件）
                from utils.srt import parse_srt, write_srt, split_long_segments
                review_srt = os.path.join(work_dir, "translated_review.srt")
                segs = split_long_segments(parse_srt(translated_source_path))
                write_srt(segs, review_srt)
                review_callback(review_srt)

        elif step_name == "synthesize":
            result = synthesize(translated_source_path, work_dir, voice)
            voice_track_path = result["voice_track"]
            subtitle_path = result["subtitle"]  # 用烧录版短帧字幕，时间轴仍与语音一致
            kr_path = result.get("keep_ranges_path")
            if kr_path and os.path.exists(kr_path):
                with open(kr_path, "r", encoding="utf-8") as f:
                    keep_ranges = [tuple(r) for r in json.load(f)]

        elif step_name == "compose":
            if not os.path.exists(translated_display) and os.path.exists(translated_merged):
                from utils.srt import parse_srt, wrap_long_segments, write_srt
                display_segs = wrap_long_segments(parse_srt(translated_merged))
                write_srt(display_segs, translated_display)
                subtitle_path = translated_display
            if keep_ranges is None and os.path.exists(keep_ranges_path):
                with open(keep_ranges_path, "r", encoding="utf-8") as f:
                    keep_ranges = [tuple(r) for r in json.load(f)]
            compose(
                video_path, no_vocals_path, voice_track_path, output_path,
                subtitle_path=subtitle_path, keep_ranges=keep_ranges,
            )

    print(f"\n{'='*60}")
    print(f"  完成！输出文件: {output_path}")
    print(f"{'='*60}\n")

    # 清理工作目录
    if not keep_workspace:
        shutil.rmtree(work_dir, ignore_errors=True)
        print("工作目录已清理")

    return output_path
