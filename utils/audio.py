"""音频工具函数：时长检测、变速、截断"""

import re
import subprocess

from config import FADE_OUT_MS, MAX_SPEED_RATIO


def get_duration_ms(audio_path: str) -> float:
    """用 ffmpeg 获取音频时长（毫秒）"""
    result = subprocess.run(
        ["ffmpeg", "-i", audio_path, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # 从 stderr 中提取 Duration: HH:MM:SS.xx
    stderr = result.stderr
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", stderr)
    if match:
        h, m, s, cs = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
        return h * 3600000 + m * 60000 + s * 1000 + cs * 10
    # fallback: 从 time= 行获取
    matches = re.findall(r"time=(\d+):(\d+):(\d+)\.(\d+)", stderr)
    if matches:
        h, m, s, cs = [int(x) for x in matches[-1]]
        return h * 3600000 + m * 60000 + s * 1000 + cs * 10
    raise RuntimeError(f"无法获取音频时长: {audio_path}")


def adjust_speed(input_path: str, output_path: str, ratio: float) -> None:
    """用 ffmpeg atempo 变速，ratio > 1 为加速"""
    # atempo 范围 [0.5, 100.0]，但我们限制在 MAX_SPEED_RATIO
    ratio = min(ratio, MAX_SPEED_RATIO)
    ratio = max(ratio, 0.5)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-filter:a", f"atempo={ratio:.4f}",
            output_path,
        ],
        capture_output=True, check=True,
    )


def truncate_with_fade(input_path: str, output_path: str, target_ms: float) -> None:
    """截断音频到指定时长并加 fade-out"""
    fade_start = max(0, target_ms - FADE_OUT_MS) / 1000
    duration = target_ms / 1000
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-t", f"{duration:.3f}",
            "-af", f"afade=t=out:st={fade_start:.3f}:d={FADE_OUT_MS / 1000:.3f}",
            output_path,
        ],
        capture_output=True, check=True,
    )
