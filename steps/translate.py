"""步骤4：OpenRouter API (Qwen) 批量翻译 SRT"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

from config import (
    OPENROUTER_BASE_URL, OPENROUTER_API_KEY, TRANSLATE_MODEL,
    TRANSLATE_BATCH_SIZE, TRANSLATE_CONCURRENCY,
)
from utils.progress import ProgressReporter
from utils.srt import SubtitleSegment, parse_srt, write_srt

TRANSLATE_MAX_RETRIES = 3

TRANSLATE_PROMPT = """\
你是字幕翻译专家。将以下英文字幕翻译为简体中文。

要求：
1. 口语化、简洁自然，适合配音朗读
2. 保持原意，但不必逐字翻译
3. 每条中文译文字数控制在原文英文单词数的 0.6-0.8 倍，务必精简（中文语速较快，字数过多会导致配音不同步）
4. 输入共 {count} 条，必须返回恰好 {count} 条翻译，一一对应，不可合并或拆分
5. 返回纯 JSON 数组，每个元素是翻译后的中文文本

输入字幕（共 {count} 条，JSON 数组）：
{subtitles}

请只返回 JSON 数组，不要其他内容。不要输出思考过程。"""


def _translate_batch(client: OpenAI, texts: list[str]) -> list[str]:
    """翻译一批字幕文本"""
    response = client.chat.completions.create(
        model=TRANSLATE_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": TRANSLATE_PROMPT.format(
                    count=len(texts),
                    subtitles=json.dumps(texts, ensure_ascii=False),
                ),
            }
        ],
    )
    response_text = response.choices[0].message.content.strip()
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
    return json.loads(response_text)


def _translate_batch_with_retry(client: OpenAI, texts: list[str]) -> list[str] | None:
    """带重试的批量翻译，失败则降级为逐条翻译"""
    for attempt in range(TRANSLATE_MAX_RETRIES):
        try:
            result = _translate_batch(client, texts)
            if len(result) == len(texts):
                return result
        except Exception:
            if attempt < TRANSLATE_MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    # 批量失败，降级逐条翻译
    translated = []
    for text in texts:
        for attempt in range(TRANSLATE_MAX_RETRIES):
            try:
                single_result = _translate_batch(client, [text])
                translated.append(single_result[0])
                break
            except Exception:
                if attempt < TRANSLATE_MAX_RETRIES - 1:
                    time.sleep(1)
        else:
            translated.append(text)  # 全部失败，保留原文
    return translated


def translate(srt_path: str, work_dir: str) -> str:
    """
    用 OpenRouter (Qwen) 翻译 SRT 字幕。
    返回翻译后的 SRT 文件路径。
    """
    progress = ProgressReporter("翻译")
    progress.start(f"模型: {TRANSLATE_MODEL}")

    translated_path = os.path.join(work_dir, "translated.srt")

    if os.path.exists(translated_path):
        progress.update("翻译结果已存在，跳过")
        progress.done()
        return translated_path

    segments = parse_srt(srt_path)
    client = OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
    )

    total = len(segments)

    # 按批次切分
    batches = []
    for batch_start in range(0, total, TRANSLATE_BATCH_SIZE):
        batch_end = min(batch_start + TRANSLATE_BATCH_SIZE, total)
        batches.append((batch_start, segments[batch_start:batch_end]))

    batch_count = len(batches)
    progress.update(f"共 {total} 段，分 {batch_count} 批，{TRANSLATE_CONCURRENCY} 路并发翻译...")

    # 并发翻译
    results = [None] * batch_count
    completed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=TRANSLATE_CONCURRENCY) as pool:
        future_to_idx = {}
        for idx, (batch_start, batch) in enumerate(batches):
            texts = [seg.text for seg in batch]
            future = pool.submit(_translate_batch_with_retry, client, texts)
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
    for idx, (batch_start, batch) in enumerate(batches):
        translated_texts = results[idx]
        if translated_texts is None:
            translated_texts = [seg.text for seg in batch]
        for seg, cn_text in zip(batch, translated_texts):
            translated_segments.append(SubtitleSegment(
                index=seg.index,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=cn_text,
            ))

    write_srt(translated_segments, translated_path)
    progress.done(f"{len(translated_segments)} 段翻译完成")
    return translated_path
