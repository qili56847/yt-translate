"""步骤2：demucs 人声/背景音分离"""

import os

import numpy as np
import soundfile as sf
import torch

from config import DEMUCS_MODEL
from utils.progress import ProgressReporter


def _load_audio(path: str, target_sr: int = 44100) -> torch.Tensor:
    """用 soundfile 加载音频，返回 (channels, samples) tensor"""
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    # data shape: (samples, channels) -> (channels, samples)
    wav = torch.from_numpy(data.T)
    # 重采样（如果需要）
    if sr != target_sr:
        import torchaudio.functional as F
        wav = F.resample(wav, sr, target_sr)
    return wav


def _save_audio(wav: torch.Tensor, path: str, sr: int = 44100):
    """保存 tensor 为 wav 文件"""
    # (channels, samples) -> (samples, channels)
    data = wav.cpu().numpy().T
    sf.write(path, data, sr)


def separate(audio_path: str, work_dir: str) -> dict:
    """
    用 demucs 分离人声和背景音。
    返回 {"vocals": path, "no_vocals": path}
    """
    progress = ProgressReporter("分离")
    progress.start("人声/背景音分离")

    output_dir = os.path.join(work_dir, DEMUCS_MODEL)
    stem_dir = os.path.join(output_dir, "original_audio")
    vocals_path = os.path.join(stem_dir, "vocals.wav")
    no_vocals_path = os.path.join(stem_dir, "no_vocals.wav")

    if not os.path.exists(vocals_path):
        progress.update("正在加载模型...")
        from demucs.pretrained import get_model
        from demucs.apply import apply_model

        model = get_model(DEMUCS_MODEL)
        model.eval()

        progress.update("正在加载音频...")
        wav = _load_audio(audio_path, model.samplerate)
        # demucs 需要 (batch, channels, samples)
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / ref.std()
        wav = wav.unsqueeze(0)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        progress.update(f"正在分离（设备: {device}，这可能需要几分钟）...")
        if device == "cuda":
            model.to(device)
        with torch.no_grad():
            sources = apply_model(model, wav, device=device, progress=True)

        # sources shape: (1, num_sources, channels, samples)
        sources = sources * ref.std() + ref.mean()

        # 找到 vocals 索引
        source_names = model.sources
        vocals_idx = source_names.index("vocals")

        os.makedirs(stem_dir, exist_ok=True)

        vocals_wav = sources[0, vocals_idx]
        _save_audio(vocals_wav, vocals_path, model.samplerate)

        # no_vocals = 其他所有源的总和
        no_vocals_wav = torch.zeros_like(vocals_wav)
        for i, name in enumerate(source_names):
            if name != "vocals":
                no_vocals_wav += sources[0, i]
        _save_audio(no_vocals_wav, no_vocals_path, model.samplerate)
    else:
        progress.update("分离结果已存在，跳过")

    progress.done()
    return {"vocals": vocals_path, "no_vocals": no_vocals_path}
