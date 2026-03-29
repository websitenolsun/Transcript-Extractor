"""
Video Transcription Web App — Flask Backend
Supports chunked uploads up to 5GB with real-time progress via SSE.
"""

import os
import json
import time
from pathlib import Path

from flask import Flask, request, jsonify, Response, render_template, send_file
from flask_cors import CORS

from transcriber import TranscriptionEngine, generate_srt, generate_vtt

# ── Config ──────────────────────────────────────────────────────────────
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
CHUNK_DIR = os.path.join(UPLOAD_DIR, "chunks")
MAX_FILE_SIZE = 5 * 1024 * 1024 * 1024  # 5 GB
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CHUNK_DIR, exist_ok=True)

# ── App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB per chunk

engine = TranscriptionEngine(model_size=WHISPER_MODEL, upload_dir=UPLOAD_DIR)


# ── Routes ──────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload/chunk", methods=["POST"])
def upload_chunk():
    """Receive a single chunk of a file upload."""
    file = request.files.get("chunk")
    upload_id = request.form.get("upload_id")
    chunk_index = request.form.get("chunk_index")
    total_chunks = request.form.get("total_chunks")
    filename = request.form.get("filename")
    total_size = request.form.get("total_size")

    if not all([file, upload_id, chunk_index, total_chunks, filename]):
        return jsonify({"error": "Eksik parametreler"}), 400

    # Check file size
    if total_size and int(total_size) > MAX_FILE_SIZE:
        return jsonify({"error": "Dosya boyutu 5GB sınırını aşıyor"}), 413

    # Save chunk
    chunk_dir = os.path.join(CHUNK_DIR, upload_id)
    os.makedirs(chunk_dir, exist_ok=True)

    chunk_path = os.path.join(chunk_dir, f"chunk_{int(chunk_index):06d}")
    file.save(chunk_path)

    return jsonify({
        "status": "ok",
        "chunk_index": int(chunk_index),
        "total_chunks": int(total_chunks),
    })


@app.route("/upload/complete", methods=["POST"])
def upload_complete():
    """Merge chunks and start transcription."""
    data = request.get_json()
    upload_id = data.get("upload_id")
    filename = data.get("filename")
    total_chunks = data.get("total_chunks")

    if not all([upload_id, filename, total_chunks]):
        return jsonify({"error": "Eksik parametreler"}), 400

    chunk_dir = os.path.join(CHUNK_DIR, upload_id)
    if not os.path.exists(chunk_dir):
        return jsonify({"error": "Yükleme bulunamadı"}), 404

    # Sanitize filename
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    if not safe_name:
        safe_name = "video.mp4"

    # Merge chunks
    output_path = os.path.join(UPLOAD_DIR, f"{upload_id}_{safe_name}")

    try:
        with open(output_path, "wb") as outfile:
            for i in range(int(total_chunks)):
                chunk_path = os.path.join(chunk_dir, f"chunk_{i:06d}")
                if not os.path.exists(chunk_path):
                    return jsonify({"error": f"Chunk {i} eksik"}), 400
                with open(chunk_path, "rb") as chunk_file:
                    while True:
                        data_block = chunk_file.read(1024 * 1024)  # 1MB at a time
                        if not data_block:
                            break
                        outfile.write(data_block)

        # Cleanup chunks
        import shutil
        shutil.rmtree(chunk_dir, ignore_errors=True)

    except Exception as e:
        return jsonify({"error": f"Dosya birleştirme hatası: {str(e)}"}), 500

    # Create and start transcription job
    job = engine.create_job(output_path)
    engine.start_job(job.job_id)

    return jsonify({
        "job_id": job.job_id,
        "message": "Transkripsiyon başlatıldı",
    })


@app.route("/status/<job_id>")
def job_status_sse(job_id):
    """Server-Sent Events endpoint for real-time progress."""
    job = engine.jobs.get(job_id)
    if not job:
        return jsonify({"error": "İş bulunamadı"}), 404

    def generate():
        last_progress = -1
        while True:
            current = engine.jobs.get(job_id)
            if not current:
                break

            # Only send when progress changes
            if current.progress != last_progress or current.status.value in ("completed", "failed"):
                data = json.dumps(current.to_dict(), ensure_ascii=False)
                yield f"data: {data}\n\n"
                last_progress = current.progress

                if current.status.value in ("completed", "failed"):
                    break

            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/result/<job_id>")
def get_result(job_id):
    """Get full transcription result."""
    job = engine.jobs.get(job_id)
    if not job:
        return jsonify({"error": "İş bulunamadı"}), 404

    if job.status.value != "completed":
        return jsonify({"error": "Transkripsiyon henüz tamamlanmadı", "status": job.status.value}), 400

    return jsonify({
        "job_id": job.job_id,
        "text": job.result.text,
        "segments": job.result.segments,
        "language": job.result.language,
        "duration": job.result.duration,
    })


@app.route("/download/<job_id>/<fmt>")
def download_result(job_id, fmt):
    """Download transcription in specified format (txt, srt, vtt)."""
    job = engine.jobs.get(job_id)
    if not job:
        return jsonify({"error": "İş bulunamadı"}), 404

    if job.status.value != "completed" or not job.result:
        return jsonify({"error": "Transkripsiyon henüz tamamlanmadı"}), 400

    if fmt == "txt":
        content = job.result.text
        mimetype = "text/plain"
        ext = "txt"
    elif fmt == "srt":
        content = generate_srt(job.result.segments)
        mimetype = "application/x-subrip"
        ext = "srt"
    elif fmt == "vtt":
        content = generate_vtt(job.result.segments)
        mimetype = "text/vtt"
        ext = "vtt"
    else:
        return jsonify({"error": "Geçersiz format. Desteklenen: txt, srt, vtt"}), 400

    return Response(
        content,
        mimetype=mimetype,
        headers={
            "Content-Disposition": f"attachment; filename=transkript.{ext}",
        },
    )


# ── Main ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  Video Transkripsiyon Uygulaması")
    print(f"  Model: {WHISPER_MODEL}")
    print(f"  Maks dosya boyutu: 5 GB")
    print("  http://localhost:5000")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
