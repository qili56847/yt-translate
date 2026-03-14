# yt-translate

英文视频中文配音工具 — 支持 YouTube 链接和本地视频文件，自动人声分离、转录、翻译、语音合成、视频合成的一站式流水线。

## 功能特性

- 支持 YouTube 视频下载（yt-dlp）和本地视频文件导入
- AI 人声/背景音分离（demucs）
- 英文语音识别（Whisper，支持 GPU 加速）
- 中文翻译（OpenRouter API）
- 中文语音合成（Edge-TTS），自动语速对齐
- 视频合成：中文配音 + 背景音混合 + 中文字幕烧录（ffmpeg）
- 每步缓存，断点可恢复

## 流水线

```
download/import → separate → transcribe → translate → synthesize → compose
 yt-dlp/本地导入   demucs     Whisper     OpenRouter   Edge-TTS     ffmpeg
```

## 安装

### 前置依赖

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html)（需加入 PATH）

### 安装 Python 依赖

```bash
pip install -r requirements.txt
```

GPU 加速（推荐，显著加快人声分离和转录速度）：

```bash
pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

### 配置 API Key

翻译步骤需要 OpenRouter API Key：

```bash
export OPENROUTER_API_KEY="your-api-key-here"
```

Windows PowerShell：

```powershell
$env:OPENROUTER_API_KEY="your-api-key-here"
```

## 使用

### CLI

```bash
# YouTube 视频
python main.py "https://www.youtube.com/watch?v=VIDEO_ID"

# 本地视频文件
python main.py --file video.mp4

# 指定选项
python main.py "URL" --voice zh-CN-YunxiNeural --whisper-model large --keep-workspace

# 从指定步骤恢复（需要之前的中间文件）
python main.py "URL" --skip-to translate --keep-workspace
```

### Web UI

```bash
python app.py
```

浏览器访问 `http://localhost:5000`，支持 YouTube 链接和本地文件上传（拖拽或点击选择，最大 2GB），实时进度显示。

## 项目结构

```
├── main.py              # CLI 入口
├── app.py               # Flask Web UI（SSE 实时进度）
├── pipeline.py          # 流水线编排
├── config.py            # 全局配置
├── steps/
│   ├── download.py      # yt-dlp 下载 / 本地文件导入
│   ├── separate.py      # demucs 人声分离
│   ├── transcribe.py    # Whisper 转录
│   ├── translate.py     # OpenRouter 翻译
│   ├── synthesize.py    # Edge-TTS 合成 + 时间对齐
│   └── compose.py       # ffmpeg 混音 + 字幕烧录
├── utils/
│   ├── srt.py           # SRT 解析/写入
│   ├── audio.py         # ffmpeg 音频工具
│   └── progress.py      # 进度报告（CLI + SSE）
└── templates/
    └── index.html       # Web UI 前端
```

## License

MIT
