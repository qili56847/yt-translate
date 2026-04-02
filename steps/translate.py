"""步骤4：OpenRouter API (Qwen) 批量翻译 SRT

翻译时根据每段时间窗计算目标中文字数，从根源控制语音时长匹配。
"""

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI


def _extract_text(content):
    """从 API 响应的 content 中提取纯文本。

    某些模型（如 Grok 4.1）返回 list[dict] 而非 str。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "")
        # fallback: 拼接所有非 thinking 部分
        return "".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") != "thinking"
        )
    return str(content)

from config import (
    OPENROUTER_BASE_URL, OPENROUTER_API_KEY, TRANSLATE_MODEL,
    TRANSLATE_BATCH_SIZE, TRANSLATE_CONCURRENCY,
    TTS_NATURAL_MS_PER_CHAR, TTS_FIXED_RATE, TTS_TARGET_FILL,
    TRANSLATE_CHAR_TOLERANCE,
    MERGE_GAP_THRESHOLD_MS, MERGE_SHORT_THRESHOLD_MS, MERGE_MAX_DURATION_MS,
)
from utils.progress import ProgressReporter
from utils.srt import SubtitleSegment, parse_srt, write_srt, merge_segments

TRANSLATE_MAX_RETRIES = 3
TRANSLATE_CACHE_VERSION = 2

TRANSLATE_PROMPT = """\
你是字幕翻译专家。将以下英文字幕翻译为简体中文。

要求：
1. 口语化、简洁自然，适合配音朗读
2. 保持原意，但不必逐字翻译
3. **严格控制每条译文的中文字数**：每条输入标注了 target_chars（目标字数），翻译后的中文字数必须在 target_chars ± 2 字范围内。
   - 字数过多 → 语音被截断
   - 字数过少 → 出现长停顿
   - **宁可多1-2字，也不要少于目标字数**
   - 如果原文信息量不够填满目标字数，可以适当补充语气词或展开表达
   - 如果句子里必须保留英文术语、产品名、代码名或数字，周围中文要进一步压缩，因为这类内容朗读更占时间
4. 输入共 {count} 条，必须返回恰好 {count} 条翻译，一一对应，不可合并或拆分
5. 返回纯 JSON 数组，每个元素是翻译后的中文文本字符串

输入（JSON 数组，每项含英文原文 text 和目标中文字数 target_chars）：
{subtitles}

请只返回 JSON 数组，不要其他内容。不要输出思考过程。"""


RETRANSLATE_PROMPT = """\
以下中文译文的字数不符合要求，请重新翻译。

要求：
1. 口语化、简洁自然，适合配音朗读
2. **每条译文的中文字数必须严格接近 target_chars（误差 ±2 字）**
3. 上次翻译的 actual_chars 偏离了目标，请调整
4. 返回纯 JSON 数组，每个元素是重新翻译后的中文文本字符串

输入（JSON 数组，每项含英文原文、目标字数、上次译文及其实际字数）：
{items}

请只返回 JSON 数组，不要其他内容。不要输出思考过程。"""


def _count_chinese_chars(text: str) -> int:
    """统计有效字符数（中文字+字母数字，去标点）"""
    import re
    clean = re.sub(r'[^\u4e00-\u9fff\u3400-\u4dbfa-zA-Z0-9]', '', text)
    return len(clean)


def _calculate_target_chars(window_ms: int) -> int:
    """根据时间窗计算目标中文字数。

    公式：target = window_ms * fill_factor / ms_per_char_at_fixed_rate
    """
    ms_per_char = TTS_NATURAL_MS_PER_CHAR / (1 + TTS_FIXED_RATE / 100)
    target = round(window_ms * TTS_TARGET_FILL / ms_per_char)
    return max(target, 2)


def _build_cache_hash(segments: list[SubtitleSegment]) -> str:
    """为翻译缓存构建稳定签名，包含源字幕和当前翻译策略。"""
    payload = {
        "cache_version": TRANSLATE_CACHE_VERSION,
        "model": TRANSLATE_MODEL,
        "tts_natural_ms_per_char": TTS_NATURAL_MS_PER_CHAR,
        "tts_fixed_rate": TTS_FIXED_RATE,
        "tts_target_fill": TTS_TARGET_FILL,
        "char_tolerance": TRANSLATE_CHAR_TOLERANCE,
        "segments": [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
            for s in segments
        ],
    }
    return hashlib.md5(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]


def _find_length_violations(
    segments: list[SubtitleSegment],
    target_chars_map: dict[int, int],
) -> list[dict]:
    """找出字数明显偏离目标的字幕段。"""
    violations = []
    for seg in segments:
        target = target_chars_map[seg.index]
        actual = _count_chinese_chars(seg.text)
        if abs(actual - target) > TRANSLATE_CHAR_TOLERANCE:
            violations.append({
                "seg": seg,
                "target_chars": target,
                "actual_chars": actual,
            })
    return violations


def _translate_batch(client: OpenAI, items: list[dict]) -> list[str]:
    """翻译一批字幕文本。items: [{"text": str, "target_chars": int}, ...]"""
    response = client.chat.completions.create(
        model=TRANSLATE_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": TRANSLATE_PROMPT.format(
                    count=len(items),
                    subtitles=json.dumps(items, ensure_ascii=False),
                ),
            }
        ],
    )
    response_text = _extract_text(response.choices[0].message.content).strip()
    # 提取 JSON（处理可能的 markdown 代码块包裹）
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        json_lines = []
        inside = False
        for line in lines:
            if line.strip().startswith("```") and not inside:
                inside = True
                continue
            if line.strip() == "```" and inside:
                break
            if inside:
                json_lines.append(line)
        response_text = "\n".join(json_lines)
    result = json.loads(response_text)
    # 确保每个元素是字符串（API 可能返回 dict 列表）
    normalized = []
    for item in result:
        if isinstance(item, str):
            normalized.append(item)
        elif isinstance(item, dict):
            normalized.append(item.get("text") or item.get("translation") or next(iter(item.values()), ""))
        else:
            normalized.append(str(item))
    return normalized


def _translate_batch_with_retry(client: OpenAI, items: list[dict]) -> list[str] | None:
    """带重试的批量翻译，失败则降级为逐条翻译"""
    for attempt in range(TRANSLATE_MAX_RETRIES):
        try:
            result = _translate_batch(client, items)
            if len(result) == len(items):
                return result
        except Exception:
            if attempt < TRANSLATE_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    # 批量失败，降级逐条翻译
    translated = []
    for item in items:
        for attempt in range(TRANSLATE_MAX_RETRIES):
            try:
                single_result = _translate_batch(client, [item])
                translated.append(single_result[0])
                break
            except Exception:
                if attempt < TRANSLATE_MAX_RETRIES - 1:
                    time.sleep(1)
        else:
            translated.append(item["text"])  # 全部失败，保留原文
    return translated


def translate(srt_path: str, work_dir: str) -> str:
    """
    用 OpenRouter (Qwen) 翻译 SRT 字幕。
    先合并 Whisper 碎片段，再按时间窗计算目标字数翻译。
    返回翻译后的 SRT 文件路径。
    """
    progress = ProgressReporter("翻译")
    progress.start(f"模型: {TRANSLATE_MODEL}")

    translated_path = os.path.join(work_dir, "translated.srt")

    # 解析英文 SRT 并合并碎片段
    raw_segments = parse_srt(srt_path)
    segments = merge_segments(
        raw_segments,
        gap_threshold_ms=MERGE_GAP_THRESHOLD_MS,
        short_threshold_ms=MERGE_SHORT_THRESHOLD_MS,
        max_duration_ms=MERGE_MAX_DURATION_MS,
        text_separator=" ",  # 英文用空格拼接
    )
    progress.update(f"合并: {len(raw_segments)} 段 → {len(segments)} 段")

    # 计算每段目标字数
    target_chars_map = {}
    for seg in segments:
        window_ms = seg.end_ms - seg.start_ms
        target_chars_map[seg.index] = _calculate_target_chars(window_ms)

    # 缓存检查：源字幕 + 翻译策略 + 长度校验都匹配才复用
    source_hash = _build_cache_hash(segments)
    cache_hash_path = os.path.join(work_dir, "translated.hash")
    if os.path.exists(translated_path) and os.path.exists(cache_hash_path):
        stored_hash = open(cache_hash_path, "r", encoding="utf-8").read().strip()
        if stored_hash == source_hash:
            cached_segments = parse_srt(translated_path)
            cache_valid = len(cached_segments) == len(segments)
            violations = (
                _find_length_violations(cached_segments, target_chars_map)
                if cache_valid else [{"seg": None}]
            )
            if cache_valid and not violations:
                progress.update("翻译结果已存在且通过当前规则校验，跳过")
                progress.done()
                return translated_path
            progress.update("缓存译文未通过当前长度校验，重新翻译")
        else:
            progress.update("源文本或翻译规则已变化，重新翻译")

    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
    )

    total = len(segments)

    # 按批次切分
    batches = []
    for batch_start in range(0, total, TRANSLATE_BATCH_SIZE):
        batch_end = min(batch_start + TRANSLATE_BATCH_SIZE, total)
        batch_segs = segments[batch_start:batch_end]
        # 构建带目标字数的输入
        items = [
            {"text": seg.text, "target_chars": target_chars_map[seg.index]}
            for seg in batch_segs
        ]
        batches.append((batch_start, batch_segs, items))

    batch_count = len(batches)
    progress.update(f"共 {total} 段，分 {batch_count} 批，{TRANSLATE_CONCURRENCY} 路并发翻译...")

    # 并发翻译
    results = [None] * batch_count
    completed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=TRANSLATE_CONCURRENCY) as pool:
        future_to_idx = {}
        for idx, (batch_start, batch_segs, items) in enumerate(batches):
            future = pool.submit(_translate_batch_with_retry, client, items)
            future_to_idx[future] = idx

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
            completed += 1

            elapsed = time.time() - start_time
            if completed > 0:
                eta = elapsed / completed * (batch_count - completed)
                progress.update(f"已完成 {completed}/{batch_count} 批，预计剩余 {eta:.0f}s")

    # 组装结果
    translated_segments = []
    for idx, (batch_start, batch_segs, items) in enumerate(batches):
        translated_texts = results[idx]
        if translated_texts is None:
            translated_texts = [seg.text for seg in batch_segs]
        for seg, cn_text in zip(batch_segs, translated_texts):
            translated_segments.append(SubtitleSegment(
                index=seg.index,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=cn_text,
            ))

    # 校验字数偏差，收集需要重译的段
    need_retranslate = _find_length_violations(translated_segments, target_chars_map)
    for item in need_retranslate:
        orig_seg = next(s for s in segments if s.index == item["seg"].index)
        item["orig_text"] = orig_seg.text

    if need_retranslate:
        progress.update(f"校验: {len(need_retranslate)}/{len(translated_segments)} 段字数偏差过大，重译中...")

        # 按批次重译
        retrans_batches = []
        for i in range(0, len(need_retranslate), TRANSLATE_BATCH_SIZE):
            retrans_batches.append(need_retranslate[i:i + TRANSLATE_BATCH_SIZE])

        for batch in retrans_batches:
            retrans_items = [
                {
                    "text": item["orig_text"],
                    "target_chars": item["target_chars"],
                    "previous_translation": item["seg"].text,
                    "actual_chars": item["actual_chars"],
                }
                for item in batch
            ]
            for attempt in range(TRANSLATE_MAX_RETRIES):
                try:
                    response = client.chat.completions.create(
                        model=TRANSLATE_MODEL,
                        max_tokens=4096,
                        messages=[{
                            "role": "user",
                            "content": RETRANSLATE_PROMPT.format(
                                items=json.dumps(retrans_items, ensure_ascii=False),
                            ),
                        }],
                    )
                    resp_text = _extract_text(response.choices[0].message.content).strip()
                    if resp_text.startswith("```"):
                        lines = resp_text.splitlines()
                        json_lines = []
                        inside = False
                        for line in lines:
                            if line.strip().startswith("```") and not inside:
                                inside = True
                                continue
                            if line.strip() == "```" and inside:
                                break
                            if inside:
                                json_lines.append(line)
                        resp_text = "\n".join(json_lines)
                    new_texts_raw = json.loads(resp_text)
                    new_texts = []
                    for item in new_texts_raw:
                        if isinstance(item, str):
                            new_texts.append(item)
                        elif isinstance(item, dict):
                            new_texts.append(item.get("text") or item.get("translation") or next(iter(item.values()), ""))
                        else:
                            new_texts.append(str(item))
                    if len(new_texts) == len(batch):
                        for item, new_text in zip(batch, new_texts):
                            item["seg"].text = new_text
                        break
                except Exception:
                    if attempt < TRANSLATE_MAX_RETRIES - 1:
                        time.sleep(2 ** attempt)

        # 统计改善情况
        still_bad = sum(
            1 for item in need_retranslate
            if abs(_count_chinese_chars(item["seg"].text) - item["target_chars"]) > TRANSLATE_CHAR_TOLERANCE
        )
        progress.update(f"重译完成: {len(need_retranslate) - still_bad}/{len(need_retranslate)} 段已修正")

    write_srt(translated_segments, translated_path)
    with open(cache_hash_path, "w") as f:
        f.write(source_hash)
    progress.done(f"{len(translated_segments)} 段翻译完成")
    return translated_path
