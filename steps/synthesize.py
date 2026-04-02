"""步骤5：Edge-TTS 生成中文语音 + 时间对齐

核心策略：段落合并 + 全局统一语速
- 合并 Whisper 切碎的连续语流，消除碎片段（<500ms）
- 所有段使用同一个 TTS rate，保证全程语速一致
- 仅对超长段做加速/截断兜底，不再对短段单独减速
- 额外产出烧录用短帧字幕，缩短长句停留时间
"""

import asyncio
import glob
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import edge_tts

from config import (
    TTS_VOICE_DEFAULT, TTS_CONCURRENCY,
    TTS_FIXED_RATE,
    MERGE_GAP_THRESHOLD_MS, MERGE_SHORT_THRESHOLD_MS, MERGE_MAX_DURATION_MS,
    AUDIO_SAMPLE_RATE, SEGMENT_GAP_MS,
)
from utils.audio import get_duration_ms, adjust_speed, truncate_with_fade
from utils.progress import ProgressReporter
from utils.srt import (
    fit_segments_to_audio,
    SubtitleSegment,
    merge_segments,
    parse_srt,
    wrap_long_segments,
    write_srt,
)


def _calculate_fixed_rate() -> str:
    """计算固定全局 TTS rate，所有段使用相同语速确保听感一致。"""
    rate_percent = TTS_FIXED_RATE
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
    segments_with_rates: list[tuple[SubtitleSegment, str]],
    voice: str,
    tts_dir: str,
) -> list[dict]:
    """并发生成所有 TTS 片段（所有段统一语速）"""
    semaphore = asyncio.Semaphore(TTS_CONCURRENCY)
    tasks = [
        _generate_one_segment(seg, voice, rate, tts_dir, semaphore)
        for seg, rate in segments_with_rates
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


ATEMPO_LIMIT = 1.4       # 加速上限，超过此倍率截断


def _align_segment(seg_info: dict, aligned_dir: str) -> dict | None:
    """对齐单个片段。

    统一语速策略下，只处理超长片段：
    - 音频未超窗 → 直接转格式
    - 音频超长但 ≤ ATEMPO_LIMIT → atempo 加速塞入
    - 音频严重超长 → 加速到 ATEMPO_LIMIT 后截断
    """
    raw_path = seg_info["path"]
    index = seg_info["index"]
    actual_ms = seg_info.get("actual_ms")
    max_ms = seg_info.get("max_duration_ms", seg_info["target_duration_ms"])

    if actual_ms is None:
        return None

    aligned_path = os.path.join(aligned_dir, f"aligned_{index:04d}.wav")

    if actual_ms > max_ms:
        # 音频超长：加速或截断
        ratio = actual_ms / max_ms
        if ratio <= ATEMPO_LIMIT:
            adjust_speed(raw_path, aligned_path, ratio)
            aligned_duration_ms = max_ms
        else:
            temp_path = os.path.join(aligned_dir, f"temp_{index:04d}.wav")
            adjust_speed(raw_path, temp_path, ATEMPO_LIMIT)
            truncate_with_fade(temp_path, aligned_path, max_ms)
            os.remove(temp_path)
            aligned_duration_ms = max_ms
    else:
        # 保持全局统一语速，只做格式转换
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path, "-ar", str(AUDIO_SAMPLE_RATE), aligned_path],
            capture_output=True, check=True,
        )
        aligned_duration_ms = actual_ms

    return {
        "path": aligned_path,
        "start_ms": seg_info["start_ms"],
        "duration_ms": aligned_duration_ms,
    }


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


def synthesize(srt_path: str, work_dir: str, voice: str = TTS_VOICE_DEFAULT) -> dict:
    """
    生成中文语音轨道并与原始时间轴对齐。
    返回 {"voice_track": 语音轨路径, "subtitle": 烧录字幕路径}。
    """
    progress = ProgressReporter("合成语音")
    progress.start(f"声音: {voice}")

    tts_dir = os.path.join(work_dir, "tts_segments")
    aligned_dir = os.path.join(work_dir, "tts_aligned")

    # 清除旧缓存（合并逻辑改变了段编号和内容，旧缓存不可复用）
    for d in [tts_dir, aligned_dir]:
        if os.path.isdir(d):
            for f in glob.glob(os.path.join(d, "*")):
                os.remove(f)

    os.makedirs(tts_dir, exist_ok=True)
    os.makedirs(aligned_dir, exist_ok=True)

    raw_segments = parse_srt(srt_path)

    # 1. 合并碎片段和紧邻段（translate 已合并，此处作为安全网）
    segments = merge_segments(
        raw_segments,
        gap_threshold_ms=MERGE_GAP_THRESHOLD_MS,
        short_threshold_ms=MERGE_SHORT_THRESHOLD_MS,
        max_duration_ms=MERGE_MAX_DURATION_MS,
    )
    progress.update(f"合并: {len(raw_segments)} 段 → {len(segments)} 段")

    # 2. 固定全局语速（所有段统一，确保听感一致）
    global_rate = _calculate_fixed_rate()
    progress.update(f"固定语速: {global_rate}，正在生成 {len(segments)} 段 TTS...")

    # 3. 并发生成 TTS（所有段同一语速）
    segments_with_rates = [(seg, global_rate) for seg in segments]
    seg_infos = asyncio.run(_generate_all_segments(segments_with_rates, voice, tts_dir))

    # 4. 测量实际时长
    progress.update("正在测量时长...")
    _measure_durations(seg_infos)

    # 5. 计算每段允许的最大时长（利用段间间隙）
    _calculate_max_durations(seg_infos)

    # 7. 对齐（格式转换 + 必要时 atempo/截断兜底）
    progress.update("正在对齐时间...")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_align_segment, info, aligned_dir) for info in seg_infos]
        results = [f.result() for f in futures]
        aligned = [r for r in results if r is not None]

    # 统计对齐情况
    truncated = 0
    sped_up = 0
    for info in seg_infos:
        actual = info.get("actual_ms")
        if not actual:
            continue
        max_ms = info.get("max_duration_ms", info["target_duration_ms"])
        if actual > max_ms:
            if actual / max_ms > ATEMPO_LIMIT:
                truncated += 1
            else:
                sped_up += 1

    # 8. 按实际语音时长重算字幕时间轴，再生成烧录字幕
    aligned_durations = [
        result["duration_ms"] if result is not None else info["target_duration_ms"]
        for result, info in zip(results, seg_infos)
    ]
    subtitle_segments = fit_segments_to_audio(
        segments,
        aligned_durations,
        min_gap_ms=SEGMENT_GAP_MS,
    )
    merged_srt_path = os.path.join(work_dir, "translated_merged.srt")
    write_srt(subtitle_segments, merged_srt_path)
    display_segments = wrap_long_segments(subtitle_segments)
    display_srt_path = os.path.join(work_dir, "translated_display.srt")
    write_srt(display_segments, display_srt_path)
    progress.update(f"字幕拆分: {len(segments)} 段 → {len(display_segments)} 帧")

    # 9. 合成语音轨
    progress.update("正在合成语音轨道...")
    total_duration_ms = max(seg.end_ms for seg in segments) if segments else 0
    voice_track_path = _mix_voice_track(aligned, work_dir, total_duration_ms)

    progress.done(f"{len(aligned)} 段已完成（{sped_up} 段加速，{truncated} 段截断）")
    return {
        "voice_track": voice_track_path,
        "subtitle": display_srt_path,
        "subtitle_merged": merged_srt_path,
    }
