"""步骤3：Whisper 语音识别 → SRT"""

import os

from utils.progress import ProgressReporter
from utils.srt import SubtitleSegment, write_srt


def transcribe(vocals_path: str, work_dir: str, whisper_model: str) -> str:
    """
    用 Whisper 转录人声音频为 SRT。
    返回 SRT 文件路径。
    """
    progress = ProgressReporter("转录")
    progress.start(f"模型: {whisper_model}")

    srt_path = os.path.join(work_dir, "transcript.srt")

    if os.path.exists(srt_path):
        progress.update("转录结果已存在，跳过")
        progress.done()
        return srt_path

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    progress.update(f"正在加载 Whisper 模型（设备: {device}）...")
    import whisper

    model = whisper.load_model(whisper_model, device=device)

    progress.update("正在转录...")
    result = model.transcribe(
        vocals_path,
        language="en",
        verbose=False,
    )

    # 将 Whisper segments 转为 SRT
    segments = []
    for i, seg in enumerate(result["segments"], 1):
        segments.append(SubtitleSegment(
            index=i,
            start_ms=int(seg["start"] * 1000),
            end_ms=int(seg["end"] * 1000),
            text=seg["text"].strip(),
        ))

    write_srt(segments, srt_path)
    progress.done(f"{len(segments)} 段字幕")
    return srt_path
