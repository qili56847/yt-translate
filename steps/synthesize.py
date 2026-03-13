"""步骤5：Edge-TTS 生成中文语音 + 时间对齐

核心策略：全局统一语速
- 根据所有字幕的总字数和总时长，计算一个全局 TTS rate
- 所有段用同一个 rate 生成，确保听感速度一致
- 不做 atempo 变速，短段留自然停顿，长段利用间隙或截断
"""

import asyncio
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import edge_tts

from config import (
    TTS_VOICE_DEFAULT, TTS_CONCURRENCY,
    TTS_MS_PER_CHAR, TTS_RATE_CLAMP_MIN, TTS_RATE_CLAMP_MAX,
    AUDIO_SAMPLE_RATE, FADE_OUT_MS, SEGMENT_GAP_MS,
)
from utils.audio import get_duration_ms, adjust_speed, truncate_with_fade
from utils.progress import ProgressReporter
from utils.srt import SubtitleSegment, parse_srt


def _count_chars(text: str) -> int:
    """统计有效字符数（去标点）"""
    clean = re.sub(r'[^\u4e00-\u9fff\u3400-\u4dbfa-zA-Z0-9]', '', text)
    return max(len(clean), 1)


def _calculate_global_rate(segments: list[SubtitleSegment]) -> str:
    """根据全部字幕的总字数和总时长，计算一个统一的 TTS rate。

    所有段共用这个 rate，保证语速听感一致。
    """
    total_chars = sum(_count_chars(seg.text) for seg in segments)
    total_ms = sum(seg.end_ms - seg.start_ms for seg in segments)

    if total_ms <= 0 or total_chars <= 0:
        return "+0%"

    # 需要的每字符时长
    needed_ms_per_char = total_ms / total_chars
    # TTS 自然每字符时长
    natural_ms_per_char = TTS_MS_PER_CHAR

    # rate_factor > 1 → 需要说快些，< 1 → 需要说慢些
    rate_factor = natural_ms_per_char / needed_ms_per_char
    rate_percent = (rate_factor - 1) * 100

    # 限制范围，防止语速过快或过慢
    rate_percent = max(TTS_RATE_CLAMP_MIN, min(TTS_RATE_CLAMP_MAX, rate_percent))

    sign = "+" if rate_percent >= 0 else ""
    return f"{sign}{rate_percent:.0f}%"


async def _generate_one_segment(
    seg: SubtitleSegment,
    voice: str,
    rate: str,
    tts_dir: str,
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> dict:
    """生成单个 TTS 片段，所有段用同一个 rate"""
    async with semaphore:
        seg_path = os.path.join(tts_dir, f"seg_{seg.index:04d}.mp3")
        target_duration = seg.end_ms - seg.start_ms

        # 跳过已存在且非空的文件
        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
            return {
                "index": seg.index,
                "path": seg_path,
                "start_ms": seg.start_ms,
                "target_duration_ms": target_duration,
            }

        for attempt in range(max_retries):
            try:
                communicate = edge_tts.Communicate(seg.text, voice, rate=rate)
                await communicate.save(seg_path)
                if os.path.getsize(seg_path) > 0:
                    break
                os.remove(seg_path)
            except Exception:
                if os.path.exists(seg_path):
                    os.remove(seg_path)
            if attempt < max_retries - 1:
                await asyncio.sleep(1)

        return {
            "index": seg.index,
            "path": seg_path,
            "start_ms": seg.start_ms,
            "target_duration_ms": target_duration,
        }


async def _generate_all_segments(
    segments: list[SubtitleSegment],
    voice: str,
    rate: str,
    tts_dir: str,
) -> list[dict]:
    """并发生成所有 TTS 片段（统一语速）"""
    semaphore = asyncio.Semaphore(TTS_CONCURRENCY)
    tasks = [
        _generate_one_segment(seg, voice, rate, tts_dir, semaphore)
        for seg in segments
    ]
    return await asyncio.gather(*tasks)


def _measure_durations(seg_infos: list[dict]) -> list[dict]:
    """测量每段 TTS 的实际时长"""
    for info in seg_infos:
        info["actual_ms"] = None
        try:
            info["actual_ms"] = get_duration_ms(info["path"])
        except Exception:
            pass
    return seg_infos


def _calculate_max_durations(seg_infos: list[dict]) -> list[dict]:
    """计算每段允许的最大时长。

    允许音频延伸到下一段开始前 SEGMENT_GAP_MS 的位置，
    而不是严格卡在自己的 end_ms。这样减少不必要的截断。
    """
    for i, info in enumerate(seg_infos):
        if i < len(seg_infos) - 1:
            next_start = seg_infos[i + 1]["start_ms"]
            # 可用时间 = 到下一段开始，减去一点间隔
            available = next_start - info["start_ms"] - SEGMENT_GAP_MS
            # 至少不小于自己的 target
            info["max_duration_ms"] = max(info["target_duration_ms"], available)
        else:
            # 最后一段：用自身 target
            info["max_duration_ms"] = info["target_duration_ms"]
    return seg_infos


ATEMPO_LIMIT = 1.4  # 超过此倍率才截断，以下用 atempo 加速塞入


def _align_segment(seg_info: dict, aligned_dir: str) -> dict | None:
    """对齐单个片段。

    - 不超长 → 直接转格式
    - 超长但 ≤ ATEMPO_LIMIT → 用 atempo 轻微加速，不丢字
    - 超长且 > ATEMPO_LIMIT → atempo 加速到 ATEMPO_LIMIT 后截断剩余
    """
    raw_path = seg_info["path"]
    index = seg_info["index"]
    actual_ms = seg_info.get("actual_ms")
    max_ms = seg_info.get("max_duration_ms", seg_info["target_duration_ms"])

    if actual_ms is None:
        return None

    aligned_path = os.path.join(aligned_dir, f"aligned_{index:04d}.wav")

    if actual_ms <= max_ms:
        # 不超长：直接转 wav，不变速
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-ar", str(AUDIO_SAMPLE_RATE), aligned_path],
            capture_output=True, check=True,
        )
    else:
        ratio = actual_ms / max_ms

        if ratio <= ATEMPO_LIMIT:
            # 轻微加速即可塞入，不会丢字
            adjust_speed(raw_path, aligned_path, ratio)
        else:
            # 先加速到 ATEMPO_LIMIT，再截断剩余部分
            temp_path = os.path.join(aligned_dir, f"temp_{index:04d}.wav")
            adjust_speed(raw_path, temp_path, ATEMPO_LIMIT)
            truncate_with_fade(temp_path, aligned_path, max_ms)
            os.remove(temp_path)

    return {"path": aligned_path, "start_ms": seg_info["start_ms"]}


MIX_BATCH_SIZE = 50


def _mix_segments_batch(segments: list[dict], output_path: str, total_duration_ms: float) -> str:
    """混合一批片段到单个音轨"""
    if not segments:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i",
                f"anullsrc=r={AUDIO_SAMPLE_RATE}:cl=stereo",
                "-t", f"{total_duration_ms / 1000:.3f}",
                output_path,
            ],
            capture_output=True, check=True,
        )
        return output_path

    inputs = []
    filter_parts = []

    for i, seg in enumerate(segments):
        inputs.extend(["-i", seg["path"]])
        delay = seg["start_ms"]
        filter_parts.append(f"[{i}]adelay={delay}|{delay}[d{i}]")

    mix_inputs = "".join(f"[d{i}]" for i in range(len(segments)))
    filter_parts.append(f"{mix_inputs}amix=inputs={len(segments)}:normalize=0[out]")

    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", str(AUDIO_SAMPLE_RATE),
        "-ac", "2",
        output_path,
    ]

    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


def _mix_voice_track(aligned_segments: list[dict], work_dir: str, total_duration_ms: float) -> str:
    """用 ffmpeg adelay 将所有对齐片段放到时间轴上，合成一条完整语音轨。
    分批混合以避免内存溢出。"""
    output_path = os.path.join(work_dir, "chinese_voice_track.wav")

    if not aligned_segments:
        _mix_segments_batch([], output_path, total_duration_ms)
        return output_path

    if len(aligned_segments) <= MIX_BATCH_SIZE:
        _mix_segments_batch(aligned_segments, output_path, total_duration_ms)
        return output_path

    # 分批混合
    mix_dir = os.path.join(work_dir, "mix_temp")
    os.makedirs(mix_dir, exist_ok=True)
    intermediate_tracks = []

    for batch_idx in range(0, len(aligned_segments), MIX_BATCH_SIZE):
        batch = aligned_segments[batch_idx:batch_idx + MIX_BATCH_SIZE]
        batch_path = os.path.join(mix_dir, f"mix_batch_{batch_idx:04d}.wav")
        _mix_segments_batch(batch, batch_path, total_duration_ms)
        intermediate_tracks.append(batch_path)

    if len(intermediate_tracks) == 1:
        os.replace(intermediate_tracks[0], output_path)
    else:
        inputs = []
        for track in intermediate_tracks:
            inputs.extend(["-i", track])

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex",
            f"amix=inputs={len(intermediate_tracks)}:normalize=0[out]",
            "-map", "[out]",
            "-ar", str(AUDIO_SAMPLE_RATE),
            "-ac", "2",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)

    for track in intermediate_tracks:
        if os.path.exists(track):
            os.remove(track)
    try:
        os.rmdir(mix_dir)
    except OSError:
        pass

    return output_path


def synthesize(srt_path: str, work_dir: str, voice: str = TTS_VOICE_DEFAULT) -> str:
    """
    生成中文语音轨道并与原始时间轴对齐。
    返回中文语音轨路径。
    """
    progress = ProgressReporter("合成语音")
    progress.start(f"声音: {voice}")

    tts_dir = os.path.join(work_dir, "tts_segments")
    aligned_dir = os.path.join(work_dir, "tts_aligned")
    os.makedirs(tts_dir, exist_ok=True)
    os.makedirs(aligned_dir, exist_ok=True)

    segments = parse_srt(srt_path)

    # 0. 重叠检测：截断前一段防止语音叠加
    for i in range(len(segments) - 1):
        cur = segments[i]
        nxt = segments[i + 1]
        if cur.end_ms > nxt.start_ms:
            segments[i] = SubtitleSegment(
                index=cur.index,
                start_ms=cur.start_ms,
                end_ms=nxt.start_ms,
                text=cur.text,
            )

    # 1. 计算全局统一语速
    global_rate = _calculate_global_rate(segments)
    progress.update(f"全局语速: {global_rate}，正在生成 {len(segments)} 段 TTS...")

    # 2. 并发生成 TTS（所有段同一语速）
    seg_infos = asyncio.run(_generate_all_segments(segments, voice, global_rate, tts_dir))

    # 3. 测量实际时长
    progress.update("正在测量时长...")
    _measure_durations(seg_infos)

    # 4. 计算每段允许的最大时长（利用段间间隙）
    _calculate_max_durations(seg_infos)

    # 5. 对齐（仅格式转换 + 必要截断，不做变速）
    progress.update("正在对齐时间...")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_align_segment, info, aligned_dir) for info in seg_infos]
        results = [f.result() for f in futures]
        aligned = [r for r in results if r is not None]

    # 统计截断情况
    truncated = sum(
        1 for info in seg_infos
        if info.get("actual_ms") and info["actual_ms"] > info.get("max_duration_ms", info["target_duration_ms"])
    )

    # 6. 合成语音轨
    progress.update("正在合成语音轨道...")
    total_duration_ms = max(seg.end_ms for seg in segments) if segments else 0
    voice_track_path = _mix_voice_track(aligned, work_dir, total_duration_ms)

    progress.done(f"{len(aligned)} 段已完成（{truncated} 段截断）")
    return voice_track_path
