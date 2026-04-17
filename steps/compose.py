"""步骤6：ffmpeg 混音 + 合成最终视频"""

import os
import subprocess
import tempfile

from config import AUDIO_SAMPLE_RATE
from utils.progress import ProgressReporter


def _build_trim_concat_parts(keep_ranges: list[tuple[int, int]]) -> tuple[list[str], int]:
    """为 keep_ranges 生成 filter_complex 的 trim/atrim + concat 片段。

    输入视频标签固定为 [0:v]，输入音频标签固定为 [1:a]。
    返回 (filter 片段列表, 分段数量)，concat 后的输出标签为 [v_cut] 和 [a_cut]。
    """
    parts = []
    concat_inputs = []
    for i, (start_ms, end_ms) in enumerate(keep_ranges):
        start_s = start_ms / 1000.0
        end_s = end_ms / 1000.0
        parts.append(
            f"[0:v]trim=start={start_s:.3f}:end={end_s:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        parts.append(
            f"[1:a]atrim=start={start_s:.3f}:end={end_s:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs.append(f"[v{i}][a{i}]")
    parts.append(
        f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=1[v_cut][a_cut]"
    )
    return parts, len(keep_ranges)


def compose(
    video_path: str,
    no_vocals_path: str,
    voice_track_path: str,
    output_path: str,
    subtitle_path: str | None = None,
    keep_ranges: list[tuple[int, int]] | None = None,
) -> str:
    """
    将中文语音轨与背景音混合，替换原视频音轨。
    可选烧录中文字幕。
    若提供 keep_ranges，则按区间裁剪视频与背景音，压缩段间停顿。
    返回输出视频路径。
    """
    progress = ProgressReporter("合成视频")
    progress.start()

    if keep_ranges:
        progress.update(f"裁剪 {len(keep_ranges)} 个保留区间并混音...")
        filter_parts, _ = _build_trim_concat_parts(keep_ranges)
        filter_parts.append(
            "[a_cut]volume=0.8[bg];[2:a]volume=1.2[voice];"
            "[bg][voice]amix=inputs=2:normalize=0[a_out]"
        )
        if subtitle_path:
            # 使用绝对路径避免 libass 路径解析问题
            abs_srt = os.path.abspath(subtitle_path)
            srt_escaped = abs_srt.replace("\\", "/").replace(":", "\\:")
            style = (
                "FontSize=16,FontName=Microsoft YaHei,"
                "PrimaryColour=&H000000FF,OutlineColour=&H00000000,"
                "Outline=2,Shadow=1,MarginV=30"
            )
            filter_parts.append(
                f"[v_cut]subtitles='{srt_escaped}':force_style='{style}'[v_out]"
            )
            video_map = "[v_out]"
        else:
            video_map = "[v_cut]"

        filter_complex = ";".join(filter_parts)

        # 将 filter_complex 写入临时文件，避免 Windows 命令行长度限制
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(filter_complex)
            filter_script = f.name

        try:
            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", no_vocals_path,
                "-i", voice_track_path,
                "-filter_complex_script", filter_script,
                "-map", video_map,
                "-map", "[a_out]",
                "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", "2",
                output_path,
            ]
            subprocess.run(cmd, check=True)
        finally:
            os.unlink(filter_script)

        progress.done(output_path)
        return output_path

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
        # Windows 路径使用绝对路径并转义，供 libass 正确解析
        srt_escaped = os.path.abspath(subtitle_path).replace("\\", "/").replace(":", "\\:")
        progress.update("正在烧录中文字幕...")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", mixed_audio,
                "-vf", f"subtitles='{srt_escaped}':force_style='FontSize=16,FontName=Microsoft YaHei,PrimaryColour=&H000000FF,OutlineColour=&H00000000,Outline=2,Shadow=1,MarginV=30'",
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
