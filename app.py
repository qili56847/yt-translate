"""Flask Web 应用 —— 英文视频中文配音工具"""

import json
import os
import queue
import threading
import time
import uuid

from flask import Flask, render_template, request, jsonify, Response, send_file

from config import WORKSPACE_ROOT, TTS_VOICE_DEFAULT, WHISPER_MODEL_DEFAULT, MAX_UPLOAD_SIZE
from pipeline import run_pipeline, STEPS, _extract_video_id, _generate_local_id
from utils.progress import set_event_queue

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE

# 存储任务状态：task_id -> {...}
tasks: dict[str, dict] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_task():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    voice = data.get("voice", TTS_VOICE_DEFAULT)
    whisper_model = data.get("whisper_model", WHISPER_MODEL_DEFAULT)
    skip_to = data.get("skip_to") or None

    task_id = uuid.uuid4().hex[:8]
    video_id = _extract_video_id(url)
    work_dir = os.path.join(WORKSPACE_ROOT, video_id)
    output_path = os.path.join(work_dir, "output.mp4")

    event_queue = queue.Queue()

    tasks[task_id] = {
        "status": "running",
        "url": url,
        "video_id": video_id,
        "output_path": output_path,
        "queue": event_queue,
        "error": None,
    }

    def run():
        set_event_queue(event_queue)
        try:
            run_pipeline(
                video_url=url,
                output_path=output_path,
                voice=voice,
                whisper_model=whisper_model,
                keep_workspace=True,
                skip_to=skip_to,
            )
            event_queue.put({"type": "complete", "step": "", "message": "done", "output": output_path})
            tasks[task_id]["status"] = "complete"
        except Exception as e:
            event_queue.put({"type": "error", "step": "", "message": str(e)})
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = str(e)
        finally:
            set_event_queue(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id, "video_id": video_id})


@app.route("/api/upload", methods=["POST"])
def upload_task():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    voice = request.form.get("voice", TTS_VOICE_DEFAULT)
    whisper_model = request.form.get("whisper_model", WHISPER_MODEL_DEFAULT)
    skip_to = request.form.get("skip_to") or None

    task_id = uuid.uuid4().hex[:8]

    # 保存上传文件到临时位置
    upload_dir = os.path.join(WORKSPACE_ROOT, "_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = f"{task_id}_{file.filename}"
    upload_path = os.path.join(upload_dir, safe_name)
    file.save(upload_path)

    # 生成 video_id
    video_id = _generate_local_id(upload_path)
    work_dir = os.path.join(WORKSPACE_ROOT, video_id)
    os.makedirs(work_dir, exist_ok=True)
    output_path = os.path.join(work_dir, "output.mp4")

    event_queue = queue.Queue()

    tasks[task_id] = {
        "status": "running",
        "url": None,
        "video_id": video_id,
        "output_path": output_path,
        "queue": event_queue,
        "error": None,
    }

    def run():
        set_event_queue(event_queue)
        try:
            run_pipeline(
                local_file=upload_path,
                output_path=output_path,
                voice=voice,
                whisper_model=whisper_model,
                keep_workspace=True,
                skip_to=skip_to,
            )
            event_queue.put({"type": "complete", "step": "", "message": "done", "output": output_path})
            tasks[task_id]["status"] = "complete"
        except Exception as e:
            event_queue.put({"type": "error", "step": "", "message": str(e)})
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = str(e)
        finally:
            set_event_queue(None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return jsonify({"task_id": task_id, "video_id": video_id})


@app.route("/api/events/<task_id>")
def stream_events(task_id):
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404

    def generate():
        q = tasks[task_id]["queue"]
        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event["type"] in ("complete", "error"):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/download/<task_id>")
def download_output(task_id):
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404
    task = tasks[task_id]
    if task["status"] != "complete":
        return jsonify({"error": "Task not complete"}), 400
    return send_file(task["output_path"], as_attachment=True, download_name="dubbed_output.mp4")


if __name__ == "__main__":
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
