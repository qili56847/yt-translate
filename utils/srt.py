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


def split_long_segments(
    segments: list[SubtitleSegment],
    max_chars: int = 20,
) -> list[SubtitleSegment]:
    """把长字幕段按标点拆分成短句，用于字幕显示。

    拆分优先级：先按句号/问号/感叹号/分号切，不够短再按逗号/顿号切。
    每个短句按字数比例分配原段的时间窗。
    """
    # 强分隔符（句子边界）和弱分隔符（短语边界）
    strong_punct = re.compile(r"(?<=[。！？；])")
    weak_punct = re.compile(r"(?<=[，、：])")

    result = []
    idx = 1
    for seg in segments:
        if len(seg.text) <= max_chars:
            result.append(SubtitleSegment(idx, seg.start_ms, seg.end_ms, seg.text))
            idx += 1
            continue

        # 先按强标点拆
        parts = [p for p in strong_punct.split(seg.text) if p.strip()]
        # 如果还有超长的，再按弱标点拆
        final_parts = []
        for part in parts:
            if len(part) <= max_chars:
                final_parts.append(part)
            else:
                sub = [p for p in weak_punct.split(part) if p.strip()]
                final_parts.extend(sub)

        if len(final_parts) <= 1:
            result.append(SubtitleSegment(idx, seg.start_ms, seg.end_ms, seg.text))
            idx += 1
            continue

        # 按字数比例分配时间
        total_chars = sum(len(p) for p in final_parts)
        total_ms = seg.end_ms - seg.start_ms
        cursor = seg.start_ms
        for i, part in enumerate(final_parts):
            ratio = len(part) / total_chars if total_chars > 0 else 1 / len(final_parts)
            duration = round(total_ms * ratio)
            end = cursor + duration if i < len(final_parts) - 1 else seg.end_ms
            result.append(SubtitleSegment(idx, cursor, end, part))
            idx += 1
            cursor = end

    return result


def _find_best_split(text: str, target_pos: int, split_positions: list[int], start: int = 0) -> int | None:
    """在 split_positions 中找最接近 target_pos 的切分点"""
    best = None
    for pos in split_positions:
        if pos <= start or pos >= len(text):
            continue
        if best is None or abs(pos - target_pos) < abs(best - target_pos):
            best = pos
    return best


def _wrap_text(text: str, max_chars_per_line: int) -> str:
    """将文本折成两行（在最接近中点的标点处换行）。"""
    if len(text) <= max_chars_per_line:
        return text
    split_positions = [m.end() for m in re.finditer(r"[。！？；，、：]", text)]
    mid = len(text) // 2
    best = _find_best_split(text, mid, split_positions)
    if best:
        return text[:best] + "\n" + text[best:]
    return text


def wrap_long_segments(
    segments: list[SubtitleSegment],
    max_chars_per_line: int = 18,
    max_lines: int = 2,
) -> list[SubtitleSegment]:
    """为视频烧录准备字幕：每帧最多 max_lines 行，每行 ≤ max_chars_per_line。

    - 短文本（≤ max_chars_per_line）：单行，保持时间窗
    - 中等文本（≤ max_chars_per_line × max_lines）：折行，保持时间窗
    - 长文本（> 以上）：按标点拆成多段时间顺序显示，每段折成 ≤ 2 行
    """
    max_screen = max_chars_per_line * max_lines  # 一帧最多显示的字数
    strong_punct = re.compile(r"(?<=[。！？；])")
    weak_punct = re.compile(r"(?<=[，、：])")

    result = []
    idx = 1
    for seg in segments:
        text = seg.text.replace("\n", "")

        if len(text) <= max_screen:
            # 短或中等：折行即可，时间窗不变
            wrapped = _wrap_text(text, max_chars_per_line)
            result.append(SubtitleSegment(idx, seg.start_ms, seg.end_ms, wrapped))
            idx += 1
            continue

        # 长文本：先按标点拆成 ≤ max_screen 的块
        pieces = [p for p in strong_punct.split(text) if p.strip()]
        chunks = []
        for piece in pieces:
            if len(piece) > max_screen:
                sub = [p for p in weak_punct.split(piece) if p.strip()]
                pieces_to_add = sub if sub else [piece]
            else:
                pieces_to_add = [piece]
            for p in pieces_to_add:
                # 尝试合并到上一个 chunk
                if chunks and len(chunks[-1]) + len(p) <= max_screen:
                    chunks[-1] += p
                else:
                    chunks.append(p)

        if len(chunks) <= 1:
            wrapped = _wrap_text(text, max_chars_per_line)
            result.append(SubtitleSegment(idx, seg.start_ms, seg.end_ms, wrapped))
            idx += 1
            continue

        # 按字数比例分配时间
        total_chars = sum(len(c) for c in chunks)
        total_ms = seg.end_ms - seg.start_ms
        cursor = seg.start_ms
        for i, chunk in enumerate(chunks):
            ratio = len(chunk) / total_chars if total_chars > 0 else 1 / len(chunks)
            duration = round(total_ms * ratio)
            end = cursor + duration if i < len(chunks) - 1 else seg.end_ms
            wrapped = _wrap_text(chunk, max_chars_per_line)
            result.append(SubtitleSegment(idx, cursor, end, wrapped))
            idx += 1
            cursor = end

    return result


def fit_segments_to_audio(
    segments: list[SubtitleSegment],
    audio_durations_ms: list[float],
    min_gap_ms: int = 10,
) -> list[SubtitleSegment]:
    """按实际语音时长重算字幕结束时间。

    每段起点保持不变，终点缩到实际语音结束位置。
    如后面还有下一段，则额外确保不越过下一段起点前的 min_gap_ms。
    """
    if len(segments) != len(audio_durations_ms):
        raise ValueError("segments 与 audio_durations_ms 长度不一致")

    fitted: list[SubtitleSegment] = []
    for i, (seg, duration_ms) in enumerate(zip(segments, audio_durations_ms)):
        end_ms = seg.start_ms + round(duration_ms)
        if i < len(segments) - 1:
            latest_end = max(seg.start_ms, segments[i + 1].start_ms - min_gap_ms)
            end_ms = min(end_ms, latest_end)
        end_ms = max(seg.start_ms, end_ms)
        fitted.append(SubtitleSegment(
            index=seg.index,
            start_ms=seg.start_ms,
            end_ms=end_ms,
            text=seg.text,
        ))
    return fitted


def write_srt(segments: list[SubtitleSegment], path: str) -> None:
    """将字幕段列表写入 SRT 文件"""
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{_format_timestamp(seg.start_ms)} --> {_format_timestamp(seg.end_ms)}\n")
            f.write(f"{seg.text}\n\n")


def compute_compressed_timeline(
    seg_infos: list[dict],
    show_tail_ms: int = 200,
    max_gap_ms: int = 300,
) -> tuple[list[dict], list[tuple[int, int]]]:
    """计算压缩后的时间轴与要保留的原视频区间。

    每段的"视觉窗口" = [orig_start, orig_start + actual + show_tail_ms]，段间最多再保
    留 max_gap_ms 的原视频作为转场缓冲。多余停顿从视频和背景音轨中切除。

    参数:
      seg_infos: 段信息列表，每项含 start_ms（原始起点）和 actual_ms（TTS 实际时长），
                 若 actual_ms 缺失则回退到 target_duration_ms。
    返回:
      new_positions: [{index, new_start_ms, kept_span_ms, voice_duration_ms}, ...]
                     每段在压缩后时间轴上的位置；kept_span_ms 是本段占用的新时间轴长度。
      keep_ranges: [(orig_start, orig_end), ...] 原视频中要保留的区间（按时间顺序）。
    """
    if not seg_infos:
        return [], []

    new_positions: list[dict] = []
    keep_ranges: list[tuple[int, int]] = []
    cursor = 0

    for i, info in enumerate(seg_infos):
        orig_start = info["start_ms"]
        actual = info.get("actual_ms") or info.get("target_duration_ms") or 0

        right = orig_start + actual + show_tail_ms

        if i < len(seg_infos) - 1:
            next_start = seg_infos[i + 1]["start_ms"]
            dead_time = next_start - right
            if dead_time > 0:
                right += min(dead_time, max_gap_ms)
            if right > next_start:
                right = next_start

        if right <= orig_start:
            right = orig_start + 1

        kept_span = right - orig_start
        keep_ranges.append((orig_start, right))
        new_positions.append({
            "index": info.get("index", i),
            "new_start_ms": cursor,
            "kept_span_ms": kept_span,
            "voice_duration_ms": actual,
        })
        cursor += kept_span

    return new_positions, keep_ranges
