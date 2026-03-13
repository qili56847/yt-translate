"""SRT 字幕文件解析与写入"""

import re
from dataclasses import dataclass


@dataclass
class SubtitleSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str


def _parse_timestamp(ts: str) -> int:
    """将 SRT 时间戳 'HH:MM:SS,mmm' 转为毫秒"""
    h, m, rest = ts.strip().split(":")
    s, ms = rest.split(",")
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def _format_timestamp(ms: int) -> str:
    """毫秒转 SRT 时间戳"""
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path: str) -> list[SubtitleSegment]:
    """解析 SRT 文件，返回字幕段列表"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    segments = []
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        index = int(lines[0].strip())
        time_match = re.match(
            r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})",
            lines[1].strip(),
        )
        if not time_match:
            continue
        start_ms = _parse_timestamp(time_match.group(1))
        end_ms = _parse_timestamp(time_match.group(2))
        text = "\n".join(lines[2:]).strip()
        segments.append(SubtitleSegment(index=index, start_ms=start_ms, end_ms=end_ms, text=text))

    return segments


def write_srt(segments: list[SubtitleSegment], path: str) -> None:
    """将字幕段列表写入 SRT 文件"""
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{_format_timestamp(seg.start_ms)} --> {_format_timestamp(seg.end_ms)}\n")
            f.write(f"{seg.text}\n\n")
