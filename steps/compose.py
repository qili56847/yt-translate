"""步骤6：ffmpeg 混音 + 合成最终视频"""

import subprocess

from config import AUDIO_SAMPLE_RATE
from utils.progress import ProgressReporter


def compose(
    video_path: str,
    no_vocals_path: str,
    voice_track_path: str,
    output_path: str,
    subtitle_path: str | None = None,
) -> str:
    """
    将中文语音轨与背景音混合，替换原视频音轨。
    可选烧录中文字幕。
    返回输出视频路径。
    """
    progress = ProgressReporter("合成视频")
    progress.start()

    # 混合背景音和中文语音
    progress.update("正在混合音轨...")
    mixed_audio = output_path.replace(".mp4", "_mixed_audio.wav")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", no_vocals_path,
            "-i", voice_track_path,
            "-filter_complex",
            "[0:a]volume=0.8[bg];[1:a]volume=1.2[voice];[bg][voice]amix=inputs=2:normalize=0[out]",
            "-map", "[out]",
            "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", "2",
            mixed_audio,
        ],
        check=True,
    )

    # 合成最终视频
    progress.update("正在合成视频...")
    if subtitle_path:
        # 烧录字幕需要重编码视频
        # Windows 路径需要转义反斜杠和冒号
        srt_escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        progress.update("正在烧录中文字幕...")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", mixed_audio,
                "-vf", f"subtitles='{srt_escaped}':force_style='FontSize=22,FontName=Microsoft YaHei,PrimaryColour=&H000000FF,OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=30'",
                "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_path,
            ],
            check=True,
        )
    else:
        # 无字幕，直接拷贝视频流
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", mixed_audio,
                "-c:v", "copy",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                output_path,
            ],
            check=True,
        )

    progress.done(output_path)
    return output_path
