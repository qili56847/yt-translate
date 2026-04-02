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


def merge_segments(
    segments: list[SubtitleSegment],
    gap_threshold_ms: int = 100,
    short_threshold_ms: int = 500,
    max_duration_ms: int = 15000,
    text_separator: str = "",
) -> list[SubtitleSegment]:
    """合并 Whisper 切碎的连续语流段落，并修复重叠。

    规则：
    1. 修复重叠：截断前一段使其不超过下一段起始时间
    2. gap <= gap_threshold_ms 的连续段合并为一组
    3. 短于 short_threshold_ms 的碎片段强制合并到相邻段
    4. 合并后时长上限 max_duration_ms
    5. text_separator: 合并文本时的分隔符（中文用""，英文用" "）
    """
    if not segments:
        return segments

    # 修复重叠：截断前一段防止语音叠加
    fixed = list(segments)
    for i in range(len(fixed) - 1):
        if fixed[i].end_ms > fixed[i + 1].start_ms:
            fixed[i] = SubtitleSegment(
                index=fixed[i].index, start_ms=fixed[i].start_ms,
                end_ms=fixed[i + 1].start_ms, text=fixed[i].text,
            )

    # 按 gap 和碎片段规则分组
    groups: list[list[SubtitleSegment]] = [[fixed[0]]]
    for i in range(1, len(fixed)):
        prev = fixed[i - 1]
        cur = fixed[i]
        gap = cur.start_ms - prev.end_ms
        prev_window = prev.end_ms - prev.start_ms
        cur_window = cur.end_ms - cur.start_ms
        is_short = (cur_window < short_threshold_ms or
                    prev_window < short_threshold_ms)
        # 短碎片段也要求间距不能太大（gap_threshold 的 5 倍），避免跨越长静音合并
        force_merge = is_short and gap <= gap_threshold_ms * 5

        if gap <= gap_threshold_ms or force_merge:
            group_duration = cur.end_ms - groups[-1][0].start_ms
            if group_duration <= max_duration_ms:
                groups[-1].append(cur)
                continue
        groups.append([cur])

    # 将每组合并为单个 SubtitleSegment
    merged = []
    for idx, group in enumerate(groups, 1):
        text = text_separator.join(s.text for s in group)
        merged.append(SubtitleSegment(
            index=idx,
            start_ms=group[0].start_ms,
            end_ms=group[-1].end_ms,
            text=text,
        ))
    return merged


def write_srt(segments: list[SubtitleSegment], path: str) -> None:
    """将字幕段列表写入 SRT 文件"""
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{_format_timestamp(seg.start_ms)} --> {_format_timestamp(seg.end_ms)}\n")
            f.write(f"{seg.text}\n\n")
