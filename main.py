"""CLI 入口：英文视频中文配音工具"""

import argparse
import sys

from config import TTS_VOICE_DEFAULT, WHISPER_MODEL_DEFAULT
from pipeline import run_pipeline, STEPS


def main():
    parser = argparse.ArgumentParser(
        description="英文视频中文配音工具 —— 自动下载/导入、转录、翻译、配音",
    )
    parser.add_argument("url", nargs="?", default=None, help="YouTube 视频 URL")
    parser.add_argument(
        "-f", "--file",
        help="本地视频文件路径",
    )
    parser.add_argument(
        "--voice",
        default=TTS_VOICE_DEFAULT,
        help=f"Edge-TTS 声音 (默认: {TTS_VOICE_DEFAULT})",
    )
    parser.add_argument(
        "--whisper-model",
        default=WHISPER_MODEL_DEFAULT,
        choices=["tiny", "base", "small", "medium", "large"],
        help=f"Whisper 模型大小 (默认: {WHISPER_MODEL_DEFAULT})",
    )
    parser.add_argument(
        "--output",
        default="output.mp4",
        help="输出文件路径 (默认: output.mp4)",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="保留中间文件（workspace 目录）",
    )
    parser.add_argument(
        "--skip-to",
        choices=STEPS,
        help="从指定步骤恢复（需要 workspace 中的中间文件）",
    )

    args = parser.parse_args()

    if not args.url and not args.file:
        parser.error("必须提供 YouTube URL 或 --file 本地文件路径")
    if args.url and args.file:
        parser.error("不能同时提供 YouTube URL 和 --file")

    try:
        run_pipeline(
            video_url=args.url,
            output_path=args.output,
            voice=args.voice,
            whisper_model=args.whisper_model,
            keep_workspace=args.keep_workspace,
            skip_to=args.skip_to,
            local_file=args.file,
        )
    except KeyboardInterrupt:
        print("\n\n已取消。中间文件保留在 workspace/ 目录中，可用 --skip-to 恢复。")
        sys.exit(1)
    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        print("提示: 中间文件保留在 workspace/ 目录中，可用 --skip-to 恢复。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
