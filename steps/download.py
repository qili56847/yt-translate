"""步骤1：yt-dlp 下载视频 / 本地文件导入 + ffmpeg 提取音频"""

import os
import shutil
import subprocess

from config import YTDLP_FORMAT, AUDIO_SAMPLE_RATE
from utils.progress import ProgressReporter

COOKIES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies.txt")


def download(video_url: str | None, work_dir: str, local_file: str | None = None) -> dict:
    """
    下载视频（或导入本地文件）并提取音频。
    返回 {"video": path, "audio": path}
    """
    progress = ProgressReporter("下载")
    progress.start(local_file or video_url)

    video_path = os.path.join(work_dir, "original.mp4")
    audio_path = os.path.join(work_dir, "original_audio.wav")

    # 获取视频文件
    if not os.path.exists(video_path):
        if local_file:
            progress.update("正在导入本地视频...")
            shutil.copy2(local_file, video_path)
        else:
            progress.update("正在下载视频...")
            cmd = [
                "yt-dlp",
                "-f", YTDLP_FORMAT,
                "--merge-output-format", "mp4",
                "--remote-components", "ejs:github",
                "-o", video_path,
                "--no-playlist",
            ]
            if os.path.exists(COOKIES_FILE):
                cmd += ["--cookies", COOKIES_FILE]
                progress.update(f"使用 cookies: {COOKIES_FILE}")
            else:
                progress.update(f"未找到 cookies 文件: {COOKIES_FILE}")
            cmd.append(video_url)
            subprocess.run(cmd, check=True)
    else:
        progress.update("视频已存在，跳过下载")

    # 提取音频
    if not os.path.exists(audio_path):
        progress.update("正在提取音频...")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", str(AUDIO_SAMPLE_RATE),
                "-ac", "2",
                audio_path,
            ],
            check=True,
        )
    else:
        progress.update("音频已存在，跳过提取")

    progress.done()
    return {"video": video_path, "audio": audio_path}
